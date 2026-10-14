"""The macOS adapter of `family_brief.system.wake`: `pmset` (ADR 0020). Callers' tests use
fake_system.FakeWake instead.

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
        self.one_off: list[str] = []   # under "Scheduled power events:", which repeat leaves alone
        self.custom = "AC Power:\n sleep                1\n"
        self.dialog = "allow"  # or "cancel", or "unavailable" (no desktop session)
        self.dialogs: list[str] = []

    def run(self, cmd: list[str], *_a: Any, **_k: Any) -> subprocess.CompletedProcess:
        if cmd[:3] == ["pmset", "-g", "custom"]:
            return subprocess.CompletedProcess(cmd, 0, self.custom, "")
        if cmd[:3] == ["pmset", "-g", "sched"]:
            listed = "".join(f"  {line}\n" for line in self.repeating)
            sched = f"Repeating power events:\n{listed}" if listed else ""
            if self.one_off:
                sched += "Scheduled power events:\n" + "".join(f" {line}\n" for line in self.one_off)
            return subprocess.CompletedProcess(cmd, 0, sched, "")
        assert cmd[0] == "osascript" and "administrator privileges" in cmd[-1], cmd
        self.dialogs.append(cmd[-1])
        if self.dialog == "cancel":
            return subprocess.CompletedProcess(cmd, 1, "", "execution error: User canceled. (-128)")
        if self.dialog == "unavailable":
            return subprocess.CompletedProcess(
                cmd, 1, "", "execution error: No user interaction allowed. (-1713)")
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


def test_the_dialog_says_who_asks_and_runs_pmset_repeat(mac):
    assert wake.set(21, 0, replace=False) == "set"

    [dialog] = mac.dialogs
    assert "/usr/bin/pmset repeat wakeorpoweron MTWRFSU 20:55:00" in dialog
    assert "Parent Recap" in dialog and "20:55" in dialog


def test_one_off_events_are_not_among_the_others(mac):
    mac.repeating = [OTHER_WAKE]
    mac.one_off = ["[0]  wake at 10/03/2026 07:00:00 by 'com.apple.alarm'"]

    assert wake.status(21, 0).others == [OTHER_WAKE]


@pytest.mark.parametrize("custom, never", [
    ("AC Power:\n displaysleep         10\n sleep                0 (sleep prevented by powerd)\n",
     True),
    ("Battery Power:\n sleep                1\nAC Power:\n sleep                0\n", False),
], ids=["never-sleeps", "sleeps-on-battery"])
def test_a_mac_never_sleeps_only_when_no_power_source_lets_it(mac, custom, never):
    mac.custom = custom

    assert wake.status(21, 0).never_sleeps is never
    assert (wake.set(21, 0, replace=False) == "never-sleeps") is never


def test_without_a_dialog_to_ask_in_the_wake_isnt_set(mac, monkeypatch):
    mac.dialog = "unavailable"
    assert wake.set(21, 0, replace=False) == "no-dialog"

    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.2 52000 10.0.0.1 22")
    assert wake.set(21, 0, replace=False) == "no-dialog"

    assert len(mac.dialogs) == 1  # over SSH, none is tried
    assert mac.repeating == []
