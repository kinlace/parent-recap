"""The Linux adapter of `wake`: a server never sleeps, so there is no wake to set or cancel."""
from __future__ import annotations

from . import wake  # its names are read at call time: wake imports this module first

OWN_SCHEDULE = False
MANUAL_CANCEL = ""


def status(hour: int, minute: int) -> wake.WakeStatus:
    return wake.WakeStatus(None, [], never_sleeps=True)


def set(hour: int, minute: int, replace: bool) -> str:
    return "never-sleeps"


def cancel() -> None:
    pass


def manual_step(hour: int, minute: int) -> str | None:
    return None
