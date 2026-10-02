"""`family-brief doctor` and `discover` speak English, whatever language the Brief is in."""
from __future__ import annotations

import re
import sys

from family_brief import __main__ as cli, ops
from family_brief.collectors import whatsapp, wilma

HAN = re.compile(r"[　-〿一-鿿＀-￯]")


def test_doctor_reports_every_check_in_english(harness, monkeypatch, capsys):
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["wilma"]["enabled"] = False
    harness.config["whatsapp"]["enabled"] = False
    monkeypatch.setattr(ops, "_launchctl_loaded", lambda: set())

    code = harness.cli("doctor", "--skip-llm")

    out = capsys.readouterr().out
    assert code == 1  # no Gmail App Password in the Keychain
    assert "Config file" in out and "2 kids" in out
    assert "No App Password in the Keychain for parent@example.com" in out
    assert "family-brief schedule install" in out
    assert not HAN.findall(out)


def test_doctor_names_an_unreadable_config_in_english(harness, capsys):
    harness.config = {"kids": "not a list"}

    assert harness.cli("doctor", "--skip-llm") == 1

    out = capsys.readouterr().out
    assert "Config file" in out
    assert not HAN.findall(out)


def test_doctor_in_a_bg_job_says_which_python_needs_whatsapp_access(harness, monkeypatch, capsys):
    monkeypatch.setenv(ops.BG_ENV, "1")
    monkeypatch.setattr(whatsapp, "check_access", lambda: whatsapp.NO_ACCESS)

    assert harness.cli("doctor", "--whatsapp-only") == 1

    out = capsys.readouterr().out
    assert "App Management" in out
    assert not HAN.findall(out)


def test_discover_without_whatsapp_access_points_to_bg(harness, monkeypatch, capsys):
    monkeypatch.delenv(ops.BG_ENV, raising=False)
    monkeypatch.setattr(whatsapp, "check_access", lambda: whatsapp.NO_ACCESS)

    assert harness.cli("discover", "whatsapp-chats") == 1

    out = capsys.readouterr().out
    assert "family-brief bg discover whatsapp-chats" in out
    assert not HAN.findall(out)


def test_discover_wilma_students_runs_before_any_config_exists(tmp_path, monkeypatch, capsys):
    # Setup signs in to Wilma before the household step, to prefill the Kids from what Wilma lists.
    monkeypatch.setattr(wilma, "_run_or_log", lambda args: {"kids": [{"name": "Aino Virtanen"}]})
    monkeypatch.setattr(sys, "argv", ["family-brief", "-c", str(tmp_path / "missing.yaml"),
                                      "discover", "wilma-students"])

    assert cli.main() == 0

    assert "Aino Virtanen" in capsys.readouterr().out


def test_discover_wilma_students_says_when_not_signed_in(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(wilma, "_run_or_log", lambda args: None)
    monkeypatch.setattr(sys, "argv", ["family-brief", "-c", str(tmp_path / "missing.yaml"),
                                      "discover", "wilma-students"])

    assert cli.main() == 1

    assert "not signed in to wilma" in capsys.readouterr().out
