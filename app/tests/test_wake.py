"""Waking the computer through `family_brief.system.wake` (ADR 0020).

The outside edges are faked: `pmset` and macOS's administrator dialog (`osascript`)."""
from __future__ import annotations

import subprocess
from typing import Any

import pytest

from family_brief.system import wake

OUR_WAKE = "wakepoweron at 8:55PM every day"
OTHER_WAKE = "wakepoweron at 7:00AM weekdays only"


class FakeMac:
    def __init__(self) -> None:
        self.repeating: list[str] = []
        self.dialog = "allow"  # or "cancel"
        self.dialogs: list[str] = []

    def run(self, cmd: list[str], *_a: Any, **_k: Any) -> subprocess.CompletedProcess:
        if cmd[:3] == ["pmset", "-g", "custom"]:
            return subprocess.CompletedProcess(cmd, 0, "AC Power:\n sleep                1\n", "")
        if cmd[:3] == ["pmset", "-g", "sched"]:
            listed = "".join(f"  {line}\n" for line in self.repeating)
            return subprocess.CompletedProcess(
                cmd, 0, f"Repeating power events:\n{listed}" if listed else "", "")
        assert cmd[0] == "osascript" and "administrator privileges" in cmd[-1], cmd
        self.dialogs.append(cmd[-1])
        if self.dialog == "cancel":
            return subprocess.CompletedProcess(cmd, 1, "", "execution error: User canceled. (-128)")
        self.repeating = [OUR_WAKE] if "wakeorpoweron MTWRFSU 20:55:00" in cmd[-1] else []
        return subprocess.CompletedProcess(cmd, 0, "", "")


@pytest.fixture
def mac(monkeypatch) -> FakeMac:
    for name in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"):
        monkeypatch.delenv(name, raising=False)
    fake = FakeMac()
    monkeypatch.setattr(subprocess, "run", fake.run)
    return fake


def test_the_wake_is_set_5_minutes_before_the_job_and_told_apart_from_others(mac):
    assert wake.set(21, 0, replace=False) == "set"
    mac.repeating.append(OTHER_WAKE)

    status = wake.status(21, 0)

    assert (status.ours, status.others, status.never_sleeps) == (OUR_WAKE, [OTHER_WAKE], False)
    assert wake.manual_step(21, 0) == "sudo pmset repeat wakeorpoweron MTWRFSU 20:55:00"


def test_another_wake_schedule_is_kept_until_the_family_agrees_to_replace_it(mac):
    mac.repeating = [OTHER_WAKE]

    assert wake.set(21, 0, replace=False) == "other-schedule"
    assert mac.dialogs == []

    assert wake.set(21, 0, replace=True) == "set"
    assert wake.status(21, 0).others == []


def test_a_cancelled_dialog_leaves_the_wake_and_says_how_to_cancel_it(mac):
    mac.repeating = [OUR_WAKE]
    mac.dialog = "cancel"

    with pytest.raises(wake.NotCancelled) as e:
        wake.cancel()

    assert mac.repeating == [OUR_WAKE]
    assert "sudo pmset repeat cancel" in str(e.value)
