"""Parent Recap's own Node, and the wilma CLI installed with it (ADR 0011).

`install.sh` unpacks a pinned Node into a versioned folder in `runtime/`, next to the pinned
Python. The setup page installs the pinned wilma CLI (ADR 0008) with that Node's npm into
`wilma/`, not into the Mac's global npm prefix. The CLI always runs on that Node, so a Node the
Mac has, or having none, changes nothing, and nothing outside Parent Recap's folder can update
either of them.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from . import install_record


def home() -> Path:
    """Parent Recap's own folder: the one install.sh put the program in."""
    programs = install_record.entries("program")
    return Path(programs[-1]).parent if programs else Path.home() / "ParentRecap"


def node() -> Path | None:
    """The pinned Node, or None when it isn't there. A newly pinned Node is unpacked before the
    old one is removed, so the newest one counts."""
    found = [p for p in (home() / "runtime").glob("node-v*/bin/node")
             if p.is_file() and os.access(p, os.X_OK)]
    return max(found, key=lambda p: [int(n) for n in re.findall(r"\d+", p.parents[1].name)],
               default=None)


def npm() -> list[str] | None:
    """The command that runs the npm that comes with the pinned Node, on that Node."""
    program = node()
    if program is None:
        return None
    cli = program.parents[1] / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"
    return [str(program), str(cli)] if cli.is_file() else None


def wilma_folder() -> Path:
    """Where the setup page installs the wilma CLI, as npm's prefix."""
    return home() / "wilma"


def wilma() -> list[str] | None:
    """The command that runs the installed wilma CLI on the pinned Node, or None without
    either."""
    program, cli = node(), wilma_folder() / "bin" / "wilma"
    return [str(program), str(cli)] if program and cli.is_file() else None


def wilma_env(config: Path | None = None) -> dict[str, str]:
    """The environment the wilma CLI runs in: without its daily check for a newer version on npm,
    since Parent Recap installs the version it pins (ADR 0008) and the CLI's advice to run
    `wilma update` would install another one, and with `config` as its config file when given."""
    env = {**os.environ, "WILMAI_NO_UPDATE_CHECK": "1"}
    if config is not None:
        env["WILMAI_CONFIG_PATH"] = str(config)
    return env
