"""The entries on Windows: two Start menu shortcuts in a `Parent Recap` folder of the user's own
Start menu, which needs no administrator. Settings runs the page as the evening task runs its job,
through the venv's `pythonw.exe` and `task_run`, so no console window opens, nor stays open,
while the page is served; what it prints goes to `logs\\settings-*.log`. Check runs Windows
PowerShell with `-NoExit`, so its window stays open once doctor ends. Both run the venv's own
Python, not pip's launcher, which Defender can block (#591).

A shortcut is a binary file only Windows' own Shell writes, so Windows PowerShell makes it through
the Shell's COM object, with each value passed in its own variable, which needs no quoting."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .entries import SETTINGS

SINGLE_QUOTES = "'‘’‚‛"  # each ends a PowerShell '...' string
_MAKE = (
    "$ErrorActionPreference = 'Stop'; "
    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:PARENT_RECAP_LINK); "
    "$s.TargetPath = $env:PARENT_RECAP_TARGET; $s.Arguments = $env:PARENT_RECAP_ARGUMENTS; "
    "$s.WorkingDirectory = $env:PARENT_RECAP_FOLDER; $s.Description = $env:PARENT_RECAP_DESCRIPTION; "
    "$s.Save()")


def offered() -> bool:
    return True


def folder() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / \
        "Parent Recap"


def path(name: str) -> Path:
    return folder() / f"{name}.lnk"


def _powershell() -> str:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return str(Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")


def _quoted(word: str) -> str:
    """`word` as a PowerShell string that expands nothing, the curly quotes doubled too."""
    return "'" + "".join(c * 2 if c in SINGLE_QUOTES else c for c in word) + "'"


def _argument(word: str) -> str:
    """`word` as one argument on a Windows command line. No Windows path has a double quote."""
    return f'"{word}"' if " " in word else word


def shortcut(name: str, target: Path) -> dict[str, str]:
    """What the shortcut `name` runs: its program, its arguments, the folder it starts in and the
    description Windows shows for it."""
    scripts = target / "app" / ".venv" / "Scripts"
    if name == SETTINGS:
        # As the evening task runs its job (task_run.py): pythonw.exe opens no window, and starts
        # the page's python.exe with a console that has none, which each program the page runs,
        # such as node or claude, shares instead of opening a console window of its own.
        logs = target / "logs"
        words = ["-m", "family_brief.system.task_run", str(logs / "settings-stdout.log"),
                 str(logs / "settings-stderr.log"), str(scripts / "python.exe"),
                 "-m", "family_brief", "setup", "page"]
        return {"target": str(scripts / "pythonw.exe"), "arguments": " ".join(map(_argument, words)),
                "folder": str(target), "description": "Open Parent Recap's setup page"}
    command = f"& {_quoted(str(scripts / 'python.exe'))} -m family_brief doctor"
    return {"target": _powershell(), "arguments": f'-NoExit -NoLogo -NoProfile -Command "{command}"',
            "folder": str(target), "description": "Check Parent Recap with parent-recap doctor"}


def place(name: str, target: Path) -> None:
    link = path(name)
    link.parent.mkdir(parents=True, exist_ok=True)
    made = shortcut(name, target)
    env = {**os.environ, "PARENT_RECAP_LINK": str(link), "PARENT_RECAP_TARGET": made["target"],
           "PARENT_RECAP_ARGUMENTS": made["arguments"], "PARENT_RECAP_FOLDER": made["folder"],
           "PARENT_RECAP_DESCRIPTION": made["description"]}
    try:
        subprocess.run([_powershell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", _MAKE],
                       env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60, check=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.CalledProcessError as e:
        raise OSError(f"Windows didn't make the shortcut {link}: {e.stderr.strip()}") from e
    except subprocess.TimeoutExpired as e:
        raise OSError(f"Windows didn't make the shortcut {link} within a minute") from e


def placed(name: str) -> bool:
    return path(name).is_file()


def remove(name: str) -> None:
    link = path(name)
    link.unlink(missing_ok=True)
    try:
        link.parent.rmdir()  # only once empty: a file of the family's own there stays
    except OSError:
        pass


def where() -> str:
    return "the Start menu"
