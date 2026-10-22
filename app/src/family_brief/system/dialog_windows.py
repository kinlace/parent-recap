"""The Windows adapter of `dialog`: Windows has no dialog with hidden input that Parent Recap shows
yet, so the family types the secret at the terminal's hidden prompt instead."""
from __future__ import annotations

from . import dialog


def ask(message: str, other: str | None) -> str:
    raise dialog.NoWayToAsk()
