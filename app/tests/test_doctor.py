"""`parent-recap doctor` and `discover` speak English, whatever language the Brief is in."""
from __future__ import annotations

import os
import plistlib
import re
import shutil
import sys
from pathlib import Path

import pytest

from family_brief import __main__ as cli, ops, setup_steps, whatsapp_python
from family_brief.collectors import whatsapp, wilma
from test_setup_whatsapp import fake_mac

HAN = re.compile(r"[　-〿一-鿿＀-￯]")


def test_doctor_reports_every_check_in_english(harness, monkeypatch, capsys):
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["wilma"]["enabled"] = False
    harness.config["whatsapp"]["enabled"] = False
    monkeypatch.setattr(ops, "launchctl_loaded", lambda: set())

    code = harness.cli("doctor", "--skip-llm")

    out = capsys.readouterr().out
    assert code == 1  # no Gmail App Password in the Keychain
    assert "Config file" in out and "2 kids" in out
    assert "No App Password in the Keychain for parent@example.com" in out
    assert "parent-recap schedule install" in out
    assert not HAN.findall(out)


@pytest.fixture
def claude_without_token(harness, monkeypatch):
    """Claude Code installed and answering, with no claude-oauth-token in the Keychain."""
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["wilma"]["enabled"] = False
    harness.config["whatsapp"]["enabled"] = False
    monkeypatch.setattr(ops, "launchctl_loaded", lambda: set())
    which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda cmd, *a, **k: f"/usr/local/bin/{cmd}"
                        if cmd == "claude" else which(cmd, *a, **k))
    harness.model_reply = "OK"


def test_doctor_fails_claude_without_a_token_for_the_evening_brief(harness, claude_without_token,
                                                                  capsys):
    assert harness.cli("doctor") == 1

    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if "Claude:" in l)
    assert line.startswith(ops.FAIL)
    assert "no claude-oauth-token" in line and "parent-recap setup claude" in line
    assert "tmux" not in line


def test_doctor_in_tmux_says_to_store_the_token_outside_it(harness, claude_without_token,
                                                          monkeypatch, capsys):
    monkeypatch.setenv("TMUX", "/private/tmp/tmux-501/default,1,0")

    harness.cli("doctor")

    line = next(l for l in capsys.readouterr().out.splitlines() if "Claude:" in l)
    assert line.startswith(ops.FAIL) and setup_steps.PLAIN_TERMINAL in line


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
    assert os.path.realpath(sys.executable) in out and "add it to Full Disk Access" in out
    assert "App Management" not in out
    assert not HAN.findall(out)


# ── Full Disk Access, checked through bg on the fake Mac without a question from macOS (ADR 0012)


@pytest.fixture
def mac_with_whatsapp(harness, monkeypatch):
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["wilma"]["enabled"] = False
    monkeypatch.delenv(ops.BG_ENV, raising=False)
    monkeypatch.setattr(ops, "launchctl_loaded", lambda: {ops.JOB_DAILY})
    mac = fake_mac(harness, monkeypatch)
    mac.install_whatsapp([("3B parents", 1, False), ("Leo piano", 2, False)])
    return mac


def whatsapp_lines(out: str) -> list[str]:
    return [l for l in out.splitlines() if "WhatsApp:" in l]


@pytest.mark.parametrize("command", [["doctor", "--skip-llm"], ["bg", "doctor", "--skip-llm"]])
def test_doctor_fails_without_full_disk_access_and_macos_never_asks(harness, mac_with_whatsapp,
                                                                    capsys, command):
    mac = mac_with_whatsapp
    mac.full_disk_access_after = 10_000
    mac.allow = "allow"  # an Allow click would let one read through

    harness.cli(*command)

    [line] = whatsapp_lines(capsys.readouterr().out)
    assert line.lstrip().startswith(ops.FAIL.strip())
    assert os.path.realpath(sys.executable) in line and "add it to Full Disk Access" in line
    assert mac.prompts == 0 and mac.reads == []


@pytest.mark.parametrize("command", [["doctor", "--skip-llm"], ["bg", "doctor", "--skip-llm"]])
def test_doctor_reads_whatsapp_with_full_disk_access_and_macos_never_asks(harness,
                                                                          mac_with_whatsapp,
                                                                          capsys, command):
    mac = mac_with_whatsapp

    harness.cli(*command)

    [line] = whatsapp_lines(capsys.readouterr().out)
    assert line.lstrip().startswith(ops.OK.strip()) and "all 2 configured groups found" in line
    assert mac.prompts == 0 and mac.reads and all(mac.reads)


# ── the Python given Full Disk Access, against the evening job's (ADR 0011, 0012)


@pytest.fixture
def evening_job(harness, monkeypatch):
    """Parent Recap's own Python under ~/ParentRecap/runtime and the evening job's venv Python
    linking to it, as install.sh and schedule install lay them out. Returns the venv's link."""
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["wilma"]["enabled"] = False
    monkeypatch.setattr(ops, "launchctl_loaded", lambda: {ops.JOB_DAILY})
    monkeypatch.setattr(ops, "LAUNCH_AGENTS", harness.home / "Library" / "LaunchAgents")
    venv_python = harness.home / "ParentRecap" / "app" / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    ops.LAUNCH_AGENTS.mkdir(parents=True)
    (ops.LAUNCH_AGENTS / f"{ops.JOB_DAILY}.plist").write_bytes(plistlib.dumps(
        {"Label": ops.JOB_DAILY, "ProgramArguments": [str(venv_python), "-m", "family_brief", "run"]}))
    return venv_python


def pinned_python(harness, version: str) -> str:
    python = harness.home / "ParentRecap" / "runtime" / f"python-{version}" / "bin" / "python3.12"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.touch()
    return os.path.realpath(python)


def allowed_python_lines(out: str) -> list[str]:
    return [l for l in out.splitlines() if "WhatsApp:" in l and "evening job's Python" in l]


def test_doctor_is_quiet_when_the_python_allowed_is_the_evening_jobs(harness, evening_job, capsys):
    python = pinned_python(harness, "3.12.7")
    evening_job.symlink_to(python)
    whatsapp_python.record(python)

    harness.cli("doctor", "--skip-llm")

    assert allowed_python_lines(capsys.readouterr().out) == []


def test_doctor_says_when_the_python_allowed_is_no_longer_the_evening_jobs(harness, evening_job,
                                                                         capsys):
    whatsapp_python.record(pinned_python(harness, "3.12.6"))
    python = pinned_python(harness, "3.12.7")
    evening_job.symlink_to(python)

    harness.cli("doctor", "--skip-llm")

    [line] = allowed_python_lines(capsys.readouterr().out)
    assert line.startswith(ops.WARN.strip())
    assert "no longer the evening job's Python" in line
    assert f"add {python} to Full Disk Access" in line
    assert "parent-recap full-disk-access" in line
    assert not HAN.findall(line)


def test_doctor_fails_when_the_evening_jobs_python_points_at_nothing(harness, evening_job, capsys):
    whatsapp_python.record(pinned_python(harness, "3.12.6"))
    evening_job.symlink_to(harness.home / "ParentRecap" / "runtime" / "python-3.12.5" / "bin" / "python3.12")

    harness.cli("doctor", "--skip-llm")

    [line] = allowed_python_lines(capsys.readouterr().out)
    assert line.startswith(ops.FAIL)
    assert f"{evening_job} points at nothing" in line
    assert "no longer the evening job's Python" in line
    assert "parent-recap schedule install" in line
    assert f"add {os.path.realpath(sys.executable)} to Full Disk Access" in line


def test_doctor_does_not_compare_an_install_from_before_the_record(harness, evening_job, capsys):
    evening_job.symlink_to(pinned_python(harness, "3.12.7"))

    harness.cli("doctor", "--skip-llm")

    assert not whatsapp_python.path().exists()
    assert allowed_python_lines(capsys.readouterr().out) == []


def test_discover_without_whatsapp_access_points_to_bg(harness, monkeypatch, capsys):
    monkeypatch.delenv(ops.BG_ENV, raising=False)
    monkeypatch.setattr(whatsapp, "check_access", lambda: whatsapp.NO_ACCESS)

    assert harness.cli("discover", "whatsapp-chats") == 1

    out = capsys.readouterr().out
    assert "parent-recap bg discover whatsapp-chats" in out
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
