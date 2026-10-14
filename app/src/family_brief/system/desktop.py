"""Opening a web page, a text file or a terminal window, and reading the computer's languages
(ADR 0020). Each system has its adapter: `open`, Terminal and `defaults` on macOS, the default
browser, Notepad and Windows PowerShell on Windows."""
from __future__ import annotations

import sys
from pathlib import Path

if sys.platform == "win32":
    from . import desktop_windows as _adapter
else:
    from . import desktop_macos as _adapter


def open_url(url: str) -> bool:
    """Opens `url` in the default browser, or the app it's for. False when nothing opened, such
    as from inside an agent's sandbox or over SSH."""
    return _adapter.open_url(url)


def open_file(path: Path) -> bool:
    """Opens the text file at `path` in the system's text editor. False when nothing opened."""
    return _adapter.open_file(path)


def open_terminal(argv: list[str], env: dict[str, str], title: str) -> bool:
    """Runs `argv` in a new terminal window titled `title`, in the family's home folder, with
    `env` added to the window's environment. Returns once the window is asked for, without
    waiting for the command; False when no window opened."""
    return _adapter.open_terminal(argv, env, title)


def forget_terminal(title: str) -> None:
    """Removes what the window titled `title` was opened with, once it has done its work: the
    window may never have run it."""
    _adapter.forget_terminal(title)


def preferred_languages() -> list[str]:
    """The computer's preferred languages as tags such as en-FI, first first; empty when they
    can't be read."""
    return _adapter.preferred_languages()
