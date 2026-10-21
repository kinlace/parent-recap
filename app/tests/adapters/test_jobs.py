"""The macOS adapter of `family_brief.system.jobs`: LaunchAgents and `launchctl` (ADR 0020).
Callers' tests use fake_system.FakeJobs instead.

The outside edge is faked: `launchctl`, which loads and unloads the plists in a temporary HOME,
starts them by hand and runs one-off jobs, which it reports as running."""
from __future__ import annotations

import os
import plistlib
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

import pytest
from conftest import program

from family_brief import install_record
from family_brief.system import jobs, jobs_macos

# The launchd adapter.
pytestmark = pytest.mark.macos


class FakeLaunchctl:
    def __init__(self) -> None:
        self.loaded: set[str] = set()
        self.running: set[str] = set()
        self.kickstart_fails = False
        self.on_print: Callable[[], None] = lambda: None  # each time a one-off job is checked on
        self.bootstrapped: list[dict[str, Any]] = []  # each one-off job's plist
        self.ran: list[list[str]] = []

    def run(self, cmd: list[str], *_a: Any, **_k: Any) -> subprocess.CompletedProcess:
        assert cmd[0] == "launchctl", cmd
        self.ran.append(list(cmd))
        if cmd[1] == "load":
            self.loaded.add(Path(cmd[2]).stem)
        elif cmd[1] == "unload":
            self.loaded.discard(Path(cmd[2]).stem)
        elif cmd[1] == "list":
            return subprocess.CompletedProcess(cmd, 0, "".join(f"-\t0\t{label}\n"
                                                               for label in self.loaded), "")
        elif cmd[1] == "print":
            label = cmd[2].split("/")[-1]
            if label.startswith(f"{jobs_macos.PREFIX}bg."):  # a one-off job is always running
                self.on_print()
                return subprocess.CompletedProcess(cmd, 0, "\tstate = running\n", "")
            if label not in self.loaded:
                return subprocess.CompletedProcess(cmd, 113, "", "Could not find service")
            state = "running" if label in self.running else "not running"
            return subprocess.CompletedProcess(cmd, 0, f"\tstate = {state}\n", "")
        elif cmd[1] == "bootstrap":
            self.bootstrapped.append(plistlib.loads(Path(cmd[3]).read_bytes()))
        elif cmd[1] == "kickstart" and self.kickstart_fails:
            return subprocess.CompletedProcess(cmd, 1, "", "Kickstart failed: 5")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def verbs(self, verb: str) -> list[list[str]]:
        return [c for c in self.ran if c[1] == verb]


@pytest.fixture
def launchctl(harness, monkeypatch) -> FakeLaunchctl:
    fake = FakeLaunchctl()
    monkeypatch.setattr(jobs, "_adapter", jobs_macos)  # in place of the harness's fake
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
    assert jobs.system_name("daily") == jobs_macos.DAILY_LABEL == "com.parentrecap.daily"


def test_a_job_runs_at_its_time_with_owner_only_logs_in_the_log_folder(harness, launchctl, tmp_path):
    jobs.install(both(tmp_path))

    plist = plistlib.loads((jobs_macos.LAUNCH_AGENTS / "com.parentrecap.weekend-events.plist")
                           .read_bytes())
    assert plist["StartCalendarInterval"] == {"Weekday": 5, "Hour": 16, "Minute": 0}
    assert plist["StandardErrorPath"] == str(tmp_path / "weekend-events-stderr.log")
    assert plist["EnvironmentVariables"]["HOME"] == str(harness.home)
    assert plist["Umask"] == 0o077  # launchd creates the log files owner-only


def job_path() -> list[str]:
    plist = plistlib.loads((jobs_macos.LAUNCH_AGENTS / "com.parentrecap.daily.plist").read_bytes())
    return plist["EnvironmentVariables"]["PATH"].split(":")


def test_a_jobs_path_has_the_native_installers_folder(harness, launchctl, tmp_path, monkeypatch):
    # Claude Code's native installer puts claude in ~/.local/bin. The shell that installs the job
    # may not have it on PATH yet, but the evening job must find claude there.
    program(harness.home / ".local" / "bin", "claude")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    jobs.install(both(tmp_path)[:1])

    assert str(harness.home / ".local" / "bin") in job_path()


def test_a_jobs_path_has_what_the_login_shell_finds(harness, launchctl, login_shell, tmp_path,
                                                    monkeypatch):
    # The family's own shell setup has claude and Node where the evening job's PATH wouldn't lead.
    folder = harness.home / "tools" / "bin"
    program(folder, "claude")
    program(folder, "node")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    login_shell(folder)

    jobs.install(both(tmp_path)[:1])

    assert str(folder) in job_path()


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


# ── starting a job by hand


def test_run_now_kickstarts_the_loaded_job(harness, launchctl, tmp_path):
    jobs.install(both(tmp_path))

    jobs.run_now(jobs.WEEKEND)

    assert launchctl.verbs("kickstart") == [
        ["launchctl", "kickstart", f"gui/{os.getuid()}/com.parentrecap.weekend-events"]]


def test_run_now_tells_a_job_not_loaded_from_one_already_running_or_refused(harness, launchctl,
                                                                            tmp_path):
    with pytest.raises(jobs.NotInstalled):
        jobs.run_now(jobs.DAILY)

    jobs.install(both(tmp_path)[:1])
    launchctl.running.add(jobs_macos.DAILY_LABEL)
    with pytest.raises(jobs.AlreadyRunning):
        jobs.run_now(jobs.DAILY)

    launchctl.running.clear()
    launchctl.kickstart_fails = True
    with pytest.raises(jobs.NotStarted, match="Kickstart failed: 5"):
        jobs.run_now(jobs.DAILY)
    assert len(launchctl.verbs("kickstart")) == 1


# ── one-off runs, which leave no job or output behind however they end


@pytest.fixture
def temp(monkeypatch, tmp_path) -> Path:
    folder = tmp_path / "tmp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    monkeypatch.setattr("time.sleep", lambda _s: None)
    return folder


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_bg_stopped_by_a_signal_boots_out_its_job_and_removes_its_output(harness, launchctl, temp,
                                                                         sig):
    launchctl.on_print = lambda: os.kill(os.getpid(), sig)
    before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}

    with pytest.raises(SystemExit) as stopped:
        harness.cli("bg", "run", "--dry-run", "--lookback-hours", "72")

    assert stopped.value.code == 128 + sig
    assert [c[2] for c in launchctl.verbs("bootout")] == [
        f"gui/{os.getuid()}/com.parentrecap.bg.{os.getpid()}"]
    assert list(temp.iterdir()) == []  # the output log holds the preview Brief
    assert {s: signal.getsignal(s) for s in before} == before


def test_bg_boots_out_its_job_when_it_times_out(harness, launchctl, temp):
    assert harness.cli("bg", "--timeout", "0", "doctor") == jobs.TIMED_OUT

    [job] = launchctl.bootstrapped
    assert job["ProgramArguments"][-1] == "doctor"
    assert job["EnvironmentVariables"][jobs.BG_ENV] == "1"  # so doctor doesn't start another
    assert len(launchctl.verbs("bootout")) == 1
    assert list(temp.iterdir()) == []
