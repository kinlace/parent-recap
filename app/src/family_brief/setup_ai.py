"""Setup's AI sign-ins: Claude's token for the evening Brief, Codex's ChatGPT login, and the
Gateway Key. The chat setup's `setup claude` and `setup gateway` test and store a token or key
through the same calls as the setup page, so both give the same results.

`claude setup-token` signs in on Ink's interactive screen, which needs a terminal, and shows the
token only there. So the page runs it in a pseudo-terminal, as the Wilma window does (#74), wide
enough that the token isn't wrapped, and reads the token from what it shows. The family only
clicks Authorize in the browser it opens. The token stays in this process: it's never put on a
command line, logged or returned to the page, and what the screen showed is dropped once read.
Windows has no pseudo-terminal for it: there the page installs Claude Code first if it isn't
there, and the family copies the token from a PowerShell window into the page.

`codex login` opens the browser for the ChatGPT sign-in itself and keeps the login in auth.json
under ~/.codex, or in the system's own credential store if `cli_auth_credentials_store` in Codex's
own config says so (https://learn.chatgpt.com/docs/auth); the page only starts it and checks
`codex login status` until it says signed in. On Windows the page installs the Codex CLI first if
it isn't there.
"""
from __future__ import annotations

import os
import re
import select
import signal
import struct
import subprocess
import sys
import threading
import time
from typing import Any

from . import install_record, llm
from .setup_report import keychain_failure
from .system import secrets

if sys.platform != "win32":  # `claude setup-token`'s pseudo-terminal, which Windows doesn't have
    import fcntl
    import termios

CLAUDE_TOKEN_ACCOUNT = "claude-oauth-token"
CLAUDE_WINDOW = "Claude sign-in"  # the title of the window `claude setup-token` runs in
# Loose on purpose, like scripts/setup_claude_token.py: the test call decides whether it works.
CLAUDE_TOKEN = re.compile(r"sk-ant-[A-Za-z0-9_-]+")
# Loose on purpose, like CLAUDE_TOKEN: the test call decides whether it works. A Gateway Key is
# sk-bf-…, and an Anthropic-compatible endpoint used in its place takes sk-ant-….
GATEWAY_KEY = re.compile(r"sk-[A-Za-z0-9_-]{8,}")
# OpenAI's page for the ChatGPT desktop app, with Codex in it, and its download for macOS.
CODEX_APP_URL = "https://developers.openai.com/codex/app"

# Wide enough that Ink never wraps the token, which it would break across lines.
COLUMNS, ROWS = 1000, 50
READ_SECONDS = 0.2
QUIET_SECONDS = 1.0
# Downloading and unpacking the Codex CLI or Claude Code, on a slow line.
CODEX_INSTALL_SECONDS = 10 * 60
# Escape sequences a terminal program draws its screen with: CSI, OSC, and the two-byte ones.
ESCAPES = re.compile(rb"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")


def open_terminal() -> tuple[int, int]:
    """A pseudo-terminal, as this end's file descriptor and the program's."""
    ours, theirs = os.openpty()
    fcntl.ioctl(theirs, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLUMNS, 0, 0))
    return ours, theirs


def read_setup_token(program: str, timeout: float,
                     stop: threading.Event | None = None) -> tuple[str, str | None]:
    """Runs `claude setup-token` in a pseudo-terminal until it shows the token, and ends it.
    Returns `token` with the token, or `sign-in-failed` (no terminal, or it ended without one)
    or `timeout`, with None."""
    try:
        ours, theirs = open_terminal()
    except OSError:
        return "sign-in-failed", None
    try:
        proc = subprocess.Popen([program, "setup-token"], stdin=theirs, stdout=theirs,
                                stderr=theirs, env=llm.named("claude").program_env(),
                                start_new_session=True)
    except OSError:
        os.close(ours)
        return "sign-in-failed", None
    finally:
        os.close(theirs)
    shown = b""
    deadline = time.monotonic() + timeout
    last = time.monotonic()
    try:
        while time.monotonic() < deadline and not (stop and stop.is_set()):
            if not select.select([ours], [], [], READ_SECONDS)[0]:
                # A token with only the screen's escape sequences after it is all there once the
                # screen has stayed still for a moment.
                token = _token(shown, ended=time.monotonic() - last >= QUIET_SECONDS)
                if token:
                    return "token", token
                continue
            try:
                chunk = os.read(ours, 65536)
            except OSError:  # it has ended
                chunk = b""
            shown += chunk
            last = time.monotonic()
            token = _token(shown, ended=not chunk)
            if token:
                return "token", token
            if not chunk:
                return "sign-in-failed", None
        return "timeout", None
    finally:
        if proc.poll() is None:
            try:  # with what it started, such as a helper opening the browser
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                proc.kill()
        proc.wait()
        os.close(ours)


def _token(shown: bytes, *, ended: bool) -> str | None:
    """The token on the screen, once it's all there: something follows it, or the program ended."""
    text = ESCAPES.sub(b"", shown).decode("utf-8", "replace")
    found = [m for m in CLAUDE_TOKEN.finditer(text) if ended or m.end() < len(text)]
    return found[-1].group(0) if found else None


def install_codex() -> bool:
    """Installs the Codex CLI on Windows with OpenAI's own installer, as their instructions say
    to: PowerShell downloads and runs it. Whether it ran through. What it said isn't kept."""
    return _install_in_powershell(llm.named("codex").install_line())


def install_claude() -> bool:
    """Installs Claude Code on Windows with Anthropic's own installer, the same way."""
    return _install_in_powershell(llm.named("claude").install_line())


def _install_in_powershell(line: str | None) -> bool:
    if line is None:  # nothing installs it on this system
        return False
    try:
        proc = subprocess.run(["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                               "-Command", line],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=CODEX_INSTALL_SECONDS, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def start_codex_login(program: str) -> subprocess.Popen | None:
    """Starts `codex login`, which opens the ChatGPT sign-in in the browser, in the background.
    Its output isn't read: it names the sign-in's address. None if it couldn't start."""
    try:
        return subprocess.Popen([program, "login"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
    except OSError:
        return None


def claude_token_sign_in(typed: str) -> tuple[str, dict[str, Any]]:
    """Makes one test call with the Claude token `typed` and stores it in the Keychain. Returns
    the step's result, `saved`, `not-a-token`, `test-call-failed`, `keychain-not-reachable` or
    `keychain-failed`, and what else it reports: for a failed test call what Claude said as
    `error`, with the token taken out, and when the Keychain refused it macOS's `code`. The setup
    page's sign-in, its own field and `setup claude` call this, so all three give the same
    results."""
    # The token has no spaces, so any are from copying it across the lines Terminal wrapped it on.
    token = "".join(typed.split())
    if not CLAUDE_TOKEN.fullmatch(token):
        # Not sent to Claude: it's most likely the family's own password, or only part of the token.
        return "not-a-token", {}

    error = llm.named("claude").test_token(token)
    if error is not None:
        return "test-call-failed", {"error": error.replace(token, "<token>").strip()[:200]}

    try:
        secrets.put(CLAUDE_TOKEN_ACCOUNT, token)
    except secrets.SecretsError as e:
        return keychain_failure(e)
    install_record.add("keychain", CLAUDE_TOKEN_ACCOUNT)
    return "saved", {}


def setup_token_window(program: str, paste_into: str = "the Parent Recap dialog") -> list[str]:
    """The command for the window `claude setup-token` runs in, whose token the family copies
    into `paste_into` (claude_window.py). This Python, since the window's PATH may not lead to
    this program."""
    return [sys.executable, "-m", "family_brief.claude_window", program, paste_into]


def gateway_sign_in(url: str, typed: str) -> tuple[str, dict[str, Any]]:
    """Makes one test call through the gateway at `url` with the Gateway Key `typed` and stores
    it in the Keychain. Returns the step's result, `saved`, `not-a-key`, `rejected`,
    `no-connection`, `test-call-failed`, `keychain-not-reachable` or `keychain-failed`, and what
    else it reports: for a failed test call what the gateway said as `error`, with the key taken
    out, and when the Keychain refused it macOS's `code`. A key that has used today's budget
    works, so it's stored."""
    from . import gateway

    key = "".join(typed.split())  # spaces and line breaks are from copying it
    if not GATEWAY_KEY.fullmatch(key):
        return "not-a-key", {}  # not sent: most likely a password, or part of the key
    outcome, detail = gateway.test_call(url, key)
    if outcome in (gateway.UNKNOWN, gateway.NO_ANSWER, gateway.FAILED):
        result = {gateway.UNKNOWN: "rejected", gateway.NO_ANSWER: "no-connection",
                  gateway.FAILED: "test-call-failed"}[outcome]
        return result, {"error": (detail or "")[:200]}
    try:
        secrets.put(gateway.ACCOUNT, key)
    except secrets.SecretsError as e:
        return keychain_failure(e)
    install_record.add("keychain", gateway.ACCOUNT)
    return "saved", {}
