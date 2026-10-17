"""The macOS adapter of `desktop`: `open`, Terminal and `defaults`. The macOS-only features'
own windows open from here too: Finder beside Full Disk Access, and the Passwords app."""
from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

# The Passwords app, or before macOS 15, where the passwords were in System Settings: each tried
# in turn.
PASSWORDS = (["-a", "Passwords"], ["x-apple.systempreferences:com.apple.Passwords-Settings.extension"])
_windows: dict[str, Path] = {}  # each open window's script folder, by its title


def open_url(url: str) -> bool:
    return _open([url])


def open_file(path: Path) -> bool:
    return _open(["-e", str(path)])  # in TextEdit


def open_terminal(argv: list[str], env: dict[str, str], title: str) -> bool:
    # Terminal opens a .command file in a new window, titled with its name, and runs it there.
    forget_terminal(title)
    folder = Path(tempfile.mkdtemp(prefix="parent-recap-window-"))
    script = folder / f"{title.replace('/', '-')}.command"
    script.write_text(_window_script(argv, env), encoding="utf-8")
    script.chmod(0o700)
    if _open(["-a", "Terminal", str(script)]):
        _windows[title] = folder
        return True
    shutil.rmtree(folder, ignore_errors=True)
    return False


def forget_terminal(title: str) -> None:
    folder = _windows.pop(title, None)
    if folder is not None:
        shutil.rmtree(folder, ignore_errors=True)


def _window_script(argv: list[str], env: dict[str, str]) -> str:
    # The script removes itself and its folder first: the shell running it keeps it open, and
    # nothing is left behind however the window ends. Full paths, whatever the window's PATH.
    return (
        "#!/bin/sh\n"
        "/bin/rm -f -- \"$0\"; /bin/rmdir -- \"${0%/*}\" 2>/dev/null\n"
        "cd ~ || exit 1\n"
        "/usr/bin/clear\n"
        + "".join(f"export {name}={shlex.quote(value)}\n" for name, value in env.items())
        + f"exec {shlex.join(argv)}\n"
    )


def preferred_languages() -> list[str]:
    try:
        listed = subprocess.run(["defaults", "read", "-g", "AppleLanguages"], capture_output=True,
                                text=True, encoding="utf-8", timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return re.findall(r"[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]+)*", listed or "")


def show_beside(path: str, pane: str) -> list[list[str]]:
    """Selects `path` in Finder and opens System Settings at `pane` next to it, for the family to
    drag it in. Returns the commands that didn't work, for the family to run in Terminal."""
    commands = [["open", "-R", path], ["open", pane]]
    return [c for c in commands if not _open(c[1:])]


def open_passwords() -> bool:
    """Opens where the Mac keeps its saved passwords."""
    return any(_open(args) for args in PASSWORDS)


def _open(args: list[str]) -> bool:
    try:
        return subprocess.run(["open", *args], capture_output=True).returncode == 0
    except OSError:
        return False
