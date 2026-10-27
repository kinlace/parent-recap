"""The setup page's steps that only macOS has (ADR 0020), offered only where the program runs on
macOS: the Passwords app. They aren't interfaces with an adapter per system; their code stays
macOS code."""
from __future__ import annotations

from . import profile

STEPS = frozenset({"passwords"})


def offers(name: str) -> bool:
    """Whether the setup page offers the step `name` on this system."""
    return name not in STEPS or profile.current().system == "macos"
