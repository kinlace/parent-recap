"""Links to a folder, such as `app` to the version that runs (ADR 0047, ADR 0020): a symlink on
macOS and Linux, a junction on Windows, where a symlink needs Developer Mode or an administrator
and a junction needs neither. Making one, reading where it points and telling it from a folder
all go through here, so versions.py is the same on every system.

Only the standard library: install.sh and the Windows installer run versions.py with a new
version's Python before anything else of it has run."""
from __future__ import annotations

from pathlib import Path

from . import adapter

_adapter = adapter("links")


def point(link: Path, to: Path) -> None:
    """Makes `link` a link to the folder `to`, in place of the link there, if any. `to` may be
    relative to `link`'s folder. Where `one_step()` is False there is a moment in between when
    `link` isn't there."""
    _adapter.point(link, to)


def target(link: Path) -> Path | None:
    """Where the link `link` points, as it was made, or None when it isn't a link."""
    return _adapter.target(link) if is_link(link) else None


def is_link(path: Path) -> bool:
    """Whether `path` is a link, not a folder of its own, whether or not what it points at is
    there."""
    return _adapter.is_link(path)


def remove(link: Path) -> None:
    """Removes the link `link` itself, never what is in the folder it points at."""
    _adapter.remove(link)


def one_step() -> bool:
    """Whether `point` replaces a link in one step, so that whatever opens it in between finds the
    folder before or the one after, never nothing."""
    return _adapter.ONE_STEP
