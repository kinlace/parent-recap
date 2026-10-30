"""The features that only macOS has (ADR 0020), offered only where the program runs on macOS: the
setup page's step that opens the Passwords app, and the Brief sent as an iMessage through
Messages. They aren't interfaces with an adapter per system; their code stays macOS code."""
from __future__ import annotations

from . import profile

STEPS = frozenset({"passwords", "imessage"})


def offers(name: str) -> bool:
    """Whether the feature `name` is offered on this system."""
    return name not in STEPS or profile.current().system == "macos"
