"""Asking the family for a secret in a dialog of the system's own, with hidden input (ADR 0005,
0020). Each system has its adapter: an AppleScript dialog through `osascript` on macOS; Windows
has none yet, so the caller asks at a hidden prompt in the terminal instead."""
from __future__ import annotations

import os
import sys

if sys.platform == "win32":
    from . import dialog_windows as _adapter
else:
    from . import dialog_macos as _adapter

TITLE = "Parent Recap"


class Cancelled(Exception):
    """The family closed the dialog, or pressed Ctrl-C at the terminal prompt."""


class OtherChosen(Exception):
    """The family pressed the dialog's second choice (`other`), instead of entering a secret."""


class NoWayToAsk(Exception):
    """No dialog can be shown, such as from inside an agent's sandbox or over SSH."""


def ask(message: str, other: str | None = None) -> str:
    """What the family typed in a dialog titled TITLE showing `message`, with hidden input, as
    typed. `other` names a second button, for when the family can't get the secret. Raises
    Cancelled, OtherChosen when they press `other`, or NoWayToAsk."""
    return _adapter.ask(message, other)


def over_ssh() -> bool:
    """Whether the program runs over SSH, where no dialog shows on the computer's screen."""
    return any(os.environ.get(v) for v in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"))
