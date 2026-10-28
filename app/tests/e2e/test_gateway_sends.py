"""The Brief sent from Parent Recap's own address (ADR 0028): a Household whose config says the
gateway sends gets its evening Brief, its first one from setup and its Weekend Picks through the
gateway's `POST /v1/brief`, with the Gateway Key and no Gmail App Password. A Household that reads
no mail then needs no Gmail at all: the run, doctor and the health check pass without it.

The relay is the outbox's (fake_outbox.py), answering as ops/gateway/brief_relay.py does; what each
of its answers means is the email Action's own test's (integration/test_email_gateway.py).
Assertions are on the email the relay was given, doctor's lines, the state and the log."""
from __future__ import annotations

import logging
from datetime import timedelta

import pytest
import time_machine

from conftest import NOW, SAYS_SOMETHING, msg
from nights import caught_up_last_night
from fake_system import MACOS, WINDOWS
from family_brief import gateway as api, ops, setup_status
from setup_page import config_file
from test_gateway import assert_key_never_leaked
from test_weekend_picks import run_weekend_picks

KEY = "sk-bf-fake-gateway-key"
URL = "https://gateway.example.test"
UNREAD = "This email comes from an address nobody reads. To reply, write to parent@example.com"
UNKNOWN_KEY = (401, {"error": "missing, unknown or revoked Gateway Key"})


@pytest.fixture
def wilma_only(harness):
    """An English Household on Claude that reads only Wilma and has the gateway send its Brief:
    a Gateway Key in the secrets store, no Gmail address and no App Password."""
    harness.config["summary_language"] = "en"
    harness.config["gmail"] = {}
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["whatsapp"]["enabled"] = False
    harness.config["email"]["sender"] = "gateway"
    harness.config["llm"]["gateway_url"] = URL
    harness.secrets[api.ACCOUNT] = KEY
    harness.secrets["claude-oauth-token"] = "sk-ant-oat01-abc"
    harness.sources = {"wilma": [msg("wilma", "message:1", "2026-09-27T08:15:00+03:00",
                                     "Retki Nuuksioon torstaina", sender="Opettaja", subject="Retki")]}
    harness.model_reply = SAYS_SOMETHING
    return harness


# ── the evening run


def test_the_evening_brief_goes_through_the_gateway_with_no_gmail(wilma_only, capsys, caplog):
    caplog.set_level(logging.DEBUG)

    assert wilma_only.run() == 0

    [email] = wilma_only.sent
    assert email.via == "gateway" and email.from_addr is None
    assert email.to == ["parent@example.com", "partner@example.com"]
    assert "Something new tonight" in email.text
    assert wilma_only.relay_keys == [KEY]
    # No Gmail: not read, and no App Password asked for.
    assert "gmail" not in wilma_only.lookback_hours
    assert set(wilma_only.secrets) == {api.ACCOUNT, "claude-oauth-token"}
    assert wilma_only.state()["brief_delivered_at"]
    assert_key_never_leaked(wilma_only, "".join(capsys.readouterr()), caplog)


@pytest.mark.parametrize("on", [MACOS, WINDOWS])
def test_the_key_is_read_from_the_systems_own_store(wilma_only, on):
    wilma_only.system = on

    wilma_only.run()

    assert [e.via for e in wilma_only.sent] == ["gateway"] and wilma_only.relay_keys == [KEY]


def test_the_brief_says_its_address_is_unread_and_whom_to_write_to(wilma_only):
    wilma_only.run()

    [email] = wilma_only.sent
    assert email.text.rstrip().endswith(UNREAD + ", who set up Parent Recap.")
    assert UNREAD in email.html


def test_a_brief_from_the_households_own_gmail_says_nothing_of_an_unread_address(wilma_only):
    wilma_only.config["email"]["sender"] = "gmail"
    wilma_only.config["gmail"] = {"username": "parent@example.com"}

    wilma_only.run()

    [email] = wilma_only.sent
    assert email.via == "gmail" and "nobody reads" not in email.text + email.html
    assert wilma_only.relay_keys == []


def test_the_from_address_is_ignored_when_the_gateway_sends(wilma_only):
    wilma_only.config["email"]["from_addr"] = "someone@gmail.com"

    wilma_only.run()

    [email] = wilma_only.sent
    assert email.via == "gateway" and "someone@gmail.com" not in email.text + (email.html or "")


@pytest.mark.parametrize("answer, says", [
    (UNKNOWN_KEY, "the gateway doesn't know the Gateway Key, or it was revoked"),
    ((429, {"error": "this key has sent its 4 Briefs for today"}),
     "the Gateway Key has sent all the Briefs it may send today"),
    ((502, {"error": "couldn't reach email delivery (OSError)"}), "the gateway couldn't send the Brief just now"),
])
def test_a_refused_send_fails_with_its_reason_and_the_night_comes_back_tomorrow(wilma_only, caplog, answer, says):
    caught_up_last_night(wilma_only)
    wilma_only.relay_answer = answer

    assert wilma_only.run() == 0

    assert wilma_only.sent == []
    assert f"email send failed: {says}" in caplog.text
    assert "Brief not delivered" in caplog.text
    assert not wilma_only.state().get("brief_delivered_at")

    wilma_only.relay_answer = (200, {"sent": 2, "refused": 0})
    with time_machine.travel(NOW + timedelta(days=1), tick=False):
        assert wilma_only.run() == 0
    [email] = wilma_only.sent
    assert "Something new tonight" in email.text


def test_without_a_gateway_key_nothing_is_sent_and_it_says_how_to_store_one(wilma_only, caplog):
    del wilma_only.secrets[api.ACCOUNT]

    wilma_only.run()

    assert wilma_only.sent == [] and wilma_only.relay_keys == []
    assert "no Gateway Key" in caplog.text and "parent-recap setup gateway" in caplog.text


# ── Weekend Picks


def test_weekend_picks_go_through_the_gateway_too(wilma_only, monkeypatch):
    wilma_only.model_reply = {"picks": [{"ext_id": "le-1", "rank": 1, "why": "Puppets"}]}

    run_weekend_picks(wilma_only, monkeypatch, "en")

    [email] = wilma_only.sent
    assert email.via == "gateway" and email.subject.startswith("Weekend Picks")
    assert UNREAD in email.text and UNREAD in email.html


# ── doctor and the health check


def doctor(harness, capsys) -> dict[str, str]:
    """doctor's lines by item. The AI's check is left out: this Household's AI is Claude Code."""
    harness.cli("doctor", "--skip-llm")
    out = capsys.readouterr().out
    assert KEY not in out
    return {line.split(":", 1)[0].split(" ", 1)[1].strip(): line for line in out.splitlines() if ":" in line}


def test_doctor_passes_for_a_household_with_only_a_gateway_key_and_wilma(wilma_only, capsys):
    wilma_only.set_up_sources()

    lines = doctor(wilma_only, capsys)

    assert lines["Brief sending"].startswith(ops.OK)
    assert URL in lines["Brief sending"]
    assert "Gmail" not in lines  # neither read nor sending: nothing to check
    # Only the scheduled job's checks, which this test hasn't installed, aren't OK.
    failed = {item for item, line in lines.items() if line.startswith(ops.FAIL)}
    assert failed <= {"Schedule", "Wake"}
    # The check sends nothing.
    assert wilma_only.sent == [] and wilma_only.relay_keys == [KEY]


def test_the_health_check_needs_no_gmail_when_the_gateway_sends(wilma_only):
    wilma_only.cli("doctor", "--skip-llm")  # writes the config

    outcome = next(o for o in setup_status.check(str(config_file(wilma_only))) if o.outcome == "doctor")

    assert not any(c.key in ("gmail", "gateway-sends") for c in outcome.checks)


def test_doctor_says_when_the_gateway_does_not_know_the_key(wilma_only, capsys):
    wilma_only.relay_answer = UNKNOWN_KEY

    line = doctor(wilma_only, capsys)["Brief sending"]

    assert line.startswith(ops.FAIL)
    assert "revoked" in line and "parent-recap setup gateway" in line


def test_doctor_says_how_to_store_a_missing_key(wilma_only, capsys):
    del wilma_only.secrets[api.ACCOUNT]

    line = doctor(wilma_only, capsys)["Brief sending"]

    assert line.startswith(ops.FAIL) and "parent-recap setup gateway" in line


def test_doctor_says_when_the_gateway_does_not_answer(wilma_only, capsys):
    wilma_only.email_error = api.GatewayError("couldn't reach the gateway at gateway.example.test (ConnectionError)")

    line = doctor(wilma_only, capsys)["Brief sending"]

    assert line.startswith(ops.FAIL) and URL in line and "online" in line


def test_doctor_still_checks_a_gmail_the_household_reads(wilma_only, capsys):
    wilma_only.config["gmail"] = {"username": "parent@example.com", "allowlist_domains": ["kilo.example.fi"]}

    lines = doctor(wilma_only, capsys)

    assert lines["Gmail"].startswith(ops.FAIL) and "App Password" in lines["Gmail"]
    assert lines["Brief sending"].startswith(ops.OK)


def test_doctor_has_no_gateway_sending_check_when_gmail_sends(harness, capsys):
    harness.secrets["claude-oauth-token"] = "sk-ant-oat01-abc"

    assert "Brief sending" not in doctor(harness, capsys)


# ── setup gateway, for a Household whose AI is its own Claude Code or Codex


def test_a_send_only_key_is_tested_by_the_relay_and_stored(wilma_only, capsys):
    # Its key has no model behind it (ops/gateway/README.md "Create a send-only Gateway Key"), so
    # it's tested as doctor tests sending, with no model call.
    del wilma_only.secrets[api.ACCOUNT]
    wilma_only.dialog.typed = KEY

    assert wilma_only.cli("setup", "gateway") == 0

    assert '"result": "saved"' in capsys.readouterr().out
    assert wilma_only.secrets[api.ACCOUNT] == KEY
    assert wilma_only.model_calls == [] and wilma_only.relay_keys == [KEY] and wilma_only.sent == []


def test_a_send_only_key_the_relay_does_not_know_is_not_stored(wilma_only, capsys):
    del wilma_only.secrets[api.ACCOUNT]
    wilma_only.dialog.typed = KEY
    wilma_only.relay_answer = UNKNOWN_KEY

    assert wilma_only.cli("setup", "gateway") == 1

    assert '"result": "rejected"' in capsys.readouterr().out
    assert api.ACCOUNT not in wilma_only.secrets
