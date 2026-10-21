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
import shlex
import socketserver
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from secrets import token_urlsafe
from typing import Any, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from . import (chat_install, feedback, llm, ops, run_lock, setup_ai, setup_connect, setup_gmail,
               setup_myclub, setup_report, setup_save, setup_status, setup_steps, setup_whatsapp,
               setup_wilma)
from . import sources as source_registry
from .actions import email as email_action
from .config import Config, Kid, LLMConfig
from .setup_connect import Nothing, Reply, Strict, parse
from .state import State
from .system import desktop, desktop_macos, jobs, macos_only, secrets, wake

IDLE_SECONDS = 30 * 60
AI_CHECK_SECONDS = 20
# The AIs Welcome offers, Claude in Claude Code, ChatGPT in Codex and, for an invited Household,
# the maintainers' gateway (ADR 0014), and what checking one says. The gateway needs nothing on
# the Mac, so it's always ready, and it has no chat to carry on in.
AIS = ("claude", "codex", "gateway")
# The gateway stays Welcome's default on Windows.
WINDOWS_AIS = ("gateway", "codex", "claude")
AI_RESULTS = ("ready", "not-installed", "signed-out", "check-failed")
# The Sources a family can skip and add later through manage, in the page's order.
SKIPPABLE = tuple(s.name for s in source_registry.in_connect_order() if s.skippable)
# What connecting Gmail on the page can say: the chat's `setup gmail` results, `no-address` for
# an address that isn't one, and `app-passwords-unavailable`, which the page's own button gives.
GMAIL_RESULTS = ("saved", "no-address", "not-an-app-password", "rejected", "no-connection",
                 "keychain-not-reachable", "keychain-failed", "app-passwords-unavailable")
# What signing in to Wilma on the page can say (ADR 0008). Only `sign-in-failed` offers the
# Terminal sign-in window: a wrong password is said to be just that.
WILMA_RESULTS = ("signed-in", "wrong-password", "sign-in-failed", "no-kids", "not-installed")
# What the town search can say when it finds nothing to show: no CLI, or a CLI without the list.
TOWN_RESULTS = ("not-installed", "no-list")
# Getting the pinned wilma CLI ready, which the town list comes with: while it installs, and why
# it couldn't be.
WILMA_READY_RESULTS = ("installing", "no-node", "install-failed")
# The Terminal sign-in window: while the family signs in there, and how it ended if not signed in.
WILMA_WINDOW_RESULTS = ("waiting", "not-signed-in", "sign-in-failed", "timeout", "no-terminal",
                        "not-installed")
WILMA_WINDOW_SECONDS = 600
# Claude's token for the evening Brief: while the family authorizes in the browser, and how the
# sign-in, or the token pasted after the Terminal window, ended. `sign-in-failed` and `timeout`
# offer the Terminal window, whose token the page takes in a field. On Windows, which signs in
# only in that window, the page installs Claude Code first: `installing`, and `install-failed` once.
CLAUDE_RESULTS = ("waiting", "saved", "not-installed", "sign-in-failed", "timeout", "not-a-token",
                  "test-call-failed", "keychain-not-reachable", "keychain-failed", "installing",
                  "install-failed")
CLAUDE_WINDOW_RESULTS = ("opened", "no-terminal", "not-installed")
CLAUDE_SIGN_IN_SECONDS = 600
# Codex's ChatGPT sign-in: signed in, signed out, `waiting` while its sign-in is open in the
# browser, and `login-failed` when that ended without signing in or didn't start. At Finish,
# `test-call-failed` when a signed-in Codex didn't answer the test call a switch needs. On
# Windows, where the page installs the Codex CLI, `installing`, and `install-failed` once.
CODEX_RESULTS = ("signed-in", "signed-out", "waiting", "login-failed", "not-installed",
                 "check-failed", "test-call-failed", "installing", "install-failed")
# The Gateway Key pasted into the page, tested and stored as `setup gateway` does.
GATEWAY_RESULTS = ("saved", "not-a-key", "rejected", "no-connection", "test-call-failed",
                   "keychain-not-reachable", "keychain-failed")
# What one read of WhatsApp can say, as `setup whatsapp` does, and whether the button opened
# Finder, with the Python to allow, and Full Disk Access.
WHATSAPP_RESULTS = ("readable", "no-permission", "waiting", "not-installed", "unreadable",
                    "bg-failed")
WHATSAPP_OPEN_RESULTS = ("opened", "not-opened")
WHATSAPP_READ_SECONDS = setup_whatsapp.WHATSAPP_READ_SECONDS
# What saving a Kid's MyClub calendar link can say, as `setup myclub` does, and adding a Kid by
# the name the family calls them, for a Household without Wilma.
MYCLUB_RESULTS = ("saved", "not-a-myclub-link", "link-failed", "not-a-calendar", "save-failed",
                  "same-link")
MYCLUB_KID_RESULTS = ("added", "kid-exists")
# Working: reading the Gmail senders for the check page, how far it is and how it ended, and
# `no-read` when this server hasn't started one. Wilma's Kids and WhatsApp's groups were read in
# Connect already.
WORKING_RESULTS = ("reading", "read", "read-failed", "no-read")
SENDER_DAYS = 60
# Check: the health check run once the check page is confirmed, and how it ended. Warnings alone
# don't hold setup up.
HEALTH_RESULTS = ("checking", "ok", "not-ok", "no-check")
# Doctor's checks as the check page names them, by the key doctor gives each, never its words.
# Any other is `other`.
HEALTH_CHECKS = {"config": "config", "recipients": "recipients", "language": "language",
                 "gmail": "gmail", "claude": "ai", "codex": "ai", "gateway": "gateway",
                 "gateway-key": "gateway", "wilma": "wilma", "whatsapp": "whatsapp",
                 "whatsapp-python": "whatsapp", "myclub": "myclub", "calendar": "calendar",
                 "feedback": "feedback", "weekend": "weekend"}
# A language's and a Kid's MyClub check carry the language or the Kid too, from their subject.
HEALTH_SUBJECTS = {"language": "language", "myclub": "kid"}
# The scheduled job's checks, which aren't run before Finish installs it; Windows adds the last two.
SCHEDULE_CHECKS = frozenset({"schedule", "last-run", "python-path", "wake", "sign-in-after-update"})
# Doctor's checks as Finish names them, with the scheduled job's. Python path is always OK, and
# Windows' Wake check says what the wake outcome does, so Finish leaves it out.
FINISH_CHECKS = {**HEALTH_CHECKS, "schedule": "schedule", "last-run": "last-run",
                 "sign-in-after-update": "sign-in-after-update"}
FINISH_LEAVES_OUT = frozenset({"wake"})
HEALTH_STATUSES = {ops.OK: "ok", ops.WARN: "warn", ops.FAIL: "fail"}
# First Brief: the real Brief made through a bg job, which reads WhatsApp with the scheduled job's
# permission, without sending it: `making` while it's made, with the Source it's reading or
# `writing`, then `made` or `make-failed`, and `no-brief` when this server hasn't made one.
BRIEF_RESULTS = ("making", "made", "make-failed", "no-brief")
BRIEF_LOOKBACK_HOURS = 72  # a single day often has nothing new
BRIEF_SECONDS = 900
# Sending it to the setup parent: `busy` while an evening run is going, which could send it too.
SEND_RESULTS = ("sent", "send-failed", "busy")
# Finish: `installing` while the evening job and the wake-up are installed, which waits for the
# Mac password in macOS's own dialog, `checking` while the outcomes are checked, then `done` once
# every outcome is true, when the server stops, or `not-done`; `install-failed` when the jobs
# couldn't be loaded, and `no-finish` when this server hasn't started one.
FINISH_RESULTS = ("installing", "checking", "done", "not-done", "install-failed", "no-finish")
# What Terminal says once the page stops, however it stopped, in the language picked on it, and
# what the PowerShell window the install line was pasted into says on Windows.
CLOSED_LINES = {
    "fi": "Parent Recapin käyttöönottosivu on suljettu. Voit sulkea tämän Terminal-ikkunan.",
    "en": "Parent Recap's setup page has closed. You can close this Terminal window.",
    "zh": "Parent Recap 的设置页面已经关闭。你可以关掉这个终端窗口了。",
}
WINDOWS_CLOSED_LINES = {
    "fi": "Parent Recapin käyttöönottosivu on suljettu. Voit sulkea tämän PowerShell-ikkunan.",
    "en": "Parent Recap's setup page has closed. You can close this PowerShell window.",
    "zh": "Parent Recap 的设置页面已经关闭。你可以关掉这个 PowerShell 窗口了。",
}
# "Continue in the chat": the setup skill as each AI's chat starts it, and what handing over
# can say. Claude Code opens in a Terminal window; a Codex family is told what to type in Codex.
CHAT_SKILLS = {"claude": "/parent-recap:setup", "codex": "$parent-recap-setup"}
CHAT_RESULTS = {"claude": ("opened", "no-terminal", "not-installed"), "codex": ("open-codex",)}
CHAT_WINDOW = "Parent Recap setup"
# The sites the page's buttons open, by name: the page itself names none.
SITES = {"app-passwords": setup_gmail.APP_PASSWORDS_URL, "two-step": setup_gmail.TWO_STEP_URL,
         "myclub": setup_myclub.MYCLUB_URL, "codex-app": setup_ai.CODEX_APP_URL}
# The apps they open, by name.
APPS = {"passwords": desktop_macos.open_passwords}
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
                               "img-src 'self'; connect-src 'self'; frame-src 'self'; "
                               "base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",  # the address carries the code
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}
# The Brief in the page's frame: its own inline styles and nothing else, sandboxed without
# scripts, and its links open nothing (a sandbox without popups drops a new window).
BRIEF_CSP = ("default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; "
             "frame-ancestors 'self'; sandbox")


def register(steps) -> None:
    p = steps.add_parser("page", help="Open the setup page in the browser, served from this Mac "
                         "until 30 minutes without use")
    p.set_defaults(func=cmd_page)


def cmd_page(args: argparse.Namespace) -> int:
    config = Path(os.path.expanduser(os.path.expandvars(args.config or "~/.family/config.yaml")))
    server = SetupServer(config)
    out: dict[str, Any] = {"result": "opened", "url": server.url}
    if not desktop.open_url(server.url):
        out = {"result": "not-opened", "url": server.url,
               "next": f"The browser didn't open. Open this address in it: {server.url}"}
    if not secrets.reachable():  # the page says so too
        out["warning"] = setup_report.KEYCHAIN_WARNING
    print(json.dumps(out, ensure_ascii=False), flush=True)
    try:
        server.serve_until_idle()
    except KeyboardInterrupt:
        server.stop()
    print(closed_line(server.chosen_language() or "en"), flush=True)
    return 0


def closed_line(language: str) -> str:
    """What the window the page runs from says once it stops."""
    return (WINDOWS_CLOSED_LINES if sys.platform == "win32" else CLOSED_LINES)[language]


class SetupServer:
    """The setup page's server for the config at `config`, listening from when it's made."""

    def __init__(self, config: Path) -> None:
        self.config = config
        self.code = token_urlsafe(32)
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
        self.connect = setup_connect.ConnectSteps(config)  # with the one saving lock
        self._wilma = threading.Lock()  # one Wilma sign-in at a time, since each writes its profile
        self._window: dict[str, Any] | None = None  # the Terminal sign-in window's, once opened
        self._ai = threading.Lock()  # one AI sign-in at a time
        self._claude: dict[str, Any] | None = None  # Claude's sign-in's, once started
        self._codex: subprocess.Popen | None = None  # Codex's sign-in, once started
        # Claude Code's install on Windows, once started: `installing`, then `install-failed`, or
        # how opening its sign-in window went, which the next check says once.
        self._claude_install: str | None = None
        # The Codex CLI's install on Windows, once started: `installing`, then `installed` or
        # `install-failed`, which the next check says once.
        self._codex_install: str | None = None
        self._whatsapp = threading.Lock()  # one read at a time, since each is a launchd job
        # What lets the family ask for changes in the chat later, by AI, once Welcome has saved
        # it: `installing`, `installed`, `install-failed`, `not-installed` (no Claude Code) or
        # `no-plugin-copy` (the program wasn't installed by the install line).
        self._chat_installs: dict[str, str] = {}
        self._chat_threads: dict[str, threading.Thread] = {}  # each AI's latest install
        self._working = threading.Lock()  # one read of the senders and one health check at a time
        self._reading: dict[str, Any] | None = None  # Working's read of the senders, once started
        self._health: dict[str, Any] | None = None  # the check page's health check, once started
        self._brief: dict[str, Any] | None = None  # the first Brief, once it's being made
        self._finish: dict[str, Any] | None = None  # Finish's install and check, once started
        # Finish was tried, with Turn it on or Check again: from then on, its checklist says
        # what's missing under each outcome that isn't true.
        self._finish_tried = False
        # How setting the wake-up went at the last Turn it on, which Check again still shows, with
        # Replace it for another wake schedule.
        self._finish_wake: dict[str, Any] = {}
        # Every outcome is true, or the family finished for now: the server stops once that's
        # been said.
        self.closing = False
        # Opened again once setup was at Finish: the family came back to change something.
        self._opened_at_finish = setup_save.read(config)["progress"]["phase"] == "finish"

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
            for window in (setup_ai.CLAUDE_WINDOW, CHAT_WINDOW):
                desktop.forget_terminal(window)
            threads = list(self._chat_threads.values())
        for thread in threads:  # an install isn't cut off halfway
            thread.join()

    # ── the API

    def state(self) -> dict[str, Any]:
        progress = setup_save.read(self.config)["progress"]
        return {"languages": [{"code": c, "name": n} for c, n in LANGUAGES.items()],
                "language": self.chosen_language(),
                "preselected": preferred_language(),
                # Which system's words the page uses, and the AIs it offers here.
                "system": "windows" if sys.platform == "win32" else "macos",
                "ais": list(_ais()),
                # In tmux or SSH, macOS won't let this server save the passwords in the Keychain.
                "keychain": {"reachable": secrets.reachable()},
                "progress": progress,
                "welcome": self._welcome(progress),
                "connect": {"sources": [{"name": s, "skippable": s in SKIPPABLE}
                                        for s in setup_connect.sources()]},
                "gmail": {"address": self._gmail_address()},
                # The name of the Python file Finder selects for Full Disk Access, such as
                # python3.13, for every text that names it. Not its path: that names the Mac's user.
                "whatsapp": {"python": os.path.basename(ops.full_disk_access_python())},
                "myclub": self._myclub(),
                "check": self._check(progress),
                "brief": {"sent": self._brief_sent()},
                "chat": dict(self._chat_installs)}

    def choose_language(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Saves the language picked as the setup parent's: the Household's summary_language,
        which the Recipients without a language of their own read."""
        if not (isinstance(raw, dict) and set(raw) == {"language"} and raw["language"] in LANGUAGES):
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Pick one of the page's languages.",
                errors=[f"language: should be one of {', '.join(LANGUAGES)}"])
        with self.connect.lock:
            out = setup_save.save(self.config, {"language": raw["language"]})
        if out["result"] != "saved":
            return HTTPStatus.CONFLICT, out
        return HTTPStatus.OK, {**out, "language": raw["language"]}

    def check_ai(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Checks that the AI picked is installed and signed in, with its own status check."""
        if not (isinstance(raw, dict) and set(raw) == {"ai"} and raw["ai"] in _ais()):
            return HTTPStatus.BAD_REQUEST, _not_offered()
        name = raw["ai"]
        if name == "gateway":
            return HTTPStatus.OK, {"result": "ready", "ai": name}
        program = self._ai_program(name)
        if program is None:
            missing = _claude_missing() if name == "claude" else {"result": "not-installed"}
            return HTTPStatus.OK, {**missing, "ai": name}
        return HTTPStatus.OK, {"result": _ai_status(program, name), "ai": name}

    def _ai_program(self, name: str) -> str | None:
        codex_path = _section(self._config_data(), "llm").get("codex_path")
        return llm.named(name, codex_path if isinstance(codex_path, str) else None).program()

    # ── the AI sign-in for the evening Brief

    def sign_in_claude(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Starts `claude setup-token` in a pseudo-terminal, which opens Anthropic's Authorize
        page in the browser, and waits for its token in the background: the page asks how it
        went with `check_claude`. The family copies nothing. Windows has no pseudo-terminal for
        it, so there the family signs in in the window `open_claude_window` opens."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, _nothing_to_give()
        if sys.platform == "win32":
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "On Windows, Claude signs in in a PowerShell window.",
                errors=["answers: the page reads Claude's sign-in only on a Mac"])
        program = _claude().program()
        if program is None:
            return HTTPStatus.OK, _claude_missing()
        with self._ai:
            if self._claude is not None and self._claude["result"] is None:
                return HTTPStatus.OK, {"result": "waiting"}  # already signing in
            sign_in: dict[str, Any] = {"result": None}
            self._claude = sign_in
        threading.Thread(target=self._wait_for_claude, args=(sign_in, program),
                         daemon=True).start()
        return HTTPStatus.OK, {"result": "waiting"}

    def check_claude(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """How Claude's sign-in went: `waiting` while the family is still authorizing. On
        Windows, while the page installs Claude Code, `installing`, then once how it ended:
        `install-failed`, or how opening its window went, as `open_claude_window` says it."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, _nothing_to_give()
        with self._ai:
            install = self._claude_install
            if install not in (None, "installing"):
                self._claude_install = None  # said once, and the family can start it again
        if install is not None:
            return HTTPStatus.OK, {"result": install}
        sign_in = self._claude
        if sign_in is None:
            return HTTPStatus.OK, {"result": "no-sign-in"}
        return HTTPStatus.OK, sign_in["result"] or {"result": "waiting"}

    def _wait_for_claude(self, sign_in: dict[str, Any], program: str) -> None:
        try:
            result, token = setup_ai.read_setup_token(program, CLAUDE_SIGN_IN_SECONDS, self._stopped)
            extra: dict[str, Any] = {}
            if token is not None:
                result, extra = setup_ai.claude_token_sign_in(token)
            sign_in["result"] = self._ai_signed_in("claude")[1] if result == "saved" \
                else {"result": result, **_keychain_code(extra)}
        except Exception:  # never leave the page waiting
            sign_in["result"] = {"result": "sign-in-failed"}

    def open_claude_window(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Opens `claude setup-token` in a Terminal window, as the chat setup does, for when the
        page's own sign-in fails: the family copies the token it shows into the page's field.
        On Windows, a PowerShell window, which is the only way the page signs in there."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, _nothing_to_give()
        program = _claude().program()
        if program is None:
            return HTTPStatus.OK, _claude_missing()
        return HTTPStatus.OK, {"result": _open_claude_window(program)}

    def install_claude(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """On Windows, installs Claude Code with its own Windows installer in the background, then
        opens its sign-in window as `open_claude_window` does. The page checks with
        `check_claude`. On a Mac the family pastes the install line into Terminal instead."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, _nothing_to_give()
        if sys.platform != "win32":
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "On a Mac, Claude Code installs from Terminal.",
                errors=["answers: the page installs Claude Code only on Windows"])
        with self._ai:
            installing = self._claude_install == "installing"
        # The installer puts claude.exe in place before it's done: its own run opens the window.
        if not installing and _claude().program() is not None:
            return self.open_claude_window({})
        with self._ai:
            if self._claude_install != "installing":
                self._claude_install = "installing"
                threading.Thread(target=self._installing_claude, daemon=True).start()
        return HTTPStatus.OK, {"result": "installing"}

    def _installing_claude(self) -> None:
        result = "install-failed"
        try:
            program = _claude().program() if setup_ai.install_claude() else None
            if program is not None:
                result = _open_claude_window(program)
        except Exception:  # never leave the page installing
            pass
        with self._ai:
            self._claude_install = result

    def save_claude_token(self, raw: Any) -> Reply:
        """Tests the token pasted into the page's own field and stores it, as the chat's Claude
        step does with the dialog's. The token is never returned, logged or put on a command line."""
        answer = parse(ClaudeToken, raw)
        program = _claude().program()
        if program is None:
            return HTTPStatus.OK, _claude_missing()
        result, extra = setup_ai.claude_token_sign_in(answer.token)
        if result != "saved":
            return HTTPStatus.OK, {"result": result, **_keychain_code(extra)}
        desktop.forget_terminal(setup_ai.CLAUDE_WINDOW)  # the window has done its work
        return self._ai_signed_in("claude")

    def check_codex(self, raw: Any) -> Reply:
        """Whether Codex is signed in, with its own status check. Once it is, the AI sign-in is
        done. While its sign-in is open in the browser, signed out is `waiting`. At Finish, where
        signing in switches the Household to ChatGPT, it must also answer a test call first."""
        parse(Nothing, raw)
        install = self._codex_installing()
        if install is not None:
            return HTTPStatus.OK, {"result": install}
        program = self._ai_program("codex")
        if program is None:
            return HTTPStatus.OK, {"result": "not-installed"}
        with self._ai:  # before the status, so a sign-in that ends meanwhile isn't called failed
            login = self._codex
            ended = login is not None and login.poll() is not None
        status = _ai_status(program, "codex")
        if status == "ready":
            return self._codex_signed_in()
        return HTTPStatus.OK, {"result": status if status != "signed-out"
                               else self._codex_login(login, ended)}

    def _codex_installing(self) -> str | None:
        """`installing` while the page installs the Codex CLI, then `install-failed` once."""
        with self._ai:
            install = self._codex_install
            if install == "install-failed":
                self._codex_install = None  # said once, and the family can start it again
        return install if install in ("installing", "install-failed") else None

    def _codex_signed_in(self) -> Reply:
        """Saves the AI sign-in as done with Codex, which at Finish must answer a test call."""
        if self.connect.progress()["phase"] == "finish" and not self._codex_answers():
            return HTTPStatus.OK, {"result": "test-call-failed"}
        return self._ai_signed_in("codex", reply={"result": "signed-in"})

    def _codex_login(self, login: subprocess.Popen | None, ended: bool) -> str:
        """How a signed-out Codex's sign-in is going: none started, `waiting` while it's open in
        the browser, or `login-failed` once, when it ended without signing in."""
        if login is None:
            return "signed-out"
        if not ended:
            return "waiting"
        with self._ai:
            if self._codex is login:
                self._codex = None  # said once, and the family can start it again
        return "login-failed"

    def _codex_answers(self) -> bool:
        """Whether Codex answers the test call doctor makes, with the Household's config as the
        evening Brief would call it. What it said isn't kept: it can name the Mac's files."""
        codex = llm.for_llm(self._config().llm.model_copy(update={"backend": "codex"}))
        try:
            data = codex.run('Reply with only this JSON: {"ok": true}',
                             "This is a connectivity test.", timeout=120, budget=0).data
        except Exception:
            return False
        return data.get("ok") is True

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

    def install_codex(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """On Windows, installs the Codex CLI with OpenAI's own installer in the background, then
        starts its sign-in as `sign_in_codex` does. The page checks with `check_codex`. A Mac's
        Codex comes with the ChatGPT app, which the page offers to download instead."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, _nothing_to_give()
        if sys.platform != "win32":
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Codex comes with the ChatGPT app on a Mac.",
                errors=["answers: the page installs Codex only on Windows"])
        if self._ai_program("codex") is not None:
            return self.sign_in_codex({})
        with self._ai:
            if self._codex_install != "installing":
                self._codex_install = "installing"
                threading.Thread(target=self._installing_codex, daemon=True).start()
        return HTTPStatus.OK, {"result": "installing"}

    def _installing_codex(self) -> None:
        program = None
        try:
            if setup_ai.install_codex():
                program = self._ai_program("codex")
        except Exception:  # never leave the page installing
            pass
        with self._ai:
            if program is not None and (self._codex is None or self._codex.poll() is not None):
                self._codex = setup_ai.start_codex_login(program)
            self._codex_install = "installed" if program else "install-failed"

    def save_gateway_key(self, raw: Any) -> Reply:
        """Tests the Gateway Key pasted into the page's own field with one call to the gateway the
        config names, and stores it, as `setup gateway` does with the dialog's (ADR 0007). The
        key, and what the gateway said, are never returned, logged or put on a command line."""
        answer = parse(GatewayKey, raw)
        url = _section(self._config_data(), "llm").get("gateway_url")
        result, extra = setup_ai.gateway_sign_in(
            url if isinstance(url, str) and url else LLMConfig().gateway_url, answer.key)
        if result != "saved":
            return HTTPStatus.OK, {"result": result, **_keychain_code(extra)}
        return self._ai_signed_in("gateway")

    # ── WhatsApp

    def check_whatsapp(self, raw: Any) -> Reply:
        """Reads WhatsApp once through a `bg` job, with the scheduled job's permission, as `setup
        whatsapp` does. The page checks again every few seconds, so once the family has given
        the permission, WhatsApp is done by itself, and the groups found are kept in setup's
        progress for the check page. Errors aren't returned: they can name the Mac's files."""
        parse(Nothing, raw)
        with self._whatsapp:
            # setup_whatsapp.read_through_bg, by the name its tests replace until #423.
            read = setup_steps.read_whatsapp_through_bg(
                str(self.config), setup_whatsapp.WHATSAPP_DAYS, WHATSAPP_READ_SECONDS)
        if read["result"] != "readable":
            return HTTPStatus.OK, {"result": read["result"]}
        # A read takes a while, and the family may have skipped WhatsApp or opened another Source
        # meanwhile: their choice stands.
        return self.connect.complete(
            "whatsapp", {"progress": {"whatsapp_chats": read["chats"]}}, keep_skipped=True,
            move_on=lambda progress: progress["source"] == "whatsapp",
            reply={"result": "readable", "chats": read["chats"]})

    def open_full_disk_access(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Selects the scheduled job's Python in Finder and opens Full Disk Access next to it, as
        `setup whatsapp` does, for the family to drag it in and turn its switch on."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, _nothing_to_give()
        _, failed = ops.show_python_for_full_disk_access()
        return HTTPStatus.OK, {"result": "not-opened" if failed else "opened"}

    def back_to_whatsapp(self, raw: Any) -> Reply:
        """Takes setup from Check or Finish back to WhatsApp's step in Connect, for ticked groups
        the health check didn't find on this Mac. The step reads the groups again, so a group's
        new name is there to tick in Check, and setup goes on from there as before."""
        return self._back_to_connect(
            raw, "whatsapp", lambda progress: progress["sources"]["whatsapp"] == "done",
            "Only Check and Finish go back to WhatsApp.",
            "answers: should be {}, at Check or Finish with WhatsApp done")

    def back_to_connect(self, raw: Any) -> Reply:
        """Takes setup from Check or Finish back to Connect's Source list, for a Source the family
        skipped and wants after all: they open it from the list, which brings it back, and setup
        goes on from there as before. A page opened again after setup is at Finish, so this is
        also how a Source is added later."""
        return self._back_to_connect(
            raw, None, lambda progress: setup_connect.skipped(progress["sources"]),
            "Only Check and Finish go back to Connect, for a Source that was skipped.",
            "answers: should be {}, at Check or Finish with a Source skipped")

    def _back_to_connect(self, raw: Any, source: str | None,
                         allowed: Callable[[dict[str, Any]], bool], refusal: str,
                         error: str) -> Reply:
        """Takes setup from Check or Finish back to Connect at `source`, or at its list for None,
        when `allowed` says setup's progress allows it, and never while a check runs."""
        parse(Nothing, raw)
        with self._working:
            if (self._health or {}).get("result") == "checking" or \
                    (self._finish or {}).get("result") in ("installing", "checking"):
                return HTTPStatus.CONFLICT, {"result": "busy"}
            status, out = self.connect.reopen(
                source, allowed, setup_save.outcome("invalid-answers", refusal, errors=[error]))
            if status == HTTPStatus.OK:
                # How the health check and Finish went was about the Sources as they were.
                self._health = None
                self._finish = None
        return status, out

    # ── MyClub

    def save_myclub_link(self, raw: Any) -> Reply:
        """Checks the calendar link pasted into the page's own field for one of the Household's
        Kids and saves it, through the chat's MyClub step. Once a link is saved, MyClub is done,
        and setup moves on once every Kid has one; until then the parent can add the next Kid's.
        The link is a secret (ADR 0007): it's never returned, logged or put on a command line."""
        answer = parse(MyClubLink, raw)
        if answer.kid not in self._kid_names():  # named only: the answers carry the link
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Pick one of the Kids.", errors=["kid: should be a Kid's name"])
        # Downloaded before the lock, so a slow MyClub holds up no other save.
        result, extra = setup_myclub.save_link(self.config, answer.kid, answer.link,
                                               self.connect.lock)
        if result != "saved":
            return HTTPStatus.OK, {"result": result}  # without the error, which can name files
        # Setup moves on by itself once every Kid has their link.
        return self.connect.complete(
            "myclub", move_on=lambda progress: progress["source"] == "myclub"
            and all(k["linked"] for k in self._myclub()["kids"]),
            reply={"kid": answer.kid, "events": extra["events"], "kids": self._myclub()["kids"]})

    def add_kid(self, raw: Any) -> Reply:
        """For a Household without Wilma: adds a Kid by the name the family calls them, so their
        MyClub link can be saved. With Wilma, the Kids are the ones Wilma lists."""
        name = parse(KidName, raw).name.strip()
        if not (name and self._myclub()["add"]):
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Type the Kid's name. A Household with Wilma has its Kids from "
                "there.", errors=["name: should be text, for a Household without Wilma"])
        with self.connect.lock:
            names = self._kid_names()
            if name.casefold() in (n.casefold() for n in names):
                return HTTPStatus.OK, {"result": "kid-exists"}
            # Only the names: each Kid already there keeps the rest, such as their link.
            status, out = self.connect.save({"kids": [{"name": n} for n in [*names, name]]})
        if status != HTTPStatus.OK:
            return status, out
        return status, {**out, "result": "added", "kids": self._myclub()["kids"]}

    def myclub_done(self, raw: Any) -> Reply:
        """Moves setup on from MyClub once a link is saved, leaving the other Kids without one."""
        parse(Nothing, raw)
        return self.connect.move_on("myclub", setup_save.outcome(
            "invalid-answers", "Save a Kid's link first, or skip MyClub.",
            errors=["answers: should be {}, once a link is saved"]))

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

    # ── Working

    def start_reading(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Once every Source is done or skipped, moves setup on to Working and reads the Gmail
        senders in the background, as `discover gmail-senders --json` does: the page asks how far
        it is with `check_reading`, and once they're read setup moves on to Check by itself."""
        with self.connect.lock:
            progress = setup_save.read(self.config)["progress"]
            if raw != {} or progress["phase"] not in ("connect", "working") \
                    or setup_connect.next_source(progress["sources"]) is not None:
                return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                    "invalid-answers", "Connect each Source, or skip it, first.",
                    errors=["answers: should be {}, once every Source is done or skipped"])
            out = setup_save.save(self.config, {"progress": {"phase": "working"}})
        if out["result"] != "saved":
            return HTTPStatus.CONFLICT, out
        with self._working:
            if self._reading is None or self._reading["result"] != "reading":
                self._reading = {"result": "reading", "read": 0, "total": None}
                threading.Thread(target=self._read_senders, daemon=True).start()
        return HTTPStatus.OK, {**out, "result": "reading"}

    def check_reading(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """How far reading the senders is: `reading`, with how many of how many are read."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, _nothing_to_give()
        return HTTPStatus.OK, self._reading or {"result": "no-read"}

    def _read_senders(self) -> None:
        def read(done: int, total: int) -> None:
            self._reading = {"result": "reading", "read": done, "total": total}
        try:
            senders = ops.gmail_senders(Config.load(self.config), SENDER_DAYS, progress=read)
            with self.connect.lock:
                out = setup_save.save(self.config, {"progress": {
                    "phase": "check", "gmail_senders": senders}})
        except Exception:  # its error can name the address: never leave the page reading
            out = {"result": "failed"}
        self._reading = {"result": "read", "progress": out["progress"]} \
            if out["result"] == "saved" else {"result": "read-failed"}

    # ── Check

    def _check(self, progress: dict[str, Any]) -> dict[str, Any]:
        """What the check page shows, each list ticked by best guess: the Kids, all ticked with
        their everyday names; the WhatsApp groups, those linked to a Kid ticked (none without
        WhatsApp); the Gmail senders that look like school, city or club mail ticked, and never
        a public one; each Recipient with their language; the evening time; and for a pilot
        Household, the label its feedback goes under (None for any other). `other_sources` is
        whether a Source besides Gmail brings news, so that a Gmail with no sender ticked only
        sends the Brief (#361)."""
        cfg = self._config()
        hour, minute = cfg.schedule.daily_hour, cfg.schedule.daily_minute
        groups = self._groups(cfg, progress) if progress["sources"]["whatsapp"] == "done" else None
        # The WhatsApp groups are picked on this page, so it's the ticked ones that count.
        others = any(s != "whatsapp" for s in cfg.other_sources()) \
            or any(g["ticked"] for g in groups or [])
        return {"kids": [{"name": k.name, "everyday_name": everyday_name(k)} for k in cfg.kids],
                "whatsapp": groups,
                "senders": self._senders(cfg, progress),
                "other_sources": others,
                "recipients": [{"address": r.address, "language": cfg.language_of(r)}
                               for r in cfg.email.to],
                "evening": f"{hour:02d}:{minute:02d}",
                "feedback": {"household_label": cfg.feedback.household_label}
                if cfg.feedback.enabled else None}

    @staticmethod
    def _groups(cfg: Config, progress: dict[str, Any]) -> list[dict[str, Any]]:
        """The groups WhatsApp's step found, then those picked before but not found this time."""
        called = {k.name: everyday_name(k) for k in cfg.kids}
        picked = {c.name: c for c in cfg.whatsapp.chats}
        groups = []
        for chat in progress.get("whatsapp_chats") or []:
            kids = [k for k in (chat.get("hint") or {}).get("kids", []) if k in called]
            groups.append({"name": chat["name"], "last": chat.get("last"),
                           "archived": bool(chat.get("archived")), "kids": [called[k] for k in kids],
                           "ticked": bool(kids) or chat["name"] in picked})
        found = {g["name"] for g in groups}
        for chat in cfg.whatsapp.chats:
            if chat.name not in found:
                kids = list(called) if chat.kid == "both" else [k for k in called if k == chat.kid]
                groups.append({"name": chat.name, "last": None, "archived": False,
                               "kids": [called[k] for k in kids], "ticked": True})
        return groups

    @staticmethod
    def _senders(cfg: Config, progress: dict[str, Any]) -> list[dict[str, Any]]:
        """The senders Working found, but no public mail service, then the city's starting
        allowlist and the domains already on the allowlist, ticked."""
        allowed = cfg.gmail.allowlist_domains
        senders = [{"domain": s["domain"], "count": s["count"], "example": s["example"],
                    "ticked": s["likely"] or s["domain"] in allowed}
                   for s in progress.get("gmail_senders") or [] if not s.get("public")]
        # Only a preset's: a town's guessed domain is ticked only once mail came from it.
        city = setup_gmail.CITY_DOMAINS.get(cfg.city or "")
        for domain in [*([city] if city else []), *allowed]:
            if domain not in (s["domain"] for s in senders):
                senders.append({"domain": domain, "count": 0, "example": "", "ticked": True})
        return senders

    def confirm_check(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Saves what the check page confirms through `setup save`, then runs the health check in
        the background: the page asks how it went with `check_health`. Only what the page offered
        can be picked."""
        try:
            answer = CheckAnswer.model_validate(raw)
        except ValidationError as e:
            return HTTPStatus.BAD_REQUEST, setup_save.invalid(e)
        with self._working:
            if self._health is not None and self._health["result"] == "checking":
                return HTTPStatus.CONFLICT, {"result": "checking"}
            with self.connect.lock:
                progress = setup_save.read(self.config)["progress"]
                cfg = self._config()
                offered = self._check(progress)
                errors = _check_errors(answer, offered, progress["phase"])
                if errors:
                    return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                        "invalid-answers", "Pick from what the check page offers.", errors=errors)
                out = setup_save.save(self.config, self._check_answers(answer, cfg, progress))
            if out["result"] != "saved":
                return (HTTPStatus.BAD_REQUEST if out["result"] == "invalid-answers"
                        else HTTPStatus.CONFLICT), out
            self._health = {"result": "checking"}
            threading.Thread(target=self._run_health_check, daemon=True).start()
        return HTTPStatus.OK, {**out, "result": "checking"}

    def check_health(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """How the health check went: `checking` while it runs. Only each check's name and status
        are given, since their details can quote an address or an error."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, _nothing_to_give()
        return HTTPStatus.OK, self._health or {"result": "no-check"}

    def _run_health_check(self) -> None:
        try:
            results = ops.health_checks(str(self.config), schedule=False)
            checks = [_health_check(c) for c in results if c.key not in SCHEDULE_CHECKS]
            if any(c["status"] == "fail" for c in checks):
                done = {"result": "not-ok", "checks": checks}
            else:
                with self.connect.lock:
                    out = setup_save.save(self.config, {"progress": {"phase": "first-brief"}})
                done = {"result": "ok", "checks": checks, "progress": out["progress"]} \
                    if out["result"] == "saved" else {"result": "not-ok", "checks": checks}
        except Exception:  # never leave the page checking
            done = {"result": "not-ok", "checks": [{"check": "other", "status": "fail"}]}
        self._health = done

    @staticmethod
    def _check_answers(answer: "CheckAnswer", cfg: Config,
                       progress: dict[str, Any]) -> dict[str, Any]:
        """The check page's answers as `setup save` takes them. A WhatsApp group is linked to the
        Kids its name matched who are still ticked, or to `both`, and labelled after what matched:
        the class, the school or an activity."""
        kept = {k.name.strip() for k in answer.kids}
        answers: dict[str, Any] = {
            "kids": [{"name": k.name.strip(), "everyday_name": k.everyday_name.strip()}
                     for k in answer.kids],
            "recipients": [{"address": r.address,
                            **({"language": r.language} if r.language != cfg.summary_language
                               else {})} for r in answer.recipients],
            "evening": answer.evening,
            "sources": {"gmail": {"allowlist_domains": answer.senders}},
        }
        if answer.household_label is not None:
            answers["feedback"] = {"household_label": answer.household_label.strip()}
        if answer.whatsapp is not None:
            found = {c["name"]: c for c in progress.get("whatsapp_chats") or []}
            before = {c.name: c for c in cfg.whatsapp.chats}
            chats = []
            for name in answer.whatsapp:
                if name in found:
                    hint = found[name].get("hint") or {}
                    kids = [k for k in hint.get("kids", []) if k in kept]
                    label = next((_label(term, k) for term in hint.get("matched", [])
                                  for k in cfg.kids if k.name in kept and _label(term, k)), None)
                else:
                    chat = before[name]
                    kids = [chat.kid] if chat.kid in kept or chat.kid == "both" else []
                    label = chat.label
                chats.append({"name": name, "label": label,
                              "kid": kids[0] if len(kids) == 1 else ("both" if kids else None)})
            answers["sources"]["whatsapp"] = {"enabled": bool(chats), "chats": chats}
        return answers

    # ── First Brief

    def make_brief(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Makes the real Brief from the Household's Sources over the last three days, as `run
        --preview` does, without sending it, in the background: the page asks how far it is with
        `check_brief`. Making it again replaces the one made before."""
        phase = setup_save.read(self.config)["progress"]["phase"]
        if raw != {} or phase != "first-brief":
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Confirm the check page first.",
                errors=["answers: should be {}, once setup is at First Brief"])
        with self._working:
            if self._brief is None or self._brief["result"] != "making":
                self._brief = {"result": "making", "sources": [], "step": None}
                threading.Thread(target=self._make_brief, daemon=True).start()
        return HTTPStatus.OK, {"result": "making"}

    def check_brief(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """How far making the Brief is: `making`, with the Sources and the one it's on, or
        `writing`; once `made`, its email HTML and whether the Household is a pilot one, with
        the feedback button. The feedback link stays here: the page opens it through the server."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, _nothing_to_give()
        brief = self._brief
        if brief is None:
            return HTTPStatus.OK, {"result": "no-brief"}
        if brief["result"] == "made":
            return HTTPStatus.OK, {"result": "made", "html": brief["html"],
                                   "feedback": brief["feedback"] is not None}
        return HTTPStatus.OK, brief

    def brief_page(self) -> bytes | None:
        """The Brief made, for the page's frame, with its links kept from opening in it."""
        brief = self._brief
        if brief is None or brief["result"] != "made":
            return None
        return brief["html"].replace("<html>", '<html><head><base target="_blank"></head>', 1).encode()

    def _make_brief(self) -> None:
        try:
            code, out = jobs.run_detached(
                ["run", "--preview", "--lookback-hours", str(BRIEF_LOOKBACK_HOURS)],
                BRIEF_SECONDS, config=str(self.config), echo=False, output=self._brief_progress)
            made = next((line for line in reversed(_preview_lines(out))
                         if line["preview"] == "made"), None)
            done = {"result": "made", **{k: made[k] for k in ("subject", "text", "html", "feedback")}} \
                if code == 0 and made else {"result": "make-failed"}
        except Exception:  # its error can name an address: never leave the page making it
            done = {"result": "make-failed"}
        self._brief = done

    def _brief_progress(self, out: str) -> None:
        sources, step = [], None
        for line in _preview_lines(out):
            sources = line.get("sources", sources)
            step = line.get("source") or ("writing" if line["preview"] == "writing" else step)
        self._brief = {"result": "making", "sources": sources, "step": step}

    def send_brief(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """"Send it to me": sends the Brief made to the setup parent, the first Recipient, and
        never to anyone else: the others' first Brief is the first evening one. It's recorded as
        delivered to them, which the outcome check counts as the first Brief, and nothing else:
        the evening Brief still reads the same Messages."""
        brief = self._brief
        cfg = self._config()
        parent = cfg.email.to[0].address if cfg.email.enabled and cfg.email.to else None
        if raw != {} or brief is None or brief["result"] != "made" or parent is None:
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Make the Brief first.",
                errors=["answers: should be {}, once the Brief is made"])
        try:
            with run_lock.exclusive(cfg):
                try:
                    email_action.send(subject=brief["subject"], body_text=brief["text"],
                                      body_html=brief["html"],
                                      from_addr=cfg.email.from_addr or cfg.gmail.username or "",
                                      to_addrs=[parent])
                except Exception:  # its error can name the address
                    return HTTPStatus.OK, {"result": "send-failed"}
                state = State(cfg.resolved_state_path())
                state.mark_delivered_now([parent])
                state.save()
        except run_lock.Busy:
            return HTTPStatus.OK, {"result": "busy"}
        return HTTPStatus.OK, {"result": "sent", "to": parent}

    def brief_done(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Moves setup on to Finish once the first Brief has reached the setup parent."""
        with self.connect.lock:
            phase = setup_save.read(self.config)["progress"]["phase"]
            if raw != {} or phase != "first-brief" or not self._brief_sent():
                return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                    "invalid-answers", "Send the first Brief to yourself first.",
                    errors=["answers: should be {}, once the first Brief is sent"])
            out = setup_save.save(self.config, {"progress": {"phase": "finish"}})
        if out["result"] != "saved":
            return HTTPStatus.CONFLICT, out
        return HTTPStatus.OK, out

    def open_feedback(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """"Something's wrong, tell us", for a pilot Household: opens the Brief's own Digest
        feedback link, pre-filled with the Brief made, in the default browser."""
        brief = self._brief
        link = brief.get("feedback") if brief and brief["result"] == "made" else None
        if raw != {} or not link:
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Only a pilot Household's Brief has the feedback link.",
                errors=["answers: should be {}, once a pilot Household's Brief is made"])
        return HTTPStatus.OK, {"result": "opened" if desktop.open_url(link) else "not-opened"}

    def _brief_sent(self) -> bool:
        """Whether a Brief has reached the setup parent, the first Recipient."""
        cfg = self._config()
        if not cfg.email.to:
            return False
        return State(cfg.resolved_state_path()).delivered_at(cfg.email.to[0].address) is not None

    def _config(self) -> Config:
        """The config as saved so far, read as the config model, or an empty one."""
        try:
            return Config.model_validate({"kids": [], **self._config_data()})
        except ValidationError:
            return Config(kids=[])

    # ── Finish

    def start_finish(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Installs the evening job and the Mac's wake-up through the schedule step, in the
        background, then checks the outcomes: the page asks how it went with `check_finish`. The
        Mac password goes into macOS's own administrator dialog, never the page (ADR 0007). The
        Mac's other repeating wake schedule is replaced only with `replace_wake`, once the family
        agrees."""
        if not (isinstance(raw, dict) and set(raw) == {"replace_wake"}
                and isinstance(raw["replace_wake"], bool)):
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Say whether to replace the Mac's other wake schedule.",
                errors=["replace_wake: should be true or false"])
        return self._start_finish(raw["replace_wake"])

    def check_outcomes(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Checks the outcomes without installing anything, for a Household that may have
        finished already: once every outcome is true, the page shows only the checklist. With
        `again`, it's Check again: the outcomes, the health check among them, are checked once
        more without loading the evening job or setting the wake-up again, and the checklist says
        what's still missing."""
        again = isinstance(raw, dict) and set(raw) == {"again"} and raw["again"] is True
        if raw != {} and not again:
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Give nothing, or say it's Check again.",
                errors=["again: should be true, or left out"])
        return self._start_finish(None, again=again)

    def check_finish(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """How Finish went: each outcome and whether it's true, without its reason, which can name
        an address, and how setting the wake-up went. Once every outcome is true, the server stops
        after saying so, unless the page was opened again after setup with a Source skipped: then
        it `stays`, for the way back to Connect to add it, until 30 minutes without a request."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, _nothing_to_give()
        finish = self._finish
        if finish is None:
            return HTTPStatus.OK, {"result": "no-finish"}
        if finish["result"] == "done":
            if self._opened_at_finish and \
                    setup_connect.skipped(self.connect.progress()["sources"]):
                return HTTPStatus.OK, {**finish, "stays": True}
            self.closing = True
        return HTTPStatus.OK, finish

    def _start_finish(self, replace: bool | None,
                      again: bool = False) -> tuple[HTTPStatus, dict[str, Any]]:
        """Installs with `replace` true or false, then checks; with None, only checks."""
        if setup_save.read(self.config)["progress"]["phase"] != "finish":
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Send the first Brief to yourself first.",
                errors=["answers: should be given once setup is at Finish"])
        with self._working:
            if self._finish is not None and self._finish["result"] in ("installing", "checking"):
                return HTTPStatus.OK, {"result": self._finish["result"]}  # never asked twice
            started = {"result": "checking" if replace is None else "installing"}
            self._finish = started
            self._finish_tried = self._finish_tried or again or replace is not None
            threading.Thread(target=self._finishing, args=(replace,), daemon=True).start()
        return HTTPStatus.OK, dict(started)

    def _finishing(self, replace: bool | None) -> None:
        if replace is not None:
            try:
                cfg = Config.load(self.config)
                ops.install_jobs(cfg)
                hour, minute = cfg.schedule.daily_hour, cfg.schedule.daily_minute
                other = wake.status(hour, minute).others
                result = wake.set(hour, minute, replace)
            except Exception:  # its error can name the Mac's files: never leave the page installing
                self._finish_wake = {}
                self._finish = {"result": "install-failed"}
                return
            self._finish_wake = {"wake": result,
                                 **({"other": other} if result == "other-schedule" else {})}
            self._finish = {"result": "checking"}
        try:
            outcomes = setup_status.check(str(self.config))
        except Exception:  # never leave the page checking
            outcomes = []
        done = bool(outcomes) and all(o.ok for o in outcomes)
        # Only checked, after a try: the checklist still says what's missing.
        tried = {"tried": True} if replace is None and self._finish_tried else {}
        self._finish = {"result": "done" if done else "not-done", **self._finish_wake, **tried,
                        "outcomes": [_finish_outcome(o) for o in outcomes]}

    def stop_for_now(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """"Finish for now": stops the page with setup not done yet, once Finish isn't installing
        or checking, which would be cut off. The family comes back with the install line or in
        the chat."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, _nothing_to_give()
        if setup_save.read(self.config)["progress"]["phase"] != "finish":
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Setup isn't at Finish yet.",
                errors=["answers: should be given once setup is at Finish"])
        with self._working:
            if self._finish is not None and self._finish["result"] in ("installing", "checking"):
                return HTTPStatus.OK, {"result": self._finish["result"]}
            self.closing = True
        return HTTPStatus.OK, {"result": "stopped"}

    # ── Continue in the chat

    def continue_in_chat(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Hands setup over to the chat with the AI picked: opens Claude Code at the setup skill
        in a Terminal window, or says what to type in Codex. The skill reads the progress and the
        answers saved so far, and carries on from the same phase and Source. The plugin or the
        Codex skills are installed first if they aren't yet, since the skill comes with them."""
        if not (isinstance(raw, dict) and set(raw) == {"ai"} and raw["ai"] in CHAT_SKILLS):
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Pick Claude or ChatGPT.", errors=["ai: should be claude or codex"])
        if raw["ai"] not in _ais():
            return HTTPStatus.BAD_REQUEST, _not_offered()
        if raw["ai"] not in _chats():
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "The chat sets up a Mac only.",
                errors=["ai: has no chat to continue in on this system"])
        skill = CHAT_SKILLS[raw["ai"]]
        if self._chat_installs.get(raw["ai"]) != "installed":  # the skill must be there to start
            self._install_chat(raw["ai"]).join()
        if raw["ai"] == "codex":
            return HTTPStatus.OK, {"result": "open-codex", "type": skill}
        program = _claude().program()
        if program is None:
            return HTTPStatus.OK, {"result": "not-installed", "install": _claude().install_line()}
        # Claude Code in the family's home folder, at the setup skill.
        if desktop.open_terminal([program, skill], {}, CHAT_WINDOW):
            return HTTPStatus.OK, {"result": "opened"}
        return HTTPStatus.OK, {"result": "no-terminal", "type": shlex.join([program, skill])}

    def _ai_signed_in(self, name: str, reply: dict[str, Any] | None = None) -> Reply:
        """Saves the AI sign-in as done, moving setup on to the next Source. Once setup is at
        Finish, it's a switch: `name`, whose test call has just passed, becomes the Household's
        one AI (ADR 0017), and its chat is installed as when Welcome picks it."""
        with self.connect.lock:
            switch = self.connect.progress()["phase"] == "finish"
            status, out = self.connect.save({"ai": name}, reply) if switch \
                else self.connect.complete("ai", reply=reply)
        if switch and status == HTTPStatus.OK and name in _chats():
            self._install_chat(name)
        return status, out

    def save_welcome(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Saves Welcome's choices: the AI, the partner or only me, and pilot feedback, and moves
        setup on to Connect. The Recipients are saved once the setup parent's address is known."""
        try:
            answer = WelcomeAnswer.model_validate(raw)
        except ValidationError as e:
            return HTTPStatus.BAD_REQUEST, setup_save.invalid(e)
        if answer.ai not in _ais():
            return HTTPStatus.BAD_REQUEST, _not_offered()
        partner = answer.partner and answer.partner.model_dump()
        answers: dict[str, Any] = {
            "ai": answer.ai,
            "progress": {"phase": "connect", "source": "wilma", "partner": partner},
        }
        if answer.feedback or feedback.pilot_form():  # without a pilot Form, setup save refuses yes
            answers["feedback"] = {"enabled": answer.feedback}
        parent = self._gmail_address()
        if parent:
            answers["recipients"] = self._recipients(parent, partner)
        with self.connect.lock:
            out = setup_save.save(self.config, answers)
        if out["result"] != "saved":
            return (HTTPStatus.BAD_REQUEST if out["result"] == "invalid-answers"
                    else HTTPStatus.CONFLICT), out
        if answer.ai in _chats():  # the gateway has no chat to install, nor Windows yet
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
                program = _claude().program()
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

    def choose_source(self, raw: Any) -> Reply:
        """Skips a Source, moving setup on to the next one to do, or opens one from the list,
        which brings a skipped one back."""
        answer = parse(SourceChoice, raw)
        if answer.action == "open":
            return self.connect.open(answer.source)
        if answer.source not in SKIPPABLE:
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Open a Source, or skip one that can be skipped.",
                errors=[f"source: skip one of {', '.join(SKIPPABLE)}"])
        return self.connect.skip(answer.source)

    def open_site(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Opens one of the sites the page sends the family to, in the default browser, or one of
        the Mac's apps."""
        apps = {name: open_app for name, open_app in APPS.items() if macos_only.offers(name)}
        if not (isinstance(raw, dict) and set(raw) == {"site"} and raw["site"] in {*SITES, *apps}):
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Open one of the page's own sites.",
                errors=[f"site: should be one of {', '.join([*SITES, *apps])}"])
        if raw["site"] in apps:
            opened = apps[raw["site"]]()
            return HTTPStatus.OK, {"result": "opened" if opened else "not-opened"}
        url = SITES[raw["site"]]
        opened = desktop.open_url(url)
        return HTTPStatus.OK, {"result": "opened" if opened else "not-opened", "url": url}

    # ── Wilma (ADR 0008)

    def wilma_ready(self, raw: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        """Installs the pinned wilma CLI unless it's there, since the town list comes with it."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Nothing to give.", errors=["answers: should be {}"])
        return HTTPStatus.OK, {"result": setup_wilma.install()}

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

    def sign_in_wilma(self, raw: Any) -> Reply:
        """Signs in to the Wilma picked from the list with the username and the password typed
        into the page's own fields, by writing the CLI's profile and reading the Kids with it.
        Once signed in, Wilma is done, and the Kids, as Wilma spells them, and the town are the
        Household's. The password is never returned, logged or put on a command line."""
        answer = parse(WilmaSignIn, raw)
        if not setup_wilma.installed():
            return HTTPStatus.OK, {"result": "not-installed"}
        listed = setup_wilma.tenants()
        if listed is None:
            return HTTPStatus.OK, {"result": "no-list"}
        tenant = _listed_tenant(listed, answer.url, answer.town)
        if tenant is None:
            return HTTPStatus.BAD_REQUEST, _wilma_invalid()
        with self._wilma:
            result, kids = setup_wilma.sign_in(tenant, answer.username.strip(), answer.password)
        if result != "signed-in":
            return HTTPStatus.OK, {"result": result}
        return self._signed_in(kids, answer.town)

    def save_town(self, raw: Any) -> Reply:
        """For a Household whose school doesn't use Wilma: saves the town picked from Wilma's list
        as its city, and moves setup on with Wilma skipped."""
        town = parse(TownPick, raw).town
        if not any(town in setup_wilma.towns_of(t) for t in setup_wilma.tenants() or []):
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Pick your town from the list.",
                errors=["town: should be a town in Wilma's list"])
        return self.connect.skip("wilma", {"city": town})

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
                         args=(window, self.chosen_language() or "en", raw["town"])).start()
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
            out = setup_wilma.sign_in_in_terminal(language, WILMA_WINDOW_SECONDS)
            if out["result"] == "signed-in":
                kids = [k for k in out["kids"] if isinstance(k.get("name"), str) and k["name"]]
                _, done = self._signed_in(kids, self._city(out.get("wilma_address"), town))
            else:
                done = {"result": out["result"]}
        except Exception:  # never leave the page waiting
            done = {"result": "sign-in-failed"}
        window["result"] = done

    def _signed_in(self, kids: list[dict[str, Any]], city: str | None) -> Reply:
        """Saves the Kids Wilma lists and the town, with Wilma on and done."""
        answers: dict[str, Any] = {
            "kids": [{"name": k["name"],
                      **({"school": k["school"]} if k.get("school") else {}),
                      **({"class_name": k["class"]} if k.get("class") else {})} for k in kids],
            "sources": {"wilma": {"enabled": True}},
            **({"city": city} if city else {}),
        }
        return self.connect.complete("wilma", answers, reply={
            "result": "signed-in", "kids": [k["name"] for k in kids], "city": city})

    @staticmethod
    def _city(address: str | None, town: str | None) -> str | None:
        """The town of the Wilma signed in to in the Terminal window: the one picked on the page
        if it's one of that Wilma's, else its first."""
        listed = setup_wilma.tenants() or []
        tenant = next((t for t in listed if urlparse(t["url"]).hostname == address), None)
        towns = setup_wilma.towns_of(tenant) if tenant else []
        if towns:
            return town if town in towns else towns[0]
        return setup_wilma.WILMA_CITIES.get(address or "")

    def connect_gmail(self, raw: Any) -> Reply:
        """Tests the App Password pasted into the page's own field with Gmail and stores it in
        the Keychain, through the chat's Gmail step (ADR 0007). Once it's saved, Gmail is done,
        the address is the Household's Gmail and the setup parent's, first among the Recipients.
        The App Password is never returned, logged, saved anywhere else or put on a command line."""
        answer = parse(GmailSignIn, raw)
        address = answer.address.strip().lower()
        if not ADDRESS.match(address):
            return HTTPStatus.OK, {"result": "no-address"}
        result, _, extra = setup_gmail.sign_in(address, answer.password)
        if result != "saved":
            return HTTPStatus.OK, {"result": result, **_keychain_code(extra)}
        return self.connect.complete("gmail", lambda progress: self._gmail_answers(address, progress),
                                     reply={"address": address})

    def _gmail_answers(self, address: str, progress: dict[str, Any]) -> dict[str, Any]:
        """Gmail's address, and the setup parent first among the Recipients."""
        answers: dict[str, Any] = {"sources": {"gmail": {"address": address}}}
        if "partner" in progress:  # Welcome's choice, waiting for the parent's address
            answers["recipients"] = self._recipients(address, progress["partner"])
        elif not _section(self._config_data(), "email").get("to"):
            answers["recipients"] = [{"address": address}]
        return answers

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
        language = self.chosen_language() or preferred_language()
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
        offered = _ais()
        enabled = _section(data, "feedback").get("enabled")
        # None when this version ships no pilot Form: the page doesn't ask, so nothing half-set
        # is ever written.
        pilot = (enabled if isinstance(enabled, bool) else True) if feedback.pilot_form() else None
        return {"ai": backend if backend in offered else offered[0], "partner": partner,
                "feedback": pilot}

    def _config_data(self) -> dict[str, Any]:
        """The config as saved so far, or empty when there's none yet."""
        try:
            data = yaml.safe_load(self.config.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return {}
        return data if isinstance(data, dict) else {}

    def chosen_language(self) -> str | None:
        """The language already picked, from the config: None until there's one the page offers."""
        language = self._config_data().get("summary_language")
        return language if language in LANGUAGES else None


# What each Connect step's call takes. A secret's field is named in a refusal, never its value.


class SourceChoice(Strict):
    source: setup_save.SourceName
    action: Literal["open", "skip"]


class GmailSignIn(Strict):
    address: str
    password: str  # the App Password


class WilmaSignIn(Strict):
    url: str
    town: str | None  # None for a Wilma whose list entry names no town
    username: str = Field(pattern=r"\S")
    password: str = Field(min_length=1)


class TownPick(Strict):
    town: str


class ClaudeToken(Strict):
    token: str


class GatewayKey(Strict):
    key: str


class MyClubLink(Strict):
    kid: str
    link: str  # a secret (ADR 0007)


class KidName(Strict):
    name: str


class PartnerAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    language: Literal["fi", "en", "zh"]


class WelcomeAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ai: Literal["claude", "codex", "gateway"]
    partner: PartnerAnswer | None  # None for only me
    feedback: StrictBool


class KidCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str            # as Wilma spells it, or the everyday name for a Kid added on the page
    everyday_name: str


class CheckAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kids: list[KidCheck]                  # the Kids ticked
    whatsapp: list[str] | None            # the groups ticked; None without WhatsApp
    senders: list[str]                    # the sender domains ticked
    recipients: list[setup_save.RecipientAnswer]
    evening: str                          # HH:MM
    household_label: str | None = None    # a pilot Household's; None for any other


def everyday_name(kid: Kid) -> str:
    """What the family calls the Kid: the name saved for it, else the first word of their name."""
    return kid.everyday_name or (kid.name.split() or [kid.name])[0]


def _check_errors(answer: CheckAnswer, offered: dict[str, Any], phase: str) -> list[str]:
    """Where the check page's answers pick something it didn't offer."""
    errors = []
    if phase != "check":
        errors.append("answers: setup isn't at Check")
    names = [k.name.strip().casefold() for k in answer.kids]
    if not names or not all(names) or len(set(names)) < len(names) \
            or not all(k.everyday_name.strip() for k in answer.kids):
        errors.append("kids: should be one or more Kids, each with a name of their own and an "
                      "everyday name")
    groups = offered["whatsapp"]
    if (answer.whatsapp is None) != (groups is None) or \
            not set(answer.whatsapp or []) <= {g["name"] for g in groups or []}:
        errors.append("whatsapp: should be groups the check page lists, or null without WhatsApp")
    if not set(answer.senders) <= {s["domain"] for s in offered["senders"]}:
        errors.append("senders: should be sender domains the check page lists")
    if [r.address for r in answer.recipients] != [r["address"] for r in offered["recipients"]]:
        errors.append("recipients: should be the Recipients the check page lists, in order")
    label = answer.household_label
    if label is not None and (offered["feedback"] is None or not label.strip()):
        errors.append("household_label: should be a name, and only for a pilot Household")
    return errors


def _health_check(check: ops.Check, names: dict[str, str] = HEALTH_CHECKS) -> dict[str, Any]:
    """One of doctor's checks as the check page shows it, or Finish with its own `names`,
    without its details: with why it isn't OK, where doctor says, and the groups that's about,
    whose names the check page shows anyway."""
    out: dict[str, Any] = {"check": names.get(check.key, "other"),
                           "status": HEALTH_STATUSES.get(check.status, "warn")}
    if check.key in HEALTH_SUBJECTS and check.subject is not None:
        out[HEALTH_SUBJECTS[check.key]] = check.subject
    if check.reason:
        out["reason"] = check.reason
    if check.groups:
        out["groups"] = list(check.groups)
    return out


def _finish_outcome(o: setup_status.Outcome) -> dict[str, Any]:
    """An outcome as Finish shows it, without its reason, which can name an address: whether
    it's true, and for a health check that isn't, the checks that aren't OK."""
    out: dict[str, Any] = {"outcome": o.outcome, "ok": o.ok}
    checks = [c for c in o.checks if c.key not in FINISH_LEAVES_OUT]
    if checks:
        out["checks"] = [_health_check(c, FINISH_CHECKS) for c in checks]
    return out


def _preview_lines(out: str) -> list[dict[str, Any]]:
    """The JSON lines `run --preview` printed, in order, without its log lines."""
    lines = []
    for line in out.splitlines():
        if line.startswith('{"preview"'):
            try:
                lines.append(json.loads(line))
            except ValueError:
                continue  # cut off while it was being written
    return lines


def _label(term: str, kid: Kid) -> str | None:
    """What a WhatsApp group whose name matched `term` is about, for `kid`."""
    if term == kid.class_name:
        return "class"
    if term == kid.school:
        return "school"
    return term if term in kid.activities else None


def _claude() -> llm.Backend:
    """Claude, which the page signs in, installs and opens the chat setup in on its own steps."""
    return llm.named("claude")


def _claude_missing() -> dict[str, Any]:
    """What the page says without Claude Code: on a Mac, with the line that installs it. On
    Windows the page installs it itself."""
    if sys.platform == "win32":
        return {"result": "not-installed"}
    return {"result": "not-installed", "install": _claude().install_line()}


def _open_claude_window(program: str) -> str:
    """Opens `claude setup-token` in a window, whose token the family pastes into the page:
    `opened`, or `no-terminal`."""
    opened = desktop.open_terminal(
        setup_ai.setup_token_window(program, "the field on the setup page"), {},
        setup_ai.CLAUDE_WINDOW)
    return "opened" if opened else "no-terminal"


def _ai_status(program: str, name: str) -> str:
    """`ready`, `signed-out` or `check-failed`, from the AI's own status check."""
    status = ["auth", "status"] if name == "claude" else ["login", "status"]
    try:
        # Only its exit code is read: the output can name the account.
        proc = subprocess.run([program, *status], capture_output=True, text=True, encoding="utf-8",
                              timeout=AI_CHECK_SECONDS, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return "check-failed"
    return "ready" if proc.returncode == 0 else "signed-out"


def _ais() -> tuple[str, ...]:
    """The AIs the page offers on this system, Welcome's default first."""
    return WINDOWS_AIS if sys.platform == "win32" else AIS


def _chats() -> dict[str, str]:
    """The AIs with a chat to continue setup in on this system, and their setup skill. The skills
    set up a Mac, so Windows has none yet."""
    return {} if sys.platform == "win32" else CHAT_SKILLS


def _not_offered() -> dict[str, Any]:
    return setup_save.outcome("invalid-answers", "Pick one of the AIs the page offers.",
                              errors=[f"ai: should be one of {', '.join(_ais())}"])


def _nothing_to_give() -> dict[str, Any]:
    return setup_save.outcome("invalid-answers", "Nothing to give.", errors=["answers: should be {}"])


def _keychain_code(extra: dict[str, Any]) -> dict[str, Any]:
    """Only macOS's code from what a Keychain step reports: a failed test call's error isn't
    returned, since it can name the Mac's files."""
    return {"code": extra["code"]} if "code" in extra else {}


def _listed_tenant(listed: list[dict[str, Any]], url: str,
                   town: str | None) -> dict[str, Any] | None:
    """The entry of Wilma's list at `url` when `town` is one of its towns, or is None for an entry
    without any; else None."""
    tenant = setup_wilma.entry(listed, url)
    towns = setup_wilma.towns_of(tenant) if tenant else []
    if tenant is None or (town not in towns if towns else town is not None):
        return None
    return tenant


def _wilma_invalid() -> dict[str, Any]:
    # Names the fields only: the answers carry the password.
    return setup_save.outcome(
        "invalid-answers", "Pick your town's Wilma from the list and give the username and the "
        "password.", errors=["answers: should be the url and town picked from the list, and the "
                             "username and the password, as text"])


def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    section = data.get(key)
    return section if isinstance(section, dict) else {}


def preferred_language() -> str:
    """The first of the page's languages among the computer's preferred ones, else English."""
    for tag in desktop.preferred_languages():
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
        if method == "GET" and path == "brief.html" and (brief := setup.brief_page()) is not None:
            return self._send(HTTPStatus.OK, brief, "text/html; charset=utf-8",
                              **{"Content-Security-Policy": BRIEF_CSP})
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
                   "api/codex/install": setup.install_codex,
                   "api/claude/install": setup.install_claude,
                   "api/gateway": setup.save_gateway_key,
                   "api/whatsapp/check": setup.check_whatsapp,
                   "api/whatsapp/open": setup.open_full_disk_access,
                   "api/whatsapp/back": setup.back_to_whatsapp,
                   "api/connect/back": setup.back_to_connect,
                   "api/myclub": setup.save_myclub_link, "api/myclub/kid": setup.add_kid,
                   "api/myclub/done": setup.myclub_done,
                   "api/working": setup.start_reading, "api/working/check": setup.check_reading,
                   "api/check": setup.confirm_check, "api/check/health": setup.check_health,
                   "api/brief": setup.make_brief, "api/brief/check": setup.check_brief,
                   "api/brief/send": setup.send_brief, "api/brief/done": setup.brief_done,
                   "api/brief/feedback": setup.open_feedback,
                   "api/finish": setup.start_finish, "api/finish/outcomes": setup.check_outcomes,
                   "api/finish/check": setup.check_finish, "api/finish/stop": setup.stop_for_now,
                   "api/chat": setup.continue_in_chat}
        if method == "POST" and path in actions:
            raw = _json_body(body)
            if raw is _NOT_JSON:
                return self._json(HTTPStatus.BAD_REQUEST, setup_save.outcome(
                    "invalid-answers", "The answers aren't JSON.", errors=["not JSON"]))
            try:
                reply = actions[path](raw)
            except setup_connect.Malformed as e:  # the same refusal from every call
                reply = HTTPStatus.BAD_REQUEST, e.answer
            self._json(*reply)
            if setup.closing:  # setup is done, and the page has been told
                setup.stop()
            return None
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
