"""The setup page's steps and doctor's checks that only macOS has (ADR 0020), offered only where
the program runs on macOS: reading WhatsApp for Mac, with Full Disk Access for the evening job's
Python (ADR 0012), and the Passwords app. They aren't interfaces with an adapter per system; their
code stays macOS code."""
from __future__ import annotations

import sys

from .. import sources

OTHER_STEPS = frozenset({"passwords"})  # the Sources' own are in their records


def offers(name: str) -> bool:
    """Whether the setup page and doctor offer the step or check `name` on this system. The
    registry is asked each time, so a Source registered later is offered as its record says."""
    source = sources.named(name)
    only_macos = source.macos_only if source else name in OTHER_STEPS
    return not only_macos or sys.platform == "darwin"
