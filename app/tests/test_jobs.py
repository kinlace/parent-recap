"""The evening jobs through `family_brief.system.jobs` (ADR 0020).

The outside edge is faked: `launchctl`, which loads and unloads the plists in a temporary HOME."""
from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path
from typing import Any

import pytest

from family_brief import install_record
from family_brief.system import jobs, jobs_macos


class FakeLaunchctl:
    def __init__(self) -> None:
        self.loaded: set[str] = set()

    def run(self, cmd: list[str], *_a: Any, **_k: Any) -> subprocess.CompletedProcess:
        assert cmd[0] == "launchctl", cmd
        if cmd[1] == "load":
            self.loaded.add(Path(cmd[2]).stem)
        elif cmd[1] == "unload":
            self.loaded.discard(Path(cmd[2]).stem)
        elif cmd[1] == "list":
            return subprocess.CompletedProcess(cmd, 0, "".join(f"-\t0\t{label}\n"
                                                               for label in self.loaded), "")
        return subprocess.CompletedProcess(cmd, 0, "", "")


@pytest.fixture
def launchctl(harness, monkeypatch) -> FakeLaunchctl:
    fake = FakeLaunchctl()
    monkeypatch.setattr(subprocess, "run", fake.run)
    monkeypatch.setattr(jobs_macos, "LAUNCH_AGENTS", harness.home / "Library" / "LaunchAgents")
    return fake


def both(log_dir: Path) -> list[jobs.Job]:
    return [jobs.Job(jobs.DAILY, "run", None, 21, 0, log_dir),
            jobs.Job(jobs.WEEKEND, "weekend-events", 5, 16, 0, log_dir)]


def test_installing_without_weekend_picks_removes_their_job(harness, launchctl, tmp_path):
    jobs.install(both(tmp_path))
    assert jobs.installed() == {"daily", "weekend-events"}

    jobs.install(both(tmp_path)[:1])

    assert jobs.installed() == {"daily"}
    assert jobs.program("weekend-events") is None
    assert jobs.program("daily")[1:] == ["-m", "family_brief", "run"]


def test_a_job_installed_but_not_loaded_waits_for_a_login(harness, launchctl, tmp_path):
    jobs.install(both(tmp_path)[:1])
    launchctl.loaded.clear()  # after a restart, before anyone logs in

    assert jobs.installed() == set() and jobs.waiting_for_login() == {"daily"}


def test_removing_all_also_removes_an_older_versions_job_from_the_record(harness, launchctl,
                                                                         tmp_path):
    jobs.install(both(tmp_path))
    older = jobs_macos.LAUNCH_AGENTS / "com.family.daily.plist"
    older.write_bytes(plistlib.dumps({"Label": "com.family.daily", "ProgramArguments": ["python"]}))
    install_record.add("launchd", str(older))
    assert {j.system_name for j in jobs.found()} == {"com.parentrecap.daily",
                                                     "com.parentrecap.weekend-events",
                                                     "com.family.daily"}

    jobs.remove_all()

    assert jobs.found() == [] and jobs.installed() == set()
    assert list(jobs_macos.LAUNCH_AGENTS.iterdir()) == []
