"""The macOS `links`: symlinks, replaced in one step by a new one renamed over the old."""
from __future__ import annotations

import os
from pathlib import Path

ONE_STEP = True


def point(link: Path, to: Path) -> None:
    incoming = link.with_name(f".{link.name}.incoming")
    incoming.unlink(missing_ok=True)
    os.symlink(to, incoming)
    os.replace(incoming, link)


def target(link: Path) -> Path:
    return Path(os.readlink(link))


def is_link(path: Path) -> bool:
    return path.is_symlink()


def remove(link: Path) -> None:
    link.unlink()
