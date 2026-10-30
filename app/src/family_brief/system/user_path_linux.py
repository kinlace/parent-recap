"""The PATH on Linux: each program in the folder linked into `~/.local/bin`, which Ubuntu's own
`~/.profile` puts on the PATH of every login shell once the folder exists. No line of any shell's
file is changed, and the links are the only thing taken out again."""
from __future__ import annotations

from pathlib import Path


def _local_bin() -> Path:
    return Path.home() / ".local" / "bin"


def _ours(link: Path, folder: Path) -> bool:
    return link.is_symlink() and link.readlink().parent == folder


def add(folder: Path) -> None:
    for program in sorted(folder.iterdir()):
        link = _local_bin() / program.name
        if link.exists() or link.is_symlink():
            continue  # ours already, or a program of the user's own: theirs wins
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(program)


def has(folder: Path) -> bool:
    return folder.is_dir() and any(_ours(_local_bin() / p.name, folder) for p in folder.iterdir())


def remove(folder: Path) -> None:
    if not _local_bin().is_dir():
        return
    for link in _local_bin().iterdir():
        if _ours(link, folder):
            link.unlink()


def where() -> str:
    return "~/.local/bin"
