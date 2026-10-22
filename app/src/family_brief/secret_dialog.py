"""Asks the family for a secret in a dialog with hidden input (ADR 0005).

Every setup step that needs a secret (the Gmail App Password, the Claude token, a MyClub link)
asks through `ask`, so the value goes from the family straight to the step and never through
the chat. The value is returned to the caller only: it's never printed, logged, or put on a
command line, where `ps` would show it. The dialog is the system's own (`system.dialog`).

Without a desktop session (over SSH, or when the system shows no dialog) it asks at a hidden
prompt in the terminal instead.

A command that needs the Mac password, setting or cancelling the wake schedule, runs through
macOS's own administrator dialog in `system.wake_macos`, so it never reaches the program either.
"""
from __future__ import annotations

import getpass

from .system import dialog
# Callers catch these as secret_dialog's.
from .system.dialog import Cancelled, NoWayToAsk, OtherChosen, over_ssh  # noqa: F401


def ask(message: str, *, other: str | None = None) -> str:
    """The secret the family entered, stripped of surrounding whitespace. `other` names a second
    button, for when the family can't get the secret; pressing it raises OtherChosen."""
    if not over_ssh():
        try:
            return dialog.ask(message, other).strip()
        except NoWayToAsk:
            pass
    return _terminal(message, other)


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
