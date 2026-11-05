"""`family-brief schedule run-now` asks the system to start the installed job by hand.

The scheduler is the harness's FakeJobs: which jobs are installed and running, and which it
started."""
from __future__ import annotations

from family_brief.system import jobs


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
