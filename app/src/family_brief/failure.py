"""Why a setup step failed, in the words the setup page shows under its message and a family
copies to us (#483): a program's exit code and the last lines it said, a time limit, or an
exception on one line. Every step that fails builds its reason here, so they all read alike, and
logs it with `logged`."""
from __future__ import annotations

import logging
import subprocess
from importlib import metadata
from pathlib import Path

from .system import profile

log = logging.getLogger(__name__)

REASON_LINES = 10  # of what a program said last, which is where its error is


def exited(name: str, code: int, stderr: str | bytes | None, stdout: str | bytes | None) -> str:
    """`name` ended with exit `code`: its last lines of errors, or of what it printed when it
    said no error, or the exit code alone."""
    said = _text(stderr).strip() or _text(stdout).strip()
    ended = f"{name} ended with exit code {code}"
    lines = said.splitlines()[-REASON_LINES:]
    return "\n".join([f"{ended}:", *lines]) if lines else f"{ended}."


def timed_out(name: str, seconds: float) -> str:
    return f"{name} didn't finish within {seconds:g} seconds."


def raised(e: BaseException, what: str) -> str:
    """`e` stopped `what`: its type and message, on one line, as the page has room for."""
    return f"{what} stopped: {type(e).__name__}: {' '.join(str(e).split())}"


def of(e: BaseException, what: str, name: str | None = None) -> str:
    """Why `e` stopped `what`: a program's time limit or exit code, by `name` or else by its
    command, or any other exception as `raised` gives it."""
    if isinstance(e, subprocess.TimeoutExpired):
        return timed_out(name or _command(e.cmd), e.timeout)
    if isinstance(e, subprocess.CalledProcessError):
        return exited(name or _command(e.cmd), e.returncode, e.stderr, e.output)
    return raised(e, what)


def logged(what: str, reason: str) -> str:
    """Logs `reason` as a warning after `what` failed, and gives it back."""
    log.warning("%s: %s", what, reason)
    return reason


def about() -> dict[str, str]:
    """What a family's report of a failure carries besides its reason, so we needn't ask: Parent
    Recap's version, and the system and its version."""
    try:
        version = metadata.version("family-brief")
    except metadata.PackageNotFoundError:
        version = "unknown"
    return {"version": version, "os": profile.current().os_version()}


def _command(cmd: object) -> str:
    """A command by its program's file name, without the folder, and its arguments."""
    if isinstance(cmd, (list, tuple)) and cmd:
        return " ".join([Path(str(cmd[0])).name, *map(str, cmd[1:])])
    return str(cmd)


def _text(out: str | bytes | None) -> str:
    if isinstance(out, bytes):
        return out.decode("utf-8", "replace")
    return out or ""
