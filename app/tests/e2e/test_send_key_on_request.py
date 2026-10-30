"""A send-only Gateway Key on request (ADR 0033): a Household whose AI is its own Claude Code or
Codex and that has no Gateway Key gets one from the gateway, bound to its Recipients, who each get
an email with a link to confirm; the gateway sends the Brief only to those who confirmed. The
setup page asks for it once the Recipients are saved, `setup confirm` from the chat, and doctor
(with it the page's Check and Finish) and the evening run on an update; doctor and the page bind a
changed Recipient, who gets a link of their own.

The gateway is the outbox's (fake_outbox.py), which makes keys and binds addresses as
ops/gateway/brief_relay.py does; `harness.confirm` is a Recipient pressing their link. What each
of the gateway's answers means is the email Action's own test's (integration/test_email_gateway.py).
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta

import pytest
import time_machine

from conftest import NOW
from nights import caught_up_last_night
from family_brief import gateway as api, install_record, ops
from family_brief.config import Config
from setup_page import call, config_file, source, welcome
from setup_page import clock, page  # noqa: F401
from test_gateway import KEY
from test_gateway_sends import doctor, no_key, wilma_only  # noqa: F401

PARENT, PARTNER = "parent@example.com", "partner@example.com"


def made_key(harness) -> str:
    [key] = harness.made_keys
    return key


def assert_made_key_never_leaked(harness, *texts: str) -> None:
    for key in harness.made_keys:
        assert not any(key in t for t in texts)
        assert not any(key in str(email.__dict__) for email in harness.sent)


# ── the setup page


def save_recipients(url: str, *recipients: dict, key: str | None = None):
    return call(url, "api/gmail/recipients", method="POST",
                body={"recipients": list(recipients), **({"key": key} if key is not None else {})})


@pytest.mark.parametrize("ai", ["claude", "codex"])
def test_saving_the_recipients_gets_a_key_and_mails_each_a_link_in_their_language(harness, page,
                                                                                caplog, ai):
    caplog.set_level(logging.DEBUG)
    welcome(page.url, ai=ai, partner={"address": PARTNER, "language": "zh"})
    source(page.url, "wilma", "skip")

    r = save_recipients(page.url, {"address": " Parent@Example.com ", "language": "fi"},
                        {"address": PARTNER, "language": "zh"})

    assert r.status == 200 and r.json()["result"] == "saved"
    assert r.json()["confirm"] == {"result": "unconfirmed", "unconfirmed": [PARENT, PARTNER],
                                   "mailed": [PARENT, PARTNER]}
    # The setup parent first: the email says they asked.
    assert harness.key_requests == [[{"address": PARENT, "language": "fi"},
                                     {"address": PARTNER, "language": "zh"}]]
    assert harness.confirmations == [(PARENT, "fi"), (PARTNER, "zh")]
    # Stored as a pasted key is, so uninstall removes it, and never shown.
    assert harness.secrets == {api.ACCOUNT: made_key(harness)}
    assert install_record.entries("keychain") == [api.ACCOUNT]
    assert harness.sent == [] and harness.model_calls == []
    assert_made_key_never_leaked(harness, r.body.decode(), caplog.text)


def test_the_page_sends_the_link_again_and_says_once_everyone_confirmed(harness, page):
    welcome(page.url, ai="claude")
    source(page.url, "wilma", "skip")
    save_recipients(page.url, {"address": PARENT, "language": "en"})

    again = call(page.url, "api/gmail/confirm", method="POST", body={"resend": True}).json()

    assert again == {"result": "unconfirmed", "unconfirmed": [PARENT], "mailed": [PARENT]}
    assert harness.confirmations == [(PARENT, "en")] * 2
    assert len(harness.made_keys) == 1  # the same key, not a new one

    harness.confirm(PARENT)
    out = call(page.url, "api/gmail/confirm", method="POST", body={}).json()

    assert out == {"result": "confirmed", "unconfirmed": [], "mailed": []}
    assert len(harness.confirmations) == 2


def test_a_changed_recipient_on_the_page_is_bound_and_gets_a_link_of_their_own(harness, page):
    welcome(page.url, ai="claude")
    source(page.url, "wilma", "skip")
    save_recipients(page.url, {"address": PARENT, "language": "en"})
    harness.confirm(PARENT)

    r = save_recipients(page.url, {"address": PARENT, "language": "en"},
                        {"address": "new@example.fi", "language": "fi"})

    assert r.json()["confirm"] == {"result": "unconfirmed", "unconfirmed": ["new@example.fi"],
                                   "mailed": ["new@example.fi"]}
    assert harness.confirmations == [(PARENT, "en"), ("new@example.fi", "fi")]
    assert len(harness.key_requests) == 1


def test_a_key_the_gateway_cannot_make_leaves_the_recipients_saved_and_says_to_try_again(
        harness, page):
    welcome(page.url, ai="claude")
    source(page.url, "wilma", "skip")
    harness.key_answer = (429, {"error": "too many keys asked for today; try again tomorrow"})

    r = save_recipients(page.url, {"address": PARENT, "language": "en"})

    assert r.json()["result"] == "saved"
    assert r.json()["confirm"] == {"result": "no-key", "unconfirmed": [], "mailed": []}
    assert harness.secrets == {}
    assert [t.address for t in Config.load(config_file(harness)).email.to] == [PARENT]

    harness.key_answer = None
    out = call(page.url, "api/gmail/confirm", method="POST", body={"resend": True}).json()

    assert out["result"] == "unconfirmed" and api.ACCOUNT in harness.secrets


def test_a_pasted_key_is_used_instead_and_none_is_asked_for(harness, page):
    welcome(page.url, ai="claude")
    source(page.url, "wilma", "skip")

    r = save_recipients(page.url, {"address": PARENT, "language": "en"}, key=KEY)

    # A key the maintainers made by hand isn't bound: the gateway sends with it to anyone.
    assert r.json()["confirm"] == {"result": "confirmed", "unconfirmed": [], "mailed": []}
    assert harness.key_requests == [] and harness.secrets == {api.ACCOUNT: KEY}


def test_with_the_gateway_as_the_ai_no_key_is_asked_for(harness, page):
    # Its key writes the Brief and costs the maintainers money: they hand it out (ADR 0033).
    welcome(page.url, ai="gateway")
    source(page.url, "wilma", "skip")

    r = save_recipients(page.url, {"address": PARENT, "language": "en"})

    assert r.json()["result"] == "saved" and "confirm" not in r.json()
    assert harness.key_requests == [] and harness.secrets == {}
    r = call(page.url, "api/gmail/confirm", method="POST", body={"resend": True})
    assert r.status == 400


# ── setup confirm, from the chat


def test_setup_confirm_gets_a_key_and_says_who_to_confirm(wilma_only, capsys, caplog):
    no_key(wilma_only)

    assert wilma_only.cli("setup", "confirm") == 0

    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "unconfirmed" and out["key_requested"] is True
    assert out["unconfirmed"] == out["mailed"] == [PARENT, PARTNER]
    assert "brief@parentrecap.com" in out["next"] and "setup confirm --resend" in out["next"]
    assert_made_key_never_leaked(wilma_only, json.dumps(out), caplog.text)

    wilma_only.confirm(PARENT, PARTNER)
    assert wilma_only.cli("setup", "confirm", "--resend") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "confirmed" and out["mailed"] == []
    assert len(wilma_only.key_requests) == 1


def test_setup_confirm_with_the_gateway_as_the_ai_says_the_key_comes_from_the_team(wilma_only, capsys):
    wilma_only.config["llm"]["backend"] = "gateway"
    no_key(wilma_only)

    assert wilma_only.cli("setup", "confirm") == 1

    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "no-key" and "setup gateway" in out["next"]
    assert wilma_only.key_requests == []


# ── doctor, and with it Check and Finish


def test_doctor_fails_until_someone_confirms_then_warns_until_everyone_has(wilma_only, capsys):
    no_key(wilma_only)
    wilma_only.set_up_sources()

    line = doctor(wilma_only, capsys)["Brief sending"]
    assert line.startswith(ops.FAIL) and "have not confirmed yet" in line

    wilma_only.confirm(PARENT)
    line = doctor(wilma_only, capsys)["Brief sending"]
    assert line.startswith(ops.WARN)
    assert f"{PARTNER} has not confirmed yet" in line

    wilma_only.confirm(PARTNER)
    assert doctor(wilma_only, capsys)["Brief sending"].startswith(ops.OK)
    assert len(wilma_only.key_requests) == 1  # one key, asked for once


def test_doctor_names_the_unconfirmed_addresses_for_the_setup_page(wilma_only):
    no_key(wilma_only)
    wilma_only.set_up_sources()
    wilma_only.cli("doctor", "--skip-llm")  # writes the config, and gets the key

    checks = ops.health_checks(str(config_file(wilma_only)), skip_llm=True, schedule=False)

    [sending] = [c for c in checks if c.key == "gateway-sends"]
    assert (sending.reason, sending.groups) == ("unconfirmed", (PARENT, PARTNER))


def test_a_recipient_added_in_manage_is_bound_by_doctor_and_mailed_a_link(wilma_only, capsys):
    # manage changes email.to in the config, then runs doctor (skills/manage/SKILL.md).
    no_key(wilma_only)
    wilma_only.set_up_sources()
    doctor(wilma_only, capsys)
    wilma_only.confirm(PARENT, PARTNER)
    wilma_only.config["email"]["to"].append({"address": "grandma@example.fi", "language": "fi"})

    line = doctor(wilma_only, capsys)["Brief sending"]

    assert line.startswith(ops.WARN) and "grandma@example.fi has not confirmed yet" in line
    assert wilma_only.confirmations[-1] == ("grandma@example.fi", "fi")
    assert wilma_only.made_keys[made_key(wilma_only)][-1]["address"] == "grandma@example.fi"


def hand_edited_before_0_10(harness) -> None:
    """#638: a Household that sent the Brief from its own Gmail, whose AI added a second Recipient
    to the config by hand, as the setup page took only one: it updates with no Gateway Key."""
    harness.config["email"] = {"enabled": True, "sender": "gmail", "from_addr": "parent@gmail.com",
                               "to": ["Parent@Hotmail.com", PARTNER]}
    no_key(harness)


def test_a_hand_edited_config_that_sent_from_gmail_gets_a_key_bound_to_both_recipients(
        wilma_only, capsys, caplog):
    hand_edited_before_0_10(wilma_only)
    wilma_only.set_up_sources()

    line = doctor(wilma_only, capsys)["Brief sending"]

    assert line.startswith(ops.FAIL)
    assert f"parent@hotmail.com, {PARTNER} have not confirmed yet" in line
    assert "setup confirm --resend" in line and "Junk" in line
    assert wilma_only.confirmations == [("parent@hotmail.com", "en"), (PARTNER, "en")]

    wilma_only.confirm("parent@hotmail.com")
    assert wilma_only.run() == 0

    [email] = wilma_only.sent
    assert email.to == ["Parent@Hotmail.com"]  # as written: the relay matches it in any case
    assert f"not sent to {PARTNER}, who has not confirmed their address yet" in caplog.text
    assert "parent@gmail.com" not in str(wilma_only.relay_keys) + str(email.__dict__)


def test_a_key_the_gateway_cannot_make_on_an_update_says_what_to_do(wilma_only, capsys, caplog):
    hand_edited_before_0_10(wilma_only)
    wilma_only.set_up_sources()
    wilma_only.key_answer = TimeoutError("timed out")

    line = doctor(wilma_only, capsys)["Brief sending"]
    wilma_only.run()

    for said in (line, caplog.text):
        assert "the gateway couldn't make one just now" in said
        assert "`parent-recap setup confirm` asks now)" in said and "next evening" in said
    assert line.endswith("from its own address (Parent Recap asks again at the next evening run; "
                         "`parent-recap setup confirm` asks now)")
    assert wilma_only.sent == [] and api.ACCOUNT not in wilma_only.secrets

    wilma_only.key_answer = None
    assert wilma_only.cli("setup", "confirm") == 0
    assert wilma_only.confirmations == [("parent@hotmail.com", "en"), (PARTNER, "en")]


def test_a_key_made_by_hand_is_never_bound_or_said_to_need_confirming(wilma_only, capsys):
    wilma_only.set_up_sources()

    assert doctor(wilma_only, capsys)["Brief sending"].startswith(ops.OK)
    assert wilma_only.key_requests == [] and wilma_only.confirmations == []


# ── the evening run


def test_the_brief_goes_only_to_the_recipients_who_confirmed(wilma_only, caplog):
    no_key(wilma_only)
    wilma_only.cli("setup", "confirm")
    wilma_only.confirm(PARENT)

    assert wilma_only.run() == 0

    [email] = wilma_only.sent
    assert email.to == [PARENT]
    assert wilma_only.relay_keys == [made_key(wilma_only)]
    assert f"not sent to {PARTNER}, who has not confirmed their address yet" in caplog.text
    assert wilma_only.state()["brief_delivered_at"]


def test_with_nobody_confirmed_the_night_waits_and_the_note_says_to_confirm(wilma_only, caplog):
    caught_up_last_night(wilma_only)
    no_key(wilma_only)
    wilma_only.cli("setup", "confirm")

    assert wilma_only.run() == 0

    assert wilma_only.sent == []
    assert f"email send failed: {api.UNCONFIRMED_MESSAGE} ({PARENT}, {PARTNER})" in caplog.text
    assert "press the link in the confirmation email" in caplog.text
    assert not wilma_only.state().get("brief_delivered_at")

    wilma_only.confirm(PARTNER)
    with time_machine.travel(NOW + timedelta(days=1), tick=False):
        assert wilma_only.run() == 0
    [email] = wilma_only.sent
    assert email.to == [PARTNER] and "Something new tonight" in email.text
