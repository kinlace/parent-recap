"""Parent Recap's own Node and the wilma CLI installed with it, laid out in a test's
~/ParentRecap as install.sh and npm lay them out (ADR 0011).

The stand-in `node` runs the script it's given as a program, so a fake wilma CLI with a Python
shebang runs on it; without it, nothing runs the CLI. npm's entry point is there but does
nothing: the tests that install the CLI fake the npm command itself.
"""
from __future__ import annotations

import json
from pathlib import Path

from family_brief import setup_wilma

NODE_VERSION = "24.21.0"
PACKAGE = Path("lib") / "node_modules" / "@wilm-ai" / "wilma-cli"


def pinned_node(home: Path, version: str = NODE_VERSION) -> Path:
    """The pinned Node in runtime/, with its npm. Returns its `node`."""
    root = home / "ParentRecap" / "runtime" / f"node-v{version}"
    (root / "bin").mkdir(parents=True, exist_ok=True)
    node = root / "bin" / "node"
    node.write_text('#!/bin/sh\nexec "$@"\n')
    node.chmod(0o755)
    npm = root / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"
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
    there, with `script` as its dist/index.js and bin/wilma linking to it by a relative path, and
    its package.json saying `version`, the pinned one unless given. Returns bin/wilma."""
    folder = prefix or wilma_folder(home)
    dist = folder / PACKAGE / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "index.js").write_text(script)
    (dist / "index.js").chmod(0o755)
    (folder / PACKAGE / "package.json").write_text(
        json.dumps({"name": setup_wilma.PACKAGE, "version": version}))
    link = folder / "bin" / "wilma"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.unlink(missing_ok=True)
    link.symlink_to(Path("..") / PACKAGE / "dist" / "index.js")
    return link


def is_npm(cmd: list[str]) -> bool:
    return len(cmd) > 1 and Path(cmd[1]).name == "npm-cli.js"


def is_wilma(cmd: list[str]) -> bool:
    return len(cmd) > 1 and Path(cmd[0]).name == "node" and Path(cmd[1]).name == "wilma"


def npm_prefix(cmd: list[str]) -> Path:
    """The folder an `npm install -g --prefix <folder>` command installs into."""
    return Path(cmd[cmd.index("--prefix") + 1])
