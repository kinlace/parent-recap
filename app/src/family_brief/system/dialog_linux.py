"""The Linux adapter of `dialog`: a server has no screen for a dialog, so the family types the
secret at the terminal's hidden prompt instead."""
from __future__ import annotations

from . import dialog


def ask(message: str, other: str | None) -> str:
    raise dialog.NoWayToAsk()
