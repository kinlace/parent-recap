"""Waking the computer every day, 5 minutes before the evening job (ADR 0020). Each system has its
adapter; only macOS's, `pmset`, exists so far."""
from __future__ import annotations

from dataclasses import dataclass

from . import wake_macos as _adapter

# What setting the daily wake can say: no wake needed (`never-sleeps`, `already-set`), another
# repeating wake schedule kept until the family agrees to replace it (`other-schedule`), set, or
# not: the dialog was closed (`cancelled`), there was no dialog to ask in (`no-dialog`), or the
# system doesn't list it after the dialog (`not-set`).
RESULTS = ("set", "already-set", "never-sleeps", "other-schedule", "cancelled", "no-dialog",
           "not-set")


@dataclass(frozen=True)
class WakeStatus:
    ours: str | None       # the daily wake setup gives for the job's time, as the system lists it
    others: list[str]      # the system's other repeating wakes, as it lists them
    never_sleeps: bool     # set never to sleep, on every power source


class NotCancelled(Exception):
    """The wake schedule wasn't cancelled; the message says how the family can cancel it."""


def time(hour: int, minute: int) -> tuple[int, int]:
    """When the computer should wake for a job at hour:minute: 5 minutes before."""
    wake_h, wake_m = divmod(hour * 60 + minute - 5, 60)
    return wake_h % 24, wake_m


def status(hour: int, minute: int) -> WakeStatus:
    """The computer's repeating wakes, told apart into the one setup gives for a job at
    hour:minute and the others."""
    return _adapter.status(hour, minute)


def set(hour: int, minute: int, replace: bool) -> str:
    """Sets the daily wake for a job at hour:minute through the system's administrator dialog,
    where the family types their password. Another repeating wake schedule is replaced only with
    `replace`, so the family can decline before any dialog opens. Returns one of RESULTS."""
    return _adapter.set(hour, minute, replace)


def cancel() -> None:
    """Cancels the computer's repeating wakes through the administrator dialog. Raises
    NotCancelled."""
    _adapter.cancel()


def manual_step(hour: int, minute: int) -> str | None:
    """The command the family runs in a terminal to set the daily wake for a job at hour:minute
    themselves, when no dialog can ask; None where there is none."""
    return _adapter.manual_step(hour, minute)


def manual_cancel() -> str:
    """The command the family runs in a terminal to cancel the computer's repeating wakes."""
    return _adapter.MANUAL_CANCEL
