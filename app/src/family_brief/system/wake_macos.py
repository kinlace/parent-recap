"""The macOS adapter of `wake`: `pmset`'s repeating power events, set and cancelled as root through
macOS's administrator dialog, so the Mac password never reaches the program."""
from __future__ import annotations

import re
import subprocess

from . import wake  # its names are read at call time: wake imports this module first
from .dialog import Cancelled, NoWayToAsk, over_ssh
from .dialog_macos import quote

SLEEPS = True
MANUAL_CANCEL = "sudo pmset repeat cancel"
OWN_SCHEDULE = True  # a pmset repeat schedule, which uninstall cancels


def _pmset(*args: str) -> str:
    try:
        return subprocess.run(["pmset", *args], capture_output=True, text=True, encoding="utf-8").stdout
    except OSError:
        return ""


def _repeating(sched: str) -> list[str]:
    """The lines under "Repeating power events:" in `pmset -g sched`, which `pmset repeat` replaces."""
    lines: list[str] = []
    inside = False
    for line in sched.splitlines():
        if not line.startswith((" ", "\t")):
            inside = line.strip() == "Repeating power events:"
        elif inside and line.strip():
            lines.append(line.strip())
    return lines


def _daily_at(line: str, hour: int, minute: int) -> bool:
    """Whether a line of `pmset -g sched` is a daily wake at hour:minute."""
    wake_12h = f"{hour % 12 or 12}:{minute:02d}{'am' if hour < 12 else 'pm'}"  # as pmset prints it
    daily = re.compile(rf"wake.*(?<!\d)({wake_12h}|{hour}:{minute:02d}).*everyday")
    return bool(daily.search(line.lower().replace(" ", "")))


def _old_time(hour: int, minute: int) -> tuple[int, int]:
    """When versions before #832 woke the Mac for a job at hour:minute: 5 minutes before."""
    wake_h, wake_m = divmod(hour * 60 + minute - 5, 60)
    return wake_h % 24, wake_m


def _never_sleeps() -> bool:
    sleeps = re.findall(r"^\s*sleep\s+(\d+)", _pmset("-g", "custom"), re.M)
    return bool(sleeps) and all(s == "0" for s in sleeps)


def _wakes(hour: int, minute: int) -> wake.WakeStatus:
    """The repeating wakes, told apart; `never_sleeps` isn't read, and is False."""
    lines = _repeating(_pmset("-g", "sched"))
    ours = [line for line in lines if _daily_at(line, hour, minute)]
    old = [line for line in lines
           if line not in ours and _daily_at(line, *_old_time(hour, minute))]
    return wake.WakeStatus(ours[0] if ours else None,
                           [line for line in lines if line not in ours and line not in old],
                           False, old[0] if old else None)


def status(hour: int, minute: int) -> wake.WakeStatus:
    never = _never_sleeps()
    found = _wakes(hour, minute)
    return wake.WakeStatus(found.ours, found.others, never, found.old)


def _pmset_wake(hour: int, minute: int) -> str:
    return f"pmset repeat wakeorpoweron MTWRFSU {hour:02d}:{minute:02d}:00"


def manual_step(hour: int, minute: int) -> str:
    return f"sudo {_pmset_wake(hour, minute)}"


def set(hour: int, minute: int, replace: bool) -> str:
    if _never_sleeps():
        return "never-sleeps"
    found = _wakes(hour, minute)
    if found.ours:
        return "already-set"
    if found.others and not replace:
        return "other-schedule"
    at = f"{hour:02d}:{minute:02d}"
    if found.old:
        old_h, old_m = _old_time(hour, minute)
        why = (f"Parent Recap wants to move your Mac's daily wake from {old_h:02d}:{old_m:02d} "
               f"to {at}, when the Brief starts, so the Mac stays awake for it.")
    else:
        why = f"Parent Recap wants to wake your Mac at {at} every day, when the Brief starts."
    # One `pmset repeat` replaces the whole schedule, so the old wake goes only once the new one
    # is set: a closed dialog or a failed pmset leaves it as it was.
    try:
        _as_administrator(f"/usr/bin/{_pmset_wake(hour, minute)}",
                          f"{why} Enter your Mac password to allow this.")
    except Cancelled:
        return "cancelled"
    except NoWayToAsk:
        return "no-dialog"
    return "set" if _wakes(hour, minute).ours else "not-set"


def cancel() -> None:
    try:
        _as_administrator("/usr/bin/pmset repeat cancel", "Parent Recap wants to remove the wake "
                          "schedule it set. Enter your Mac password to allow this.")
    except (Cancelled, NoWayToAsk):
        raise wake.NotCancelled("the administrator dialog was cancelled or couldn't open. Run this "
                                "in Terminal instead (it asks for your Mac password): "
                                f"{MANUAL_CANCEL}")


def _as_administrator(command: str, prompt: str) -> None:
    """Runs a shell command as root once the family enters their Mac password in macOS's
    administrator dialog, which shows `prompt`. Raises Cancelled when they cancel it, and
    NoWayToAsk without a desktop session or when the dialog or the command fails; there is no
    Terminal fallback here, since `sudo` asks for the password itself."""
    if over_ssh():
        raise NoWayToAsk()
    script = (f"do shell script {quote(command)} with prompt "
              f"{quote(prompt)} with administrator privileges")
    try:
        proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, encoding="utf-8")
    except OSError as e:  # no osascript: not a Mac
        raise NoWayToAsk() from e
    if proc.returncode != 0:
        if "(-128)" in proc.stderr:
            raise Cancelled()
        raise NoWayToAsk()
