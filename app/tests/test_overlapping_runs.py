"""Runs never overlap, and `bg` leaves no job or output behind when it is stopped.

`launchctl` is faked: it records what it is asked to do and reports the job as running."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time

import pytest

from conftest import msg

from family_brief import ops, run_lock
from family_brief.config import Config


def one_message(h) -> None:
    h.sources = {"gmail": [msg("gmail", "g-1", "2026-09-27T08:15:00+03:00",
                               "Class photos for 1A are on Wednesday morning.",
                               sender="office@kilo.example.fi", subject="Valokuvaus ke 30.9.")]}


def another_run_is_going(h):
    return run_lock.exclusive(Config.model_validate(h.config))


def test_a_run_while_another_is_going_stops_with_a_message_and_sends_nothing(harness, caplog):
    one_message(harness)

    with another_run_is_going(harness):
        code = harness.run()

    assert code == run_lock.BUSY
    assert "Another FamilyBrief run is still going" in caplog.text
    assert harness.sent == [] and harness.model_calls == []
    assert not harness.state_path.exists()


def test_a_run_goes_ahead_once_the_other_has_finished(harness):
    one_message(harness)
    with another_run_is_going(harness):
        pass

    assert harness.run() == 0
    assert len(harness.sent) == 1


def test_a_preview_isnt_held_up_by_a_run_that_is_going(harness):
    one_message(harness)

    with another_run_is_going(harness):
        assert harness.run("--dry-run") == 0

    assert harness.sent == [] and not harness.state_path.exists()


def test_a_run_that_was_killed_doesnt_keep_the_next_one_out(harness, tmp_path):
    one_message(harness)
    ready = tmp_path / "locked"
    holder = subprocess.Popen([sys.executable, "-c", (
        "import pathlib, sys, time\n"
        "from family_brief import run_lock\n"
        "from family_brief.config import Config\n"
        f"cfg = Config.model_validate({harness.config!r})\n"
        "with run_lock.exclusive(cfg):\n"
        f"    pathlib.Path({str(ready)!r}).touch()\n"
        "    time.sleep(60)\n")], env={**os.environ, "HOME": str(harness.home)})
    try:
        for _ in range(100):
            if ready.exists():
                break
            time.sleep(0.1)
        assert harness.run() == run_lock.BUSY
    finally:
        holder.kill()
        holder.wait()

    assert harness.run() == 0
    assert len(harness.sent) == 1


def test_weekend_picks_while_another_run_is_going_stop_with_a_message(harness, caplog):
    harness.config["weekend_events"] = {"enabled": True}

    with another_run_is_going(harness):
        code = harness.cli("weekend-events")

    assert code == run_lock.BUSY
    assert "Another FamilyBrief run is still going" in caplog.text


class FakeLaunchctl:
    """Reports the job as running; `on_print` runs each time `bg` checks on it."""

    def __init__(self, on_print=lambda: None) -> None:
        self.calls: list[list[str]] = []
        self.on_print = on_print

    def __call__(self, cmd, *_a, **_k):
        assert cmd[0] == "launchctl", cmd
        self.calls.append(list(cmd))
        if cmd[1] == "print":
            self.on_print()
            return subprocess.CompletedProcess(cmd, 0, "\tstate = running\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def booted_out(self) -> list[str]:
        return [c[2] for c in self.calls if c[1] == "bootout"]


@pytest.fixture
def bg(harness, monkeypatch, tmp_path):
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.setattr("time.sleep", lambda _s: None)
    harness.temp = temp
    return harness


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_bg_stopped_by_a_signal_boots_out_its_job_and_removes_its_output(bg, monkeypatch, sig):
    launchctl = FakeLaunchctl(on_print=lambda: os.kill(os.getpid(), sig))
    monkeypatch.setattr(subprocess, "run", launchctl)
    before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}

    with pytest.raises(SystemExit) as stopped:
        bg.cli("bg", "run", "--dry-run", "--lookback-hours", "72")

    assert stopped.value.code == 128 + sig
    assert launchctl.booted_out() == [f"gui/{os.getuid()}/com.family.bg.{os.getpid()}"]
    assert list(bg.temp.iterdir()) == []  # the output log holds the preview Brief
    assert {s: signal.getsignal(s) for s in before} == before


def test_bg_boots_out_its_job_when_it_times_out(bg, monkeypatch):
    launchctl = FakeLaunchctl()
    monkeypatch.setattr(subprocess, "run", launchctl)

    assert bg.cli("bg", "--timeout", "0", "doctor") == 124

    assert len(launchctl.booted_out()) == 1
    assert list(bg.temp.iterdir()) == []
