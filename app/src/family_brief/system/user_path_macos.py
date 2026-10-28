"""The PATH on macOS: one marked line in `~/.zprofile`, which zsh, macOS's default shell, reads in
every new Terminal window. `/usr/local/bin` and `/etc/paths.d` would need the family's Mac
password."""
from __future__ import annotations

import shlex
from pathlib import Path

MARK = "# Parent Recap's command; parent-recap uninstall removes this line"


def _zprofile() -> Path:
    return Path.home() / ".zprofile"


def _line(folder: Path) -> str:
    return f"export PATH={shlex.quote(str(folder))}:\"$PATH\"  {MARK}"


def _lines() -> list[str]:
    try:
        return _zprofile().read_text(encoding="utf-8").splitlines(keepends=True)
    except FileNotFoundError:
        return []


def add(folder: Path) -> None:
    if has(folder):
        return
    lines = _lines()
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    _zprofile().write_text("".join([*lines, _line(folder) + "\n"]), encoding="utf-8")


def has(folder: Path) -> bool:
    return any(line.rstrip("\n") == _line(folder) for line in _lines())


def remove(folder: Path) -> None:
    lines = _lines()
    kept = [line for line in lines if line.rstrip("\n") != _line(folder)]
    if kept == lines:
        return
    if kept:
        _zprofile().write_text("".join(kept), encoding="utf-8")
    else:  # it held only Parent Recap's line, so add made it
        _zprofile().unlink()


def where() -> str:
    return "~/.zprofile"
