"""The setup page's AI sign-ins: Claude's token for the evening Brief, and Codex's ChatGPT login.

`claude setup-token` signs in on Ink's interactive screen, which needs a terminal, and shows the
token only there. So the page runs it in a pseudo-terminal, as the Wilma window does (#74), wide
enough that the token isn't wrapped, and reads the token from what it shows. The family only
clicks Authorize in the browser it opens. The token stays in this process: it's never put on a
command line, logged or returned to the page, and what the screen showed is dropped once read.

`codex login` opens the browser for the ChatGPT sign-in itself and keeps the login in ~/.codex;
the page only starts it and checks `codex login status` until it says signed in.
"""
from __future__ import annotations

import fcntl
import os
import re
import select
import signal
import struct
import subprocess
import termios
import threading
import time

from .setup_steps import CLAUDE_TOKEN
from .summarize import _sessionless_env

# Wide enough that Ink never wraps the token, which it would break across lines.
COLUMNS, ROWS = 1000, 50
READ_SECONDS = 0.2
QUIET_SECONDS = 1.0
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
                                stderr=theirs, env=_sessionless_env(), start_new_session=True)
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


def start_codex_login(program: str) -> subprocess.Popen | None:
    """Starts `codex login`, which opens the ChatGPT sign-in in the browser, in the background.
    Its output isn't read: it names the sign-in's address. None if it couldn't start."""
    try:
        return subprocess.Popen([program, "login"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
    except OSError:
        return None
