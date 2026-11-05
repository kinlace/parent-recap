"""`family-brief schedule run-now` asks the system to start the installed job by hand, and
`schedule refresh` installs again the jobs an older version installed.

The scheduler is the harness's FakeJobs: which jobs are installed and running, and which it
started."""
from __future__ import annotations

from family_brief import ops
from family_brief.system import jobs
from fake_system import LINUX


def test_starts_the_evening_job_and_says_where_its_logs_are(harness, capsys):
    harness.jobs.add(jobs.DAILY)
    assert harness.cli("schedule", "run-now") == 0
    assert harness.jobs.started == [jobs.DAILY]
    out = capsys.readouterr().out
    assert f"Started {jobs.system_name(jobs.DAILY)}" in out
    assert "run-stdout.log" in out and "run-stderr.log" in out


def test_weekend_starts_the_weekend_job(harness, capsys):
    harness.jobs.add(jobs.DAILY, jobs.WEEKEND)
    assert harness.cli("schedule", "run-now", "--weekend") == 0
    assert harness.jobs.started == [jobs.WEEKEND]
    assert "weekend-events-stderr.log" in capsys.readouterr().out


def test_a_job_that_isnt_installed_points_to_schedule_install(harness, capsys):
    assert harness.cli("schedule", "run-now") == 1
    assert harness.jobs.started == []
    out = capsys.readouterr().out
    assert "isn't installed" in out and "family-brief schedule install" in out


def test_a_run_already_going_isnt_started_again(harness, capsys):
    harness.jobs.add(jobs.DAILY)
    harness.jobs.running.add(jobs.DAILY)
    assert harness.cli("schedule", "run-now") == 1
    assert harness.jobs.started == []
    assert "already running" in capsys.readouterr().out


def test_a_refused_start_shows_the_systems_reason(harness, capsys):
    harness.jobs.add(jobs.DAILY)
    harness.jobs.start_fails = "Kickstart failed: 5"
    assert harness.cli("schedule", "run-now") == 1
    assert "Kickstart failed: 5" in capsys.readouterr().out


# ── `schedule refresh`, which an update runs: the evening job checks its own time (#776)

def test_refresh_installs_again_an_evening_job_an_older_version_installed(harness, capsys):
    harness.jobs.add(jobs.DAILY)  # runs `run`, without --scheduled

    assert harness.cli("schedule", "refresh") == 0

    assert harness.jobs.programs[jobs.DAILY][3:] == ["run", jobs.SCHEDULED]
    assert "installed again" in capsys.readouterr().out


def test_refresh_leaves_jobs_alone_when_there_is_none_or_it_is_this_version_s(harness, capsys):
    assert harness.cli("schedule", "refresh") == 0
    assert harness.jobs.programs == {}

    harness.cli("schedule", "install")
    capsys.readouterr()
    assert harness.cli("schedule", "refresh") == 0
    assert capsys.readouterr().out == ""


def test_refresh_leaves_a_server_s_jobs_alone(harness, capsys):
    # Its timers keep the Household's zone, so its evening run needs no flag.
    harness.system = LINUX
    harness.jobs.add(jobs.DAILY)

    assert harness.cli("schedule", "refresh") == 0

    assert harness.jobs.programs[jobs.DAILY][3:] == ["run"]


def test_refresh_without_a_config_does_nothing(harness):
    # A first install, before setup.
    assert ops.refresh_jobs(str(harness.home / ".family" / "config.yaml")) == 0
