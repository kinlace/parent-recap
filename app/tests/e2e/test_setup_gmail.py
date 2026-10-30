"""Connecting Gmail with `parent-recap setup gmail`, and on the setup page's Connect step: the App
Password goes from macOS's dialog, or the page, to a test sign-in and the secrets store, and nowhere
else (ADR 0005).

The outside edges are faked: the dialog, the browser, the secrets store and Gmail's IMAP server.
The assistant reads only the command's one-line JSON result, so assertions are on that result,
on what the store holds, and on every place the App Password must never reach. The page's tests
assert on its responses the same way."""
from __future__ import annotations

import getpass
import imaplib
import importlib.util
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from family_brief import gateway, install_record, setup_save, setup_server
from family_brief.config import Config
from family_brief.system import secrets
from setup_page import (APP_PASSWORD, PAGE_DIR, Response, call, config_file, refuse_secrets, source,
                        statuses, welcome)
from setup_page import clock, page  # noqa: F401
from setup_page import assert_never_leaked as assert_never_leaked_by_page
from test_gateway import KEY as GATEWAY_KEY, assert_key_never_leaked

TYPED = "abcd efgh ijkl mnop"  # how Google shows it, and how it's copied
ACCOUNT = "gmail-imap-parent@example.com"
TWO_STEP = "https://myaccount.google.com/signinoptions/two-step-verification"
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "setup_gmail_imap.py"


class GmailServer:
    """Gmail's IMAP server for the test sign-in: accepts only `password`, or can't be reached."""

    def __init__(self, password: str = APP_PASSWORD) -> None:
        self.password = password
        self.logins: list[tuple[str, str]] = []
        self.reachable = True

    def __call__(self, *_a: Any, **_k: Any) -> "GmailServer":
        if not self.reachable:
            raise OSError("nodename nor servname provided")
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
    # #501: in the family's Gmail, not the browser's default Google account.
    url = "https://myaccount.google.com/apppasswords?authuser=parent%40example.com"
    assert harness.desktop.opened == [url]
    assert len(harness.dialog.shown) == 1 and url in harness.dialog.shown[0]
    assert gmail.logins == [("parent@example.com", APP_PASSWORD)]
    assert harness.secrets == {ACCOUNT: APP_PASSWORD}
    assert install_record.entries("keychain") == [ACCOUNT]
    assert_never_leaked(harness, printed, caplog)


def test_the_address_can_be_given_when_the_config_has_none_yet(harness, gmail, capsys):
    del harness.config["gmail"]
    harness.dialog.typed = TYPED

    assert harness.cli("setup", "gmail", "--address", "Other@Example.com") == 0

    assert result(capsys)[0]["result"] == "saved"
    assert gmail.logins == [("other@example.com", APP_PASSWORD)]
    assert harness.secrets == {"gmail-imap-other@example.com": APP_PASSWORD}


def test_without_an_address_it_says_how_to_give_one(harness, gmail, capsys):
    del harness.config["gmail"]

    assert harness.cli("setup", "gmail") == 1

    res, _ = result(capsys)
    assert res["result"] == "no-address" and "--address" in res["next"]
    assert harness.dialog.shown == [] and harness.secrets == {}


def test_a_rejected_sign_in_points_to_two_step_verification(harness, gmail, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    wrong = "zyxwvutsrqponmlk"
    harness.dialog.typed = wrong

    assert harness.cli("setup", "gmail") == 1

    res, printed = result(capsys)
    assert res["result"] == "rejected"
    assert TWO_STEP in res["next"]
    assert harness.secrets == {}
    assert_never_leaked(harness, printed, caplog, wrong)


def test_when_the_app_passwords_page_is_not_available_it_points_to_two_step_verification(
        harness, gmail, capsys):
    harness.dialog.button = "other"

    assert harness.cli("setup", "gmail") == 1

    res, _ = result(capsys)
    assert res["result"] == "app-passwords-unavailable"
    assert TWO_STEP in res["next"]
    assert gmail.logins == [] and harness.secrets == {}


def test_something_that_is_not_an_app_password_is_never_sent_to_google(harness, gmail, capsys,
                                                                       caplog):
    # Most likely the family's own Google password, which Parent Recap must not use.
    caplog.set_level(logging.DEBUG)
    harness.dialog.typed = "MyGooglePassword1"

    assert harness.cli("setup", "gmail") == 1

    res, printed = result(capsys)
    assert res["result"] == "not-an-app-password"
    assert gmail.logins == [] and harness.secrets == {}
    assert_never_leaked(harness, printed, caplog, "MyGooglePassword1")


def test_closing_the_dialog_stores_nothing(harness, gmail, capsys):
    harness.dialog.button = "cancel"

    assert harness.cli("setup", "gmail") == 1

    assert result(capsys)[0]["result"] == "cancelled"
    assert gmail.logins == [] and harness.secrets == {}


def test_no_connection_to_gmail_says_so(harness, monkeypatch, capsys):
    def unreachable(*_a: Any, **_k: Any) -> None:
        raise OSError("nodename nor servname provided")
    monkeypatch.setattr(imaplib, "IMAP4_SSL", unreachable)
    harness.dialog.typed = TYPED

    assert harness.cli("setup", "gmail") == 1

    assert result(capsys)[0]["result"] == "no-connection"
    assert harness.secrets == {}


def test_no_open_leaves_the_page_alone(harness, gmail, capsys):
    harness.dialog.typed = TYPED

    assert harness.cli("setup", "gmail", "--no-open") == 0

    assert harness.desktop.opened == []


def test_storing_it_again_replaces_an_app_password_the_keychain_asks_about(harness, gmail, capsys):
    harness.secrets[ACCOUNT] = "oldoldoldoldoldo"
    harness.secrets.asks.add(ACCOUNT)
    harness.dialog.typed = TYPED

    assert harness.cli("setup", "gmail") == 0

    assert result(capsys)[0]["result"] == "saved"
    assert harness.secrets == {ACCOUNT: APP_PASSWORD} and not harness.secrets.asks


def test_doctor_tells_an_app_password_the_keychain_asks_about_from_a_missing_one(
        harness, monkeypatch, capsys):
    from family_brief import ops
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["wilma"]["enabled"] = False
    harness.config["whatsapp"]["enabled"] = False
    harness.secrets[ACCOUNT] = APP_PASSWORD
    harness.secrets.asks.add(ACCOUNT)

    harness.cli("doctor", "--skip-llm")

    line = next(l for l in capsys.readouterr().out.splitlines() if "Gmail:" in l)
    assert line.startswith(ops.FAIL) and "No App Password" not in line
    assert "asks for the Keychain password" in line and "evening" in line
    assert "parent-recap setup gmail" in line and "setup page" in line


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
    assert harness.secrets == {ACCOUNT: APP_PASSWORD}
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
    assert harness.secrets == {ACCOUNT: APP_PASSWORD}


def test_with_neither_a_dialog_nor_a_terminal_it_says_where_to_run_it(harness, gmail, terminal,
                                                                     capsys):
    harness.dialog.button = "fails"
    terminal["value"] = EOFError()

    assert harness.cli("setup", "gmail") == 1

    res, _ = result(capsys)
    assert res["result"] == "no-prompt" and "parent-recap setup gmail" in res["next"]
    assert harness.secrets == {}


def test_a_keychain_that_refuses_says_so_with_its_code(harness, gmail, capsys):
    harness.secrets.refuses = secrets.SecretsError("the store refused", "failed", -25293)
    harness.dialog.typed = TYPED

    assert harness.cli("setup", "gmail") == 1

    res, _ = result(capsys)
    assert res["result"] == "keychain-failed" and res["code"] == -25293
    assert "click Allow" in res["next"]


def test_a_keychain_out_of_reach_says_to_run_it_outside_tmux_or_ssh(harness, gmail, capsys):
    harness.secrets.refuses = secrets.SecretsError("the store can't be reached", "not-reachable",
                                                   -25308)
    harness.dialog.typed = TYPED

    assert harness.cli("setup", "gmail") == 1

    res, _ = result(capsys)
    assert res["result"] == "keychain-not-reachable" and res["code"] == -25308
    assert "outside tmux or SSH" in res["next"] and "Shell → New Command…" in res["next"]
    assert "setup gmail --address parent@example.com" in res["next"]
    assert "Allow" not in res["next"]  # macOS shows no prompt there


@pytest.mark.parametrize("var", ["TMUX", "SSH_CONNECTION"])
def test_in_tmux_or_ssh_it_warns_before_the_app_password_is_asked_for(harness, gmail, terminal,
                                                                     monkeypatch, capsys, var):
    harness.secrets.reachable_here = False
    monkeypatch.setenv(var, "/private/tmp/tmux-501/default,1234,0" if var == "TMUX"
                       else "10.0.0.2 52000 10.0.0.1 22")
    harness.dialog.typed = TYPED

    harness.cli("setup", "gmail")

    out, err = capsys.readouterr()
    assert len(out.strip().splitlines()) == 1  # still one JSON line on stdout
    assert err.count("outside tmux or SSH") == 1
    [asked] = harness.dialog.shown + terminal["prompts"]  # over SSH, the Terminal prompt
    assert "outside tmux or SSH" in asked


def test_outside_tmux_and_ssh_there_is_no_warning(harness, gmail, capsys):
    harness.dialog.typed = TYPED

    assert harness.cli("setup", "gmail") == 0

    assert "tmux" not in capsys.readouterr().err
    assert "tmux" not in harness.dialog.shown[0]


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

    assert harness.secrets == {ACCOUNT: APP_PASSWORD}
    assert APP_PASSWORD not in capsys.readouterr().out


# ── Connect: Gmail, on the setup page

def connect_gmail(url: str, address: str = "Parent@Example.com", password: str = TYPED) -> Response:
    return call(url, "api/gmail", method="POST", body={"address": address, "password": password})


def test_the_button_opens_google_s_app_passwords_page_in_the_family_s_gmail(harness, page):
    r = call(page.url, "api/open", method="POST",
             body={"site": "app-passwords", "address": " Parent+Kids@Example.com "})

    assert r.status == 200 and r.json()["result"] == "opened"
    assert harness.desktop.opened == [
        "https://myaccount.google.com/apppasswords?authuser=parent%2Bkids%40example.com"]

    call(page.url, "api/open", method="POST", body={"site": "two-step"})
    assert harness.desktop.opened[-1] == "https://myaccount.google.com/signinoptions/two-step-verification"
    for address in ["", "parent", "parent@example"]:
        r = call(page.url, "api/open", method="POST",
                 body={"site": "app-passwords", "address": address})
        assert r.status == 200 and r.json()["result"] == "no-address", address
    for body in [{"site": "https://evil.example"}, {}, {"site": "two-step", "x": 1},
                 {"site": "app-passwords"}, {"site": "two-step", "address": "a@b.fi"}]:
        assert call(page.url, "api/open", method="POST", body=body).status == 400, body
    assert len(harness.desktop.opened) == 2


def test_a_valid_app_password_is_tested_stored_and_gmail_turns_done(harness, page, gmail, caplog,
                                                                    capsys):
    caplog.set_level(logging.DEBUG)
    welcome(page.url, partner={"address": "partner@example.com", "language": "fi"})
    source(page.url, "wilma", "skip")
    household = Config.load(config_file(harness)).summary_language
    save_recipients(page.url, {"address": "parent@hotmail.com", "language": household},
                    {"address": "partner@example.com", "language": "fi"}, key=GATEWAY_KEY)

    r = connect_gmail(page.url)

    assert r.status == 200
    out = r.json()
    assert out["result"] == "saved" and out["address"] == "parent@example.com"
    assert gmail.logins == [("parent@example.com", APP_PASSWORD)]
    assert harness.secrets == {"gmail-imap-parent@example.com": APP_PASSWORD,
                               gateway.ACCOUNT: GATEWAY_KEY}
    assert out["progress"]["sources"]["gmail"] == "done"
    assert out["progress"]["source"] == "ai"
    cfg = Config.load(config_file(harness))
    assert cfg.gmail.username == "parent@example.com"
    # Gmail is only read (ADR 0032): who gets the Brief stays as saved.
    assert [(t.address, t.language) for t in cfg.email.to] == \
        [("parent@hotmail.com", None), ("partner@example.com", "fi")]
    assert call(page.url, "api/state").json()["gmail"] == {
        "address": "parent@example.com",
        "to": [{"address": "parent@hotmail.com", "language": household},
               {"address": "partner@example.com", "language": "fi"}]}
    assert_never_leaked_by_page(harness, [r], caplog, capsys, TYPED)


def test_gmail_s_address_never_becomes_a_recipient_by_itself(harness, page, gmail):
    assert connect_gmail(page.url).json()["result"] == "saved"

    assert Config.load(config_file(harness)).email.to == []


@pytest.mark.parametrize("typed, result", [
    ("MyGooglePassword1", "not-an-app-password"),
    ("zyxwvutsrqponmlk", "rejected"),
])
def test_a_password_gmail_would_not_take_is_explained_and_not_stored(harness, page, gmail, caplog,
                                                                     capsys, typed, result):
    caplog.set_level(logging.DEBUG)

    r = connect_gmail(page.url, password=typed)

    assert r.status == 200 and r.json()["result"] == result
    assert harness.secrets == {}
    assert statuses(page.url)["gmail"] == "to-do"
    if result == "not-an-app-password":  # most likely the Google password: never sent to Google
        assert gmail.logins == []
    assert_never_leaked_by_page(harness, [r], caplog, capsys, typed)


def test_no_connection_to_gmail_says_so_on_the_page(harness, page, gmail):
    gmail.reachable = False

    assert connect_gmail(page.url).json()["result"] == "no-connection"
    assert harness.secrets == {}


def test_a_keychain_that_refuses_says_so(harness, page, gmail):
    refuse_secrets(harness, -128)  # errSecUserCanceled

    assert connect_gmail(page.url).json()["result"] == "keychain-failed"
    assert statuses(page.url)["gmail"] == "to-do"


def test_a_keychain_out_of_reach_says_so_with_its_code(harness, page, gmail):
    refuse_secrets(harness, -25308)  # errSecInteractionNotAllowed

    assert connect_gmail(page.url).json() == {"result": "keychain-not-reachable", "code": -25308}
    assert statuses(page.url)["gmail"] == "to-do"
    refuse_secrets(harness, -25293)
    assert connect_gmail(page.url).json() == {"result": "keychain-failed", "code": -25293}


def test_an_address_that_is_not_one_is_explained(harness, page, gmail):
    for address in ["", "parent", "parent@", "a b@example.com"]:
        assert connect_gmail(page.url, address=address).json()["result"] == "no-address", address
    assert gmail.logins == []
    for body in [{"address": "parent@example.com"}, {"password": TYPED},
                 {"address": "parent@example.com", "password": 1},
                 {"address": "parent@example.com", "password": TYPED, "x": 1}]:
        r = call(page.url, "api/gmail", method="POST", body=body)
        assert r.status == 400 and r.json()["result"] == "invalid-answers", body
        assert TYPED.encode() not in r.body


def test_every_gmail_result_is_explained_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        for result in setup_server.gmail.GMAIL_RESULTS:
            assert table.get(f"gmail.{result}", "").strip(), (language, result)
        assert "Parent Recap" in table["gmail.explain"], language
        for name in setup_save.SOURCES:
            assert table.get(f"source.{name}", "").strip(), (language, name)
        for status in ("to-do", "done", "skipped"):
            assert table.get(f"status.{status}", "").strip(), (language, status)


def test_the_gmail_step_warns_before_google_and_its_fold_names_the_likely_causes_in_order(page):
    # #528: where the first outside tester got stuck, said before the parent goes to Google.
    html = call(page.url).body.decode()
    step = html[html.index('id="source-gmail"'):html.index('id="source-ai"')]

    assert step.index('data-text="gmail.before"') < step.index('id="gmail-open"')
    fold = step[step.index('data-text="gmail.unavailable"'):]
    assert (fold.index('data-text="gmail.unavailable.account"')
            < fold.index('data-text="gmail.unavailable.two-step"')
            < fold.index('id="gmail-two-step-more"'))
    english = json.loads((PAGE_DIR / "text.json").read_text())["en"]
    assert "2-Step Verification" in english["gmail.before"]
    assert "password reset" in english["gmail.before"]
    assert "Switch account" in english["gmail.unavailable.account"]


def test_the_gmail_fields_are_the_page_s_own(page):
    html = call(page.url).body.decode()

    password = re.search(r'<input[^>]*id="gmail-password"[^>]*>', html).group(0)
    assert 'type="password"' in password
    address = re.search(r'<input[^>]*id="gmail-address"[^>]*>', html).group(0)
    assert 'type="email"' in address


# ── Connect: who gets the Brief, which Parent Recap sends from its own address (ADR 0032)


def save_recipients(url: str, *recipients: dict[str, Any], key: str | None = None) -> Response:
    """Who gets the Brief, with the Gateway Key for sending when it's given."""
    return call(url, "api/gmail/recipients", method="POST",
                body={"recipients": list(recipients), **({"key": key} if key is not None else {})})


def with_the_gateway_as_the_ai(url: str, partner: dict[str, str] | None = None) -> None:
    welcome(url, ai="gateway", partner=partner)
    source(url, "wilma", "skip")


def test_with_the_gateway_as_the_ai_the_recipients_are_saved_and_gmail_waits_to_be_connected_or_skipped(
        harness, page, gmail):
    with_the_gateway_as_the_ai(page.url, {"address": "partner@example.com", "language": "fi"})

    r = save_recipients(page.url, {"address": " Parent@Hotmail.com ", "language": "en"},
                        {"address": "partner@example.com", "language": "fi"})

    assert r.status == 200 and r.json()["result"] == "saved"
    progress = r.json()["progress"]
    assert (progress["source"], progress["sources"]["gmail"]) == ("gmail", "to-do")
    cfg = Config.load(config_file(harness))
    # The setup parent first; a language the same as the Household's isn't saved twice.
    assert [(t.address, t.language) for t in cfg.email.to] == \
        [("parent@hotmail.com", None), ("partner@example.com", "fi")]
    assert "sender" not in setup_save.read(config_file(harness)).get("email", {})
    # No App Password is asked for, tested or stored.
    assert gmail.logins == [] and harness.secrets == {}

    skipped = source(page.url, "gmail", "skip").json()["progress"]

    assert (skipped["source"], skipped["sources"]["gmail"]) == ("ai", "skipped")
    cfg = Config.load(config_file(harness))
    assert cfg.gmail.username is None and not cfg.gmail.allowlist_domains
    assert [t.address for t in cfg.email.to] == ["parent@hotmail.com", "partner@example.com"]


def test_gmail_cannot_be_skipped_before_who_gets_the_brief_is_saved(harness, page):
    with_the_gateway_as_the_ai(page.url)

    r = source(page.url, "gmail", "skip")

    assert r.status == 400 and r.json()["result"] == "invalid-answers"
    assert statuses(page.url)["gmail"] == "to-do"


def test_with_the_gateway_as_the_ai_its_key_is_not_asked_for_again(harness, page):
    # The AI step tests it with a model call (test_setup_ai.py), which a key for sending alone fails.
    with_the_gateway_as_the_ai(page.url)

    r = save_recipients(page.url, {"address": "parent@example.com", "language": "en"},
                        key=GATEWAY_KEY)

    assert r.status == 400 and r.json()["result"] == "invalid-answers"
    assert GATEWAY_KEY.encode() not in r.body
    assert harness.relay_keys == [] and harness.secrets == {}
    assert Config.load(config_file(harness)).email.to == []


@pytest.mark.parametrize("ai", ["claude", "codex"])
def test_with_its_own_ai_the_brief_is_sent_with_a_key_tested_by_the_relay(harness, page, gmail,
                                                                          caplog, ai):
    # A send-only key has no model behind it (ops/gateway/README.md), so no model call tests it.
    caplog.set_level(logging.DEBUG)
    welcome(page.url, ai=ai)
    source(page.url, "wilma", "skip")

    r = save_recipients(page.url, {"address": "parent@hotmail.com", "language": "en"},
                        key=f" {GATEWAY_KEY}\n")  # as copied from a message

    assert r.status == 200 and r.json()["result"] == "saved"
    assert r.json()["progress"]["sources"]["gmail"] == "to-do"  # still to connect or skip
    assert harness.relay_keys == [GATEWAY_KEY] and harness.sent == []
    assert harness.model.reached == [] and harness.model_calls == []
    assert harness.secrets == {gateway.ACCOUNT: GATEWAY_KEY}
    assert install_record.entries("keychain") == [gateway.ACCOUNT]  # so uninstall removes it
    cfg = Config.load(config_file(harness))
    assert cfg.llm.backend == ai
    assert [t.address for t in cfg.email.to] == ["parent@hotmail.com"]
    assert gmail.logins == []
    assert_key_never_leaked(harness, r.body.decode(), caplog)


@pytest.mark.parametrize("answer, result", [
    ((401, {"error": "missing, unknown or revoked Gateway Key"}), "rejected"),
    ((502, {"error": "the gateway is down"}), "test-call-failed"),
    (None, "no-connection"),
])
def test_a_key_the_relay_does_not_take_is_not_stored_and_says_what_to_do(harness, page, caplog,
                                                                          answer, result):
    caplog.set_level(logging.DEBUG)
    welcome(page.url, ai="claude")
    source(page.url, "wilma", "skip")
    if answer is None:
        harness.email_error = gateway.GatewayError(
            "couldn't reach the gateway at gateway.parentrecap.com (ConnectionError)")
    else:
        harness.relay_answer = answer

    r = save_recipients(page.url, {"address": "parent@example.com", "language": "en"},
                        key=GATEWAY_KEY)

    assert r.status == 200 and r.json() == {"result": result}
    assert harness.secrets == {} and install_record.entries("keychain") == []
    assert Config.load(config_file(harness)).email.to == []  # nothing saved without the key
    assert_key_never_leaked(harness, r.body.decode(), caplog)


def test_with_its_own_ai_a_key_is_needed_and_what_is_not_one_is_never_sent(harness, page):
    welcome(page.url, ai="codex")

    r = save_recipients(page.url, {"address": "parent@example.com", "language": "en"})
    assert r.status == 400 and r.json()["result"] == "invalid-answers"
    r = save_recipients(page.url, {"address": "parent@example.com", "language": "en"},
                        key="my-password")
    assert r.json() == {"result": "not-a-key"}
    # Addresses are checked before the key goes anywhere.
    r = save_recipients(page.url, {"address": "parent", "language": "en"}, key=GATEWAY_KEY)
    assert r.json() == {"result": "no-address"}

    assert harness.relay_keys == [] and harness.secrets == {}
    assert Config.load(config_file(harness)).email.to == []


def test_a_keychain_that_refuses_the_key_says_so_with_its_code(harness, page):
    welcome(page.url, ai="claude")
    refuse_secrets(harness, -25293)

    r = save_recipients(page.url, {"address": "parent@example.com", "language": "en"},
                        key=GATEWAY_KEY)

    assert r.json() == {"result": "keychain-failed", "code": -25293}
    assert Config.load(config_file(harness)).email.to == []


def test_each_recipient_needs_an_address_of_their_own(harness, page):
    with_the_gateway_as_the_ai(page.url)

    for given in [[{"address": "parent", "language": "en"}],
                  [{"address": "parent@example.com", "language": "en"},
                   {"address": "a b@example.com", "language": "fi"}]]:
        assert save_recipients(page.url, *given).json() == {"result": "no-address"}, given
    same = save_recipients(page.url, {"address": "parent@example.com", "language": "en"},
                           {"address": "Parent@Example.com", "language": "fi"})
    assert same.json() == {"result": "same-address"}
    for body in [{}, {"recipients": []}, {"recipients": [{"address": "parent@example.com"}]},
                 {"recipients": [{"address": "parent@example.com", "language": "sv"}]},
                 {"recipients": [{"address": "parent@example.com", "language": "en"}], "x": 1}]:
        r = call(page.url, "api/gmail/recipients", method="POST", body=body)
        assert r.status == 400 and r.json()["result"] == "invalid-answers", body
    assert Config.load(config_file(harness)).email.to == []


def test_gmail_opened_again_after_it_was_skipped_only_adds_reading(harness, page, gmail):
    with_the_gateway_as_the_ai(page.url, {"address": "partner@example.com", "language": "fi"})
    save_recipients(page.url, {"address": "parent@hotmail.com", "language": "en"},
                    {"address": "partner@example.com", "language": "fi"})
    source(page.url, "gmail", "skip")
    assert source(page.url, "gmail", "open").json()["progress"]["sources"]["gmail"] == "to-do"

    r = connect_gmail(page.url)

    assert r.status == 200 and r.json()["result"] == "saved"
    cfg = Config.load(config_file(harness))
    assert cfg.gmail.username == "parent@example.com"
    assert [t.address for t in cfg.email.to] == ["parent@hotmail.com", "partner@example.com"]


def test_welcome_saved_again_keeps_the_setup_parent_first(harness, page, gmail):
    with_the_gateway_as_the_ai(page.url)
    save_recipients(page.url, {"address": "parent@hotmail.com", "language": "en"})
    connect_gmail(page.url)  # read only

    welcome(page.url, ai="gateway", partner={"address": "partner@example.com", "language": "fi"})

    assert [t.address for t in Config.load(config_file(harness)).email.to] == \
        ["parent@hotmail.com", "partner@example.com"]


def test_a_key_for_sending_alone_given_later_is_tested_by_the_relay_and_stored(harness, page,
                                                                              caplog):
    # Check and Finish take it when doctor says there's none (test_gateway_sends.py).
    caplog.set_level(logging.DEBUG)
    welcome(page.url, ai="claude")

    for body in [{}, {"key": 1}, {"key": GATEWAY_KEY, "x": 1}]:
        assert call(page.url, "api/gmail/key", method="POST", body=body).status == 400, body
    assert call(page.url, "api/gmail/key", method="POST",
                body={"key": "my-password"}).json() == {"result": "not-a-key"}
    assert harness.relay_keys == []
    harness.relay_answer = (401, {"error": "missing, unknown or revoked Gateway Key"})
    assert call(page.url, "api/gmail/key", method="POST",
                body={"key": GATEWAY_KEY}).json() == {"result": "rejected"}
    assert harness.secrets == {}
    harness.relay_answer = (200, {"sent": 1, "refused": 0})

    r = call(page.url, "api/gmail/key", method="POST", body={"key": GATEWAY_KEY})

    assert r.json() == {"result": "saved"}
    assert harness.secrets == {gateway.ACCOUNT: GATEWAY_KEY} and harness.model_calls == []
    assert_key_never_leaked(harness, r.body.decode(), caplog)


def test_who_gets_the_brief_and_the_key_s_results_are_explained_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        for result in setup_server.gmail.RECIPIENTS_RESULTS:
            assert table.get(f"gmail.brief.{result}", "").strip(), (language, result)
        for key in ("gmail.brief.explain", "gmail.brief.unread", "gmail.brief.junk", "brief.junk",
                    "gmail.brief.to", "gmail.brief.change", "gmail.key.saved",
                    "gmail.brief.key", "gmail.brief.key.explain",
                    "gmail.brief.key.explain.windows"):
            assert table.get(key, "").strip(), (language, key)
        # A key the relay doesn't take is said as the gateway AI's key field says it.
        for result in setup_server.gmail.SEND_KEY_RESULTS:
            assert table.get(f"gateway.{result}", "").strip(), (language, result)
        assert "sk-" in table["gmail.brief.key.explain"], language
        assert "brief@parentrecap.com" in table["gmail.brief.explain"], language
        # No choice of how the Brief is sent is left (ADR 0032).
        assert not any(k.startswith(("gmail.choice", "gmail.gateway.")) for k in table), language
    english = text["en"]
    assert "only for sending" in english["gmail.brief.key.explain"]
    assert "Nobody reads" in english["gmail.brief.unread"]
    assert "never sends from your Gmail" in english["gmail.explain"]
    # #602: the Gmail step says Gmail is read for the school's mail, only from the senders ticked.
    assert "reads the school's mail" in english["gmail.explain"]
    assert "only from the senders you tick" in english["gmail.explain"]
    assert "sends your Brief" not in english["gmail.explain"]
