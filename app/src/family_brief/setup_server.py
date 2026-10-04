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
import shlex
import socketserver
import subprocess
import tempfile
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from . import chat_install, ops, setup_ai, setup_save, setup_steps, setup_wilma, summarize

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
# What signing in to Wilma on the page can say (ADR 0008). Only `sign-in-failed` offers the
# Terminal sign-in window: a wrong password is said to be just that.
WILMA_RESULTS = ("signed-in", "wrong-password", "sign-in-failed", "no-kids", "not-installed")
# What the town search can say when it finds nothing to show: no CLI, or a CLI without the list.
TOWN_RESULTS = ("not-installed", "no-list")
# Getting the pinned wilma CLI ready, which the town list comes with: while it installs, and why
# it couldn't be.
WILMA_READY_RESULTS = ("installing", "no-npm", "install-failed")
# The Terminal sign-in window: while the family signs in there, and how it ended if not signed in.
WILMA_WINDOW_RESULTS = ("waiting", "not-signed-in", "sign-in-failed", "timeout", "no-terminal",
                        "not-installed")
WILMA_WINDOW_SECONDS = 600
# Claude's token for the evening Brief: while the family authorizes in the browser, and how the
# sign-in, or the token pasted after the Terminal window, ended. `sign-in-failed` and `timeout`
# offer the Terminal window, whose token the page takes in a field.
CLAUDE_RESULTS = ("waiting", "saved", "not-installed", "sign-in-failed", "timeout", "not-a-token",
                  "test-call-failed", "keychain-failed")
CLAUDE_WINDOW_RESULTS = ("opened", "no-terminal", "not-installed")
CLAUDE_SIGN_IN_SECONDS = 600
# Codex's ChatGPT sign-in: signed in, signed out, `waiting` while its sign-in is open in the
# browser, and `login-failed` when that ended without signing in or didn't start.
CODEX_RESULTS = ("signed-in", "signed-out", "waiting", "login-failed", "not-installed",
                 "check-failed")
# What one read of WhatsApp can say, as `setup whatsapp` does, and whether the button opened
# Finder, with the Python to allow, and App Management.
WHATSAPP_RESULTS = ("readable", "no-permission", "waiting", "not-installed", "unreadable",
                    "bg-failed")
WHATSAPP_OPEN_RESULTS = ("opened", "not-opened")
WHATSAPP_READ_SECONDS = setup_steps.WHATSAPP_READ_SECONDS
# What saving a Kid's MyClub calendar link can say, as `setup myclub` does, and adding a Kid by
# the name the family calls them, for a Household without Wilma.
MYCLUB_RESULTS = ("saved", "not-a-myclub-link", "link-failed", "not-a-calendar", "save-failed")
MYCLUB_KID_RESULTS = ("added", "kid-exists")
# "Continue in the chat": the setup skill as each AI's chat starts it, and what handing over
# can say. Claude Code opens in a Terminal window; a Codex family is told what to type in Codex.
CHAT_SKILLS = {"claude": "/parent-recap:setup", "codex": "$parent-recap-setup"}
CHAT_RESULTS = {"claude": ("opened", "no-terminal", "not-installed"), "codex": ("open-codex",)}
# The sites the page's buttons open, by name: the page itself names none.
SITES = {"app-passwords": setup_steps.APP_PASSWORDS_URL, "two-step": setup_steps.TWO_STEP_URL,
         "myclub": setup_steps.MYCLUB_URL}
# The apps they open, each tried in turn: the Passwords app, or before macOS 15, where the
# passwords were in System Settings.
APPS = {"passwords": (["-a", "Passwords"],
                      ["x-apple.systempreferences:com.apple.Passwords-Settings.extension"])}
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
        self._wilma = threading.Lock()  # one Wilma sign-in at a time, since each writes its profile
        self._window: dict[str, Any] | None = None  # the Terminal sign-in window's, once opened
        self._ai = threading.Lock()  # one AI sign-in at a time
        self._claude: dict[str, Any] | None = None  # Claude's sign-in's, once started
        self._claude_script: tempfile.TemporaryDirectory | None = None  # its Terminal script's
        self._codex: subprocess.Popen | None = None  # Codex's sign-in, once started
        self._whatsapp = threading.Lock()  # one read at a time, since each is a launchd job
        self._chat_script: tempfile.TemporaryDirectory | None = None  # the chat's Terminal script's
        # What lets the family ask for changes in the chat later, by AI, once Welcome has saved
        # it: `installing`, `installed`, `install-failed`, `not-installed` (no Claude Code) or
        # `no-plugin-copy` (the program wasn't installed by the install line).
        self._chat_installs: dict[str, str] = {}
        self._chat_threads: dict[str, threading.Thread] = {}  # each AI's latest install

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
            self._stopped.set()  # also ends Claude's sign-in
            if not self._serving:
                self._httpd.server_close()
        with self._ai:
            if self._codex is not None and self._codex.poll() is None:
                self._codex.kill()
                self._codex.wait()
            if self._claude_script is not None:
                self._claude_script.cleanup()
            if self._chat_script is not None:
                self._chat_script.cleanup()
            threads = list(self._chat_threads.values())
        for thread in threads:  # an install isn't cut off halfway
            thread.join()

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
                "gmail": {"address": self._gmail_address()},
                "myclub": self._myclub(),
                "chat": dict(self._chat_installs)}

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
        program = self._ai_program(name)
        if program is None:
            install = summarize.CLAUDE_INSTALL if name == "claude" else None
            return HTTPStatus.OK, {"result": "not-installed", "ai": name,
                                   **({"install": install} if install else {})}
        return HTTPStatus.OK, {"result": _ai_status(program, name), "ai": name}

    def _ai_program(self, name: str) -> str | None:
        if name == "claude":
            return summarize.find_claude()
        codex_path = _section(self._config_data(), "llm").get("codex_path")
        return summarize.find_codex_at(codex_path if isinstance(codex_path, str) else None)

    # ── the AI sign-in for the evening Brief

    def sign_in_claude(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Starts `claude setup-token` in a pseudo-terminal, which opens Anthropic's Authorize
        page in the browser, and waits for its token in the background: the page asks how it
        went with `check_claude`. The family copies nothing."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, _nothing_to_give()
        program = summarize.find_claude()
        if program is None:
            return HTTPStatus.OK, {"result": "not-installed", "install": summarize.CLAUDE_INSTALL}
        with self._ai:
            if self._claude is not None and self._claude["result"] is None:
                return HTTPStatus.OK, {"result": "waiting"}  # already signing in
            sign_in: dict[str, Any] = {"result": None}
            self._claude = sign_in
        threading.Thread(target=self._wait_for_claude, args=(sign_in, program),
                         daemon=True).start()
        return HTTPStatus.OK, {"result": "waiting"}

    def check_claude(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """How Claude's sign-in went: `waiting` while the family is still authorizing."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, _nothing_to_give()
        sign_in = self._claude
        if sign_in is None:
            return HTTPStatus.OK, {"result": "no-sign-in"}
        return HTTPStatus.OK, sign_in["result"] or {"result": "waiting"}

    def _wait_for_claude(self, sign_in: dict[str, Any], program: str) -> None:
        try:
            result, token = setup_ai.read_setup_token(program, CLAUDE_SIGN_IN_SECONDS, self._stopped)
            if token is not None:
                result, _ = setup_steps.claude_token_sign_in(program, token)
            sign_in["result"] = self._ai_signed_in() if result == "saved" else {"result": result}
        except Exception:  # never leave the page waiting
            sign_in["result"] = {"result": "sign-in-failed"}

    def open_claude_window(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Opens `claude setup-token` in a Terminal window, as the chat setup does, for when the
        page's own sign-in fails: the family copies the token it shows into the page's field."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, _nothing_to_give()
        program = summarize.find_claude()
        if program is None:
            return HTTPStatus.OK, {"result": "not-installed", "install": summarize.CLAUDE_INSTALL}
        with self._ai:
            if self._claude_script is not None:
                self._claude_script.cleanup()
            # Kept until the next window or the page stops, since Terminal runs the script from it.
            self._claude_script = tempfile.TemporaryDirectory(prefix="parent-recap-claude-")
            script = Path(self._claude_script.name) / "Claude sign-in.command"
            script.write_text(setup_steps.setup_token_script(program, "the field on the setup page"))
            script.chmod(0o700)
        opened = _open(["-a", "Terminal", str(script)])
        return HTTPStatus.OK, {"result": "opened" if opened else "no-terminal"}

    def save_claude_token(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Tests the token pasted into the page's own field and stores it, as the chat's Claude
        step does with the dialog's. The token is never returned, logged or put on a command line."""
        if not (isinstance(raw, dict) and set(raw) == {"token"} and isinstance(raw["token"], str)):
            # Names the field only: the answers carry the token.
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Paste the token.", errors=["token: should be text"])
        program = summarize.find_claude()
        if program is None:
            return HTTPStatus.OK, {"result": "not-installed", "install": summarize.CLAUDE_INSTALL}
        result, _ = setup_steps.claude_token_sign_in(program, raw["token"])
        if result != "saved":
            return HTTPStatus.OK, {"result": result}
        with self._ai:
            if self._claude_script is not None:  # the window's script has done its work
                self._claude_script.cleanup()
                self._claude_script = None
        out = self._ai_signed_in()
        return (HTTPStatus.OK if out["result"] == "saved" else HTTPStatus.CONFLICT), out

    def check_codex(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Whether Codex is signed in, with its own status check. Once it is, the AI sign-in is
        done. While its sign-in is open in the browser, signed out is `waiting`."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, _nothing_to_give()
        program = self._ai_program("codex")
        if program is None:
            return HTTPStatus.OK, {"result": "not-installed"}
        with self._ai:  # before the status, so a sign-in that ends meanwhile isn't called failed
            login = self._codex
            ended = login is not None and login.poll() is not None
        status = _ai_status(program, "codex")
        if status == "ready":
            out = self._ai_signed_in()
            return (HTTPStatus.OK, {**out, "result": "signed-in"}) if out["result"] == "saved" \
                else (HTTPStatus.CONFLICT, out)
        if status != "signed-out":
            return HTTPStatus.OK, {"result": status}
        if login is None:
            return HTTPStatus.OK, {"result": "signed-out"}
        if ended:
            with self._ai:
                if self._codex is login:
                    self._codex = None  # said once, and the family can start it again
            return HTTPStatus.OK, {"result": "login-failed"}
        return HTTPStatus.OK, {"result": "waiting"}

    def sign_in_codex(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Starts `codex login`, which opens the ChatGPT sign-in in the browser. The page then
        checks with `check_codex` until it's signed in."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, _nothing_to_give()
        program = self._ai_program("codex")
        if program is None:
            return HTTPStatus.OK, {"result": "not-installed"}
        with self._ai:
            if self._codex is None or self._codex.poll() is not None:
                self._codex = setup_ai.start_codex_login(program)
            started = self._codex is not None
        return HTTPStatus.OK, {"result": "waiting" if started else "login-failed"}

    # ── WhatsApp

    def check_whatsapp(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Reads WhatsApp once through a `bg` job, with the scheduled job's permission, as `setup
        whatsapp` does. The page checks again every few seconds, so once the family has given
        the permission, WhatsApp is done by itself, and the groups found are kept in setup's
        progress for the check page. Errors aren't returned: they can name the Mac's files."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, _nothing_to_give()
        with self._whatsapp:
            read = setup_steps.read_whatsapp_through_bg(
                str(self.config), setup_steps.WHATSAPP_DAYS, WHATSAPP_READ_SECONDS)
        if read["result"] != "readable":
            return HTTPStatus.OK, {"result": read["result"]}
        with self._saving:
            # A read takes a while, and the family may have skipped WhatsApp or opened another
            # Source meanwhile: their choice stands.
            progress = setup_save.read(self.config)["progress"]
            if progress["sources"]["whatsapp"] == "skipped":
                return HTTPStatus.OK, {"result": "readable", "chats": read["chats"],
                                       "progress": progress}
            statuses = {**progress["sources"], "whatsapp": "done"}
            here = progress["source"] == "whatsapp"
            out = setup_save.save(self.config, {"progress": {
                "sources": {"whatsapp": "done"},
                "source": _next_source(statuses) if here else progress["source"],
                "whatsapp_chats": read["chats"]}})
        if out["result"] != "saved":
            return HTTPStatus.CONFLICT, out
        return HTTPStatus.OK, {**out, "result": "readable", "chats": read["chats"]}

    def open_app_management(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Selects the scheduled job's Python in Finder and opens App Management next to it, as
        `setup whatsapp` does, for the family to drag it in and turn its switch on."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, _nothing_to_give()
        _, failed = ops.show_python_for_app_management()
        return HTTPStatus.OK, {"result": "not-opened" if failed else "opened"}

    # ── MyClub

    def save_myclub_link(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Checks the calendar link pasted into the page's own field for one of the Household's
        Kids and saves it, through the chat's MyClub step. Once a link is saved, MyClub is done,
        and setup moves on once every Kid has one; until then the parent can add the next Kid's.
        The link is a secret (ADR 0007): it's never returned, logged or put on a command line."""
        if not (isinstance(raw, dict) and set(raw) == {"kid", "link"}
                and isinstance(raw["link"], str) and raw["kid"] in self._kid_names()):
            # Names the fields only: the answers carry the link.
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Pick one of the Kids and paste their calendar link.",
                errors=["answers: should be one of the Kids' names and the link, as text"])
        # Downloaded before the lock, so a slow MyClub holds up no other save.
        result, extra = setup_steps.myclub_link(self.config, raw["kid"], raw["link"], self._saving)
        if result != "saved":
            return HTTPStatus.OK, {"result": result}  # without the error, which can name files
        with self._saving:
            progress = setup_save.read(self.config)["progress"]
            statuses = {**progress["sources"], "myclub": "done"}
            here = progress["source"] == "myclub"
            linked = all(k["linked"] for k in self._myclub()["kids"])
            out = setup_save.save(self.config, {"progress": {
                "sources": {"myclub": "done"},
                "source": _next_source(statuses) if here and linked else progress["source"]}})
        if out["result"] != "saved":
            return HTTPStatus.CONFLICT, out
        return HTTPStatus.OK, {**out, "kid": raw["kid"], "events": extra["events"],
                               "kids": self._myclub()["kids"]}

    def add_kid(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """For a Household without Wilma: adds a Kid by the name the family calls them, so their
        MyClub link can be saved. With Wilma, the Kids are the ones Wilma lists."""
        name = raw.get("name") if isinstance(raw, dict) and set(raw) == {"name"} else None
        if not (isinstance(name, str) and name.strip() and self._myclub()["add"]):
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Type the Kid's name. A Household with Wilma has its Kids from "
                "there.", errors=["name: should be text, for a Household without Wilma"])
        name = name.strip()
        with self._saving:
            names = self._kid_names()
            if name.casefold() in (n.casefold() for n in names):
                return HTTPStatus.OK, {"result": "kid-exists"}
            # Only the names: each Kid already there keeps the rest, such as their link.
            out = setup_save.save(self.config, {"kids": [{"name": n} for n in [*names, name]]})
        if out["result"] != "saved":
            return HTTPStatus.CONFLICT, out
        return HTTPStatus.OK, {**out, "result": "added", "kids": self._myclub()["kids"]}

    def myclub_done(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Moves setup on from MyClub once a link is saved, leaving the other Kids without one."""
        with self._saving:
            statuses = setup_save.read(self.config)["progress"]["sources"]
            if raw != {} or statuses["myclub"] != "done":
                return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                    "invalid-answers", "Save a Kid's link first, or skip MyClub.",
                    errors=["answers: should be {}, once a link is saved"])
            out = setup_save.save(self.config, {"progress": {"source": _next_source(statuses)}})
        if out["result"] != "saved":
            return HTTPStatus.CONFLICT, out
        return HTTPStatus.OK, out

    def _myclub(self) -> dict[str, Any]:
        """The Kids known so far, each with whether their link is saved but never the link, and
        whether the family adds them here: only without Wilma."""
        data = self._config_data()
        kids = [k for k in data.get("kids") or []
                if isinstance(k, dict) and isinstance(k.get("name"), str)]
        return {"kids": [{"name": k["name"], "linked": bool(k.get("myclub_ical_url"))}
                         for k in kids],
                "add": _section(data, "wilma").get("enabled") is not True}

    def _kid_names(self) -> list[str]:
        return [k["name"] for k in self._myclub()["kids"]]

    # ── Continue in the chat

    def continue_in_chat(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Hands setup over to the chat with the AI picked: opens Claude Code at the setup skill
        in a Terminal window, or says what to type in Codex. The skill reads the progress and the
        answers saved so far, and carries on from the same phase and Source. The plugin or the
        Codex skills are installed first if they aren't yet, since the skill comes with them."""
        if not (isinstance(raw, dict) and set(raw) == {"ai"} and raw["ai"] in AIS):
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Pick Claude or ChatGPT.", errors=["ai: should be claude or codex"])
        skill = CHAT_SKILLS[raw["ai"]]
        if self._chat_installs.get(raw["ai"]) != "installed":  # the skill must be there to start
            self._install_chat(raw["ai"]).join()
        if raw["ai"] == "codex":
            return HTTPStatus.OK, {"result": "open-codex", "type": skill}
        program = summarize.find_claude()
        if program is None:
            return HTTPStatus.OK, {"result": "not-installed", "install": summarize.CLAUDE_INSTALL}
        with self._ai:
            if self._chat_script is not None:
                self._chat_script.cleanup()
            # Kept until the next window or the page stops, since Terminal runs the script from it.
            self._chat_script = tempfile.TemporaryDirectory(prefix="parent-recap-chat-")
            script = Path(self._chat_script.name) / "Parent Recap setup.command"
            script.write_text(chat_script(program))
            script.chmod(0o700)
        if _open(["-a", "Terminal", str(script)]):
            return HTTPStatus.OK, {"result": "opened"}
        return HTTPStatus.OK, {"result": "no-terminal", "type": shlex.join([program, skill])}

    def _ai_signed_in(self) -> dict[str, Any]:
        """Saves the AI sign-in as done, moving setup on to the next Source."""
        with self._saving:
            statuses = {**setup_save.read(self.config)["progress"]["sources"], "ai": "done"}
            return setup_save.save(self.config, {"progress": {
                "sources": {"ai": "done"}, "source": _next_source(statuses)}})

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
        self._install_chat(answer.ai)
        return HTTPStatus.OK, out

    def _install_chat(self, name: str) -> threading.Thread:
        """Installs Claude Code's plugin or the Codex skills in the background, so that changes
        after setup work in the chat with the AI picked, unless they're being installed already.
        `api/state` says how it went."""
        with self._ai:
            if self._chat_installs.get(name) == "installing":
                return self._chat_threads[name]
            self._chat_installs[name] = "installing"
            thread = threading.Thread(target=self._installing_chat, args=(name,), daemon=True)
            self._chat_threads[name] = thread
        thread.start()
        return thread

    def _installing_chat(self, name: str) -> None:
        try:
            if name == "claude":
                program = summarize.find_claude()
                result = "not-installed" if program is None \
                    else chat_install.install_claude_plugin(program)
            else:
                root = chat_install.plugin_copy()
                if root is None:
                    result = "no-plugin-copy"
                else:
                    chat_install.install_codex_skills(root)
                    result = "installed"
        except Exception:  # never leave it installing
            result = "install-failed"
        self._chat_installs[name] = result

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
        """Opens one of the sites the page sends the family to, in the default browser, or one of
        the Mac's apps."""
        if not (isinstance(raw, dict) and set(raw) == {"site"} and raw["site"] in {*SITES, *APPS}):
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Open one of the page's own sites.",
                errors=[f"site: should be one of {', '.join([*SITES, *APPS])}"])
        if raw["site"] in APPS:
            opened = any(_open(args) for args in APPS[raw["site"]])
            return HTTPStatus.OK, {"result": "opened" if opened else "not-opened"}
        url = SITES[raw["site"]]
        return HTTPStatus.OK, {"result": "opened" if _open([url]) else "not-opened", "url": url}

    # ── Wilma (ADR 0008)

    def wilma_ready(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Installs the pinned wilma CLI unless it's there, since the town list comes with it."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Nothing to give.", errors=["answers: should be {}"])
        result = setup_wilma.install()
        return HTTPStatus.OK, {"result": result,
                               **({"install": setup_wilma.NODE_INSTALL} if result == "no-npm" else {})}

    def find_towns(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """The entries of Wilma's list that the town or name typed finds, each with its town."""
        if not (isinstance(raw, dict) and set(raw) == {"query"} and isinstance(raw["query"], str)):
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Type a town.", errors=["query: should be text"])
        if not setup_wilma.installed():
            return HTTPStatus.OK, {"result": "not-installed"}
        listed = setup_wilma.tenants()
        if listed is None:
            return HTTPStatus.OK, {"result": "no-list"}
        return HTTPStatus.OK, {"result": "found", "towns": setup_wilma.search(listed, raw["query"])}

    def sign_in_wilma(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Signs in to the Wilma picked from the list with the username and the password typed
        into the page's own fields, by writing the CLI's profile and reading the Kids with it.
        Once signed in, Wilma is done, and the Kids, as Wilma spells them, and the town are the
        Household's. The password is never returned, logged or put on a command line."""
        fields = {"url", "town", "username", "password"}
        if not (isinstance(raw, dict) and set(raw) == fields
                and all(isinstance(raw[k], str) for k in ("url", "username", "password"))
                and (raw["town"] is None or isinstance(raw["town"], str))
                and raw["username"].strip() and raw["password"]):
            return HTTPStatus.BAD_REQUEST, _wilma_invalid()
        if not setup_wilma.installed():
            return HTTPStatus.OK, {"result": "not-installed"}
        listed = setup_wilma.tenants()
        if listed is None:
            return HTTPStatus.OK, {"result": "no-list"}
        tenant = setup_wilma.entry(listed, raw["url"])
        towns = setup_wilma.towns_of(tenant) if tenant else []
        if tenant is None or (raw["town"] not in towns if towns else raw["town"] is not None):
            return HTTPStatus.BAD_REQUEST, _wilma_invalid()
        with self._wilma:
            result, kids = setup_wilma.sign_in(tenant, raw["username"].strip(), raw["password"])
        if result != "signed-in":
            return HTTPStatus.OK, {"result": result}
        return self._signed_in(kids, raw["town"])

    def save_town(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """For a Household whose school doesn't use Wilma: saves the town picked from Wilma's list
        as its city, and moves setup on with Wilma skipped."""
        listed = setup_wilma.tenants() or []
        if not (isinstance(raw, dict) and set(raw) == {"town"} and isinstance(raw["town"], str)
                and any(raw["town"] in setup_wilma.towns_of(t) for t in listed)):
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Pick your town from the list.",
                errors=["town: should be a town in Wilma's list"])
        with self._saving:
            statuses = {**setup_save.read(self.config)["progress"]["sources"], "wilma": "skipped"}
            out = setup_save.save(self.config, {
                "city": raw["town"], "sources": {"wilma": {"enabled": False}},
                "progress": {"sources": {"wilma": "skipped"}, "source": _next_source(statuses)}})
        if out["result"] != "saved":
            return HTTPStatus.CONFLICT, out
        return HTTPStatus.OK, out

    def open_wilma_window(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Opens the Terminal sign-in window (#74), for when the page's own sign-in fails for any
        reason but a wrong password, and waits for it in the background: the page asks how it
        went with `check_wilma_window`. `town` is the one picked on the page, if any."""
        if not (isinstance(raw, dict) and set(raw) == {"town"}
                and (raw["town"] is None or isinstance(raw["town"], str))):
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Give the town picked, or null.",
                errors=["town: should be text or null"])
        with self._wilma:
            if self._window is not None and self._window["result"] is None:
                return HTTPStatus.OK, {"result": "waiting"}  # the window is already open
            window: dict[str, Any] = {"result": None}
            self._window = window
        threading.Thread(target=self._wait_for_window, daemon=True,
                         args=(window, self._chosen_language() or "en", raw["town"])).start()
        return HTTPStatus.OK, {"result": "waiting"}

    def check_wilma_window(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """How the Terminal sign-in window went: `waiting` while the family is still in it."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Nothing to give.", errors=["answers: should be {}"])
        window = self._window
        if window is None:
            return HTTPStatus.OK, {"result": "no-window"}
        return HTTPStatus.OK, window["result"] or {"result": "waiting"}

    def _wait_for_window(self, window: dict[str, Any], language: str, town: str | None) -> None:
        try:
            out = setup_steps.sign_in_in_terminal(language, WILMA_WINDOW_SECONDS)
            if out["result"] == "signed-in":
                kids = [k for k in out["kids"] if isinstance(k.get("name"), str) and k["name"]]
                _, done = self._signed_in(kids, self._city(out.get("wilma_address"), town))
            else:
                done = {"result": out["result"]}
        except Exception:  # never leave the page waiting
            done = {"result": "sign-in-failed"}
        window["result"] = done

    def _signed_in(self, kids: list[dict[str, Any]],
                   city: str | None) -> tuple[HTTPStatus, dict[str, Any]]:
        """Saves the Kids Wilma lists and the town, with Wilma on and done."""
        with self._saving:
            statuses = {**setup_save.read(self.config)["progress"]["sources"], "wilma": "done"}
            answers: dict[str, Any] = {
                "kids": [{"name": k["name"],
                          **({"school": k["school"]} if k.get("school") else {}),
                          **({"class_name": k["class"]} if k.get("class") else {})} for k in kids],
                "sources": {"wilma": {"enabled": True}},
                "progress": {"sources": {"wilma": "done"}, "source": _next_source(statuses)},
            }
            if city:
                answers["city"] = city
            out = setup_save.save(self.config, answers)
        if out["result"] != "saved":
            return HTTPStatus.CONFLICT, out
        return HTTPStatus.OK, {"result": "signed-in", "kids": [k["name"] for k in kids],
                               "city": city, "progress": out["progress"]}

    @staticmethod
    def _city(address: str | None, town: str | None) -> str | None:
        """The town of the Wilma signed in to in the Terminal window: the one picked on the page
        if it's one of that Wilma's, else its first."""
        listed = setup_wilma.tenants() or []
        tenant = next((t for t in listed if urlparse(t["url"]).hostname == address), None)
        towns = setup_wilma.towns_of(tenant) if tenant else []
        if towns:
            return town if town in towns else towns[0]
        return setup_steps.WILMA_CITIES.get(address or "")

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


def chat_script(program: str) -> str:
    """The Terminal window's script for "Continue in the chat": Claude Code in the family's home
    folder, at the setup skill."""
    return f"#!/bin/sh\ncd ~ || exit 1\nexec {shlex.join([program, CHAT_SKILLS['claude']])}\n"


def _open(args: list[str]) -> bool:
    try:
        return subprocess.run(["open", *args], capture_output=True).returncode == 0
    except OSError:
        return False


def _ai_status(program: str, name: str) -> str:
    """`ready`, `signed-out` or `check-failed`, from the AI's own status check."""
    status = ["auth", "status"] if name == "claude" else ["login", "status"]
    try:
        # Only its exit code is read: the output can name the account.
        proc = subprocess.run([program, *status], capture_output=True, text=True,
                              timeout=AI_CHECK_SECONDS, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return "check-failed"
    return "ready" if proc.returncode == 0 else "signed-out"


def _nothing_to_give() -> dict[str, Any]:
    return setup_save.outcome("invalid-answers", "Nothing to give.", errors=["answers: should be {}"])


def _wilma_invalid() -> dict[str, Any]:
    # Names the fields only: the answers carry the password.
    return setup_save.outcome(
        "invalid-answers", "Pick your town's Wilma from the list and give the username and the "
        "password.", errors=["answers: should be the url and town picked from the list, and the "
                             "username and the password, as text"])


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
        # Read all of what was sent before any answer: answering and closing while the body is
        # still coming in resets the connection, and the sender never sees the answer.
        body = self._read_body()
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
                   "api/open": setup.open_site, "api/gmail": setup.connect_gmail,
                   "api/wilma/install": setup.wilma_ready, "api/towns": setup.find_towns,
                   "api/wilma": setup.sign_in_wilma, "api/town": setup.save_town,
                   "api/wilma/terminal": setup.open_wilma_window,
                   "api/wilma/check": setup.check_wilma_window,
                   "api/claude": setup.sign_in_claude, "api/claude/check": setup.check_claude,
                   "api/claude/terminal": setup.open_claude_window,
                   "api/claude/token": setup.save_claude_token,
                   "api/codex": setup.check_codex, "api/codex/login": setup.sign_in_codex,
                   "api/whatsapp/check": setup.check_whatsapp,
                   "api/whatsapp/open": setup.open_app_management,
                   "api/myclub": setup.save_myclub_link, "api/myclub/kid": setup.add_kid,
                   "api/myclub/done": setup.myclub_done,
                   "api/chat": setup.continue_in_chat}
        if method == "POST" and path in actions:
            raw = _json_body(body)
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

    def _read_body(self) -> bytes | None:
        """The request's body, read to its end; None if it has no length that can be read, or is
        longer than MAX_BODY, whose rest is read and dropped."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length < 0:
            return None
        body = self.rfile.read(min(length, MAX_BODY))
        remaining = length - len(body)
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, MAX_BODY))
            if not chunk:
                break
            remaining -= len(chunk)
        return body if length <= MAX_BODY else None

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


def _json_body(body: bytes | None) -> Any:
    if not body:
        return _NOT_JSON
    try:
        return json.loads(body)
    except ValueError:
        return _NOT_JSON
