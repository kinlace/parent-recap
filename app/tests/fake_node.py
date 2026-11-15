"""Parent Recap's own Node and the wilma CLI and Baileys tree installed with it, laid out in a
test's ~/ParentRecap as the install and npm lay them out on the system the tests run on (ADR 0011),
and the fake Baileys package the Linked Device's program runs on in its tests.

On macOS, the stand-in `node` runs the script it's given as a program, so a fake wilma CLI with
a Python shebang runs on it; without it, nothing runs the CLI. On Windows, `node.exe` is an empty
file, and the tests that run the CLI there fake the command. npm's entry point is there but does
nothing: the tests that install the CLI fake the npm command itself.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from family_brief import baileys_install, setup_wilma

NODE_VERSION = "24.21.0"
WINDOWS = sys.platform == "win32"
# Where npm puts the packages of a global install, and the Node zip its own npm: on Windows in
# node_modules beside node.exe and the commands, elsewhere in lib/ beside bin/.
MODULES = Path("node_modules") if WINDOWS else Path("lib") / "node_modules"
PACKAGE = MODULES / "@wilm-ai" / "wilma-cli"
LOCKED_PACKAGE = Path("node_modules") / "@wilm-ai" / "wilma-cli"  # where `npm ci` puts it
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
    """The CLI's package in wilma/: where `npm ci` put it, or else where a global install did."""
    locked = wilma_folder(home) / LOCKED_PACKAGE
    return locked if locked.is_dir() else wilma_folder(home) / PACKAGE


def install_wilma(home: Path, script: str, version: str = setup_wilma.WILMA_CLI_VERSION,
                  prefix: Path | None = None, locked: bool = False) -> Path:
    """The wilma CLI's package in wilma/, or in `prefix`, with `script` as its dist/index.js and
    its package.json saying `version`, the pinned one unless given.

    As `npm install -g --prefix` put it there before #975, unless `locked`: elsewhere than
    Windows bin/wilma links to the script by a relative path, and is returned; on Windows, where
    npm's wilma.cmd wrapper isn't what Parent Recap runs, the script is returned.

    `locked`: as `npm ci` leaves it from the committed npm files, the same on every system: the
    package in node_modules beside the npm files, and its script returned."""
    folder = prefix or wilma_folder(home)
    package = folder / (LOCKED_PACKAGE if locked else PACKAGE)
    (package / SCRIPT.parent).mkdir(parents=True, exist_ok=True)
    (package / SCRIPT).write_text(script)
    (package / SCRIPT).chmod(0o755)
    (package / "package.json").write_text(json.dumps(
        {"name": setup_wilma.PACKAGE, "version": version, "bin": {"wilma": SCRIPT.as_posix()}}))
    if locked:
        for name in ("package.json", "package-lock.json"):
            (folder / name).write_bytes((setup_wilma.PINNED / name).read_bytes())
        return package / SCRIPT
    if WINDOWS:
        return folder / PACKAGE / SCRIPT
    link = folder / "bin" / "wilma"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.unlink(missing_ok=True)
    link.symlink_to(Path("..") / PACKAGE / SCRIPT)
    return link


def baileys_folder(home: Path) -> Path:
    return home / "ParentRecap" / "baileys"


def install_baileys(folder: Path, version: str = baileys_install.BAILEYS_VERSION,
                    lockfile: bytes | None = None) -> None:
    """The Baileys tree in `folder` as `npm ci` leaves it, from the committed npm files, the
    lockfile `lockfile` instead when given, with Baileys' package.json saying `version`."""
    package = folder / "node_modules" / baileys_install.BAILEYS_PACKAGE
    package.mkdir(parents=True, exist_ok=True)
    (package / "package.json").write_text(json.dumps(
        {"name": baileys_install.BAILEYS_PACKAGE, "version": version}))
    (folder / "package.json").write_bytes((baileys_install.PINNED / "package.json").read_bytes())
    (folder / "package-lock.json").write_bytes(
        lockfile or (baileys_install.PINNED / "package-lock.json").read_bytes())


def install_fake_baileys(folder: Path) -> None:
    """The fake Baileys package in `folder`'s node_modules, where the Linked Device's program
    imports it from (`fake_baileys/index.mjs` says what it plays)."""
    package = folder / "node_modules" / baileys_install.BAILEYS_PACKAGE
    shutil.copytree(Path(__file__).parent / "fake_baileys", package)


def is_npm(cmd: list[str]) -> bool:
    return len(cmd) > 1 and Path(cmd[1]).name == "npm-cli.js"


def is_wilma(cmd: list[str]) -> bool:
    if len(cmd) < 2 or Path(cmd[0]).stem != "node":
        return False
    cli = Path(cmd[1])
    return cli.name == "wilma" or cli.as_posix().endswith((LOCKED_PACKAGE / SCRIPT).as_posix())


def npm_prefix(cmd: list[str]) -> Path:
    """The folder an `npm ci --prefix <folder>` command installs into."""
    return Path(cmd[cmd.index("--prefix") + 1])
