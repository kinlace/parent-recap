"""Doctor's checks and schedule's words that only Windows has (ADR 0021), offered only where the
program runs on Windows: whether "Allow wake timers" lets the evening job's task wake the PC, and
whether Windows signs the family back in after it restarts for an update ("Use my sign-in info to
automatically finish setting up after an update"), without which that night's run doesn't happen.
They aren't interfaces with an adapter per system; their code stays Windows code."""
from __future__ import annotations

import re
import subprocess
import sys

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
