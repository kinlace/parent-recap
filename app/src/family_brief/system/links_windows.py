"""The Windows `links`: directory junctions, which any user may make without Developer Mode or an
administrator, unlike a symlink. A junction holds a full path, and Windows can't rename one over
another, so `point` makes the new one beside it, removes the old one and renames the new one into
its place: `link` is missing between those two steps."""
from __future__ import annotations

import os
from pathlib import Path

ONE_STEP = False
_LONG = "\\\\?\\"  # how a junction's own target starts, as os.readlink gives it


def point(link: Path, to: Path) -> None:
    import _winapi  # Windows' own; CPython's, used by its own tests to make junctions
    to = Path(os.path.abspath(link.parent / to))
    incoming = link.with_name(f".{link.name}.incoming")
    if is_link(incoming):
        os.rmdir(incoming)
    _winapi.CreateJunction(str(to), str(incoming))
    before = target(link) if is_link(link) else None
    if before is not None:
        os.rmdir(link)  # the link only: a junction is removed as an empty folder is
    try:
        os.rename(incoming, link)
    except OSError:
        # As it was, so the version before still runs.
        if before is not None and not os.path.lexists(link):
            _winapi.CreateJunction(str(before), str(link))
        os.rmdir(incoming)
        raise


def target(link: Path) -> Path:
    text = os.readlink(link)
    return Path(text[len(_LONG):] if text.startswith(_LONG) else text)


def is_link(path: Path) -> bool:
    return path.is_junction() or path.is_symlink()


def remove(link: Path) -> None:
    os.rmdir(link)
