"""`python -m family_brief.schedule_update`, which install.sh runs on a Mac after each install or
update: a scheduled Household gets this release's jobs, and an older version's wake, 5 minutes
before the job, moves to the job's own time (#832).

The outside edges are the harness's: its jobs and its wake, with the administrator dialog."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from family_brief import run_lock, schedule_update
from family_brief.config import Config
from family_brief.system import jobs, wake

# The Mac's wake dialog, which install.sh runs this for.
pytestmark = pytest.mark.macos


def config(harness) -> Path:
    path = harness.home / ".family" / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(harness.config, allow_unicode=True, sort_keys=False))
    return path


def scheduled(harness, *wakes: str) -> Path:
    """An install an older version scheduled: its daily job waits for a login, so installing it
    again loads it, and `wakes` are the Mac's repeating wakes."""
    harness.jobs.add(jobs.DAILY, loaded=False)
    harness.wake.wakes = list(wakes)
    return config(harness)


def test_an_older_versions_wake_moves_to_the_jobs_time_and_the_job_is_installed_again(
        harness, capsys):
    path = scheduled(harness, harness.wake.old_line(21, 0))

    schedule_update.update(str(path))

    out = capsys.readouterr().out
    assert harness.jobs.installed() == {jobs.DAILY}
    assert harness.wake.asked == ["set 21:00"]
    assert harness.wake.wakes == [harness.wake.line(21, 0)]
    assert "asks for your Mac password" in out
    assert "Wake: the Mac wakes at 21:00" in out


def test_run_for_parent_recap_update_it_asks_for_no_password_and_keeps_the_older_wake(
        harness, capsys, monkeypatch):
    old = harness.wake.old_line(21, 0)
    path = scheduled(harness, old)
    monkeypatch.setenv(schedule_update.UPDATING, "1")  # as `parent-recap update` runs install.sh

    schedule_update.update(str(path))

    out = capsys.readouterr().out
    assert harness.wake.asked == [] and harness.wake.wakes == [old]
    assert harness.jobs.installed() == {jobs.DAILY}
    assert "wasn't moved to 21:00" in out and "parent-recap schedule install" in out


@pytest.mark.parametrize("dialog", ["cancel", "none"])
def test_a_wake_that_couldnt_be_moved_keeps_the_older_versions_and_says_how(
        harness, capsys, dialog):
    old = harness.wake.old_line(21, 0)
    path = scheduled(harness, old)
    harness.wake.dialog = dialog

    schedule_update.update(str(path))

    out = capsys.readouterr().out
    assert harness.wake.wakes == [old]
    assert harness.jobs.installed() == {jobs.DAILY}  # the job is updated all the same
    assert "still wakes 5 minutes before the Brief" in out
    assert wake.manual_step(21, 0) in out


def test_a_wake_at_the_jobs_time_or_none_of_ours_asks_for_no_password(harness, capsys):
    path = scheduled(harness, harness.wake.line(21, 0))
    schedule_update.update(str(path))

    harness.wake.wakes = []  # never set, or the family cancelled it: doctor says so
    schedule_update.update(str(path))

    assert harness.wake.asked == []
    assert capsys.readouterr().out == ""
    assert harness.jobs.installed() == {jobs.DAILY}


def test_an_older_versions_wake_beside_another_schedule_waits_for_the_family(harness, capsys):
    old, other = harness.wake.old_line(21, 0), "shutdown every day at 23:00"
    path = scheduled(harness, old, other)

    schedule_update.update(str(path))

    assert harness.wake.asked == [] and harness.wake.wakes == [old, other]
    assert "schedule install --replace-wake" in capsys.readouterr().out


def test_an_install_that_isnt_scheduled_is_left_alone(harness, capsys):
    harness.wake.wakes = [harness.wake.old_line(21, 0)]
    path = config(harness)

    schedule_update.update(str(path))

    assert harness.jobs.installed() == set() and harness.wake.asked == []
    assert capsys.readouterr().out == ""


def test_a_brief_being_made_isnt_stopped_by_installing_its_job_again(harness, capsys):
    path = scheduled(harness, harness.wake.old_line(21, 0))

    with run_lock.exclusive(Config.load(path)):
        schedule_update.update(str(path))

    assert harness.jobs.installed() == set() and harness.wake.asked == []
    assert "parent-recap schedule install once it has gone out" in capsys.readouterr().out


def test_a_config_that_cant_be_read_leaves_the_schedule_and_says_so(harness, capsys):
    path = scheduled(harness, harness.wake.old_line(21, 0))
    path.write_text("schedule: [", encoding="utf-8")

    schedule_update.update(str(path))

    assert harness.wake.asked == []
    assert "the config can't be read" in capsys.readouterr().out


def test_install_sh_never_fails_over_the_schedule(harness, monkeypatch, capsys):
    def broken(_config=None):
        raise RuntimeError("launchctl went away")

    monkeypatch.setattr(schedule_update, "update", broken)

    assert schedule_update.main([]) == 0
    assert "parent-recap schedule install" in capsys.readouterr().out
