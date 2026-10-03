"""setup page — serve the setup page on this Mac and open it in the browser (ADR 0006).

The page is plain HTML, CSS and JavaScript in `page/`, shipped with the program. It talks to this
server through a small JSON API under the same address, one call per action, and the server saves
the answers through `setup save`, so the page and the chat setup write the same config.

Safeguards, since anything on the Mac can reach a local port:
  - it listens on 127.0.0.1 only, on a free port
  - the address carries a one-time random code as its first path segment, and every request must
    have it; the page's own links are relative, so they carry it too
  - a request whose Host or Origin isn't the page's own address is refused, which stops other
    sites and DNS rebinding; an answer must also come from the page's own script, as JSON
  - it serves only the files in `page/`, by name, and they load nothing from the internet
  - it stops after 30 minutes without a request
It logs nothing about requests, since their address carries the code.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import secrets
import subprocess
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import yaml

from . import setup_save

IDLE_SECONDS = 30 * 60
POLL_SECONDS = 1.0
clock = time.monotonic  # the idle clock
# The page's languages, in the order it offers them, each named in itself.
LANGUAGES = {"fi": "Suomi", "en": "English", "zh": "中文"}
PAGE_DIR = Path(__file__).parent / "page"
FILES = {  # what the page is made of, by the path under its address
    "": ("index.html", "text/html; charset=utf-8"),
    "page.js": ("page.js", "text/javascript; charset=utf-8"),
    "page.css": ("page.css", "text/css; charset=utf-8"),
    "text.json": ("text.json", "application/json; charset=utf-8"),
}
MAX_BODY = 64 * 1024
HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; "
                               "img-src 'self'; connect-src 'self'; base-uri 'none'; "
                               "form-action 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",  # the address carries the code
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}


def register(steps) -> None:
    p = steps.add_parser("page", help="Open the setup page in the browser, served from this Mac "
                         "until 30 minutes without use")
    p.set_defaults(func=cmd_page)


def cmd_page(args: argparse.Namespace) -> int:
    config = Path(os.path.expanduser(os.path.expandvars(args.config or "~/.family/config.yaml")))
    server = SetupServer(config)
    out: dict[str, Any] = {"result": "opened", "url": server.url}
    try:
        opened = subprocess.run(["open", server.url], capture_output=True).returncode == 0
    except OSError:
        opened = False
    if not opened:
        out = {"result": "not-opened", "url": server.url,
               "next": f"The browser didn't open. Open this address in it: {server.url}"}
    print(json.dumps(out, ensure_ascii=False), flush=True)
    try:
        server.serve_until_idle()
    except KeyboardInterrupt:
        server.stop()
    return 0


class SetupServer:
    """The setup page's server for the config at `config`, listening from when it's made."""

    def __init__(self, config: Path) -> None:
        self.config = config
        self.code = secrets.token_urlsafe(32)
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._httpd.setup = self  # type: ignore[attr-defined]
        host, port = self._httpd.server_address[:2]
        self.address = (str(host), int(port))
        self.host = f"{host}:{port}"
        self.origin = f"http://{self.host}"
        self.url = f"{self.origin}/{self.code}/"
        self.last_request = clock()
        self._stopped = threading.Event()
        self._serving = False
        self._state = threading.Lock()
        self._saving = threading.Lock()  # one save at a time

    def serve_until_idle(self) -> None:
        """Serves until `stop`, or until IDLE_SECONDS pass without a request."""
        with self._state:
            if self._stopped.is_set():
                return
            self._serving = True
        thread = threading.Thread(target=self._httpd.serve_forever, args=(POLL_SECONDS,),
                                  daemon=True)
        thread.start()
        try:
            while not self._stopped.wait(POLL_SECONDS):
                if clock() - self.last_request >= IDLE_SECONDS:
                    break
        finally:
            self._httpd.shutdown()
            self._httpd.server_close()
            thread.join()

    def stop(self) -> None:
        with self._state:
            self._stopped.set()
            if not self._serving:
                self._httpd.server_close()

    # ── the API

    def state(self) -> dict[str, Any]:
        return {"languages": [{"code": c, "name": n} for c, n in LANGUAGES.items()],
                "language": self._chosen_language(),
                "preselected": mac_language(),
                "progress": setup_save.read(self.config)["progress"]}

    def choose_language(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Saves the language picked as the setup parent's: the Household's summary_language,
        which the Recipients without a language of their own read."""
        if not (isinstance(raw, dict) and set(raw) == {"language"} and raw["language"] in LANGUAGES):
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Pick one of the page's languages.",
                errors=[f"language: should be one of {', '.join(LANGUAGES)}"])
        with self._saving:
            out = setup_save.save(self.config, {"language": raw["language"]})
        if out["result"] != "saved":
            return HTTPStatus.CONFLICT, out
        return HTTPStatus.OK, {**out, "language": raw["language"]}

    def _chosen_language(self) -> str | None:
        """The language already picked, from the config: None until there's one the page offers."""
        try:
            data = yaml.safe_load(self.config.read_text())
        except (OSError, yaml.YAMLError):
            return None
        language = data.get("summary_language") if isinstance(data, dict) else None
        return language if language in LANGUAGES else None


def mac_language() -> str:
    """The first of the page's languages among the Mac's preferred ones, else English."""
    try:
        listed = subprocess.run(["defaults", "read", "-g", "AppleLanguages"], capture_output=True,
                                text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return "en"
    for tag in re.findall(r"[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]+)*", listed or ""):
        language = re.split(r"[-_]", tag)[0].lower()
        if language in LANGUAGES:
            return language
    return "en"


class _Handler(BaseHTTPRequestHandler):
    server_version = "ParentRecap"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:
        pass  # a request's address carries the code

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def _handle(self, method: str) -> None:
        setup: SetupServer = self.server.setup  # type: ignore[attr-defined]
        path = self._own_path(setup, method)
        if path is None:
            return self._send(HTTPStatus.FORBIDDEN, b"Forbidden\n", "text/plain; charset=utf-8")
        setup.last_request = clock()
        if not path:  # the address without its final slash, which the page's links need
            return self._send(HTTPStatus.MOVED_PERMANENTLY, b"", "text/plain; charset=utf-8",
                              Location=setup.url)
        path = path[1:]
        if method == "GET" and path in FILES:
            name, kind = FILES[path]
            return self._send(HTTPStatus.OK, (PAGE_DIR / name).read_bytes(), kind)
        if method == "GET" and path == "api/state":
            return self._json(HTTPStatus.OK, setup.state())
        if method == "POST" and path == "api/language":
            raw = self._body()
            if raw is _NOT_JSON:
                return self._json(HTTPStatus.BAD_REQUEST, setup_save.outcome(
                    "invalid-answers", "The answers aren't JSON.", errors=["not JSON"]))
            return self._json(*setup.choose_language(raw))
        return self._send(HTTPStatus.NOT_FOUND, b"Not found\n", "text/plain; charset=utf-8")

    def _own_path(self, setup: SetupServer, method: str) -> str | None:
        """The path after the code, from its slash (empty without one), or None if the request
        isn't the page's own: the wrong Host or Origin, or without the code."""
        if self.headers.get("Host") != setup.host:
            return None
        origin = self.headers.get("Origin")
        if (origin is not None or method != "GET") and origin != setup.origin:
            return None
        if method == "POST" and self.headers.get_content_type() != "application/json":
            return None
        _, _, rest = self.path.partition("?")[0].partition("/")
        code, slash, path = rest.partition("/")
        if not hmac.compare_digest(code.encode(), setup.code.encode()):
            return None
        return slash + path

    def _body(self) -> Any:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return _NOT_JSON
        if not 0 < length <= MAX_BODY:
            return _NOT_JSON
        try:
            return json.loads(self.rfile.read(length))
        except ValueError:
            return _NOT_JSON

    def _json(self, status: HTTPStatus, out: dict[str, Any]) -> None:
        self._send(status, json.dumps(out, ensure_ascii=False).encode(),
                   "application/json; charset=utf-8")

    def _send(self, status: HTTPStatus, body: bytes, kind: str, **headers: str) -> None:
        self.send_response(status)
        for name, value in {**HEADERS, **headers, "Content-Type": kind,
                            "Content-Length": str(len(body))}.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


_NOT_JSON = object()
