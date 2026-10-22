"""What else differs between the systems the program runs on, besides the OS services behind their
own interfaces (ADR 0020): the words the family reads for their computer and its terminal, the
AIs the setup page offers and installs, the chats setup continues in, where programs are found
and how the install lays them out. One profile per system, in profile_macos.py and
profile_windows.py; callers ask `current()` instead of checking the system themselves, and the
setup page gets it as data. A new system adds its profile here, and changes no caller."""
from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Profile:
    system: str                   # "macos" or "windows": which system's words the setup page uses
    computer: str                 # what the family calls their computer: "Mac", "PC"
    terminal: str                 # the window the family types commands in: "Terminal", "PowerShell"
    remove_folder: str            # the command that removes {folder} and what's in it
    reinstall: str                # how the program is installed again, for the assistant
    missed_run: str               # why the evening run may not have run for a day
    closed_lines: Mapping[str, str]  # what the terminal says once the setup page stops, by language
    ais: tuple[str, ...]          # the AIs the setup page offers, Welcome's default first
    page_installs: frozenset[str]  # the AIs the setup page installs itself
    install_lines: Mapping[str, str]  # the line that installs each AI's program in the terminal
    chats: frozenset[str]         # the AIs whose chat has a setup skill for this system
    pseudo_terminal: bool         # whether a program can run in a pseudo-terminal the program reads
    login_shell: bool             # whether the family's login shell can say where a program is
    program_endings: tuple[str, ...]  # what a program's file name ends in, in the order tried
    program_folders: Callable[[], list[str]]  # where installers put programs, besides the home's
    install_folders: Callable[[Path], list[Path]]  # where the install puts Parent Recap, for a home
    command: Path                 # the program's command, in its venv
    node: Path                    # `node`, in the folder Node was unpacked into
    node_modules: Path            # where npm puts packages, under a Node folder or a prefix
    npm_links_programs: bool      # whether npm puts a package's program in <prefix>/bin
    removes_running: bool         # whether a program's files can be removed while it runs


if sys.platform == "win32":
    from . import profile_windows as _adapter
else:
    from . import profile_macos as _adapter


def current() -> Profile:
    """The profile of the system the program runs on."""
    return _adapter.PROFILE
