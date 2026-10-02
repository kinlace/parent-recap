"""`family-brief setup status` reports the outcomes that decide whether setup is done.

macOS's edges are faked: `launchctl` (which jobs are loaded), `pmset` (sleep and the wake
schedule), the Keychain, Gmail's IMAP server and the `claude` test call. The program folder,
config, state and archive are real files in a temporary HOME. Assertions are on what the
assistant reads: one JSON line, or the readable lines with --text."""
from __future__ import annotations

import imaplib
import json
import shutil
from typing import Any

import pytest
from conftest import msg

from family_brief import install_record, ops

APP_PASSWORD = "abcdabcdabcdabcd"
CLAUDE_TOKEN = "sk-ant-oat01-secret"
SLEEPS = "AC Power:\n sleep                1\n"
NEVER_SLEEPS = "AC Power:\n sleep                0\nBattery Power:\n sleep                0\n"
OUR_WAKE = "wakepoweron at 8:55PM every day"


class CountingImap:
    """Gmail's IMAP server as doctor sees it: a sign-in and one search."""

    def __call__(self, *_a: Any, **_k: Any) -> "CountingImap":
        return self

    def login(self, *_a: Any) -> None:
        pass

    def select(self, *_a: Any, **_k: Any) -> None:
        pass

    def search(self, *_a: Any) -> tuple[str, list[bytes]]:
        return "OK", [b"1 2 3"]

    def logout(self) -> None:
        pass


class FakeMac:
    """The jobs launchctl has loaded and what pmset says about sleep and the wake schedule."""

    def __init__(self) -> None:
        self.loaded: set[str] = set()
        self.sleep = SLEEPS
        self.repeating: list[str] = []

    def pmset(self, *args: str) -> str:
        if args == ("-g", "custom"):
            return self.sleep
        return "Repeating power events:\n" + "".join(f"  {l}\n" for l in self.repeating) \
            if self.repeating else ""


@pytest.fixture
def mac(harness, monkeypatch) -> FakeMac:
    # Sources doctor would reach on the real Mac are off; Gmail and Claude are faked.
    harness.config["wilma"]["enabled"] = False
    harness.config["whatsapp"]["enabled"] = False
    del harness.config["kids"][0]["myclub_ical_url"]
    fake = FakeMac()
    monkeypatch.setattr(ops, "launchctl_loaded", lambda: set(fake.loaded))
    monkeypatch.setattr(ops, "_pmset", fake.pmset)
    return fake


def status(harness, capsys, *args: str) -> tuple[int, dict[str, Any]]:
    code = harness.cli("setup", "status", *args)
    out = capsys.readouterr().out
    assert len(out.splitlines()) == 1, out
    return code, json.loads(out)


def outcome(report: dict[str, Any], name: str) -> dict[str, Any]:
    return next(o for o in report["outcomes"] if o["outcome"] == name)


def install_program(harness) -> None:
    app = harness.home / "FamilyBrief" / "app"
    (app / ".venv" / "bin").mkdir(parents=True)
    (app / ".venv" / "bin" / "family-brief").write_text("#!/bin/sh\n")
    (app / "pyproject.toml").write_text('[project]\nname = "family-brief"\n')
    (app / "VERSION").write_text("0.5.0\n")
    install_record.add("program", str(app))


# ── 1. The program is installed


def test_the_program_is_installed_when_install_sh_has_put_it_in_place(harness, mac, capsys):
    install_program(harness)

    _, report = status(harness, capsys)

    installed = outcome(report, "installed")
    assert installed["ok"] is True
    assert "0.5.0" in installed["reason"] and "FamilyBrief/app" in installed["reason"]


def test_the_program_isnt_installed_before_install_sh_has_run(harness, mac, capsys):
    code, report = status(harness, capsys)

    installed = outcome(report, "installed")
    assert installed["ok"] is False
    assert "install.sh" in installed["reason"]
    assert code == 1 and report["result"] == "not-done"


# ── 2. The health check is all OK


@pytest.fixture
def healthy(harness, mac, monkeypatch) -> None:
    """Everything doctor checks works: Gmail signs in, Claude answers, the nightly job is loaded."""
    harness.keychain["gmail-imap-parent@example.com"] = APP_PASSWORD
    harness.keychain["claude-oauth-token"] = CLAUDE_TOKEN
    monkeypatch.setattr(imaplib, "IMAP4_SSL", CountingImap())
    which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda cmd, *a, **k: f"/usr/local/bin/{cmd}"
                        if cmd == "claude" else which(cmd, *a, **k))
    harness.model_reply = "OK"
    mac.loaded.add(ops.JOB_DAILY)


def test_the_health_check_is_all_ok_when_every_doctor_check_is(harness, healthy, capsys):
    _, report = status(harness, capsys)

    doctor = outcome(report, "doctor")
    assert doctor["ok"] is True, doctor
    assert "checks OK" in doctor["reason"]


def test_the_health_check_names_the_checks_that_arent_ok(harness, healthy, capsys):
    del harness.keychain["gmail-imap-parent@example.com"]

    _, report = status(harness, capsys)

    doctor = outcome(report, "doctor")
    assert doctor["ok"] is False
    assert "Gmail" in doctor["reason"] and "family-brief doctor" in doctor["reason"]
    assert "Claude" not in doctor["reason"]


# ── 3. The first Brief reached every Recipient


MESSAGE_TEXT = "Bring the signed trip form to school on Tuesday"


def send_a_brief(harness) -> None:
    harness.sources["gmail"] = [msg("gmail", "g1", "2026-09-27T09:00:00+03:00", MESSAGE_TEXT,
                                    sender="teacher@kilo.example.fi", subject="Trip")]
    harness.model_reply = {"per_kid": [], "calendar_events": [], "message_digest": MESSAGE_TEXT}
    assert harness.run() == 0


def test_the_first_brief_reached_every_recipient_once_it_was_sent_to_them(harness, mac, capsys):
    send_a_brief(harness)
    capsys.readouterr()

    _, report = status(harness, capsys)

    brief = outcome(report, "brief")
    assert brief["ok"] is True
    assert "parent@example.com" in brief["reason"] and "partner@example.com" in brief["reason"]


def test_no_brief_has_reached_the_recipients_before_the_first_one(harness, mac, capsys):
    _, report = status(harness, capsys)

    brief = outcome(report, "brief")
    assert brief["ok"] is False
    assert "parent@example.com" in brief["reason"] and "partner@example.com" in brief["reason"]


def test_a_brief_that_couldnt_be_sent_hasnt_reached_anyone(harness, mac, capsys):
    harness.email_error = OSError("SMTP connection refused")
    send_a_brief(harness)
    capsys.readouterr()

    _, report = status(harness, capsys)

    assert outcome(report, "brief")["ok"] is False


def test_a_recipient_added_after_the_first_brief_hasnt_had_one_yet(harness, mac, capsys):
    harness.config["email"]["to"] = ["parent@example.com"]
    send_a_brief(harness)
    capsys.readouterr()
    harness.config["email"]["to"] = ["parent@example.com",
                                     {"address": "partner@example.com", "language": "fi"}]

    _, report = status(harness, capsys)

    brief = outcome(report, "brief")
    assert brief["ok"] is False
    assert "partner@example.com" in brief["reason"]
    assert "parent@example.com" not in brief["reason"]


def test_a_brief_sent_only_by_imessage_reaches_its_recipients(harness, mac, capsys):
    harness.config["email"]["enabled"] = False
    harness.config["imessage"] = {"enabled": True, "recipients": ["+358401234567"]}
    send_a_brief(harness)
    capsys.readouterr()

    _, report = status(harness, capsys)

    brief = outcome(report, "brief")
    assert brief["ok"] is True and "+358401234567" in brief["reason"]


def test_a_brief_with_no_recipients_reaches_nobody(harness, mac, capsys):
    harness.config["email"]["to"] = []

    _, report = status(harness, capsys)

    assert outcome(report, "brief")["ok"] is False


# ── 4. The nightly job is loaded


def test_the_nightly_job_is_loaded_once_the_schedule_is_installed(harness, mac, capsys):
    mac.loaded.add(ops.JOB_DAILY)

    _, report = status(harness, capsys)

    nightly = outcome(report, "nightly")
    assert nightly["ok"] is True
    assert "21:00" in nightly["reason"]


def test_the_nightly_job_isnt_loaded_before_the_schedule_is_installed(harness, mac, capsys):
    mac.loaded.add(ops.JOB_WEEKEND)  # Weekend Picks' job isn't the nightly one

    _, report = status(harness, capsys)

    nightly = outcome(report, "nightly")
    assert nightly["ok"] is False
    assert "family-brief schedule install" in nightly["reason"]


# ── 5. The wake schedule is set, or the Mac never sleeps


def test_the_wake_schedule_is_set_when_the_mac_wakes_before_the_nightly_job(harness, mac, capsys):
    mac.repeating = [OUR_WAKE]

    _, report = status(harness, capsys)

    wake = outcome(report, "wake")
    assert wake["ok"] is True
    assert "20:55" in wake["reason"]


def test_a_mac_that_never_sleeps_needs_no_wake_schedule(harness, mac, capsys):
    mac.sleep = NEVER_SLEEPS

    _, report = status(harness, capsys)

    wake = outcome(report, "wake")
    assert wake["ok"] is True
    assert "never sleeps" in wake["reason"]


@pytest.mark.parametrize("repeating", [[], ["wakepoweron at 7:00AM weekdays"]])
def test_the_wake_schedule_isnt_set_when_the_mac_sleeps_through_the_nightly_job(
        harness, mac, capsys, repeating):
    mac.repeating = repeating

    _, report = status(harness, capsys)

    wake = outcome(report, "wake")
    assert wake["ok"] is False
    assert "20:55" in wake["reason"]
    # Without the flag, schedule install only warns about the other schedule again.
    assert ("--replace-wake" in wake["reason"]) == bool(repeating)


# ── The whole checklist


@pytest.fixture
def set_up(harness, healthy, mac) -> None:
    """Setup's end: installed, healthy, the first Brief sent, the nightly job and the wake set."""
    install_program(harness)
    mac.repeating = [OUR_WAKE]
    send_a_brief(harness)
    harness.model_reply = "OK"  # for doctor's test call


def test_setup_is_done_when_all_five_outcomes_are_true(harness, set_up, capsys):
    capsys.readouterr()

    code, report = status(harness, capsys)

    assert code == 0 and report["result"] == "done"
    assert [o["outcome"] for o in report["outcomes"]] == \
        ["installed", "doctor", "brief", "nightly", "wake"]
    assert all(o["ok"] and o["reason"] for o in report["outcomes"])


def test_the_readable_form_has_a_short_line_per_outcome(harness, set_up, capsys):
    capsys.readouterr()
    code = harness.cli("setup", "status", "--text")
    lines = capsys.readouterr().out.splitlines()

    assert code == 0
    assert len(lines) == 5 and all(line.startswith(ops.OK) for line in lines)
    assert any("20:55" in line for line in lines)


def test_the_readable_form_marks_what_isnt_done(harness, mac, capsys):
    assert harness.cli("setup", "status", "--text") == 1

    lines = capsys.readouterr().out.splitlines()
    nightly = next(line for line in lines if ops.JOB_DAILY in line)
    assert nightly.startswith(ops.FAIL) and "family-brief schedule install" in nightly


def test_no_secret_or_message_text_is_in_either_form(harness, set_up, capsys, monkeypatch):
    class Rejecting(CountingImap):
        def login(self, *_a: Any) -> None:  # an error that quotes what it was given
            raise imaplib.IMAP4.error(f"login failed for {APP_PASSWORD}: {MESSAGE_TEXT}")
    monkeypatch.setattr(imaplib, "IMAP4_SSL", Rejecting())
    harness.model_error = f"auth failed with {CLAUDE_TOKEN}"
    capsys.readouterr()

    harness.cli("setup", "status")
    harness.cli("setup", "status", "--text")

    out = capsys.readouterr()
    for secret in (APP_PASSWORD, CLAUDE_TOKEN, MESSAGE_TEXT, "trip form"):
        assert secret not in out.out and secret not in out.err
    assert "Gmail" in out.out and "Claude" in out.out  # the failing checks are still named
