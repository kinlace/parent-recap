"""Connecting Gmail with `family-brief setup gmail`: the App Password goes from macOS's dialog
to a test sign-in and the Keychain, and nowhere else (ADR 0005).

The outside edges are faked: the dialog, `open`, the Keychain and Gmail's IMAP server. The
assistant reads only the command's one-line JSON result, so assertions are on that result, on
what the Keychain holds, and on every place the App Password must never reach."""
from __future__ import annotations

import getpass
import imaplib
import importlib.util
import json
import logging
import sys
from pathlib import Path
from typing import Any

import pytest

from family_brief import install_record

APP_PASSWORD = "abcdefghijklmnop"
TYPED = "abcd efgh ijkl mnop"  # how Google shows it, and how it's copied
ACCOUNT = "gmail-imap-parent@example.com"
TWO_STEP = "https://myaccount.google.com/signinoptions/two-step-verification"
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup_gmail_imap.py"


class GmailServer:
    """Gmail's IMAP server for the test sign-in: accepts only `password`."""

    def __init__(self, password: str = APP_PASSWORD) -> None:
        self.password = password
        self.logins: list[tuple[str, str]] = []

    def __call__(self, *_a: Any, **_k: Any) -> "GmailServer":
        return self

    def __enter__(self) -> "GmailServer":
        return self

    def __exit__(self, *_a: Any) -> None:
        pass

    def login(self, user: str, password: str) -> None:
        self.logins.append((user, password))
        if password != self.password:
            raise imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Invalid credentials (Failure)")

    def logout(self) -> None:
        pass


@pytest.fixture
def gmail(monkeypatch) -> GmailServer:
    server = GmailServer()
    monkeypatch.setattr(imaplib, "IMAP4_SSL", server)
    return server


@pytest.fixture(autouse=True)
def desktop(monkeypatch):
    """A desktop session, as when the family runs setup on their own Mac."""
    for var in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def terminal(monkeypatch) -> dict[str, Any]:
    """The hidden Terminal prompt: what's typed there, or an exception for no Terminal at all."""
    typed: dict[str, Any] = {"value": APP_PASSWORD, "prompts": []}

    def fake_getpass(prompt: str = "") -> str:
        typed["prompts"].append(prompt)
        if isinstance(typed["value"], BaseException):
            raise typed["value"]
        return typed["value"]
    monkeypatch.setattr(getpass, "getpass", fake_getpass)
    return typed


def result(capsys) -> tuple[dict[str, Any], str]:
    out, err = capsys.readouterr()
    lines = out.strip().splitlines()
    assert len(lines) == 1, f"expected one JSON line, got: {out!r}"
    return json.loads(lines[0]), out + err


def assert_never_leaked(harness, printed: str, caplog, secret: str = APP_PASSWORD) -> None:
    for value in {secret, TYPED}:
        assert value not in printed
        assert value not in caplog.text
        assert not any(value in arg for cmd in harness.commands for arg in cmd)


# ── the dialog


def test_an_app_password_typed_in_the_dialog_is_tested_and_stored(harness, gmail, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    harness.dialog.typed = TYPED

    assert harness.cli("setup", "gmail") == 0

    res, printed = result(capsys)
    assert res["result"] == "saved"
    assert harness.opened == ["https://myaccount.google.com/apppasswords"]
    assert len(harness.dialog.shown) == 1 and "hidden answer" in harness.dialog.shown[0]
    assert gmail.logins == [("parent@example.com", APP_PASSWORD)]
    assert harness.keychain == {ACCOUNT: APP_PASSWORD}
    assert install_record.entries("keychain") == [ACCOUNT]
    assert_never_leaked(harness, printed, caplog)


def test_the_address_can_be_given_when_the_config_has_none_yet(harness, gmail, capsys):
    del harness.config["gmail"]
    harness.dialog.typed = TYPED

    assert harness.cli("setup", "gmail", "--address", "Other@Example.com") == 0

    assert result(capsys)[0]["result"] == "saved"
    assert gmail.logins == [("other@example.com", APP_PASSWORD)]
    assert harness.keychain == {"gmail-imap-other@example.com": APP_PASSWORD}


def test_without_an_address_it_says_how_to_give_one(harness, gmail, capsys):
    del harness.config["gmail"]

    assert harness.cli("setup", "gmail") == 1

    res, _ = result(capsys)
    assert res["result"] == "no-address" and "--address" in res["next"]
    assert harness.dialog.shown == [] and harness.keychain == {}


def test_a_rejected_sign_in_points_to_two_step_verification(harness, gmail, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    wrong = "zyxwvutsrqponmlk"
    harness.dialog.typed = wrong

    assert harness.cli("setup", "gmail") == 1

    res, printed = result(capsys)
    assert res["result"] == "rejected"
    assert TWO_STEP in res["next"]
    assert harness.keychain == {}
    assert_never_leaked(harness, printed, caplog, wrong)


def test_when_the_app_passwords_page_is_not_available_it_points_to_two_step_verification(
        harness, gmail, capsys):
    harness.dialog.button = "other"

    assert harness.cli("setup", "gmail") == 1

    res, _ = result(capsys)
    assert res["result"] == "app-passwords-unavailable"
    assert TWO_STEP in res["next"]
    assert gmail.logins == [] and harness.keychain == {}


def test_something_that_is_not_an_app_password_is_never_sent_to_google(harness, gmail, capsys,
                                                                       caplog):
    # Most likely the family's own Google password, which Parent Recap must not use.
    caplog.set_level(logging.DEBUG)
    harness.dialog.typed = "MyGooglePassword1"

    assert harness.cli("setup", "gmail") == 1

    res, printed = result(capsys)
    assert res["result"] == "not-an-app-password"
    assert gmail.logins == [] and harness.keychain == {}
    assert_never_leaked(harness, printed, caplog, "MyGooglePassword1")


def test_closing_the_dialog_stores_nothing(harness, gmail, capsys):
    harness.dialog.button = "cancel"

    assert harness.cli("setup", "gmail") == 1

    assert result(capsys)[0]["result"] == "cancelled"
    assert gmail.logins == [] and harness.keychain == {}


def test_no_connection_to_gmail_says_so(harness, monkeypatch, capsys):
    def unreachable(*_a: Any, **_k: Any) -> None:
        raise OSError("nodename nor servname provided")
    monkeypatch.setattr(imaplib, "IMAP4_SSL", unreachable)
    harness.dialog.typed = TYPED

    assert harness.cli("setup", "gmail") == 1

    assert result(capsys)[0]["result"] == "no-connection"
    assert harness.keychain == {}


def test_no_open_leaves_the_page_alone(harness, gmail, capsys):
    harness.dialog.typed = TYPED

    assert harness.cli("setup", "gmail", "--no-open") == 0

    assert harness.opened == []


# ── without a desktop session


def test_over_ssh_it_asks_in_a_hidden_terminal_prompt(harness, gmail, terminal, monkeypatch,
                                                      capsys, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.2 52000 10.0.0.1 22")
    terminal["value"] = TYPED

    assert harness.cli("setup", "gmail") == 0

    res, printed = result(capsys)
    assert res["result"] == "saved"
    assert harness.dialog.shown == []
    assert len(terminal["prompts"]) == 1
    assert harness.keychain == {ACCOUNT: APP_PASSWORD}
    assert_never_leaked(harness, printed, caplog)


def test_when_the_dialog_cannot_be_shown_it_asks_in_terminal(harness, gmail, terminal, capsys,
                                                             caplog):
    caplog.set_level(logging.DEBUG)
    harness.dialog.button = "fails"

    assert harness.cli("setup", "gmail") == 0

    res, printed = result(capsys)
    assert res["result"] == "saved"
    assert_never_leaked(harness, printed, caplog)
    assert len(terminal["prompts"]) == 1
    assert harness.keychain == {ACCOUNT: APP_PASSWORD}


def test_with_neither_a_dialog_nor_a_terminal_it_says_where_to_run_it(harness, gmail, terminal,
                                                                     capsys):
    harness.dialog.button = "fails"
    terminal["value"] = EOFError()

    assert harness.cli("setup", "gmail") == 1

    res, _ = result(capsys)
    assert res["result"] == "no-prompt" and "family-brief setup gmail" in res["next"]
    assert harness.keychain == {}


def test_an_empty_terminal_answer_means_the_page_is_not_available(harness, gmail, terminal,
                                                                  monkeypatch, capsys):
    monkeypatch.setenv("SSH_TTY", "/dev/ttys003")
    terminal["value"] = ""

    assert harness.cli("setup", "gmail") == 1

    assert result(capsys)[0]["result"] == "app-passwords-unavailable"


# ── the Terminal script from 0.4.1 and older


def test_the_terminal_script_still_stores_the_app_password(harness, gmail, terminal, monkeypatch,
                                                           capsys):
    spec = importlib.util.spec_from_file_location("setup_gmail_imap", SCRIPT)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    terminal["value"] = TYPED
    monkeypatch.setattr(sys, "argv", ["setup_gmail_imap.py", "parent@example.com"])

    assert script.main() == 0

    assert harness.keychain == {ACCOUNT: APP_PASSWORD}
    assert APP_PASSWORD not in capsys.readouterr().out
