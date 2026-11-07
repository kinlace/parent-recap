"""The entries on Linux: none. Only the hosted version runs on Linux (ADR 0029), and a Hosted
Household's settings are the maintainers' (ADR 0042)."""
from __future__ import annotations

from pathlib import Path


def offered() -> bool:
    return False


def path(name: str) -> Path:
    raise RuntimeError("Parent Recap puts no entries on Linux")


def place(name: str, target: Path) -> None:
    raise RuntimeError("Parent Recap puts no entries on Linux")


def placed(name: str) -> bool:
    return False


def remove(name: str) -> None:
    pass


def where() -> str:
    return ""
