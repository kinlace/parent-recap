"""The Windows adapter of `family_brief.system.jobs`: Task Scheduler tasks (ADR 0020, ADR 0021).
Callers' tests use fake_system.FakeJobs instead.

The task each job becomes, what the task runs and the one-off runs are checked on any system.
Registering, listing, starting and removing the tasks is checked on Windows only, for real, in
the Task Scheduler of the Windows account running the tests, under names of each test's own
(`task_scheduler`), removed after. A CI runner can't show that a task wakes a sleeping PC."""
from __future__ import annotations

import sys
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pytest

from family_brief.system import jobs, jobs_windows, task_run

NS = {"t": jobs_windows.TASK_NS}
EVENING = datetime(2026, 9, 27, 18, 30)


def both(log_dir: Path) -> list[jobs.Job]:
    return [jobs.Job(jobs.DAILY, "run", None, 21, 0, log_dir),
            jobs.Job(jobs.WEEKEND, "weekend-events", 5, 16, 0, log_dir)]


def task(job: jobs.Job, now: datetime = EVENING) -> ET.Element:
    return ET.fromstring(jobs_windows.task_xml(job, now))


def text(task: ET.Element, path: str) -> str | None:
    found = task.find(path, NS)
    return None if found is None else found.text


# ── the task each job becomes


def test_the_evening_job_runs_every_day_at_its_time_from_its_next_one(tmp_path):
    daily = task(both(tmp_path)[0])

    assert text(daily, "t:Triggers/t:CalendarTrigger/t:StartBoundary") == "2026-09-27T21:00:00"
    assert text(daily, "t:Triggers/t:CalendarTrigger/t:ScheduleByDay/t:DaysInterval") == "1"


def test_a_time_already_gone_today_starts_tomorrow_so_there_is_no_missed_start_to_catch_up(
        tmp_path):
    late = task(both(tmp_path)[0], datetime(2026, 9, 27, 22, 15))

    assert text(late, "t:Triggers/t:CalendarTrigger/t:StartBoundary") == "2026-09-28T21:00:00"


def test_weekend_picks_run_once_a_week_on_their_day(tmp_path):
    weekend = task(both(tmp_path)[1])

    weekly = weekend.find("t:Triggers/t:CalendarTrigger/t:ScheduleByWeek", NS)
    assert [d.tag.split("}")[1] for d in weekly.find("t:DaysOfWeek", NS)] == ["Friday"]
    assert text(weekend, "t:Triggers/t:CalendarTrigger/t:StartBoundary").endswith("T16:00:00")


def test_a_task_runs_only_while_signed_in_wakes_the_pc_and_catches_up_a_missed_start(tmp_path):
    daily = task(both(tmp_path)[0])

    assert text(daily, "t:Principals/t:Principal/t:LogonType") == "InteractiveToken"
    assert text(daily, "t:Principals/t:Principal/t:RunLevel") == "LeastPrivilege"
    assert text(daily, "t:Settings/t:WakeToRun") == "true"
    assert text(daily, "t:Settings/t:StartWhenAvailable") == "true"
    assert text(daily, "t:Settings/t:DisallowStartIfOnBatteries") == "false"
    assert text(daily, "t:Settings/t:StopIfGoingOnBatteries") == "false"


def test_a_task_runs_the_job_without_a_window_and_with_its_log_files(tmp_path, monkeypatch):
    python = tmp_path / "venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    (python.parent / "pythonw.exe").touch()
    monkeypatch.setattr(sys, "executable", str(python))

    action = task(both(tmp_path / "log folder")[0]).find("t:Actions/t:Exec", NS)

    assert text(action, "t:Command") == str(python.parent / "pythonw.exe")
    args = text(action, "t:Arguments")
    assert args.startswith(f"-m {jobs_windows.RUNNER} ")
    assert f'"{tmp_path / "log folder" / "run-stdout.log"}"' in args  # quoted for its space
    assert args.endswith(f"{python} -m family_brief run")


def test_the_evening_jobs_sign_in_task_catches_up_a_run_missed_while_signed_out(tmp_path,
                                                                                monkeypatch):
    # Task Scheduler doesn't start a run missed while nobody was signed in (#386).
    monkeypatch.setenv("USERDOMAIN", "KITCHEN-PC")
    monkeypatch.setenv("USERNAME", "family")
    python = tmp_path / "python.exe"
    python.touch()
    monkeypatch.setattr(sys, "executable", str(python))

    catch_up = ET.fromstring(jobs_windows.catch_up_xml(both(tmp_path)[0]))

    [trigger] = catch_up.find("t:Triggers", NS)
    assert trigger.tag == f"{{{jobs_windows.TASK_NS}}}LogonTrigger"
    # Only the family's own sign-in, not anyone's.
    assert text(trigger, "t:UserId") == "KITCHEN-PC\\family"
    assert text(catch_up, "t:Principals/t:Principal/t:LogonType") == "InteractiveToken"
    assert text(catch_up, "t:Settings/t:WakeToRun") == "false"
    assert text(catch_up, "t:Settings/t:DisallowStartIfOnBatteries") == "false"
    args = text(catch_up, "t:Actions/t:Exec/t:Arguments")
    assert str(tmp_path / "run-stdout.log") in args  # the evening job's own log files
    assert args.endswith(f"{python} -m family_brief run --if-missed")


def test_the_runner_appends_the_jobs_output_to_its_log_files_and_gives_its_exit_code(tmp_path):
    out, err = tmp_path / "run-stdout.log", tmp_path / "run-stderr.log"
    out.write_text("yesterday\n")
    script = ("import os, sys; print('Brief sent'); print('UTF-8', os.environ['PYTHONUTF8'], "
              "file=sys.stderr); sys.exit(3)")

    code = task_run.main([str(out), str(err), sys.executable, "-c", script])

    assert code == 3
    assert out.read_text().splitlines() == ["yesterday", "Brief sent"]
    assert err.read_text().strip() == "UTF-8 1"


# ── the always-running job's task


def test_the_always_running_job_starts_at_the_familys_sign_in_and_runs_without_end(tmp_path,
                                                                                  monkeypatch):
    monkeypatch.setenv("USERDOMAIN", "KITCHEN-PC")
    monkeypatch.setenv("USERNAME", "family")
    node = tmp_path / "runtime" / "node.exe"

    always = ET.fromstring(jobs_windows.always_xml([str(node), "linked-device.mjs", "follow"],
                                                   tmp_path / "log folder"))

    [trigger] = always.find("t:Triggers", NS)
    assert trigger.tag == f"{{{jobs_windows.TASK_NS}}}LogonTrigger"
    assert text(trigger, "t:UserId") == "KITCHEN-PC\\family"
    assert text(trigger, "t:Delay") is None
    assert text(always, "t:Principals/t:Principal/t:LogonType") == "InteractiveToken"
    assert text(always, "t:Settings/t:RestartOnFailure/t:Interval") == "PT1M"
    assert text(always, "t:Settings/t:ExecutionTimeLimit") == "PT0S"  # not stopped after 72 h
    assert text(always, "t:Settings/t:WakeToRun") == "false"
    assert text(always, "t:Settings/t:DisallowStartIfOnBatteries") == "false"
    args = text(always, "t:Actions/t:Exec/t:Arguments")
    # task_run starts it again unless its link ended.
    assert args.startswith(f"-m {jobs_windows.RUNNER} --restart-unless {jobs.LINK_ENDED} ")
    assert f'"{tmp_path / "log folder" / "linked-device-stderr.log"}"' in args
    assert args.endswith(f"{node} linked-device.mjs follow")


def test_the_evening_jobs_tasks_are_not_restarted_and_keep_their_time_limit(tmp_path):
    daily = task(both(tmp_path)[0])

    assert daily.find("t:Settings/t:RestartOnFailure", NS) is None
    assert daily.find("t:Settings/t:ExecutionTimeLimit", NS) is None


RUNS = """import pathlib, sys
runs = pathlib.Path(sys.argv[1])
n = len(runs.read_text()) if runs.exists() else 0
runs.write_text("x" * (n + 1))
print("run", n + 1)
sys.exit({codes}[n])"""


def test_the_runner_starts_the_always_running_job_again_until_its_link_ended(tmp_path,
                                                                            monkeypatch):
    monkeypatch.setattr(task_run, "RESTART_AFTER", 0)
    out, err = tmp_path / "linked-device-stdout.log", tmp_path / "linked-device-stderr.log"
    script = RUNS.format(codes=[1, 3, jobs.LINK_ENDED, 1])

    code = task_run.main(["--restart-unless", str(jobs.LINK_ENDED), str(out), str(err),
                          sys.executable, "-c", script, str(tmp_path / "runs")])

    assert code == jobs.LINK_ENDED
    assert out.read_text().splitlines() == ["run 1", "run 2", "run 3"]


def test_the_runner_gives_an_evening_jobs_exit_code_without_starting_it_again(tmp_path):
    out, err = tmp_path / "run-stdout.log", tmp_path / "run-stderr.log"

    code = task_run.main([str(out), str(err), sys.executable, "-c", RUNS.format(codes=[1, 0]),
                          str(tmp_path / "runs")])

    assert code == 1 and out.read_text().splitlines() == ["run 1"]


# ── one-off runs, a hidden child process


@pytest.fixture
def child_process(harness, monkeypatch):
    monkeypatch.setattr(jobs, "_adapter", jobs_windows)  # in place of the harness's fake


def test_bg_runs_the_command_and_gives_its_output_and_exit_code(harness, child_process):
    code, out = jobs.run_detached(["--help"], 60, echo=False)

    assert code == 0 and "usage:" in out


def test_bg_stops_a_run_that_takes_too_long(harness, child_process):
    code, _ = jobs.run_detached(["--help"], 0, echo=False)

    assert code == jobs.TIMED_OUT


# ── Task Scheduler itself


@pytest.fixture
def task_scheduler(monkeypatch):
    """Windows' own Task Scheduler, under names of the test's own, so no task of the family whose
    PC runs the tests is touched; they're removed after. Without the harness, whose fake
    `subprocess.run` would stand in for schtasks."""
    monkeypatch.setattr(jobs, "_adapter", jobs_windows)
    monkeypatch.setattr(jobs_windows, "PREFIX", f"Parent Recap test {uuid.uuid4().hex[:8]} ")
    yield
    jobs.remove_all()


@pytest.mark.windows
def test_installing_registers_both_tasks_and_without_weekend_picks_removes_theirs(task_scheduler,
                                                                                  tmp_path):
    catch_up = jobs_windows.CATCH_UP
    jobs.install(both(tmp_path))
    assert jobs.installed() == {jobs.DAILY, jobs.WEEKEND}
    assert jobs.program(jobs.DAILY) == [sys.executable, "-m", "family_brief", "run"]
    assert jobs.program(catch_up) == [sys.executable, "-m", "family_brief", "run", "--if-missed"]
    assert {j.system_name for j in jobs.found()} == {jobs.system_name(jobs.DAILY),
                                                     jobs.system_name(catch_up),
                                                     jobs.system_name(jobs.WEEKEND)}

    jobs.install(both(tmp_path)[:1])

    assert jobs.installed() == {jobs.DAILY}
    assert jobs.program(jobs.WEEKEND) is None
    assert jobs.waiting_for_login() == set()


@pytest.mark.windows
def test_the_evening_job_without_its_sign_in_task_is_not_installed(task_scheduler, tmp_path):
    # As after an upgrade from 0.6.2, so doctor asks for `schedule install` (#386).
    jobs.install(both(tmp_path))
    r = jobs_windows._schtasks("/Delete", "/TN", jobs.system_name(jobs_windows.CATCH_UP), "/F")
    assert r.returncode == 0, r.stderr

    assert jobs.installed() == {jobs.WEEKEND}


@pytest.mark.windows
def test_removing_all_unregisters_every_task_of_ours(task_scheduler, tmp_path):
    jobs.install(both(tmp_path))

    jobs.remove_all()

    assert jobs.installed() == set() and jobs.found() == []


def wait_for(check, seconds: int = 60) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline and not check():
        time.sleep(1)
    return check()


# A job that never ends, writing a line every half second, so whether it still runs shows.
TICKS = "import time\nwhile True:\n    print('tick', flush=True)\n    time.sleep(0.5)"


@pytest.mark.windows
def test_the_always_running_job_is_installed_running_stopped_and_removed(task_scheduler,
                                                                         tmp_path):
    log = tmp_path / f"{jobs.ALWAYS}-stdout.log"
    ticks = [sys.executable, "-c", TICKS]
    assert jobs.always_status() == jobs.AlwaysStatus(installed=False, running=False)

    jobs.install_always(ticks, tmp_path)

    assert wait_for(lambda: jobs.always_status() == jobs.AlwaysStatus(installed=True, running=True))
    assert wait_for(lambda: log.exists() and "tick" in log.read_text())
    assert jobs.program(jobs.ALWAYS) == ticks
    jobs.install(both(tmp_path)[:1])  # the evening jobs leave it be
    assert jobs.installed() == {jobs.DAILY} and jobs.always_status().installed

    jobs.stop_always()

    assert wait_for(lambda: jobs.always_status() == jobs.AlwaysStatus(installed=True, running=False))
    time.sleep(2)  # ending the task ends the job it started too
    stopped_at = log.stat().st_size
    time.sleep(2)
    assert log.stat().st_size == stopped_at

    jobs.remove_always()

    assert jobs.always_status() == jobs.AlwaysStatus(installed=False, running=False)
    assert {j.system_name for j in jobs.found()} == {jobs.system_name(jobs.DAILY),
                                                     jobs.system_name(jobs_windows.CATCH_UP)}


@pytest.mark.windows
def test_removing_all_also_stops_and_removes_the_always_running_job(task_scheduler, tmp_path):
    jobs.install_always([sys.executable, "-c", TICKS], tmp_path)
    assert wait_for(lambda: jobs.always_status().running)

    jobs.remove_all()

    assert jobs.always_status() == jobs.AlwaysStatus(installed=False, running=False)
    assert jobs.found() == []


@pytest.mark.windows
def test_run_now_starts_the_task_which_writes_its_log_files(task_scheduler, tmp_path):
    with pytest.raises(jobs.NotInstalled):
        jobs.run_now(jobs.DAILY)
    # A job whose command only prints the program's help, so starting it sends no Brief.
    jobs.install([jobs.Job(jobs.DAILY, "--help", None, 21, 0, tmp_path)])

    jobs.run_now(jobs.DAILY)

    log = tmp_path / "--help-stdout.log"
    deadline = time.time() + 60
    while time.time() < deadline and "usage:" not in (log.read_text() if log.exists() else ""):
        time.sleep(1)
    assert "usage:" in log.read_text()
