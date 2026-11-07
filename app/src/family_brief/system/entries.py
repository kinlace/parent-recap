"""The two entries the install puts where the system lists programs (ADR 0042): Parent Recap
Settings, which opens the setup page as `parent-recap setup page` does, and Parent Recap Check,
which runs `parent-recap doctor` in a window that stays open for a screenshot. Each system has its
adapter: small app bundles in `~/Applications` on macOS, where Spotlight and Launchpad find them,
and Start menu shortcuts on Windows. Linux, where only the hosted version runs, gets none: a
Hosted Household's settings are the maintainers'. Opening either asks the family for no
permission. Their names stay in English on every system: they hold the product's name, so a
search for "Parent Recap" finds both in any language."""
from __future__ import annotations

from pathlib import Path

from . import adapter

SETTINGS = "Parent Recap Settings"
CHECK = "Parent Recap Check"
NAMES = (SETTINGS, CHECK)

_adapter = adapter("entries")


def offered() -> bool:
    """Whether the install puts the entries on this system."""
    return _adapter.offered()


def path(name: str) -> Path:
    """Where the entry `name` goes."""
    return _adapter.path(name)


def place(name: str, target: Path) -> None:
    """Makes the entry `name` for the install in `target`, or makes it again as this version has
    it. Raises OSError when it can't."""
    _adapter.place(name, target)


def placed(name: str) -> bool:
    """Whether the entry `name` is there, made by `place`."""
    return _adapter.placed(name)


def remove(name: str) -> None:
    """Removes the entry `name`, and nothing of the family's own beside it."""
    _adapter.remove(name)


def where() -> str:
    """Where the entries are, as doctor and uninstall say it."""
    return _adapter.where()
