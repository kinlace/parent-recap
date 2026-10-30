"""What else differs between the systems the program runs on, besides the OS services behind their
own interfaces (ADR 0020): the words the family reads for their computer and its terminal, the
AIs the setup page offers and installs, the chats setup continues in, where programs are found
and how the install lays them out. One profile per system, in profile_macos.py,
profile_windows.py and profile_linux.py; callers ask `current()` instead of checking the system
themselves, and the setup page gets it as data. A new system adds its profile here, and changes no caller."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from . import adapter


@dataclass(frozen=True)
class Profile:
    system: str                   # "macos", "windows" or "linux": the setup page's words for it
    computer: str                 # what the family calls their computer: "Mac", "PC"
    terminal: str                 # the window the family types commands in: "Terminal", "PowerShell"
    remove_folder: str            # the command that removes {folder} and what's in it
    reinstall: str                # how the program is installed again, for the assistant
    missed_run: str               # why the evening run may not have run for a day
    closed_lines: Mapping[str, str]  # what the terminal says once the setup page stops, by language
    ais: tuple[str, ...]          # the AIs the setup page offers, Welcome's default first
    page_installs: frozenset[str]  # the AIs the setup page installs itself
    install_lines: Mapping[str, str]  # the line that installs each AI's program in the terminal
    installer: tuple[str, ...]    # the command the setup page runs such a line with, line last
    codex_apps: bool              # whether the Codex or ChatGPT desktop app, with a codex inside, runs here
    chats: frozenset[str]         # the AIs whose chat has a setup skill for this system
    page_skips: frozenset[str]    # the setup page's Connect steps never offered here, by name
    pseudo_terminal: bool         # whether a program can run in a pseudo-terminal the program reads
    login_shell: bool             # whether the family's login shell can say where a program is
    program_endings: tuple[str, ...]  # what a program's file name ends in, in the order tried
    program_folders: Callable[[], list[str]]  # where installers put programs, besides the home's
    install_folders: Callable[[Path], list[Path]]  # where the install puts Parent Recap, for a home
    command: Path                 # the program's command, in its venv
    path_command: Path            # what the PATH's `parent-recap` runs, in its venv
    links_command: bool           # whether that is a link to it, or a .cmd that runs it -m family_brief
    node: Path                    # `node`, in the folder Node was unpacked into
    node_modules: Path            # where npm puts packages, under a Node folder or a prefix
    npm_links_programs: bool      # whether npm puts a package's program in <prefix>/bin
    removes_running: bool         # whether a program's files can be removed while it runs
    os_version: Callable[[], str]  # the system and its version, as a report of a failure names it


# After Profile, which each adapter imports from here.
_adapter = adapter("profile")


def current() -> Profile:
    """The profile of the system the program runs on."""
    return _adapter.PROFILE
