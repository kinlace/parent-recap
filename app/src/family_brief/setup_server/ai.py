"""The AI sign-in for the evening Brief: Claude's token, Codex's ChatGPT sign-in and the Gateway
Key, and on Windows installing Claude Code or the Codex CLI first. At Finish, signing in to
another AI switches the Household to it (ADR 0017)."""
from __future__ import annotations

import subprocess
import threading
from http import HTTPStatus
from typing import Any

from .. import llm, setup_ai, setup_save
from ..config import LLMConfig
from ..setup_connect import Nothing, Reply, Strict, parse
from ..system import desktop, profile
from .context import (Action, Context, Phase, chats, claude, claude_missing, keychain_code,
                      nothing_to_give)

# Claude's token for the evening Brief: while the family authorizes in the browser, and how the
# sign-in, or the token pasted after the Terminal window, ended. `sign-in-failed` and `timeout`
# offer the Terminal window, whose token the page takes in a field. On Windows, which signs in
# only in that window, the page installs Claude Code first: `installing`, and `install-failed` once.
CLAUDE_RESULTS = ("waiting", "saved", "not-installed", "sign-in-failed", "timeout", "not-a-token",
                  "test-call-failed", "keychain-not-reachable", "keychain-failed", "installing",
                  "install-failed")
CLAUDE_WINDOW_RESULTS = ("opened", "no-terminal", "not-installed")
# Codex's ChatGPT sign-in: signed in, signed out, `waiting` while its sign-in is open in the
# browser, and `login-failed` when that ended without signing in or didn't start. At Finish,
# `test-call-failed` when a signed-in Codex didn't answer the test call a switch needs. On
# Windows, where the page installs the Codex CLI, `installing`, and `install-failed` once.
CODEX_RESULTS = ("signed-in", "signed-out", "waiting", "login-failed", "not-installed",
                 "check-failed", "test-call-failed", "installing", "install-failed")
# The Gateway Key pasted into the page, tested and stored as `setup gateway` does.
GATEWAY_RESULTS = ("saved", "not-a-key", "rejected", "no-connection", "test-call-failed",
                   "keychain-not-reachable", "keychain-failed")


class ClaudeToken(Strict):
    token: str


class GatewayKey(Strict):
    key: str


class AISignIn(Phase):
    def __init__(self, context: Context) -> None:
        super().__init__(context)
        self._lock = threading.Lock()  # one AI sign-in at a time
        self._claude: dict[str, Any] | None = None  # Claude's sign-in's, once started
        self._codex: subprocess.Popen | None = None  # Codex's sign-in, once started
        # Claude Code's install on Windows, once started: `installing`, then `install-failed`, or
        # how opening its sign-in window went, which the next check says once.
        self._claude_install: str | None = None
        # The Codex CLI's install on Windows, once started: `installing`, then `installed` or
        # `install-failed`, which the next check says once.
        self._codex_install: str | None = None

    def routes(self) -> dict[str, Action]:
        return {"api/claude": self.sign_in_claude, "api/claude/check": self.check_claude,
                "api/claude/terminal": self.open_claude_window,
                "api/claude/token": self.save_claude_token,
                "api/claude/install": self.install_claude,
                "api/codex": self.check_codex, "api/codex/login": self.sign_in_codex,
                "api/codex/install": self.install_codex,
                "api/gateway": self.save_gateway_key}

    def stop(self) -> None:
        with self._lock:
            if self._codex is not None and self._codex.poll() is None:
                self._codex.kill()
                self._codex.wait()
            desktop.forget_terminal(setup_ai.CLAUDE_WINDOW)

    # ── Claude

    def sign_in_claude(self, raw: Any) -> Reply:
        """Starts `claude setup-token` in a pseudo-terminal, which opens Anthropic's Authorize
        page in the browser, and waits for its token in the background: the page asks how it
        went with `check_claude`. The family copies nothing. Windows has no pseudo-terminal for
        it, so there the family signs in in the window `open_claude_window` opens."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, nothing_to_give()
        if not profile.current().pseudo_terminal:
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "On Windows, Claude signs in in a PowerShell window.",
                errors=["answers: the page reads Claude's sign-in only on a Mac"])
        program = claude().program()
        if program is None:
            return HTTPStatus.OK, claude_missing()
        with self._lock:
            if self._claude is not None and self._claude["result"] is None:
                return HTTPStatus.OK, {"result": "waiting"}  # already signing in
            sign_in: dict[str, Any] = {"result": None}
            self._claude = sign_in
        threading.Thread(target=self._wait_for_claude, args=(sign_in, program),
                         daemon=True).start()
        return HTTPStatus.OK, {"result": "waiting"}

    def check_claude(self, raw: Any) -> Reply:
        """How Claude's sign-in went: `waiting` while the family is still authorizing. On
        Windows, while the page installs Claude Code, `installing`, then once how it ended:
        `install-failed`, or how opening its window went, as `open_claude_window` says it."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, nothing_to_give()
        with self._lock:
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
            result, token = setup_ai.read_setup_token(
                program, self.context.timeouts.claude_sign_in, self.context.stopped)
            extra: dict[str, Any] = {}
            if token is not None:
                result, extra = setup_ai.claude_token_sign_in(token)
            sign_in["result"] = self._signed_in("claude")[1] if result == "saved" \
                else {"result": result, **keychain_code(extra)}
        except Exception:  # never leave the page waiting
            sign_in["result"] = {"result": "sign-in-failed"}

    def open_claude_window(self, raw: Any) -> Reply:
        """Opens `claude setup-token` in a Terminal window, as the chat setup does, for when the
        page's own sign-in fails: the family copies the token it shows into the page's field.
        On Windows, a PowerShell window, which is the only way the page signs in there."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, nothing_to_give()
        program = claude().program()
        if program is None:
            return HTTPStatus.OK, claude_missing()
        return HTTPStatus.OK, {"result": _open_claude_window(program)}

    def install_claude(self, raw: Any) -> Reply:
        """On Windows, installs Claude Code with its own Windows installer in the background, then
        opens its sign-in window as `open_claude_window` does. The page checks with
        `check_claude`. On a Mac the family pastes the install line into Terminal instead."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, nothing_to_give()
        if "claude" not in profile.current().page_installs:
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "On a Mac, Claude Code installs from Terminal.",
                errors=["answers: the page installs Claude Code only on Windows"])
        with self._lock:
            installing = self._claude_install == "installing"
        # The installer puts claude.exe in place before it's done: its own run opens the window.
        if not installing and claude().program() is not None:
            return self.open_claude_window({})
        with self._lock:
            if self._claude_install != "installing":
                self._claude_install = "installing"
                threading.Thread(target=self._installing_claude, daemon=True).start()
        return HTTPStatus.OK, {"result": "installing"}

    def _installing_claude(self) -> None:
        result = "install-failed"
        try:
            program = claude().program() if setup_ai.install_claude() else None
            if program is not None:
                result = _open_claude_window(program)
        except Exception:  # never leave the page installing
            pass
        with self._lock:
            self._claude_install = result

    def save_claude_token(self, raw: Any) -> Reply:
        """Tests the token pasted into the page's own field and stores it, as the chat's Claude
        step does with the dialog's. The token is never returned, logged or put on a command line."""
        answer = parse(ClaudeToken, raw)
        program = claude().program()
        if program is None:
            return HTTPStatus.OK, claude_missing()
        result, extra = setup_ai.claude_token_sign_in(answer.token)
        if result != "saved":
            return HTTPStatus.OK, {"result": result, **keychain_code(extra)}
        desktop.forget_terminal(setup_ai.CLAUDE_WINDOW)  # the window has done its work
        return self._signed_in("claude")

    # ── Codex

    def check_codex(self, raw: Any) -> Reply:
        """Whether Codex is signed in, with its own status check. Once it is, the AI sign-in is
        done. While its sign-in is open in the browser, signed out is `waiting`. At Finish, where
        signing in switches the Household to ChatGPT, it must also answer a test call first."""
        parse(Nothing, raw)
        install = self._codex_installing()
        if install is not None:
            return HTTPStatus.OK, {"result": install}
        codex = program(self.context, "codex")
        if codex is None:
            return HTTPStatus.OK, {"result": "not-installed"}
        with self._lock:  # before the status, so a sign-in that ends meanwhile isn't called failed
            login = self._codex
            ended = login is not None and login.poll() is not None
        result = status(self.context, codex, "codex")
        if result == "ready":
            return self._codex_signed_in()
        return HTTPStatus.OK, {"result": result if result != "signed-out"
                               else self._codex_login(login, ended)}

    def _codex_installing(self) -> str | None:
        """`installing` while the page installs the Codex CLI, then `install-failed` once."""
        with self._lock:
            install = self._codex_install
            if install == "install-failed":
                self._codex_install = None  # said once, and the family can start it again
        return install if install in ("installing", "install-failed") else None

    def _codex_signed_in(self) -> Reply:
        """Saves the AI sign-in as done with Codex, which at Finish must answer a test call."""
        if self.context.connect.progress()["phase"] == "finish" and not self._codex_answers():
            return HTTPStatus.OK, {"result": "test-call-failed"}
        return self._signed_in("codex", reply={"result": "signed-in"})

    def _codex_login(self, login: subprocess.Popen | None, ended: bool) -> str:
        """How a signed-out Codex's sign-in is going: none started, `waiting` while it's open in
        the browser, or `login-failed` once, when it ended without signing in."""
        if login is None:
            return "signed-out"
        if not ended:
            return "waiting"
        with self._lock:
            if self._codex is login:
                self._codex = None  # said once, and the family can start it again
        return "login-failed"

    def _codex_answers(self) -> bool:
        """Whether Codex answers the test call doctor makes, with the Household's config as the
        evening Brief would call it. What it said isn't kept: it can name the Mac's files."""
        codex = llm.for_llm(self.context.model().llm.model_copy(update={"backend": "codex"}))
        try:
            data = codex.run('Reply with only this JSON: {"ok": true}',
                             "This is a connectivity test.", timeout=120, budget=0).data
        except Exception:
            return False
        return data.get("ok") is True

    def sign_in_codex(self, raw: Any) -> Reply:
        """Starts `codex login`, which opens the ChatGPT sign-in in the browser. The page then
        checks with `check_codex` until it's signed in."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, nothing_to_give()
        codex = program(self.context, "codex")
        if codex is None:
            return HTTPStatus.OK, {"result": "not-installed"}
        with self._lock:
            if self._codex is None or self._codex.poll() is not None:
                self._codex = setup_ai.start_codex_login(codex)
            started = self._codex is not None
        return HTTPStatus.OK, {"result": "waiting" if started else "login-failed"}

    def install_codex(self, raw: Any) -> Reply:
        """On Windows, installs the Codex CLI with OpenAI's own installer in the background, then
        starts its sign-in as `sign_in_codex` does. The page checks with `check_codex`. A Mac's
        Codex comes with the ChatGPT app, which the page offers to download instead."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, nothing_to_give()
        if "codex" not in profile.current().page_installs:
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Codex comes with the ChatGPT app on a Mac.",
                errors=["answers: the page installs Codex only on Windows"])
        if program(self.context, "codex") is not None:
            return self.sign_in_codex({})
        with self._lock:
            if self._codex_install != "installing":
                self._codex_install = "installing"
                threading.Thread(target=self._installing_codex, daemon=True).start()
        return HTTPStatus.OK, {"result": "installing"}

    def _installing_codex(self) -> None:
        codex = None
        try:
            if setup_ai.install_codex():
                codex = program(self.context, "codex")
        except Exception:  # never leave the page installing
            pass
        with self._lock:
            if codex is not None and (self._codex is None or self._codex.poll() is not None):
                self._codex = setup_ai.start_codex_login(codex)
            self._codex_install = "installed" if codex else "install-failed"

    # ── the gateway

    def save_gateway_key(self, raw: Any) -> Reply:
        """Tests the Gateway Key pasted into the page's own field with one call to the gateway the
        config names, and stores it, as `setup gateway` does with the dialog's (ADR 0007). The
        key, and what the gateway said, are never returned, logged or put on a command line."""
        answer = parse(GatewayKey, raw)
        url = self.context.section("llm").get("gateway_url")
        result, extra = setup_ai.gateway_sign_in(
            url if isinstance(url, str) and url else LLMConfig().gateway_url, answer.key)
        if result != "saved":
            return HTTPStatus.OK, {"result": result, **keychain_code(extra)}
        return self._signed_in("gateway")

    def _signed_in(self, name: str, reply: dict[str, Any] | None = None) -> Reply:
        """Saves the AI sign-in as done, moving setup on to the next Source. Once setup is at
        Finish, it's a switch: `name`, whose test call has just passed, becomes the Household's
        one AI (ADR 0017), and its chat is installed as when Welcome picks it."""
        connect = self.context.connect
        with connect.lock:
            switch = connect.progress()["phase"] == "finish"
            status, out = connect.save({"ai": name}, reply) if switch \
                else connect.complete("ai", reply=reply)
        if switch and status == HTTPStatus.OK and name in chats():
            self.context.chat.install(name)
        return status, out


def program(context: Context, name: str) -> str | None:
    """The AI `name`'s program on this Mac, at the Codex the config names if it names one."""
    codex_path = context.section("llm").get("codex_path")
    return llm.named(name, codex_path if isinstance(codex_path, str) else None).program()


def status(context: Context, program: str, name: str) -> str:
    """`ready`, `signed-out` or `check-failed`, from the AI's own status check."""
    command = ["auth", "status"] if name == "claude" else ["login", "status"]
    try:
        # Only its exit code is read: the output can name the account.
        proc = subprocess.run([program, *command], capture_output=True, text=True,
                              encoding="utf-8", timeout=context.timeouts.ai_check,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return "check-failed"
    return "ready" if proc.returncode == 0 else "signed-out"


def _open_claude_window(program: str) -> str:
    """Opens `claude setup-token` in a window, whose token the family pastes into the page:
    `opened`, or `no-terminal`."""
    opened = desktop.open_terminal(
        setup_ai.setup_token_window(program, "the field on the setup page"), {},
        setup_ai.CLAUDE_WINDOW)
    return "opened" if opened else "no-terminal"
