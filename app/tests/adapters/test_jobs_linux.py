"""The Linux adapter of `family_brief.system.jobs`: systemd user timers in ~/.config/systemd/user,
run with the Household's user lingering so nobody needs to be signed in (ADR 0029). Callers' tests
use fake_system.FakeJobs instead.

The outside edge is faked: `systemctl --user`, which enables, starts and stops the units written
in a temporary HOME, and `loginctl`, which keeps whether the user lingers."""
from __future__ import annotations

import getpass
import subprocess
from pathlib import Path
from typing import Any

import pytest

from family_brief.system import jobs, jobs_linux

# The fake reads no real systemd, but the adapter finds the user's runtime folder by its uid.
pytestmark = pytest.mark.posix


class FakeSystemd:
    def __init__(self, units: Path) -> None:
        self.units = units
        self.active: set[str] = set()     # timers enabled and waiting for their time
        self.times: dict[str, str] = {}   # each running timer's OnCalendar, read when it started
        self.running: set[str] = set()    # services running now
        self.lingers = False
        self.linger_fails = False
        self.start_fails = False
        self.ran: list[list[str]] = []

    def run(self, cmd: list[str], *_a: Any, **_k: Any) -> subprocess.CompletedProcess:
        self.ran.append(list(cmd))
        if cmd[0] == "loginctl":
            assert getpass.getuser() in cmd, cmd
            if cmd[1] == "show-user":
                return self._done(cmd, 0, "yes\n" if self.lingers else "no\n")
            assert cmd[1] == "enable-linger", cmd
            if self.linger_fails:
                return self._done(cmd, 1, "", "Could not enable linger: Access denied")
            self.lingers = True
            return self._done(cmd, 0)
        assert cmd[:2] == ["systemctl", "--user"], cmd
        verb, unit = cmd[2], cmd[-1]
        if verb == "enable":
            assert (self.units / unit).exists(), cmd
        elif verb == "restart":  # a running timer keeps the time it started with until then
            assert (self.units / unit).exists(), cmd
            self.active.add(unit)
            self.times[unit] = unit_file(self.units / unit)["OnCalendar"]
        elif verb == "disable":
            self.active.discard(unit)
        elif verb == "stop":
            self.running.discard(unit)
        elif verb == "is-active":
            return self._done(cmd, 0 if unit in self.active else 3,
                              "active\n" if unit in self.active else "inactive\n")
        elif verb == "show":
            load = "loaded" if (self.units / unit).exists() else "not-found"
            state = "activating" if unit in self.running else "inactive"
            return self._done(cmd, 0, f"LoadState={load}\nActiveState={state}\n")
        elif verb == "start":
            if self.start_fails:
                return self._done(cmd, 1, "", f"Failed to start {unit}: Unit is masked.")
            self.running.add(unit)
        else:
            assert verb == "daemon-reload", cmd
        return self._done(cmd, 0)

    @staticmethod
    def _done(cmd: list[str], code: int, out: str = "", err: str = "") -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(cmd, code, out, err)

    def verbs(self, verb: str) -> list[list[str]]:
        return [c for c in self.ran if c[0] == "systemctl" and c[2] == verb]


@pytest.fixture
def systemd(harness, monkeypatch) -> FakeSystemd:
    units = harness.home / ".config" / "systemd" / "user"
    fake = FakeSystemd(units)
    monkeypatch.setattr(jobs, "_adapter", jobs_linux)  # in place of the harness's fake
    monkeypatch.setattr(subprocess, "run", fake.run)
    monkeypatch.setattr(jobs_linux, "UNITS", units)
    return fake


def both(log_dir: Path) -> list[jobs.Job]:
    return [jobs.Job(jobs.DAILY, "run", None, 21, 0, log_dir, "Europe/Helsinki"),
            jobs.Job(jobs.WEEKEND, "weekend-events", 5, 16, 0, log_dir, "Europe/Helsinki")]


def unit_file(path: Path) -> dict[str, str]:
    """A unit file's settings, by key."""
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)


def unit(name: str) -> dict[str, str]:
    return unit_file(jobs_linux.UNITS / name)


def test_installing_writes_timers_and_services_turns_on_lingering_and_lists_them(systemd, tmp_path):
    jobs.install(both(tmp_path))

    assert systemd.lingers
    assert systemd.active == {"parent-recap-daily.timer", "parent-recap-weekend-events.timer"}
    assert jobs.installed() == {"daily", "weekend-events"}
    assert jobs.system_name("daily") == "parent-recap-daily"
    assert jobs.program("daily")[1:] == ["-m", "family_brief", "run"]
    assert jobs.waiting_for_login() == set()  # lingering: it runs with nobody signed in


def test_a_job_runs_at_its_time_in_the_household_s_time_zone_and_catches_up(systemd, tmp_path):
    jobs.install(both(tmp_path))

    daily = unit("parent-recap-daily.timer")
    assert daily["OnCalendar"] == "*-*-* 21:00:00 Europe/Helsinki"
    assert daily["Unit"] == "parent-recap-daily.service"
    assert daily["Persistent"] == "true"  # a run missed while the server was down, after boot
    assert unit("parent-recap-weekend-events.timer")["OnCalendar"] == \
        "Fri *-*-* 16:00:00 Europe/Helsinki"


def test_a_job_has_a_cpu_quota_a_memory_ceiling_and_owner_only_logs(systemd, harness, tmp_path):
    jobs.install(both(tmp_path))

    service = unit("parent-recap-weekend-events.service")
    assert service["CPUQuota"] == "50%"
    assert service["MemoryMax"] == "400M"
    assert service["UMask"] == "0077"
    assert service["StandardError"] == f"append:{tmp_path / 'weekend-events-stderr.log'}"
    assert f'"HOME={harness.home}"' in service["Environment"]


def test_installing_again_at_another_time_moves_the_running_timer(systemd, tmp_path):
    jobs.install(both(tmp_path))

    jobs.install([jobs.Job(jobs.DAILY, "run", None, 20, 30, tmp_path, "Europe/Helsinki")])

    assert systemd.times["parent-recap-daily.timer"] == "*-*-* 20:30:00 Europe/Helsinki"


def test_a_path_with_a_percent_or_dollar_sign_reaches_the_job_as_it_is(systemd, harness,
                                                                     tmp_path):
    logs = tmp_path / "100% $HOME"
    jobs.install([jobs.Job(jobs.DAILY, "run", None, 21, 0, logs)])

    service = unit("parent-recap-daily.service")
    # systemd reads %% as % everywhere, and $$ as $ only in a command line.
    assert service["StandardOutput"] == f"append:{tmp_path}/100%% $HOME/run-stdout.log"
    assert jobs.program("daily")[1:] == ["-m", "family_brief", "run"]
    assert unit("parent-recap-daily.timer")["OnCalendar"] == "*-*-* 21:00:00"  # the server's zone


def test_installing_without_weekend_picks_removes_their_job(systemd, tmp_path):
    jobs.install(both(tmp_path))

    jobs.install(both(tmp_path)[:1])

    assert jobs.installed() == {"daily"}
    assert jobs.program("weekend-events") is None
    assert sorted(p.name for p in jobs_linux.UNITS.iterdir()) == [
        "parent-recap-daily.service", "parent-recap-daily.timer"]


def test_without_lingering_no_job_counts_as_installed(systemd, tmp_path):
    jobs.install(both(tmp_path))
    systemd.lingers = False  # an administrator turned it off: the timers wait for a sign-in

    assert jobs.installed() == set()


def test_a_user_who_may_not_linger_is_told_how_an_administrator_turns_it_on(systemd, tmp_path):
    systemd.linger_fails = True

    with pytest.raises(jobs.NotStarted, match="sudo loginctl enable-linger"):
        jobs.install(both(tmp_path))

    assert systemd.active == set()


def test_removing_all_disables_and_deletes_the_units(systemd, tmp_path):
    jobs.install(both(tmp_path))
    assert {f.system_name for f in jobs.found()} == {"parent-recap-daily",
                                                     "parent-recap-weekend-events"}

    jobs.remove_all()

    assert jobs.found() == [] and jobs.installed() == set()
    assert systemd.active == set() and list(jobs_linux.UNITS.iterdir()) == []


# ── starting a job by hand


def test_run_now_starts_the_installed_job_without_waiting(systemd, tmp_path):
    jobs.install(both(tmp_path))

    jobs.run_now(jobs.WEEKEND)

    assert systemd.verbs("start") == [
        ["systemctl", "--user", "start", "--no-block", "parent-recap-weekend-events.service"]]


def test_run_now_tells_a_job_not_installed_from_one_already_running_or_refused(systemd, tmp_path):
    with pytest.raises(jobs.NotInstalled):
        jobs.run_now(jobs.DAILY)

    jobs.install(both(tmp_path)[:1])
    systemd.running.add("parent-recap-daily.service")
    with pytest.raises(jobs.AlreadyRunning):
        jobs.run_now(jobs.DAILY)

    systemd.running.clear()
    systemd.start_fails = True
    with pytest.raises(jobs.NotStarted, match="Unit is masked"):
        jobs.run_now(jobs.DAILY)
    assert len(systemd.verbs("start")) == 1


def test_schedule_install_and_status_list_the_jobs(systemd, harness, capsys):
    harness.config["weekend_events"] = {"enabled": True}

    assert harness.cli("schedule", "install") == 0
    assert harness.cli("schedule", "status") == 0

    out = capsys.readouterr().out
    assert "parent-recap-daily\n" in out and "parent-recap-weekend-events\n" in out
    assert "(not installed)" not in out
    assert "Europe/Helsinki" in unit("parent-recap-daily.timer")["OnCalendar"]


# ── the always-running job, the Linked Device's, which a server without WhatsApp doesn't have


def test_the_always_running_job_says_it_isn_t_available_without_raising(systemd, tmp_path, capsys):
    jobs.install_always(["node", "linked-device.mjs", "follow"], tmp_path)
    jobs.remove_always()
    jobs.stop_always()

    assert jobs.always_status() == jobs.AlwaysStatus(installed=False, running=False)
    said = capsys.readouterr().err.splitlines()
    assert said == [jobs_linux.NO_ALWAYS] * 2 and "Linux" in jobs_linux.NO_ALWAYS
    assert systemd.ran == []
