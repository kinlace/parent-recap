"""Parent Recap's own Node, and the wilma CLI installed with it (ADR 0011).

`install.sh` (`install.ps1` on Windows) unpacks a pinned Node into a versioned folder in
`runtime/`, next to the pinned Python. The setup page installs the pinned wilma CLI (ADR 0008)
with that Node's npm into `wilma/`, not into the computer's global npm prefix, and the install
the Linked Device's Baileys tree into `baileys/` (`baileys_install`), both staged the same way
(`install_staged`). The CLI always runs
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
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from . import failure, install_record
from .system import profile

WILMA_PACKAGE = "@wilm-ai/wilma-cli"


def home() -> Path:
    """Parent Recap's own folder: the one the install put the program in. Only installs from
    before setup's record, all on macOS, have none."""
    programs = install_record.entries("program")
    return Path(programs[-1]).parent if programs else Path.home() / "ParentRecap"


def _program(folder: Path) -> Path:
    """The `node` in a folder Node was unpacked into."""
    return folder / profile.current().node


def _folder() -> Path | None:
    """The folder the pinned Node was unpacked into, or None when it isn't there. A newly pinned
    Node is unpacked before the old one is removed, so the newest one counts."""
    found = [folder for folder in (home() / "runtime").glob("node-v*")
             if (program := _program(folder)).is_file() and os.access(program, os.X_OK)]
    return max(found, key=lambda f: [int(n) for n in re.findall(r"\d+", f.name)], default=None)


def node() -> Path | None:
    """The pinned Node, or None when it isn't there."""
    folder = _folder()
    return _program(folder) if folder else None


def modules(prefix: Path) -> Path:
    """The folder npm puts packages in, in a Node folder or for `npm install -g --prefix`."""
    return prefix / profile.current().node_modules


def npm() -> list[str] | None:
    """The command that runs the npm that comes with the pinned Node, on that Node."""
    folder = _folder()
    if folder is None:
        return None
    cli = modules(folder) / "npm" / "bin" / "npm-cli.js"
    return [str(_program(folder)), str(cli)] if cli.is_file() else None


def run_npm(npm: list[str], args: list[str], seconds: int,
            cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Runs `npm` with `args`, its output captured, ended after `seconds`."""
    # npm's cache in a temporary folder, so nothing is left outside Parent Recap's.
    with tempfile.TemporaryDirectory(prefix="parent-recap-npm-") as cache:
        # npm runs package scripts with the `node` its PATH finds: this one, not the computer's.
        env = {**os.environ, "npm_config_cache": cache, "npm_config_update_notifier": "false",
               "PATH": os.pathsep.join([str(Path(npm[0]).parent), os.environ.get("PATH", "")])}
        return subprocess.run([*npm, *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=seconds, stdin=subprocess.DEVNULL,
                              env=env, cwd=cwd)


def install_staged(folder: Path, install: Callable[[Path], str | None], what: str,
                   seconds: int) -> str | None:
    """Has `install` put what npm installs into a new folder next to `folder`, which takes
    `folder`'s place only once `install` has finished without a reason it failed, so an npm that
    fails, or is ended at the time limit `seconds`, changes nothing. Returns why it failed, which
    is also logged after `what`, or None once it's in place."""
    folder.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    for left in folder.parent.glob(f".{folder.name}-*"):  # by an install that was itself ended
        shutil.rmtree(left, ignore_errors=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{folder.name}-", dir=folder.parent))
    try:
        reason = install(staging)
        if reason is None:
            _replace(folder, staging)
    except subprocess.TimeoutExpired:
        reason = failure.timed_out("npm", seconds)
    except (OSError, subprocess.SubprocessError) as e:  # npm's, or the move after it
        reason = failure.raised(e, "The install")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return failure.logged(what, reason) if reason else None


def _replace(folder: Path, new: Path) -> None:
    """Puts what npm installed in `new` in `folder`'s place, and removes what was there. npm
    links bin/ to the packages by relative paths, so they run from their new place."""
    old = new.with_name(new.name + "-old")
    if folder.exists():
        folder.rename(old)
    try:
        new.rename(folder)
    except OSError:
        if old.exists():
            old.rename(folder)
        raise
    shutil.rmtree(old, ignore_errors=True)


def wilma_folder() -> Path:
    """Where the setup page installs the wilma CLI, as npm's prefix."""
    return home() / "wilma"


def wilma_cli(prefix: Path | None = None) -> Path | None:
    """The wilma CLI's script npm installed in `prefix` (Parent Recap's own unless given), which
    Node runs, or None when it isn't there."""
    prefix = prefix or wilma_folder()
    if profile.current().npm_links_programs:
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
