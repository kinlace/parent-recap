"""The macOS adapter of `wake`: `pmset`'s repeating power events, set and cancelled as root through
macOS's administrator dialog, so the Mac password never reaches the program."""
from __future__ import annotations

import re
import subprocess

from .. import secret_dialog
from . import wake  # its names are read at call time: wake imports this module first

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


def _is_ours(line: str, hour: int, minute: int) -> bool:
    """Whether a line of `pmset -g sched` is the daily wake setup gives for a job at hour:minute."""
    wake_h, wake_m = wake.time(hour, minute)
    wake_12h = f"{wake_h % 12 or 12}:{wake_m:02d}{'am' if wake_h < 12 else 'pm'}"  # as pmset prints it
    our_wake = re.compile(rf"wake.*(?<!\d)({wake_12h}|{wake_h}:{wake_m:02d}).*everyday")
    return bool(our_wake.search(line.lower().replace(" ", "")))


def _never_sleeps() -> bool:
    sleeps = re.findall(r"^\s*sleep\s+(\d+)", _pmset("-g", "custom"), re.M)
    return bool(sleeps) and all(s == "0" for s in sleeps)


def _wakes(hour: int, minute: int) -> tuple[str | None, list[str]]:
    lines = _repeating(_pmset("-g", "sched"))
    ours = [line for line in lines if _is_ours(line, hour, minute)]
    return (ours[0] if ours else None), [line for line in lines if line not in ours]


def status(hour: int, minute: int) -> wake.WakeStatus:
    never = _never_sleeps()
    ours, others = _wakes(hour, minute)
    return wake.WakeStatus(ours, others, never)


def _pmset_wake(hour: int, minute: int) -> str:
    wake_h, wake_m = wake.time(hour, minute)
    return f"pmset repeat wakeorpoweron MTWRFSU {wake_h:02d}:{wake_m:02d}:00"


def manual_step(hour: int, minute: int) -> str:
    return f"sudo {_pmset_wake(hour, minute)}"


def set(hour: int, minute: int, replace: bool) -> str:
    if _never_sleeps():
        return "never-sleeps"
    ours, others = _wakes(hour, minute)
    if ours:
        return "already-set"
    if others and not replace:
        return "other-schedule"
    wake_h, wake_m = wake.time(hour, minute)
    try:
        _as_administrator(
            f"/usr/bin/{_pmset_wake(hour, minute)}",
            f"Parent Recap wants to wake your Mac at {wake_h:02d}:{wake_m:02d} every day, 5 "
            "minutes before the Brief. Enter your Mac password to allow this.")
    except secret_dialog.Cancelled:
        return "cancelled"
    except secret_dialog.NoWayToAsk:
        return "no-dialog"
    return "set" if _wakes(hour, minute)[0] else "not-set"


def cancel() -> None:
    try:
        _as_administrator("/usr/bin/pmset repeat cancel", "Parent Recap wants to remove the wake "
                          "schedule it set. Enter your Mac password to allow this.")
    except (secret_dialog.Cancelled, secret_dialog.NoWayToAsk):
        raise wake.NotCancelled("the administrator dialog was cancelled or couldn't open. Run this "
                                "in Terminal instead (it asks for your Mac password): "
                                f"{MANUAL_CANCEL}")


def _as_administrator(command: str, prompt: str) -> None:
    """Runs a shell command as root once the family enters their Mac password in macOS's
    administrator dialog, which shows `prompt`. Raises Cancelled when they cancel it, and
    NoWayToAsk without a desktop session or when the dialog or the command fails; there is no
    Terminal fallback here, since `sudo` asks for the password itself."""
    if secret_dialog.over_ssh():
        raise secret_dialog.NoWayToAsk()
    script = (f"do shell script {secret_dialog.quote(command)} with prompt "
              f"{secret_dialog.quote(prompt)} with administrator privileges")
    try:
        proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, encoding="utf-8")
    except OSError as e:  # no osascript: not a Mac
        raise secret_dialog.NoWayToAsk() from e
    if proc.returncode != 0:
        if "(-128)" in proc.stderr:
            raise secret_dialog.Cancelled()
        raise secret_dialog.NoWayToAsk()
