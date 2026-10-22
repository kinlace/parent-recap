"""What only Windows checks for the evening run (ADR 0021): that "Allow wake timers" lets the task
wake the PC, and that Windows signs the family back in after an update restart. `doctor` reports
both beside the tasks, and `schedule install` says how the wake stands, each with a fix in plain
words. And uninstall's last step, which removes a program once it has stopped running.

The jobs, the wake and the sign-in setting are the harness's fakes, and the system is said to be
Windows; reading the sign-in setting is checked with its registry faked, and on Windows for real."""
from __future__ import annotations

import subprocess
import sys
import time

import pytest

from family_brief import ops
from family_brief.system import jobs, windows_only

from fake_system import MACOS, WINDOWS, FakeWake


@pytest.fixture
def windows(harness):
    """A Windows PC that doesn't sign the family back in after an update restart, with doctor's
    network checks off."""
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["wilma"]["enabled"] = False
    harness.config["whatsapp"]["enabled"] = False
    harness.system = WINDOWS
    harness.wake.settable = False
    harness.signs_in_after_update = False


def line(out: str, item: str) -> str:
    [found] = [l for l in out.splitlines() if f" {item}:" in l]
    return found


def test_doctor_says_what_to_change_for_each_of_the_evening_runs_checks(harness, windows, capsys):
    harness.cli("doctor", "--skip-llm")

    out = capsys.readouterr().out
    schedule = line(out, "Schedule")
    assert schedule.startswith(ops.WARN.strip()) and "parent-recap schedule install" in schedule
    wake = line(out, "Wake")
    assert wake.startswith(ops.WARN.strip()) and "Allow wake timers is off" in wake
    assert harness.wake.manual_step(21, 0) in wake
    sign_in = line(out, "Sign-in after updates")
    assert sign_in.startswith(ops.WARN.strip()) and "nobody is signed in" in sign_in
    assert windows_only.SIGN_IN_SETTING in sign_in
    assert "Full Disk Access" not in out and "pmset" not in out


def test_doctor_is_happy_once_the_tasks_wake_timers_and_sign_in_are_all_set(harness, windows,
                                                                           capsys):
    harness.jobs.add(jobs.DAILY)
    harness.wake.wakes = [FakeWake.line(21, 0)]
    harness.signs_in_after_update = True

    harness.cli("doctor", "--skip-llm")

    out = capsys.readouterr().out
    for item in ("Schedule", "Wake", "Sign-in after updates"):
        assert line(out, item).startswith(ops.OK)


def test_doctor_asks_to_check_a_sign_in_setting_windows_has_never_set(harness, windows, capsys):
    harness.signs_in_after_update = None

    harness.cli("doctor", "--skip-llm")

    sign_in = line(capsys.readouterr().out, "Sign-in after updates")
    assert sign_in.startswith(ops.WARN.strip()) and "couldn't tell" in sign_in
    assert windows_only.SIGN_IN_SETTING in sign_in


def test_a_pc_that_never_sleeps_needs_no_wake_timers(harness, windows, capsys):
    harness.wake.never_sleeps = True

    harness.cli("doctor", "--skip-llm")

    assert "never sleeps" in line(capsys.readouterr().out, "Wake")


def test_doctor_on_macos_has_neither_windows_check(harness, windows, capsys):
    harness.system = MACOS

    harness.cli("doctor", "--skip-llm")

    out = capsys.readouterr().out
    assert " Wake:" not in out and "Sign-in after updates" not in out


def test_schedule_install_registers_the_tasks_and_says_where_to_allow_wake_timers(harness, windows,
                                                                                capsys):
    harness.config["weekend_events"] = {"enabled": True}

    assert harness.cli("schedule", "install") == 0

    out = capsys.readouterr().out
    assert harness.jobs.installed() == {jobs.DAILY, jobs.WEEKEND}
    assert "Allow wake timers is off" in out and harness.wake.manual_step(21, 0) in out
    assert harness.wake.asked == []  # no dialog: the family turns it on themselves
    assert "Mac" not in out


def test_schedule_install_says_the_task_wakes_the_pc_once_wake_timers_are_on(harness, windows,
                                                                            capsys):
    harness.wake.wakes = [FakeWake.line(21, 0)]

    harness.cli("schedule", "install")

    assert f"{ops.OK} Wake: Allow wake timers is on" in capsys.readouterr().out


def test_schedule_uninstall_removes_the_tasks(harness, windows):
    harness.cli("schedule", "install")

    assert harness.cli("schedule", "uninstall") == 0

    assert harness.jobs.installed() == set()


# ── reading Windows' sign-in after an update


@pytest.fixture
def registry(monkeypatch) -> dict[tuple[str, str], int]:
    """The PC's registry numbers, by key and value, with this user's identifier S-1-5-21-7."""
    numbers: dict[tuple[str, str], int] = {}
    monkeypatch.setattr(windows_only, "_dword", lambda key, value: numbers.get((key, value)))
    monkeypatch.setattr(windows_only, "_sid", lambda: "S-1-5-21-7")
    return numbers


OPT_OUT = (f"{windows_only.USER_ARSO}\\S-1-5-21-7", "OptOut")


@pytest.mark.parametrize("opt_out, signs_in", [(0, True), (1, False), (None, None)])
def test_the_users_own_choice_says_whether_windows_signs_them_back_in(registry, opt_out,
                                                                     signs_in):
    if opt_out is not None:
        registry[OPT_OUT] = opt_out

    assert windows_only.signs_back_in_after_update() is signs_in


def test_a_policy_that_turns_it_off_for_the_pc_wins(registry):
    registry[OPT_OUT] = 0
    registry[(windows_only.POLICY, "DisableAutomaticRestartSignOn")] = 1

    assert windows_only.signs_back_in_after_update() is False


@pytest.mark.windows
def test_this_pcs_own_setting_is_read():
    assert windows_only.signs_back_in_after_update() in (True, False, None)
    assert windows_only._sid().startswith("S-1-5-")


@pytest.mark.windows
def test_a_program_s_folder_goes_once_the_program_running_from_it_has_ended(tmp_path):
    own = tmp_path / "ParentRecap"
    program, runtime = own / "app", own / "runtime"
    for folder in (program, runtime):
        folder.mkdir(parents=True)
    held = program / "held.txt"
    held.write_text("")
    # Holds a file open, as Windows holds a running program's files, for a few seconds.
    running = subprocess.Popen([sys.executable, "-c",
                                f"import time; f = open({str(held)!r}); time.sleep(4)"])
    time.sleep(1)

    windows_only.remove_after_exit([program, runtime], [own], pid=running.pid)

    time.sleep(1)
    assert held.exists()  # not while it runs
    running.wait()
    deadline = time.time() + 60
    while own.exists() and time.time() < deadline:
        time.sleep(1)
    assert not own.exists()
