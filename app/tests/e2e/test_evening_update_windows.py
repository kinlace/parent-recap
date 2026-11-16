"""The evening run on a Windows PC updates Parent Recap by itself (ADR 0052, #994), as on a Mac
(ADR 0047, ADR 0051): once the evening task's run has sent the Brief, it installs a release this PC
has seen on `stable` a day, leaves a younger one, a release that needs the family and one that
changes nothing the Household uses, and the next Brief says what it did. The update runs from
pythonw.exe, so every program it starts is started without a console window. An update that fails
or that the system stops never fails the evening run, leaves the evening task and the Linked
Device's as they were, and `doctor` shows the step that stopped it and the error.

Driven through main() as the evening task starts it, with test_update_windows.py's installed PC
(`app` a junction on Windows, signatures checked by the system's own `ssh-keygen` against a test
key) and test_evening_update.py's clock. Runs on macOS, Linux and on Windows' CI (`windows_too`)."""
from __future__ import annotations

import subprocess
import sys
from datetime import timedelta
from typing import Any

import pytest

from conftest import NOW, REAL_RUN
from family_brief import update, versions
from family_brief.brief_text import TEXT
from family_brief.system import jobs
from test_doctor import nothing_loaded, updates_line  # noqa: F401  the fixture
from test_evening_update import DAY, at, exit_recorded, last_run
from test_evening_update_brief import CHANGES, UPDATE_MARK, a_message, quiet, top, updates_off
from test_update import ASKS, INSTALLED, needing_family
from test_update_skips import KONNECT_ONLY
from test_update_windows import BLOCKED, PC, github, keys, pc, release, stopped_at_the_link, update_log  # noqa: F401

pytestmark = pytest.mark.windows_too

NO_WINDOW = 0x08000000  # Windows' CREATE_NO_WINDOW


@pytest.fixture
def evening(harness, pc, monkeypatch):  # noqa: F811
    """The PC's Household with its evening task starting the run, whose run last evening went."""
    monkeypatch.setenv(jobs.SCHEDULED, "1")
    last_run(harness, 24)
    quiet(harness)
    return harness


def evening_on(h, day: int, at_time: timedelta = timedelta()) -> None:
    with at(NOW + day * DAY + at_time):
        assert h.run() == 0
    assert exit_recorded(h) == 0


def installed_on_evening_1(h, github, keys, pc) -> None:  # noqa: F811
    """0.20.0 is out on NOW's evening and installed by the next evening's run, after its Brief."""
    release(github, keys, "0.20.0")
    evening_on(h, 0)
    evening_on(h, 1)
    assert pc.version() == "0.20.0"


# ---------------------------------------------------------------- a release a day old

def test_a_release_a_day_old_is_installed_after_the_brief_and_a_younger_one_is_left(
        evening, github, keys, pc):  # noqa: F811
    release(github, keys, "0.20.0")
    a_message(evening, 0)
    tasks = pc.tasks()

    evening_on(evening, 0)  # tonight: the release is new to this PC, so it's left alone

    assert len(evening.sent) == 1
    assert pc.version() == INSTALLED and pc.installs == []
    assert update.state(pc.folder)["seen"]["version"] == "0.20.0"

    # The next evening's run starts a moment sooner after the Brief's time than tonight's look.
    evening_on(evening, 1, -timedelta(seconds=30))
    assert pc.version() == "0.20.0"
    assert versions.version(versions.previous(pc.folder)) == INSTALLED
    # The tasks are as they were: they start `app`, which now points at 0.20.0, and the Linked
    # Device's runs again, having been stopped for the switch.
    assert pc.tasks() == tasks
    assert pc.linked_device_at_points and not any(pc.linked_device_at_points)


def test_the_update_starts_only_once_the_brief_is_sent(evening, github, keys, pc, monkeypatch):  # noqa: F811
    release(github, keys, "0.20.0")
    evening_on(evening, 0)
    a_message(evening, 1)
    sent_at_install: list[int] = []
    pc.at_install = lambda: sent_at_install.append(len(evening.sent))

    evening_on(evening, 1)

    assert sent_at_install == [1] and pc.version() == "0.20.0"


# ---------------------------------------------------------------- what the next Brief says

@pytest.mark.parametrize("language", ["en", "zh", "fi"])
def test_the_next_brief_says_once_that_parent_recap_updated_and_to_which_version(
        evening, github, keys, pc, language):  # noqa: F811
    t = TEXT[language]
    evening.config["summary_language"] = language
    installed_on_evening_1(evening, github, keys, pc)
    assert evening.sent == []  # quiet evenings: the update's line waits for a Brief

    a_message(evening, 2)
    evening_on(evening, 2)
    a_message(evening, 3)
    evening_on(evening, 3)

    first, second = evening.sent
    assert top(first.text) == [t.updated.format(version="0.20.0", link=CHANGES.format("0.20.0")),
                               t.updates_itself]
    assert UPDATE_MARK not in second.text


@pytest.mark.parametrize("language", ["en", "zh", "fi"])
def test_the_next_brief_says_once_that_the_update_failed_and_the_version_before_runs(
        evening, github, keys, pc, language):  # noqa: F811
    t = TEXT[language]
    evening.config["summary_language"] = language
    release(github, keys, "0.20.0")
    pc.fails["0.20.0"] = ["Calendar"]
    evening_on(evening, 0)
    evening_on(evening, 1)  # installs it, its doctor fails, and it switches back
    assert pc.version() == INSTALLED and pc.installs

    for day in (2, 3, 4):  # each evening after, it isn't tried again
        a_message(evening, day)
        evening_on(evening, day)

    assert top(evening.sent[0].text) == [t.update_failed.format(version="0.20.0", running=INSTALLED)]
    assert all(UPDATE_MARK not in later.text for later in evening.sent[1:])
    assert len(pc.installs) == 1


# ---------------------------------------------------------------- updates off, the family's, skipped

def test_with_updates_off_the_run_makes_the_one_request_and_the_brief_says_once_a_release_is_out(
        evening, github, keys, pc):  # noqa: F811
    t = TEXT["en"]
    evening.config["summary_language"] = "en"
    updates_off(evening)
    release(github, keys, "0.20.0")

    evening_on(evening, 0)

    assert [r["url"] for r in github.requests] == [update.STABLE_RELEASE]
    assert pc.installs == [] and evening.commands == []
    assert not list((pc.folder / update.UPDATE).glob(f"{update.DOWNLOAD}*"))
    for day in (1, 2):
        a_message(evening, day)
        evening_on(evening, day)
    first, second = evening.sent
    assert top(first.text) == [t.update_available.format(version="0.20.0")]
    assert UPDATE_MARK not in second.text
    assert pc.version() == INSTALLED and pc.installs == []


def test_a_release_that_needs_the_family_waits_for_them_and_the_brief_and_doctor_say_how_to_start_it(
        evening, nothing_loaded, github, keys, pc, capsys):  # noqa: F811
    evening.config["summary_language"] = "en"
    release(github, keys, "0.20.0", needing_family("0.20.0"))
    evening_on(evening, 0)
    evening_on(evening, 1)  # downloads it once to read that it needs the family, and leaves it

    a_message(evening, 2)
    evening_on(evening, 2)

    assert pc.version() == INSTALLED and pc.installs == []
    [email] = evening.sent
    assert top(email.text) == [TEXT["en"].update_waiting.format(version="0.20.0", asks=ASKS["en"])]
    line = updates_line(evening, capsys)
    assert line.endswith(f"an update is waiting: version 0.20.0 needs you at the computer "
                         f"({ASKS['en']}), so it isn't installed by itself (parent-recap update "
                         "installs it)")


def test_a_release_that_changes_nothing_the_household_uses_is_skipped(evening, github, keys, pc):  # noqa: F811
    github.parts = KONNECT_ONLY  # a Household without Konnect
    release(github, keys, "0.20.0")

    for day in (0, 1, 2):
        a_message(evening, day)
        evening_on(evening, day)

    assert pc.version() == INSTALLED and pc.installs == []
    assert all(r["url"] == update.STABLE_RELEASE for r in github.requests)
    assert update.state(pc.folder)["skipped"] == "0.20.0"
    assert all(UPDATE_MARK not in brief.text for brief in evening.sent)


# ---------------------------------------------------------------- no console window

def test_every_program_the_update_starts_opens_no_console_window(evening, github, keys, pc, monkeypatch):  # noqa: F811
    # Off Windows the flag isn't there, and the update passes 0: here it is Windows'.
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", NO_WINDOW, raising=False)
    release(github, keys, "0.20.0")
    pc.fails["0.20.0"] = ["Calendar"]  # so the switch back's steps start programs too
    evening_on(evening, 0)
    flags: list[tuple[str, Any]] = []

    def noting(cmd: list[str], prog: str, **k: Any) -> subprocess.CompletedProcess | None:
        flags.append((cmd[0], k.get("creationflags")))
        if prog == "ssh-keygen" and sys.platform != "win32":  # the flag is Windows' alone
            return REAL_RUN(cmd, **{**k, "creationflags": 0})
        return None
    evening.processes.answers.insert(0, noting)

    evening_on(evening, 1)

    assert pc.installs and pc.version() == INSTALLED  # it installed, then switched back
    started = {program for program, _ in flags}
    assert any(p.endswith("ssh-keygen") or p.endswith("ssh-keygen.exe") for p in started)
    assert pc.python() in started
    assert [f for f in flags if f[1] != NO_WINDOW] == []


# ---------------------------------------------------------------- an update that fails

def installer_stopped(github, pc, monkeypatch) -> None:  # noqa: F811
    pc.installer_raises = BLOCKED


def doctor_stopped(github, pc, monkeypatch) -> None:  # noqa: F811
    pc.doctor_raises["0.20.0"] = BLOCKED


def check_fails(github, pc, monkeypatch) -> None:  # noqa: F811
    pc.fails["0.20.0"] = ["Wilma (Mia)"]


def installer_ends_after_switch(github, pc, monkeypatch) -> None:  # noqa: F811
    pc.ends_after_switch = 1


def crashes(github, pc, monkeypatch) -> None:  # noqa: F811
    def crash(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("disk full")
    monkeypatch.setattr(update, "_update_to", crash)


@pytest.mark.parametrize("fail", [installer_stopped, stopped_at_the_link, doctor_stopped, check_fails,
                                  installer_ends_after_switch, crashes],
                         ids=["installer", "link", "doctor", "check", "unfinished", "crash"])
def test_an_update_that_fails_or_is_stopped_never_fails_the_evening_run_and_leaves_the_tasks(
        evening, github, keys, pc, monkeypatch, fail):  # noqa: F811
    release(github, keys, "0.20.0")
    evening_on(evening, 0)
    a_message(evening, 1)
    tasks = pc.tasks()
    fail(github, pc, monkeypatch)

    evening_on(evening, 1)  # exit 0, recorded as 0

    assert len(evening.sent) == 1
    assert pc.version() == INSTALLED
    assert pc.tasks() == tasks
    assert evening.jobs.always_running


# ---------------------------------------------------------------- doctor shows the last failure

@pytest.mark.parametrize("fail, step, says", [
    (installer_stopped, "install",
     f"0.20.0: windows_install.py couldn't be started (OSError: {BLOCKED}); app points at {INSTALLED}"),
    (stopped_at_the_link, "install",
     "0.20.0: windows_install.py ended with exit status 1; app points at 0.19.0; it said: Parent Recap "
     "wasn't installed. Parent Recap couldn't switch {app} to the new version (OSError: " f"{BLOCKED})."),
    (doctor_stopped, "check", f"0.20.0's doctor didn't run (OSError: {BLOCKED})"),
    (check_fails, "check", "0.20.0's doctor fails 1 check that version 0.19.0 passed"),
], ids=["installer", "link", "doctor", "check"])
def test_doctor_shows_the_last_failed_update_s_step_and_error_and_nothing_of_the_household_s(
        evening, nothing_loaded, github, keys, pc, monkeypatch, capsys, fail, step, says):  # noqa: F811
    release(github, keys, "0.20.0")
    evening_on(evening, 0)
    fail(github, pc, monkeypatch)
    evening_on(evening, 1)

    line = updates_line(evening, capsys)

    assert line.startswith(f"✅ Updates: version {INSTALLED}; updates are on")
    said = says.format(app=pc.folder / "app")
    assert line.endswith(f"failed at its {step} step: {said}"), line
    assert "; the last update, to version 0.20.0 on 2026-" in line
    assert "Mia" not in line  # a check's item names a kid: only the update log has it
    if fail is not check_fails:  # the system's words, as the update log has them
        assert str(BLOCKED) in said and str(BLOCKED) in update_log(pc)
    state = update.state(pc.folder)["last_failure"]
    assert (state["version"], state["step"], state["error"]) == ("0.20.0", step, said)


def test_once_an_update_installs_doctor_no_longer_shows_the_failure_before(
        evening, nothing_loaded, github, keys, pc, capsys):  # noqa: F811
    release(github, keys, "0.20.0")
    evening_on(evening, 0)
    pc.installer_raises = BLOCKED
    evening_on(evening, 1)
    assert "failed at its install step" in updates_line(evening, capsys)

    pc.installer_raises = None
    release(github, keys, "0.20.1")
    evening_on(evening, 2)
    evening_on(evening, 3)

    assert pc.version() == "0.20.1"
    assert "failed at" not in updates_line(evening, capsys)
    assert update.state(pc.folder)["last_failure"] is None
