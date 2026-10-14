"""Parent Recap's own Node and the wilma CLI installed with it, laid out in a test's
~/ParentRecap as the install and npm lay them out on the system the tests run on (ADR 0011).

On macOS, the stand-in `node` runs the script it's given as a program, so a fake wilma CLI with
a Python shebang runs on it; without it, nothing runs the CLI. On Windows, `node.exe` is an empty
file, and the tests that run the CLI there fake the command. npm's entry point is there but does
nothing: the tests that install the CLI fake the npm command itself.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from family_brief import setup_wilma

NODE_VERSION = "24.21.0"
WINDOWS = sys.platform == "win32"
# Where npm puts the packages of a global install, and the Node zip its own npm: on Windows in
# node_modules beside node.exe and the commands, elsewhere in lib/ beside bin/.
MODULES = Path("node_modules") if WINDOWS else Path("lib") / "node_modules"
PACKAGE = MODULES / "@wilm-ai" / "wilma-cli"
SCRIPT = Path("dist") / "index.js"  # the package's `bin`


def pinned_node(home: Path, version: str = NODE_VERSION) -> Path:
    """The pinned Node in runtime/, with its npm. Returns its `node`."""
    root = home / "ParentRecap" / "runtime" / f"node-v{version}"
    if WINDOWS:
        root.mkdir(parents=True, exist_ok=True)
        node = root / "node.exe"
        node.write_bytes(b"")
    else:
        (root / "bin").mkdir(parents=True, exist_ok=True)
        node = root / "bin" / "node"
        node.write_text('#!/bin/sh\nexec "$@"\n')
    node.chmod(0o755)
    npm = root / MODULES / "npm" / "bin" / "npm-cli.js"
    npm.parent.mkdir(parents=True, exist_ok=True)
    npm.write_text("#!/usr/bin/env node\n")
    return node


def wilma_folder(home: Path) -> Path:
    return home / "ParentRecap" / "wilma"


def wilma_package(home: Path) -> Path:
    return wilma_folder(home) / PACKAGE


def install_wilma(home: Path, script: str, version: str = setup_wilma.WILMA_CLI_VERSION,
                  prefix: Path | None = None) -> Path:
    """The wilma CLI's package in wilma/, or in `prefix`, as `npm install -g --prefix` puts it
    there, with `script` as its dist/index.js and its package.json saying `version`, the pinned
    one unless given. Elsewhere than Windows bin/wilma links to the script by a relative path,
    and is returned; on Windows, where npm's wilma.cmd wrapper isn't what Parent Recap runs, the
    script is returned."""
    folder = prefix or wilma_folder(home)
    dist = folder / PACKAGE / SCRIPT.parent
    dist.mkdir(parents=True, exist_ok=True)
    (folder / PACKAGE / SCRIPT).write_text(script)
    (folder / PACKAGE / SCRIPT).chmod(0o755)
    (folder / PACKAGE / "package.json").write_text(json.dumps(
        {"name": setup_wilma.PACKAGE, "version": version, "bin": {"wilma": SCRIPT.as_posix()}}))
    if WINDOWS:
        return folder / PACKAGE / SCRIPT
    link = folder / "bin" / "wilma"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.unlink(missing_ok=True)
    link.symlink_to(Path("..") / PACKAGE / SCRIPT)
    return link


def is_npm(cmd: list[str]) -> bool:
    return len(cmd) > 1 and Path(cmd[1]).name == "npm-cli.js"


def is_wilma(cmd: list[str]) -> bool:
    if len(cmd) < 2 or Path(cmd[0]).stem != "node":
        return False
    cli = Path(cmd[1])
    return cli.name == "wilma" or cli.as_posix().endswith((PACKAGE / SCRIPT).as_posix())


def npm_prefix(cmd: list[str]) -> Path:
    """The folder an `npm install -g --prefix <folder>` command installs into."""
    return Path(cmd[cmd.index("--prefix") + 1])
