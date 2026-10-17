"""Parent Recap's own Node, and the wilma CLI installed with it (ADR 0011).

`install.sh` (`install.ps1` on Windows) unpacks a pinned Node into a versioned folder in
`runtime/`, next to the pinned Python. The setup page installs the pinned wilma CLI (ADR 0008)
with that Node's npm into `wilma/`, not into the computer's global npm prefix. The CLI always runs
on that Node, so a Node the computer has, or having none, changes nothing, and nothing outside
Parent Recap's folder can update either of them.

Node and npm lay their files out differently on Windows: `node.exe` at the top of Node's folder
with npm beside it in `node_modules`, and a global install's packages in `<prefix>/node_modules`
with only `.cmd` wrappers beside them, so the CLI runs from its package's own script there.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from . import install_record

WILMA_PACKAGE = "@wilm-ai/wilma-cli"


def _windows() -> bool:
    return sys.platform == "win32"


def home() -> Path:
    """Parent Recap's own folder: the one the install put the program in. Only installs from
    before setup's record, all on macOS, have none."""
    programs = install_record.entries("program")
    return Path(programs[-1]).parent if programs else Path.home() / "ParentRecap"


def _program(folder: Path) -> Path:
    """The `node` in a folder Node was unpacked into."""
    return folder / "node.exe" if _windows() else folder / "bin" / "node"


def node() -> Path | None:
    """The pinned Node, or None when it isn't there. A newly pinned Node is unpacked before the
    old one is removed, so the newest one counts."""
    found = [(folder, program) for folder in (home() / "runtime").glob("node-v*")
             if (program := _program(folder)).is_file() and os.access(program, os.X_OK)]
    newest = max(found, key=lambda f: [int(n) for n in re.findall(r"\d+", f[0].name)],
                 default=None)
    return newest[1] if newest else None


def modules(prefix: Path) -> Path:
    """The folder npm puts packages in, in a Node folder or for `npm install -g --prefix`."""
    return prefix / "node_modules" if _windows() else prefix / "lib" / "node_modules"


def npm() -> list[str] | None:
    """The command that runs the npm that comes with the pinned Node, on that Node."""
    program = node()
    if program is None:
        return None
    folder = program.parent if _windows() else program.parents[1]
    cli = modules(folder) / "npm" / "bin" / "npm-cli.js"
    return [str(program), str(cli)] if cli.is_file() else None


def wilma_folder() -> Path:
    """Where the setup page installs the wilma CLI, as npm's prefix."""
    return home() / "wilma"


def wilma_cli(prefix: Path | None = None) -> Path | None:
    """The wilma CLI's script npm installed in `prefix` (Parent Recap's own unless given), which
    Node runs, or None when it isn't there."""
    prefix = prefix or wilma_folder()
    if not _windows():
        cli = prefix / "bin" / "wilma"
        return cli if cli.is_file() else None
    package = modules(prefix) / WILMA_PACKAGE
    try:
        bin_ = json.loads((package / "package.json").read_text(encoding="utf-8")).get("bin")
    except (OSError, ValueError, AttributeError):
        return None
    script = bin_.get("wilma") if isinstance(bin_, dict) else bin_
    if not isinstance(script, str):
        return None
    cli = package / script
    return cli if cli.is_file() else None


def wilma() -> list[str] | None:
    """The command that runs the installed wilma CLI on the pinned Node, or None without
    either."""
    program, cli = node(), wilma_cli()
    return [str(program), str(cli)] if program and cli else None


def wilma_env(config: Path | None = None) -> dict[str, str]:
    """The environment the wilma CLI runs in: without its daily check for a newer version on npm,
    since Parent Recap installs the version it pins (ADR 0008) and the CLI's advice to run
    `wilma update` would install another one, and with `config` as its config file when given."""
    env = {**os.environ, "WILMAI_NO_UPDATE_CHECK": "1"}
    if config is not None:
        env["WILMAI_CONFIG_PATH"] = str(config)
    return env
