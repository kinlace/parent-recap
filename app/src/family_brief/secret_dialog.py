"""Asks the family for a secret in a macOS dialog with hidden input (ADR 0005).

Every setup step that needs a secret (the Gmail App Password, the Claude token, a MyClub link)
asks through `ask`, so the value goes from the family straight to the step and never through
the chat. The value is returned to the caller only: it's never printed, logged, or put on a
command line, where `ps` would show it. The dialog's script goes to `osascript` on standard
input and the typed text comes back on its standard output, which is captured.

Without a desktop session (over SSH, or when macOS won't show the dialog) it asks at a hidden
prompt in Terminal instead.

A command that needs the Mac password, such as setting the wake schedule, runs through
`as_administrator`: macOS's own administrator dialog asks for the password, so it never reaches
the program either.
"""
from __future__ import annotations

import getpass
import os
import subprocess

TITLE = "Parent Recap"
# macOS's own lock icon, so the password dialog doesn't show osascript's generic folder icon.
LOCK_ICON = "/System/Library/CoreServices/CoreTypes.bundle/Contents/Resources/LockedIcon.icns"
_SAVE, _CANCEL = "Save", "Cancel"


class Cancelled(Exception):
    """The family closed the dialog, or pressed Ctrl-C at the Terminal prompt."""


class OtherChosen(Exception):
    """The family pressed the dialog's second choice (`other`), instead of entering a secret."""


class NoWayToAsk(Exception):
    """Neither a dialog nor a Terminal prompt can be shown, such as from inside an agent's sandbox."""


def ask(message: str, *, other: str | None = None) -> str:
    """The secret the family entered, stripped of surrounding whitespace. `other` names a second
    button, for when the family can't get the secret; pressing it raises OtherChosen."""
    if not over_ssh():
        try:
            return _dialog(message, other)
        except NoWayToAsk:
            pass
    return _terminal(message, other)


def as_administrator(command: str, prompt: str) -> None:
    """Runs a shell command as root once the family enters their Mac password in macOS's
    administrator dialog, which shows `prompt`. Raises Cancelled when they cancel it, and
    NoWayToAsk without a desktop session or when the dialog or the command fails; there is no
    Terminal fallback here, since `sudo` asks for the password itself."""
    if over_ssh():
        raise NoWayToAsk()
    script = (f"do shell script {_quote(command)} with prompt {_quote(prompt)} "
              "with administrator privileges")
    try:
        proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    except OSError as e:  # no osascript: not a Mac
        raise NoWayToAsk() from e
    if proc.returncode != 0:
        if "(-128)" in proc.stderr:
            raise Cancelled()
        raise NoWayToAsk()


def over_ssh() -> bool:
    return any(os.environ.get(v) for v in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"))


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _dialog(message: str, other: str | None) -> str:
    buttons = [b for b in (other, _CANCEL, _SAVE) if b]
    icon = f"((POSIX file {_quote(LOCK_ICON)}) as alias)" if os.path.isfile(LOCK_ICON) else "note"
    # Returns "1" and the typed text for Save, "2" for the other button; Cancel exits with -128.
    script = (
        f"set r to display dialog {_quote(message)} default answer \"\" with hidden answer "
        f"with title {_quote(TITLE)} with icon {icon} "
        f"buttons {{{', '.join(_quote(b) for b in buttons)}}} "
        f"default button {_quote(_SAVE)} cancel button {_quote(_CANCEL)}\n"
        f"if button returned of r is {_quote(_SAVE)} then return \"1\" & text returned of r\n"
        "return \"2\"\n"
    )
    try:
        proc = subprocess.run(["osascript", "-"], input=script, capture_output=True, text=True)
    except OSError as e:  # no osascript: not a Mac
        raise NoWayToAsk() from e
    if proc.returncode != 0:
        if "(-128)" in proc.stderr:
            raise Cancelled()
        raise NoWayToAsk()
    answer = proc.stdout.removesuffix("\n")
    if answer.startswith("1"):
        return answer[1:].strip()
    raise OtherChosen()


def _terminal(message: str, other: str | None) -> str:
    hint = f" Press Enter without typing anything if {other[0].lower() + other[1:]}." if other else ""
    try:
        value = getpass.getpass(f"{message}{hint}\n(hidden input): ").strip()
    except KeyboardInterrupt as e:
        raise Cancelled() from e
    except (EOFError, OSError) as e:
        raise NoWayToAsk() from e
    if value:
        return value
    if other:
        raise OtherChosen()
    raise Cancelled()
