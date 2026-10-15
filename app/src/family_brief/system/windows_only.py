"""Doctor's checks and schedule's words that only Windows has (ADR 0021), offered only where the
program runs on Windows: whether "Allow wake timers" lets the evening job's task wake the PC, and
whether Windows signs the family back in after it restarts for an update ("Use my sign-in info to
automatically finish setting up after an update"), without which that night's run doesn't happen.
And uninstall's last step, which only Windows needs: removing the program and its Python once
uninstall, which runs from them, has ended. They aren't interfaces with an adapter per system;
their code stays Windows code."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from functools import partial
from pathlib import Path

CHECKS = frozenset({"wake-timers", "sign-in-after-update"})
SIGN_IN_SETTING = ("Settings › Accounts › Sign-in options › Use my sign-in info to automatically "
                   "finish setting up after an update")
POLICY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
USER_ARSO = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\UserARSO"


def offers(name: str) -> bool:
    """Whether doctor and schedule offer the check `name` on this system."""
    return name not in CHECKS or sys.platform == "win32"


def _dword(key: str, value: str) -> int | None:
    """A number Windows keeps for the whole PC, None when there is none."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
            found, _ = winreg.QueryValueEx(k, value)
    except OSError:
        return None
    return found if isinstance(found, int) else None


def _sid() -> str | None:
    """This Windows user's security identifier, which names their sign-in settings."""
    try:
        out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True,
                             text=True, errors="replace").stdout
    except OSError:
        return None
    m = re.search(r"S-1-[\d-]+", out)
    return m.group(0) if m else None


def signs_back_in_after_update() -> bool | None:
    """Whether Windows signs this user back in after it restarts for an update; None when it
    hasn't been set either way, and Windows' default is all there is."""
    if _dword(POLICY, "DisableAutomaticRestartSignOn") == 1:  # turned off for the whole PC
        return False
    sid = _sid()
    opt_out = _dword(f"{USER_ARSO}\\{sid}", "OptOut") if sid else None
    return None if opt_out is None else opt_out == 0


# Waits for the process, then removes each path, trying again for a minute while the launchers
# that started it (parent-recap.exe, the venv's python.exe) end too, then the folders left empty.
_REMOVE = (
    "$ErrorActionPreference = 'SilentlyContinue'; "
    "Wait-Process -Id ([int]$env:PARENT_RECAP_PID); "
    "$paths = @($env:PARENT_RECAP_PATHS -split \"`n\" | Where-Object { $_ }); "
    "for ($i = 0; $i -lt 60; $i++) { "
    "$left = @($paths | Where-Object { Test-Path -LiteralPath $_ }); "
    "if ($left.Count -eq 0) { break }; "
    "foreach ($p in $left) { Remove-Item -LiteralPath $p -Recurse -Force }; "
    "Start-Sleep -Seconds 1 }; "
    "foreach ($f in @($env:PARENT_RECAP_FOLDERS -split \"`n\" | Where-Object { $_ })) { "
    "if ((Test-Path -LiteralPath $f -PathType Container) -and "
    "-not (Get-ChildItem -LiteralPath $f -Force)) { Remove-Item -LiteralPath $f -Force } }")


def remove_after_exit(paths: list[Path], folders: list[Path], pid: int | None = None) -> None:
    """Removes `paths`, then each of `folders` that is left empty, deepest first, once the process
    `pid` (this one unless given) has ended, from a hidden PowerShell that outlives it: Windows
    doesn't let a program, or the Python it runs on, be removed while it runs. Raises OSError when
    that PowerShell can't be started."""
    deepest = sorted(folders, key=lambda f: len(f.parts), reverse=True)
    env = {**os.environ, "PARENT_RECAP_PID": str(os.getpid() if pid is None else pid),
           "PARENT_RECAP_PATHS": "\n".join(map(str, paths)),
           "PARENT_RECAP_FOLDERS": "\n".join(map(str, deepest))}
    flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    start = partial(subprocess.Popen, ["powershell.exe", "-NoProfile", "-NonInteractive",
                                       "-Command", _REMOVE],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, cwd=Path.home(), env=env)
    try:
        # Out of a job uninstall may run in, which could end it together with uninstall.
        start(creationflags=flags | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0))
    except OSError:
        # A job that lets nothing break away. The launchers' own jobs let their programs'
        # children out by themselves.
        start(creationflags=flags)
