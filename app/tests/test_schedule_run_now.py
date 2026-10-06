"""`family-brief schedule run-now` asks launchd to start the installed job by hand.

`launchctl` is faked: which jobs are loaded and running, and the commands it is given."""
from __future__ import annotations

import os
import subprocess
from typing import Any

import pytest

from family_brief import ops


class FakeLaunchctl:
    def __init__(self) -> None:
        self.loaded: set[str] = set()
        self.running: set[str] = set()
        self.kickstart_fails = False
        self.ran: list[list[str]] = []

    def run(self, cmd: list[str], *_a: Any, **_k: Any) -> subprocess.CompletedProcess:
        cmd = list(cmd)
        self.ran.append(cmd)
        assert cmd[0] == "launchctl", cmd
        label = cmd[2].split("/")[-1]
        if cmd[1] == "print":
            if label not in self.loaded:
                return subprocess.CompletedProcess(cmd, 113, "", "Could not find service")
            state = "running" if label in self.running else "not running"
            return subprocess.CompletedProcess(cmd, 0, f"\tstate = {state}\n", "")
        assert cmd[1] == "kickstart", cmd
        if self.kickstart_fails:
            return subprocess.CompletedProcess(cmd, 1, "", "Kickstart failed: 5")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def kickstarts(self) -> list[list[str]]:
        return [c for c in self.ran if c[1] == "kickstart"]


@pytest.fixture
def launchctl(harness, monkeypatch) -> FakeLaunchctl:
    fake = FakeLaunchctl()
    monkeypatch.setattr(subprocess, "run", fake.run)
    return fake


def test_starts_the_evening_job_and_says_where_its_logs_are(harness, launchctl, capsys):
    launchctl.loaded.add(ops.JOB_DAILY)
    assert harness.cli("schedule", "run-now") == 0
    assert launchctl.kickstarts() == [["launchctl", "kickstart", f"gui/{os.getuid()}/{ops.JOB_DAILY}"]]
    out = capsys.readouterr().out
    assert f"Started {ops.JOB_DAILY}" in out
    assert "run-stdout.log" in out and "run-stderr.log" in out


def test_weekend_starts_the_weekend_job(harness, launchctl, capsys):
    launchctl.loaded.update({ops.JOB_DAILY, ops.JOB_WEEKEND})
    assert harness.cli("schedule", "run-now", "--weekend") == 0
    assert launchctl.kickstarts() == [["launchctl", "kickstart", f"gui/{os.getuid()}/{ops.JOB_WEEKEND}"]]
    assert "weekend-events-stderr.log" in capsys.readouterr().out


def test_a_job_that_isnt_installed_points_to_schedule_install(harness, launchctl, capsys):
    assert harness.cli("schedule", "run-now") == 1
    assert launchctl.kickstarts() == []
    out = capsys.readouterr().out
    assert "isn't installed" in out and "family-brief schedule install" in out


def test_a_run_already_going_isnt_started_again(harness, launchctl, capsys):
    launchctl.loaded.add(ops.JOB_DAILY)
    launchctl.running.add(ops.JOB_DAILY)
    assert harness.cli("schedule", "run-now") == 1
    assert launchctl.kickstarts() == []
    assert "already running" in capsys.readouterr().out


def test_a_refused_start_shows_launchctls_reason(harness, launchctl, capsys):
    launchctl.loaded.add(ops.JOB_DAILY)
    launchctl.kickstart_fails = True
    assert harness.cli("schedule", "run-now") == 1
    assert "Kickstart failed: 5" in capsys.readouterr().out
