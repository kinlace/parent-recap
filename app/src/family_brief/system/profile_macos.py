"""The macOS `profile`: install.sh's layout, Terminal, Homebrew's and nix's folders, the setup
skills, which set up a Mac, and the Codex CLI's install, which the setup page runs when no Codex
or ChatGPT desktop app brought one (#473)."""
from __future__ import annotations

import os
from pathlib import Path

from .profile import Profile

# Homebrew on Apple silicon and on Intel, and nix-darwin's per-user profile.
SYSTEM_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/etc/profiles/per-user/{user}/bin")
# The Codex CLI the way OpenAI installs it on a Mac: its own install.sh, published with each
# release, puts `codex` in ~/.local/bin (one of tools.find's HOME_DIRS) without administrator
# rights. Claude Code's line stays the family's to paste: its sign-in needs the Terminal anyway.
CODEX_INSTALL = "curl -fsSL https://github.com/openai/codex/releases/latest/download/install.sh | sh"


def _program_folders() -> list[str]:
    user = os.environ.get("USER", "")
    return [d.format(user=user) for d in SYSTEM_DIRS if user or "{user}" not in d]


def _install_folders(home: Path) -> list[Path]:
    return [home / "ParentRecap"]  # install.sh's


PROFILE = Profile(
    system="macos",
    computer="Mac",
    terminal="Terminal",
    remove_folder="rm -rf {folder}",
    reinstall="run the plugin's install.sh",
    missed_run="the Mac may be asleep, consider a pmset wake-up",
    closed_lines={
        "fi": "Parent Recapin käyttöönottosivu on suljettu. Voit sulkea tämän Terminal-ikkunan.",
        "en": "Parent Recap's setup page has closed. You can close this Terminal window.",
        "zh": "Parent Recap 的设置页面已经关闭。你可以关掉这个终端窗口了。",
    },
    ais=("claude", "codex", "gateway"),
    page_installs=frozenset({"codex"}),  # the family pastes Claude's line
    install_lines={"claude": "curl -fsSL https://claude.ai/install.sh | bash",  # Claude Code's own
                   "codex": CODEX_INSTALL},
    installer=("/bin/sh", "-c"),
    codex_apps=True,
    chats=frozenset({"claude", "codex"}),
    pseudo_terminal=True,
    login_shell=True,
    program_endings=("",),
    program_folders=_program_folders,
    install_folders=_install_folders,
    command=Path("bin/family-brief"),
    node=Path("bin/node"),
    node_modules=Path("lib/node_modules"),
    npm_links_programs=True,
    removes_running=True,
)
