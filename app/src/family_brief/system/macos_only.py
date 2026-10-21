"""The setup page's steps and doctor's checks that only macOS has (ADR 0020), offered only where
the program runs on macOS: reading WhatsApp for Mac, with Full Disk Access for the evening job's
Python (ADR 0012), and the Passwords app. They aren't interfaces with an adapter per system; their
code stays macOS code."""
from __future__ import annotations

import sys

from .. import sources

STEPS = frozenset({*(s.name for s in sources.ALL if s.macos_only), "passwords"})


def offers(name: str) -> bool:
    """Whether the setup page and doctor offer the step or check `name` on this system."""
    return name not in STEPS or sys.platform == "darwin"
