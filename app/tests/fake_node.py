"""Parent Recap's own Node and the wilma CLI installed with it, laid out in a test's
~/ParentRecap as install.sh and npm lay them out (ADR 0011).

The stand-in `node` runs the script it's given as a program, so a fake wilma CLI with a Python
shebang runs on it; without it, nothing runs the CLI. npm's entry point is there but does
nothing: the tests that install the CLI fake the npm command itself.
"""
from __future__ import annotations

from pathlib import Path

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


def install_wilma(home: Path, script: str) -> Path:
    """The wilma CLI's package in wilma/, as `npm install -g --prefix` puts it there, with
    `script` as its dist/index.js and bin/wilma linking to it. Returns bin/wilma."""
    dist = wilma_package(home) / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "index.js").write_text(script)
    (dist / "index.js").chmod(0o755)
    link = wilma_folder(home) / "bin" / "wilma"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.unlink(missing_ok=True)
    link.symlink_to(Path("..") / PACKAGE / "dist" / "index.js")
    return link


def is_npm(cmd: list[str]) -> bool:
    return len(cmd) > 1 and Path(cmd[1]).name == "npm-cli.js"


def is_wilma(cmd: list[str]) -> bool:
    return len(cmd) > 1 and Path(cmd[0]).name == "node" and Path(cmd[1]).name == "wilma"
