"""The Windows adapter of `wake`: the wake rides on the evening job's task ("wake the computer to
run this task", ADR 0021), so there is no schedule of its own to set or cancel. Windows honours it
only while the power plan's "Allow wake timers" is on, which is the family's own setting: this
adapter reads it with `powercfg` and tells the family where to turn it on, and never changes it."""
from __future__ import annotations

import re
import subprocess

from . import wake  # its names are read at call time: wake imports this module first

ALLOWED = "Allow wake timers is on"  # the wake as status() lists it
ALLOW = ("open Control Panel › Hardware and Sound › Power Options › Change plan settings › Change "
         "advanced power settings, and under Sleep › Allow wake timers choose Enable")
# Nothing to cancel: the wake goes with the evening job's tasks, and Allow wake timers stays as
# the family set it.
MANUAL_CANCEL = "nothing: Parent Recap's wake-up goes away with its scheduled tasks"
OWN_SCHEDULE = False
ENABLE = 1  # Allow wake timers' Enable; 0 is Disable, 2 Important Wake Timers Only


def _setting(name: str) -> list[int]:
    """A setting of the power plan in use under Sleep, plugged in first, then on battery; empty
    when it can't be read. `powercfg /query` words its output in the PC's language, but its two
    current values are always its last two hexadecimal numbers."""
    try:
        out = subprocess.run(["powercfg", "/query", "SCHEME_CURRENT", "SUB_SLEEP", name],
                             capture_output=True, text=True, errors="replace").stdout
    except OSError:
        return []
    values = [int(v, 16) for v in re.findall(r"\b0x([0-9a-fA-F]+)\b", out)]
    return values[-2:] if len(values) >= 2 else []


def _never_sleeps() -> bool:
    sleep, hibernate = _setting("STANDBYIDLE"), _setting("HIBERNATEIDLE")
    return bool(sleep) and bool(hibernate) and all(v == 0 for v in sleep + hibernate)


def _allowed() -> bool:
    # Plugged in: until a laptop has been tested, Windows families leave the PC plugged in.
    values = _setting("RTCWAKE")
    return bool(values) and values[0] == ENABLE


def status(hour: int, minute: int) -> wake.WakeStatus:
    return wake.WakeStatus(ALLOWED if _allowed() else None, [], _never_sleeps())


def set(hour: int, minute: int, replace: bool) -> str:
    if _never_sleeps():
        return "never-sleeps"
    return "already-set" if _allowed() else "not-set"


def cancel() -> None:
    pass


def manual_step(hour: int, minute: int) -> str:
    return ALLOW
