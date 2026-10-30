"""The Linux `profile`: a server the maintainers run Households on (ADR 0029), with install.sh's
layout under the Household user's home. The gateway is its only AI, so nothing is offered to
choose, installed by the page or continued in a chat, and the setup page asks for neither the AI
nor Gmail."""
from __future__ import annotations

import platform
from pathlib import Path

from . import profile_macos
from .profile import Profile


def _install_folders(home: Path) -> list[Path]:
    return [home / "ParentRecap"]  # install.sh's


def _os_version() -> str:
    try:
        name = platform.freedesktop_os_release().get("PRETTY_NAME", "")
    except OSError:
        name = ""
    return f"{name or 'Linux ' + platform.release()} ({platform.machine()})"


PROFILE = Profile(
    system="linux",
    computer="server",
    terminal="terminal",
    remove_folder=profile_macos.PROFILE.remove_folder,
    reinstall="run the plugin's install.sh",
    missed_run="the server may have been down or restarting",
    closed_lines=dict(
        fi="Parent Recapin käyttöönottosivu on suljettu. Voit sulkea tämän terminal-ikkunan.",
        en="Parent Recap's setup page has closed. You can close this terminal.",
        zh="Parent Recap 的设置页面已经关闭。你可以关掉这个 terminal 了。",
    ),
    ais=("gateway",),
    page_installs=frozenset(),
    # Never offered here; the lines work on Linux all the same.
    install_lines=profile_macos.PROFILE.install_lines,
    installer=("/bin/sh", "-c"),
    codex_apps=False,
    chats=frozenset(),
    # A Hosted Household's sign-up mints its Gateway Key, and it reads no Gmail yet (ADR 0031).
    page_skips=frozenset({"ai", "gmail"}),
    pseudo_terminal=True,
    login_shell=True,
    program_endings=("",),
    program_folders=lambda: [],  # the PATH has /usr/local/bin and /usr/bin already
    install_folders=_install_folders,
    command=Path("bin/family-brief"),
    path_command=Path("bin/parent-recap"),
    links_command=True,
    node=Path("bin/node"),
    node_modules=Path("lib/node_modules"),
    npm_links_programs=True,
    removes_running=True,
    os_version=_os_version,
    maxrss_bytes=1024,  # kilobytes
)
