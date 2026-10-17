"""The Windows adapter of `desktop`: the default browser, Notepad, Windows PowerShell windows and
the Windows display languages."""
from __future__ import annotations

import base64
import ctypes
import msvcrt
import os
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

MUI_LANGUAGE_NAME = 0x8  # GetUserPreferredUILanguages' tags such as fi-FI, not numeric ids
SINGLE_QUOTES = "'‘’‚‛"  # each ends a PowerShell '...' string


def open_url(url: str) -> bool:
    try:
        os.startfile(url)  # type: ignore[attr-defined]  # Windows only
    except OSError:
        return False
    return True


def open_file(path: Path) -> bool:
    # Notepad, since a .yaml file has no program of its own on a new PC.
    return _start(["notepad.exe", str(path)])


def open_terminal(argv: list[str], env: dict[str, str], title: str) -> bool:
    # -NoExit keeps the window open once the command ends, so the family reads what it said, as
    # in a Terminal window. The command goes encoded, so no quoting of Windows' command line
    # reaches PowerShell, and nothing is written to disk for it.
    encoded = base64.b64encode(window_script(argv, title).encode("utf-16-le")).decode()
    return _start(["powershell.exe", "-NoLogo", "-NoProfile", "-NoExit", "-EncodedCommand",
                   encoded], env={**os.environ, **env}, cwd=str(Path.home()),
                  creationflags=subprocess.CREATE_NEW_CONSOLE)  # type: ignore[attr-defined]


def forget_terminal(title: str) -> None:
    pass  # a window's command is on its command line, not in a file


def window_script(argv: list[str], title: str) -> str:
    """The PowerShell the window runs: its title, then `argv`, each word as it is. Raises
    ValueError for a word with a double quote: Windows PowerShell 5.1 drops it on the way to the
    program, which then runs with a word it wasn't given (#376). No Windows path has one."""
    if any('"' in word for word in argv):
        raise ValueError("Windows PowerShell would drop the double quotes in a window's command")
    # Without a console of its own PowerShell can't set a title, which changes nothing else.
    return (f"try {{ $Host.UI.RawUI.WindowTitle = {_quoted(title)} }} catch {{ }}\n"
            f"& {' '.join(map(_quoted, argv))}\n")


def _quoted(word: str) -> str:
    """`word` as a PowerShell string that expands nothing. PowerShell takes the curly single quotes
    for one too, as in a home folder named O’Brien, so each is doubled the same way. Windows
    PowerShell passes it on to a program as one word, without any double quote in it."""
    return "'" + "".join(c * 2 if c in SINGLE_QUOTES else c for c in word) + "'"


def preferred_languages() -> list[str]:
    try:
        kernel32 = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined]
        count, size = wintypes.ULONG(), wintypes.ULONG(0)
        if not kernel32.GetUserPreferredUILanguages(MUI_LANGUAGE_NAME, ctypes.byref(count), None,
                                                    ctypes.byref(size)):
            return []
        listed = ctypes.create_unicode_buffer(size.value)
        if not kernel32.GetUserPreferredUILanguages(MUI_LANGUAGE_NAME, ctypes.byref(count), listed,
                                                    ctypes.byref(size)):
            return []
    except (OSError, AttributeError):
        return []
    return [tag for tag in listed[:size.value].split("\0") if tag]  # each ends in \0


def typing_at_terminal() -> bool:
    """`isatty()` alone is also True for the null device, a character device, which is what an
    agent's stdin is: only a console has a console mode."""
    try:
        if sys.stdin is None or not sys.stdin.isatty():
            return False
        kernel32 = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined]
        handle = wintypes.HANDLE(msvcrt.get_osfhandle(sys.stdin.fileno()))
        return bool(kernel32.GetConsoleMode(handle, ctypes.byref(wintypes.DWORD())))
    except (OSError, ValueError, AttributeError):  # closed, or not a real file
        return False


def _start(argv: list[str], **kwargs) -> bool:
    try:
        subprocess.Popen(argv, **kwargs)
    except OSError:
        return False
    return True
