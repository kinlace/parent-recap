"""The Linux adapter of `family_brief.system.wake`: a server never sleeps, so there is no wake to
set or cancel, and `doctor`, `setup status` and `schedule install` say nothing of one. Callers'
tests use fake_system.FakeWake instead.

Nothing is faked but the jobs, the harness's: the adapter runs no program."""
from __future__ import annotations

import json
import subprocess

import pytest

from family_brief.system import wake, wake_linux

from fake_system import LINUX


@pytest.fixture
def server(harness, monkeypatch) -> list[list[str]]:
    """A Linux server with doctor's network checks off; records any program the wake runs."""
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["wilma"]["enabled"] = False
    harness.config["whatsapp"]["enabled"] = False
    harness.system = LINUX
    monkeypatch.setattr(wake, "_adapter", wake_linux)  # in place of the harness's fake
    ran: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **k: ran.append(cmd))
    return ran


def test_a_server_never_sleeps_so_it_has_no_wake_to_set(server):
    assert not wake.sleeps()
    assert wake.status(21, 0) == wake.WakeStatus(None, [], never_sleeps=True)
    assert wake.set(21, 0, replace=True) == "never-sleeps"
    wake.cancel()
    assert wake.manual_step(21, 0) is None and not wake.has_own_schedule()
    assert server == []


def test_doctor_has_no_wake_line(harness, server, capsys):
    harness.cli("doctor", "--skip-llm")

    assert not [line for line in capsys.readouterr().out.splitlines() if " Wake:" in line]


def test_setup_status_has_no_wake_outcome(harness, server, capsys):
    harness.cli("setup", "status")

    outcomes = json.loads(capsys.readouterr().out)["outcomes"]
    assert [o["outcome"] for o in outcomes] == ["installed", "doctor", "brief", "nightly"]


def test_schedule_install_says_nothing_of_a_wake(harness, server, capsys):
    assert harness.cli("schedule", "install") == 0

    out = capsys.readouterr().out
    assert "Brief: every day" in out and "sleep" not in out and "Wake" not in out
