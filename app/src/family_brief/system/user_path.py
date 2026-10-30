"""The user's PATH, which a new Terminal or PowerShell window starts with (ADR 0020, #560). Each
system has its adapter: a marked line in `~/.zprofile` on macOS, whose default shell is zsh, and
the user's own PATH under `HKCU\\Environment` on Windows, and a link in `~/.local/bin` on Linux.
None needs an administrator, and
only the folder added is ever taken out: the rest stays as the family had it."""
from __future__ import annotations

from pathlib import Path

from . import adapter

_adapter = adapter("user_path")


def add(folder: Path) -> None:
    """Puts `folder` on the PATH of the windows opened from now on, once however often it runs."""
    _adapter.add(folder)


def has(folder: Path) -> bool:
    """Whether `add` put `folder` there."""
    return _adapter.has(folder)


def remove(folder: Path) -> None:
    """Takes out what `add` put there for `folder`, and nothing else."""
    _adapter.remove(folder)


def where() -> str:
    """Where the PATH is kept, as uninstall lists it."""
    return _adapter.where()
