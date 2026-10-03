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
import socketserver
import subprocess
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from . import setup_save, setup_steps, summarize

IDLE_SECONDS = 30 * 60
AI_CHECK_SECONDS = 20
# The AIs Welcome offers, Claude in Claude Code and ChatGPT in Codex, and what checking one says.
AIS = ("claude", "codex")
AI_RESULTS = ("ready", "not-installed", "signed-out", "check-failed")
# The Sources a family can skip and add later through manage. Wilma too, for a school without it.
SKIPPABLE = ("wilma", "whatsapp", "myclub")
# What connecting Gmail on the page can say: the chat's `setup gmail` results, `no-address` for
# an address that isn't one, and `app-passwords-unavailable`, which the page's own button gives.
GMAIL_RESULTS = ("saved", "no-address", "not-an-app-password", "rejected", "no-connection",
                 "keychain-failed", "app-passwords-unavailable")
# The sites the page's buttons open, by name: the page itself names none.
SITES = {"app-passwords": setup_steps.APP_PASSWORDS_URL, "two-step": setup_steps.TWO_STEP_URL}
ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
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
        self._httpd = _HTTPServer(("127.0.0.1", 0), _Handler)
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
        progress = setup_save.read(self.config)["progress"]
        return {"languages": [{"code": c, "name": n} for c, n in LANGUAGES.items()],
                "language": self._chosen_language(),
                "preselected": mac_language(),
                "progress": progress,
                "welcome": self._welcome(progress),
                "connect": {"sources": [{"name": s, "skippable": s in SKIPPABLE}
                                        for s in setup_save.SOURCES]},
                "gmail": {"address": self._gmail_address()}}

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

    def check_ai(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Checks that the AI picked is installed and signed in, with its own status check."""
        if not (isinstance(raw, dict) and set(raw) == {"ai"} and raw["ai"] in AIS):
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Pick Claude or ChatGPT.", errors=["ai: should be claude or codex"])
        name = raw["ai"]
        codex_path = _section(self._config_data(), "llm").get("codex_path")
        program, status, install = {
            "claude": (summarize.find_claude, ["auth", "status"], summarize.CLAUDE_INSTALL),
            "codex": (lambda: summarize.find_codex_at(codex_path if isinstance(codex_path, str)
                                                      else None), ["login", "status"], None),
        }[name]
        program = program()
        if program is None:
            return HTTPStatus.OK, {"result": "not-installed", "ai": name,
                                   **({"install": install} if install else {})}
        try:
            # Only its exit code is read: the output can name the account.
            proc = subprocess.run([program, *status], capture_output=True, text=True,
                                  timeout=AI_CHECK_SECONDS, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            return HTTPStatus.OK, {"result": "check-failed", "ai": name}
        return HTTPStatus.OK, {"result": "ready" if proc.returncode == 0 else "signed-out", "ai": name}

    def save_welcome(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Saves Welcome's choices: the AI, the partner or only me, and pilot feedback, and moves
        setup on to Connect. The Recipients are saved once the setup parent's address is known."""
        try:
            answer = WelcomeAnswer.model_validate(raw)
        except ValidationError as e:
            return HTTPStatus.BAD_REQUEST, setup_save.invalid(e)
        partner = answer.partner and answer.partner.model_dump()
        answers: dict[str, Any] = {
            "ai": answer.ai,
            "feedback": {"enabled": answer.feedback},
            "progress": {"phase": "connect", "source": "wilma", "partner": partner},
        }
        parent = self._gmail_address()
        if parent:
            answers["recipients"] = self._recipients(parent, partner)
        with self._saving:
            out = setup_save.save(self.config, answers)
        if out["result"] != "saved":
            return HTTPStatus.CONFLICT, out
        return HTTPStatus.OK, out

    def choose_source(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Skips a Source, moving setup on to the next one to do, or opens one from the list,
        which brings a skipped one back."""
        if not (isinstance(raw, dict) and set(raw) == {"source", "action"}
                and raw["source"] in setup_save.SOURCES
                and (raw["action"] == "open" or raw["action"] == "skip" and raw["source"] in SKIPPABLE)):
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Open a Source, or skip one that can be skipped.",
                errors=[f"source: open one of {', '.join(setup_save.SOURCES)}, or skip one of "
                        f"{', '.join(SKIPPABLE)}"])
        name, skip = raw["source"], raw["action"] == "skip"
        with self._saving:
            statuses = setup_save.read(self.config)["progress"]["sources"]
            if skip:
                statuses[name] = "skipped"
            elif statuses[name] == "skipped":
                statuses[name] = "to-do"
            answers: dict[str, Any] = {"progress": {
                "sources": {name: statuses[name]},
                "source": _next_source(statuses) if skip else name}}
            if skip and name == "wilma":  # the Household's school doesn't use it
                answers["sources"] = {"wilma": {"enabled": False}}
            out = setup_save.save(self.config, answers)
        if out["result"] != "saved":
            return HTTPStatus.CONFLICT, out
        return HTTPStatus.OK, out

    def open_site(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Opens one of the sites the page sends the family to, in the default browser."""
        if not (isinstance(raw, dict) and set(raw) == {"site"} and raw["site"] in SITES):
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Open one of the page's own sites.",
                errors=[f"site: should be one of {', '.join(SITES)}"])
        url = SITES[raw["site"]]
        try:
            opened = subprocess.run(["open", url], capture_output=True).returncode == 0
        except OSError:
            opened = False
        return HTTPStatus.OK, {"result": "opened" if opened else "not-opened", "url": url}

    def connect_gmail(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Tests the App Password pasted into the page's own field with Gmail and stores it in
        the Keychain, through the chat's Gmail step (ADR 0007). Once it's saved, Gmail is done,
        the address is the Household's Gmail and the setup parent's, first among the Recipients.
        The App Password is never returned, logged, saved anywhere else or put on a command line."""
        if not (isinstance(raw, dict) and set(raw) == {"address", "password"}
                and isinstance(raw["address"], str) and isinstance(raw["password"], str)):
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Give the Gmail address and the App Password.",
                errors=["answers: should be the address and the password, as text"])
        address = raw["address"].strip().lower()
        if not ADDRESS.match(address):
            return HTTPStatus.OK, {"result": "no-address"}
        result, _ = setup_steps.gmail_sign_in(address, raw["password"])
        if result != "saved":
            return HTTPStatus.OK, {"result": result}
        with self._saving:
            progress = setup_save.read(self.config)["progress"]
            statuses = {**progress["sources"], "gmail": "done"}
            answers: dict[str, Any] = {
                "sources": {"gmail": {"address": address}},
                "progress": {"sources": {"gmail": "done"}, "source": _next_source(statuses)},
            }
            if "partner" in progress:  # Welcome's choice, waiting for the parent's address
                answers["recipients"] = self._recipients(address, progress["partner"])
            elif not _section(self._config_data(), "email").get("to"):
                answers["recipients"] = [{"address": address}]
            out = setup_save.save(self.config, answers)
        if out["result"] != "saved":
            return HTTPStatus.CONFLICT, out
        return HTTPStatus.OK, {**out, "address": address}

    def _recipients(self, parent: str, partner: dict[str, Any] | None) -> list[dict[str, Any]]:
        """The setup parent first, keeping the language saved for them, then the partner."""
        to = _section(self._config_data(), "email").get("to")
        first = to[0] if isinstance(to, list) and to else None
        first = {"address": first} if isinstance(first, str) else first
        same = isinstance(first, dict) and first.get("address") == parent
        also = partner and partner["address"].lower() != parent.lower()  # not the parent twice
        return [first if same else {"address": parent}, *([partner] if also else [])]

    def _gmail_address(self) -> str | None:
        """The Household's Gmail address, the setup parent's, once it's known."""
        address = _section(self._config_data(), "gmail").get("username")
        return address if isinstance(address, str) and address else None

    def _welcome(self, progress: dict[str, Any]) -> dict[str, Any]:
        """Welcome's choices as saved, each with its default until it is."""
        data = self._config_data()
        language = self._chosen_language() or mac_language()
        if "partner" in progress:
            saved = progress["partner"]
        else:  # a config the chat setup wrote: its second Recipient is the partner
            to = _section(data, "email").get("to")
            saved = to[1] if isinstance(to, list) and len(to) > 1 else {}
            saved = {"address": saved} if isinstance(saved, str) else saved
        partner = {"add": saved is not None, "address": "", "language": language}
        if isinstance(saved, dict):
            partner |= {k: v for k, v in saved.items() if k in ("address", "language")}
        backend = _section(data, "llm").get("backend")
        enabled = _section(data, "feedback").get("enabled")
        return {"ai": backend if backend in AIS else "claude", "partner": partner,
                "feedback": enabled if isinstance(enabled, bool) else True}

    def _config_data(self) -> dict[str, Any]:
        """The config as saved so far, or empty when there's none yet."""
        try:
            data = yaml.safe_load(self.config.read_text())
        except (OSError, yaml.YAMLError):
            return {}
        return data if isinstance(data, dict) else {}

    def _chosen_language(self) -> str | None:
        """The language already picked, from the config: None until there's one the page offers."""
        language = self._config_data().get("summary_language")
        return language if language in LANGUAGES else None


class PartnerAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    language: Literal["fi", "en", "zh"]


class WelcomeAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ai: Literal["claude", "codex"]
    partner: PartnerAnswer | None  # None for only me
    feedback: StrictBool


def _next_source(statuses: dict[str, str]) -> str | None:
    """The first Source in the list still to do, or None once each is done or skipped."""
    return next((s for s in setup_save.SOURCES if statuses.get(s) == "to-do"), None)


def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    section = data.get(key)
    return section if isinstance(section, dict) else {}


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


class _HTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer without its look-up of this Mac's host name when it binds, which
    can take several seconds on some Macs and networks while the family waits for the page."""

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name, self.server_port = str(host), int(port)


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
        actions = {"api/language": setup.choose_language, "api/ai": setup.check_ai,
                   "api/welcome": setup.save_welcome, "api/source": setup.choose_source,
                   "api/open": setup.open_site, "api/gmail": setup.connect_gmail}
        if method == "POST" and path in actions:
            raw = self._body()
            if raw is _NOT_JSON:
                return self._json(HTTPStatus.BAD_REQUEST, setup_save.outcome(
                    "invalid-answers", "The answers aren't JSON.", errors=["not JSON"]))
            return self._json(*actions[path](raw))
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
