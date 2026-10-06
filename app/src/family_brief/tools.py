"""Finding the programs of the Mac's own that Parent Recap runs (claude, codex, and node for a
claude installed with npm) when they're installed but this process's PATH doesn't lead to them.
The wilma CLI isn't one: it runs on Parent Recap's own Node (`own_node`, ADR 0011).

Terminal's shell, the setup page and the evening job each have their own PATH, and a family's own
shell setup (fish, nix, a custom npm prefix such as ~/.npm-global/bin) can put a program on one of
them only. So after PATH, a program is looked for in the folders installers commonly use, then
the login shell is asked for it. Where it was found is remembered, so the evening job, which runs
without the shell's PATH, runs the same one."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

# Under the home folder: Claude Code's native installer, a custom npm prefix, nix.
HOME_DIRS = (".local/bin", ".npm-global/bin", ".nix-profile/bin")
# Homebrew on Apple silicon and on Intel, and nix-darwin's per-user profile.
SYSTEM_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/etc/profiles/per-user/{user}/bin")
# A login shell reads the family's whole shell setup, which can be slow, or wait for something.
SHELL_SECONDS = 5


def remembered_path() -> Path:
    return Path.home() / ".family" / "programs.json"


def find(name: str) -> str | None:
    """`name`'s full path, or None when it isn't installed. One found outside PATH is
    remembered, and its folder goes on this process's PATH, with node's for a Node script such
    as a claude installed with npm, so that the programs it starts can run them too."""
    found = shutil.which(name)
    if found:
        return found
    remembered = _remembered().get(name)
    if not (remembered and _runnable(remembered)):
        remembered = None
    found = remembered or _in_common_folders(name) or _from_login_shell(name)
    if found is None:
        return None
    if found != remembered:
        _remember(name, found)
    _put_on_path(Path(found).parent)
    if name != "node" and _is_node_script(found):
        find("node")
    return found


def _runnable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def _in_common_folders(name: str) -> str | None:
    user = os.environ.get("USER", "")
    folders = [str(Path.home() / d) for d in HOME_DIRS]
    folders += [d.format(user=user) for d in SYSTEM_DIRS if user or "{user}" not in d]
    return next((p for d in folders if _runnable(p := os.path.join(d, name))), None)


def _from_login_shell(name: str) -> str | None:
    """Where the family's login shell finds `name`: `command -v` works the same in zsh, bash
    and fish. A login shell can print its own greeting first, so only a path counts."""
    shell = os.environ.get("SHELL")
    if not shell or not os.path.isabs(shell):
        return None
    try:
        proc = subprocess.run([shell, "-l", "-c", f"command -v {shlex.quote(name)}"],
                              capture_output=True, text=True, timeout=SHELL_SECONDS,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = [line.strip() for line in proc.stdout.splitlines()]
    return next((p for p in reversed(lines) if p.startswith("/") and _runnable(p)), None)


def _put_on_path(folder: Path) -> None:
    path = os.environ.get("PATH", "")
    if str(folder) not in path.split(os.pathsep):
        os.environ["PATH"] = f"{path}{os.pathsep}{folder}" if path else str(folder)


def _is_node_script(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            first = f.readline(256)
    except OSError:
        return False
    return first.startswith(b"#!") and b"node" in first


def _remembered() -> dict[str, str]:
    try:
        data = json.loads(remembered_path().read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}


def _remember(name: str, path: str) -> None:
    """Best effort: without it, the evening job still finds what PATH and the folders lead to."""
    data = {**_remembered(), name: path}
    file = remembered_path()
    try:
        file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        tmp.chmod(0o600)
        tmp.replace(file)
    except OSError:
        pass
