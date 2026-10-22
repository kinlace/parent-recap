"""The Windows `profile`: install.ps1's layout under %LOCALAPPDATA%, PowerShell, the folders
Windows installers use, and no setup skill yet, since the skills set up a Mac."""
from __future__ import annotations

import os
from pathlib import Path

from . import profile

# Under %LOCALAPPDATA%: the folder OpenAI's install.ps1 puts the Codex CLI in by default
# (https://github.com/openai/codex/blob/main/scripts/install/install.ps1).
CODEX_DIR = ("Programs", "OpenAI", "Codex", "bin")
# The Codex CLI the way OpenAI installs it on Windows: its own install.ps1, published with each
# release, puts codex.exe in CODEX_DIR by default.
CODEX_INSTALL = "irm https://github.com/openai/codex/releases/latest/download/install.ps1 | iex"
# Claude Code's native installer for PowerShell, which puts claude.exe in
# %USERPROFILE%\.local\bin, without admin (https://code.claude.com/docs/en/setup).
CLAUDE_INSTALL = "irm https://claude.ai/install.ps1 | iex"


def _program_folders() -> list[str]:
    # Where OpenAI's own installer puts codex.exe, which goes on the user's PATH only in windows
    # opened after it ran: before an older codex npm may have installed. Then npm's global
    # folder, where it puts a `.cmd` for each program, and a script for Git Bash without an
    # extension, which Windows can't run.
    appdata, local = os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA")
    return ([os.path.join(local, *CODEX_DIR)] if local else []) + \
        ([os.path.join(appdata, "npm")] if appdata else [])


def _install_folders(home: Path) -> list[Path]:
    # install.ps1's, then install.sh's.
    local = os.environ.get("LOCALAPPDATA")
    return list(dict.fromkeys([*([Path(local) / "ParentRecap"] if local else []),
                               home / "ParentRecap"]))


PROFILE = profile.Profile(
    system="windows",
    computer="PC",
    terminal="PowerShell",
    remove_folder="Remove-Item -Recurse -Force {folder}",
    reinstall="the family pastes the install line into PowerShell again",
    missed_run="the PC may have been asleep, signed out or off",
    closed_lines={
        "fi": "Parent Recapin käyttöönottosivu on suljettu. Voit sulkea tämän PowerShell-ikkunan.",
        "en": "Parent Recap's setup page has closed. You can close this PowerShell window.",
        "zh": "Parent Recap 的设置页面已经关闭。你可以关掉这个 PowerShell 窗口了。",
    },
    ais=("gateway", "codex", "claude"),  # the gateway stays Welcome's default on Windows
    page_installs=frozenset({"claude", "codex"}),
    install_lines={"claude": CLAUDE_INSTALL, "codex": CODEX_INSTALL},
    chats=frozenset(),
    pseudo_terminal=False,
    login_shell=False,
    program_endings=(".exe", ".cmd", ".bat"),  # in the order Windows' own PATHEXT tries them
    program_folders=_program_folders,
    install_folders=_install_folders,
    command=Path("Scripts/parent-recap.exe"),
    node=Path("node.exe"),  # npm beside it, in node_modules
    node_modules=Path("node_modules"),
    npm_links_programs=False,  # only .cmd wrappers, so the program runs from its package's script
    removes_running=False,  # Windows doesn't let a running program, or its Python, be removed
)
