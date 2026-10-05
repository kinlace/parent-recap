"""Google's client packages are only installed for the Google Calendar mode (the `google` extra).

Without them, which is every install that keeps the `.ics` attachment, the program imports, runs
the evening Brief, Weekend Picks, setup and doctor. A Household in google mode without them gets
its events as `.ics` and a doctor line that says how to install them. The packages are blocked
from import here, as if they weren't installed.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from family_brief import google_packages, ops
from test_nightly_run import ICS_FALLBACK, normal_night
from test_setup_save import ANSWERS, save, saved
from test_setup_status import mac, outcome, status  # noqa: F401 (mac is a fixture)
from test_weekend_picks import candidate

NO_GOOGLE = Path(__file__).with_name("no_google")
_spec = importlib.util.spec_from_file_location("no_google", NO_GOOGLE / "sitecustomize.py")
blocker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(blocker)


@pytest.fixture
def no_google(monkeypatch):
    for name in list(sys.modules):
        if name.split(".")[0] in blocker.BLOCKED:
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "meta_path", [blocker.Blocker(), *sys.meta_path])


def test_every_module_of_the_program_imports_without_them():
    script = ("import importlib, pkgutil, family_brief\n"
              "for m in pkgutil.walk_packages(family_brief.__path__, 'family_brief.'):\n"
              "    importlib.import_module(m.name)\n")
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60,
                            env={**os.environ, "PYTHONPATH": str(NO_GOOGLE)})

    assert result.returncode == 0, result.stderr


def test_the_evening_brief_with_ics_runs_without_them(harness, no_google):
    normal_night(harness)

    assert harness.run() == 0

    [email] = harness.sent
    [(name, _, mime)] = email.attachments
    assert (name, mime) == ("parent-recap-2026-09-27.ics", "text/calendar")


def test_google_mode_without_them_sends_the_events_as_ics(harness, no_google):
    normal_night(harness)
    harness.config["google_calendar"] = {"mode": "google"}
    harness.authorize_google_calendar()

    assert harness.run() == 0

    [email] = harness.sent
    assert email.text.endswith(ICS_FALLBACK)
    assert email.attachment(".ics")
    assert harness.calendar.inserted == []


def test_weekend_picks_without_them_still_go_out(harness, monkeypatch, no_google):
    from family_brief import weekend_pipeline
    from family_brief.config import Config
    harness.config["weekend_events"] = {"enabled": True}
    harness.authorize_google_calendar()  # left from when the Household used Google Calendar
    harness.model_reply = {"picks": [{"ext_id": "le-1", "rank": 1, "why": "puppets"}]}
    monkeypatch.setattr(weekend_pipeline.we, "collect", lambda cfg: [candidate()])

    assert weekend_pipeline.run(Config.model_validate(harness.config)) == 0

    [email] = harness.sent
    assert "Puppet theatre" in email.text
    assert harness.calendar.inserted == []


def test_setup_saves_and_reports_without_them(harness, mac, no_google, capsys):
    harness.ship_pilot_form()

    assert save(harness, ANSWERS) == 0
    assert saved(harness)["kids"]
    capsys.readouterr()

    _, report = status(harness, capsys)

    assert outcome(report, "doctor")["ok"] is False  # no App Password, but every check ran


def doctor(harness, monkeypatch, capsys) -> str:
    monkeypatch.setattr(ops, "launchctl_loaded", lambda: set())
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["wilma"]["enabled"] = False
    harness.config["whatsapp"]["enabled"] = False
    harness.cli("doctor", "--skip-llm")
    return capsys.readouterr().out


def calendar_line(out: str) -> str:
    [line] = [line for line in out.splitlines() if "Calendar" in line]
    return line


def test_doctor_in_ics_mode_runs_without_them(harness, monkeypatch, no_google, capsys):
    line = calendar_line(doctor(harness, monkeypatch, capsys))

    assert "ics mode" in line and "❌" not in line


def test_doctor_says_google_mode_needs_them_and_how_to_install_them(harness, monkeypatch, no_google,
                                                                     capsys):
    harness.config["google_calendar"] = {"mode": "google"}
    harness.authorize_google_calendar()

    line = calendar_line(doctor(harness, monkeypatch, capsys))

    assert "❌" in line
    assert "Google's packages" in line and "install.sh" in line


# ── What install.sh asks: does this config need the `google` extra?


def wanted(tmp_path: Path, config: str | None) -> bool:
    path = tmp_path / "config.yaml"
    if config is not None:
        path.write_text(config)
    return google_packages.wanted(path)


def test_the_extra_is_wanted_only_for_google_mode(tmp_path):
    assert wanted(tmp_path, "google_calendar:\n  mode: google\n")
    assert not wanted(tmp_path, "google_calendar:\n  mode: ics\n")
    assert not wanted(tmp_path, "kids: []\n")  # ics is the default
    assert not wanted(tmp_path, None)          # not set up yet
    assert not wanted(tmp_path, "kids: [\n")   # unreadable: setup or doctor says so


def test_install_sh_asks_through_the_module_and_exits_0_for_google_mode(tmp_path):
    (tmp_path / ".family").mkdir()
    (tmp_path / ".family" / "config.yaml").write_text("google_calendar: {mode: google}\n")
    run = lambda: subprocess.run([sys.executable, "-m", "family_brief.google_packages", "wanted"],
                                 env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
                                 capture_output=True, text=True, timeout=60)

    assert run().returncode == 0
    (tmp_path / ".family" / "config.yaml").write_text("google_calendar: {mode: ics}\n")
    assert run().returncode == 1
