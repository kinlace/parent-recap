"""The Linux `links`: symlinks, as on macOS."""
from __future__ import annotations

from .links_macos import ONE_STEP, is_link, point, remove, target

__all__ = ["ONE_STEP", "is_link", "point", "remove", "target"]
