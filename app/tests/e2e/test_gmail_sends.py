"""The Brief sent from the Household's own Gmail (ADR 0034): a Household whose AI is its own Claude
Code or Codex and that reads Gmail with an App Password may turn it on, from Finish's "Later, if
you need it" or with `setup sending` (the manage skill's way), and back off. Turned on, its
evening Brief, its first Brief and its Weekend Picks go from that Gmail over SMTP: the gateway is
never called, no send-only key is asked for and nobody confirms anything. doctor, Check and Finish
check the transport in use. Never offered with the gateway as the AI, or without Gmail.

Gmail's SMTP server is the outbox's (fake_outbox.py), and its IMAP server the sources'; what the
message itself looks like is the email Action's own test's (integration/test_email_gmail.py).
Assertions are on the email sent and by which way, the gateway's requests, doctor's lines, the
command's and the page's replies and the config written."""
from __future__ import annotations

import json
import logging

import pytest
import yaml

from conftest import GATEWAY_KEY, SAYS_SOMETHING, msg
from family_brief import gateway as api, ops
from nights import caught_up_last_night
from setup_page import APP_PASSWORD, PAGE_DIR, at_finish, call, config_file, finished
from setup_page import clock, health, page, schedule  # noqa: F401
from test_gateway_sends import doctor, no_key
from test_setup_first_brief import at_the_first_brief, brief_made, make_brief, send_it_to_me
from test_weekend_picks import run_weekend_picks

PARENT = "parent@example.com"  # the Household's Gmail and the setup parent
RECIPIENTS = [PARENT, "partner@example.com"]
REVOKED = "the App Password Google no longer takes"
BOTH_STOPPED = "neither read that Gmail nor send the Brief from it"
REAL_HEALTH_CHECKS = ops.health_checks  # before the setup page's tests fake it


@pytest.fixture
def own_gmail(harness):
    """An English Household on Claude Code that reads its Gmail and sends the Brief from it, with
    no Gateway Key."""
    harness.config["summary_language"] = "en"
    harness.config["email"]["from_gmail"] = True
    harness.secrets["claude-oauth-token"] = "sk-ant-oat01-abc"
    harness.sources = {"gmail": [msg("gmail", "1001", "2026-09-27T08:15:00+03:00",
                                     "Retki Nuuksioon torstaina", sender="Opettaja <ope@kilo.example.fi>",
                                     subject="Retki")]}
    harness.model_reply = SAYS_SOMETHING
    no_key(harness)
    return harness


def revoke_app_password(harness) -> None:
    """Google stops taking the App Password, for reading and sending alike."""
    harness.outbox.gmail_password = REVOKED
    harness.source_fakes["gmail"].password = REVOKED


def never_through_the_gateway(harness) -> None:
    assert harness.relay_keys == [] and harness.key_requests == [] and harness.binds == []
    assert harness.confirmations == []


def saved_config(harness) -> dict:
    return yaml.safe_load(config_file(harness).read_text())


def setup_sending(harness, capsys, to: str) -> tuple[int, dict]:
    code = harness.cli("setup", "sending", to)
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    harness.config = saved_config(harness)  # the next command writes the config the harness has
    return code, out


# ── the evening run and Weekend Picks


def test_the_evening_brief_goes_from_the_households_gmail_and_never_through_the_gateway(own_gmail):
    assert own_gmail.run() == 0

    [email] = own_gmail.sent
    assert (email.via, email.from_addr, email.to) == ("gmail", PARENT, RECIPIENTS)
    assert "Something new tonight" in email.text
    never_through_the_gateway(own_gmail)
    assert api.ACCOUNT not in own_gmail.secrets
    # From the parent's own address a reply reaches them, and Hotmail knows it.
    assert "nobody reads" not in email.text + (email.html or "")
    assert own_gmail.state()["brief_delivered_at"]


def test_a_gateway_key_stored_before_stays_stored_unused(own_gmail):
    own_gmail.secrets[api.ACCOUNT] = GATEWAY_KEY

    own_gmail.run()

    [email] = own_gmail.sent
    assert email.via == "gmail"
    never_through_the_gateway(own_gmail)
    assert own_gmail.secrets[api.ACCOUNT] == GATEWAY_KEY


def test_with_the_gateway_as_the_ai_the_brief_goes_through_the_gateway_whatever_the_config(own_gmail):
    own_gmail.config["llm"]["backend"] = "gateway"
    own_gmail.gateway_key = GATEWAY_KEY

    own_gmail.run()

    [email] = own_gmail.sent
    assert email.via == "gateway" and own_gmail.relay_keys == [GATEWAY_KEY]


def test_weekend_picks_go_from_the_households_gmail_too(own_gmail, monkeypatch):
    own_gmail.set_up_sources()
    own_gmail.model_reply = {"picks": [{"ext_id": "le-1", "rank": 1, "why": "Puppets"}]}

    run_weekend_picks(own_gmail, monkeypatch, "en")

    [email] = own_gmail.sent
    assert email.subject.startswith("Weekend Picks")
    assert (email.via, email.from_addr) == ("gmail", PARENT)
    assert "nobody reads" not in email.text + (email.html or "")
    never_through_the_gateway(own_gmail)


def test_a_revoked_app_password_fails_the_night_saying_reading_and_sending_stopped(own_gmail, caplog):
    caplog.set_level(logging.INFO)
    caught_up_last_night(own_gmail)
    own_gmail.set_up_sources()
    revoke_app_password(own_gmail)
    own_gmail.sources["wilma"] = [msg("wilma", "message:1", "2026-09-27T08:15:00+03:00",
                                      "Retki torstaina", sender="Opettaja", subject="Retki")]

    assert own_gmail.run() == 0

    assert own_gmail.sent == []
    assert f"email send failed: Gmail didn't accept the App Password for {PARENT}" in caplog.text
    assert BOTH_STOPPED in caplog.text
    assert APP_PASSWORD not in caplog.text
    assert not own_gmail.state().get("brief_delivered_at")
    never_through_the_gateway(own_gmail)


# ── doctor


def test_doctor_says_the_brief_is_sent_from_the_households_gmail(own_gmail, capsys):
    own_gmail.set_up_sources()

    lines = doctor(own_gmail, capsys)

    assert lines["Brief sending"].startswith(ops.OK)
    assert f"own Gmail, {PARENT}" in lines["Brief sending"]
    assert lines["Gmail"].startswith(ops.OK)
    assert own_gmail.sent == [] and own_gmail.outbox.gmail_logins == 1
    never_through_the_gateway(own_gmail)


def test_doctor_says_a_rejected_app_password_stops_both_reading_and_sending(own_gmail, capsys):
    own_gmail.set_up_sources()
    revoke_app_password(own_gmail)

    lines = doctor(own_gmail, capsys)

    assert lines["Brief sending"].startswith(ops.FAIL)
    assert BOTH_STOPPED in lines["Brief sending"]
    assert lines["Gmail"].startswith(ops.FAIL)
    assert "the Brief, which is sent from this Gmail, can't be sent either" in lines["Gmail"]


def test_doctor_says_when_no_app_password_is_stored(own_gmail, capsys):
    line = doctor(own_gmail, capsys)["Brief sending"]  # the Sources aren't set up: none stored

    assert line.startswith(ops.FAIL) and "no Gmail App Password in the" in line
    assert "parent-recap setup gmail" in line


# ── `setup sending`, as the manage skill runs it


def test_setup_sending_gmail_turns_it_on_once_gmail_takes_the_app_password(harness, capsys):
    no_key(harness)
    harness.set_up_sources()

    code, out = setup_sending(harness, capsys, "gmail")

    assert code == 0 and out == {"result": "saved", "sending": "gmail"}
    assert saved_config(harness)["email"]["from_gmail"] is True
    assert harness.outbox.gmail_logins == 1 and harness.sent == []
    harness.sources = {"gmail": [msg("gmail", "1001", "2026-09-27T08:15:00+03:00", "Retki",
                                     sender="Opettaja <ope@kilo.example.fi>", subject="Retki")]}
    harness.model_reply = SAYS_SOMETHING
    harness.run()
    [email] = harness.sent
    assert email.via == "gmail"
    never_through_the_gateway(harness)


@pytest.mark.parametrize("household", ["gateway-ai", "no-gmail"])
def test_setup_sending_gmail_is_not_offered_with_the_gateway_as_the_ai_or_without_gmail(
        harness, capsys, household):
    if household == "gateway-ai":
        harness.config["llm"]["backend"] = "gateway"
    else:
        harness.config["gmail"] = {}
    harness.set_up_sources()

    code, out = setup_sending(harness, capsys, "gmail")

    assert code == 1 and out["result"] == "not-offered" and out["sending"] == "gateway"
    assert "from_gmail" not in saved_config(harness)["email"]
    assert harness.outbox.gmail_logins == 0


def test_setup_sending_gmail_with_a_refused_app_password_changes_nothing(harness, capsys):
    harness.set_up_sources()
    revoke_app_password(harness)

    code, out = setup_sending(harness, capsys, "gmail")

    assert code == 1 and (out["result"], out["sending"]) == ("rejected", "gateway")
    assert "parent-recap setup gmail" in out["next"]
    assert "from_gmail" not in saved_config(harness)["email"]


def test_setup_sending_gateway_goes_back_and_asks_for_a_send_only_key_to_confirm(own_gmail, capsys):
    code, out = setup_sending(own_gmail, capsys, "gateway")

    assert code == 0 and (out["result"], out["sending"]) == ("saved", "gateway")
    assert "from_gmail" not in saved_config(own_gmail)["email"]
    assert out["key_requested"] and out["unconfirmed"] == RECIPIENTS
    assert [a for a, _ in own_gmail.confirmations] == RECIPIENTS
    assert "have not confirmed yet" in out["next"]
    [made] = own_gmail.made_keys
    assert own_gmail.secrets[api.ACCOUNT] == made


def test_setup_sending_gateway_with_the_key_it_kept_needs_no_new_confirmation(own_gmail, capsys):
    own_gmail.secrets[api.ACCOUNT] = GATEWAY_KEY  # made by hand: bound to nobody

    code, out = setup_sending(own_gmail, capsys, "gateway")

    assert code == 0 and out["sending"] == "gateway" and out["unconfirmed"] == []
    assert own_gmail.key_requests == [] and own_gmail.confirmations == []
    assert "next" not in out


def test_setup_confirm_has_nothing_to_confirm_from_gmail(own_gmail, capsys):
    assert own_gmail.cli("setup", "confirm") == 0

    assert json.loads(capsys.readouterr().out) == {"result": "not-needed", "sending": "gmail"}
    never_through_the_gateway(own_gmail)


# ── the setup page


def sending(url: str, to: str) -> dict:
    r = call(url, "api/finish/sending", method="POST", body={"to": to})
    assert r.status == 200
    return r.json()


def test_finish_turns_sending_from_gmail_on_and_back_off(harness, page):
    at_finish(harness)
    harness.secrets[f"gmail-imap-{PARENT}"] = APP_PASSWORD

    assert call(page.url, "api/state").json()["sending"] == {"address": PARENT, "gmail": False}
    assert sending(page.url, "gmail") == {"result": "saved", "sending": "gmail"}

    assert saved_config(harness)["email"]["from_gmail"] is True
    assert call(page.url, "api/state").json()["sending"] == {"address": PARENT, "gmail": True}
    never_through_the_gateway(harness)

    # Back to the gateway, which asks for a send-only key and mails each Recipient a link.
    out = sending(page.url, "gateway")

    assert (out["result"], out["sending"]) == ("saved", "gateway")
    assert out["confirm"] == {"result": "unconfirmed", "unconfirmed": RECIPIENTS, "mailed": RECIPIENTS}
    assert "from_gmail" not in saved_config(harness)["email"]


@pytest.mark.parametrize("household", ["gateway-ai", "no-gmail"])
def test_finish_does_not_offer_it_with_the_gateway_as_the_ai_or_without_gmail(harness, page, household):
    if household == "gateway-ai":
        harness.config["llm"]["backend"] = "gateway"
    else:
        harness.config["gmail"] = {}
    at_finish(harness)

    out = sending(page.url, "gmail")

    assert out == {"result": "not-offered", "sending": "gateway"}
    assert "from_gmail" not in saved_config(harness)["email"]


def test_finish_keeps_the_gateway_when_gmail_refuses_the_app_password(harness, page):
    at_finish(harness)
    harness.secrets[f"gmail-imap-{PARENT}"] = APP_PASSWORD
    revoke_app_password(harness)

    out = sending(page.url, "gmail")

    assert out == {"result": "rejected", "sending": "gateway"}  # nothing that names the address
    assert "from_gmail" not in saved_config(harness)["email"]


def test_finish_checks_the_gmail_the_brief_is_sent_from(harness, page, schedule, monkeypatch):
    # Doctor's own Brief sending check, the others being the harness's (they're its own tests').
    monkeypatch.setattr(ops, "health_checks", lambda config, **k: [
        c for c in REAL_HEALTH_CHECKS(config, **{**k, "skip_llm": True}) if c.key.endswith("-sends")])
    harness.config["email"]["from_gmail"] = True
    at_finish(harness)
    harness.secrets[f"gmail-imap-{PARENT}"] = APP_PASSWORD
    revoke_app_password(harness)

    out = call(page.url, "api/finish/outcomes", method="POST", body={}).json()
    out = finished(page.url) if out["result"] == "checking" else out

    doctor_outcome = next(o for o in out["outcomes"] if o["outcome"] == "doctor")
    [sending_check] = doctor_outcome["checks"]
    assert (sending_check["check"], sending_check["status"], sending_check["reason"]) == \
        ("gmail-sends", "fail", "rejected")
    never_through_the_gateway(harness)


def test_the_page_has_its_words_for_gmail_sending_in_every_language():
    text = json.loads((PAGE_DIR / "text.json").read_text())
    for table in text.values():
        assert table["brief.send.gmail-refused"]
        assert table["health.check.gmail-sends"]
        for prefix in ("health", "finish"):
            for key in ("", ".rejected", ".no-password"):
                assert table[f"{prefix}.fail.gmail-sends{key}"]
        for result in ("not-offered", "rejected", "no-password", "no-answer", "save-failed"):
            assert table[f"finish.sending.{result}"]


def test_send_it_to_me_goes_from_the_households_gmail(harness, page):
    no_key(harness)
    harness.config["email"]["from_gmail"] = True
    at_the_first_brief(harness)
    make_brief(page.url)
    made = brief_made(page.url)

    r = send_it_to_me(page.url)

    assert r.json() == {"result": "sent", "to": PARENT}
    [email] = harness.sent
    assert (email.via, email.from_addr, email.to) == ("gmail", PARENT, [PARENT])
    assert email.html == made["html"]
    assert "nobody reads" not in email.html
    never_through_the_gateway(harness)


def test_send_it_to_me_says_when_gmail_refuses_the_app_password(harness, page):
    no_key(harness)
    harness.config["email"]["from_gmail"] = True
    at_the_first_brief(harness)
    make_brief(page.url)
    brief_made(page.url)
    harness.outbox.gmail_password = REVOKED

    r = send_it_to_me(page.url)

    assert r.json() == {"result": "gmail-refused"}
    assert harness.sent == []
    never_through_the_gateway(harness)


def test_asking_who_confirmed_asks_the_gateway_nothing_when_gmail_sends(harness, page):
    no_key(harness)
    harness.config["email"]["from_gmail"] = True
    at_finish(harness)

    r = call(page.url, "api/gmail/confirm", method="POST", body={"resend": True})

    assert r.json() == {"result": "not-needed", "unconfirmed": [], "mailed": []}
    # Nor does the page's asking every few seconds while it waits (#712).
    r = call(page.url, "api/gmail/confirmed", method="POST", body={})
    assert r.json() == {"result": "not-needed", "recipients": []}
    never_through_the_gateway(harness)


def test_saving_the_recipients_asks_for_no_key_when_gmail_sends(harness, page):
    no_key(harness)
    harness.config["email"]["from_gmail"] = True
    at_finish(harness)

    r = call(page.url, "api/gmail/recipients", method="POST", body={"recipients": [
        {"address": PARENT, "language": "zh"}, {"address": "new@example.fi", "language": "fi"}]})

    assert r.json()["result"] == "saved" and "confirm" not in r.json()
    never_through_the_gateway(harness)
