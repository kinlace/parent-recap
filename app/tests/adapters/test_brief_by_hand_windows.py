"""A Brief started by hand on a Windows PC (ADR 0019): `parent-recap run` on the gateway, with the
Gmail App Password and the Gateway Key in Windows' own Credential Manager.

The real Gmail Source reads the harness's IMAP server, and the model and the email are the
harness's fakes; the secrets are stored and read for real (conftest's `credential_manager`)."""
from __future__ import annotations

import logging

import pytest

from nights import caught_up_last_night
from test_gateway import KEY, URL, assert_key_never_leaked
from test_source_failures import gmail_messages

from family_brief import gateway
from family_brief.collectors import gmail
from family_brief.system import secrets

pytestmark = pytest.mark.windows

APP_PASSWORD = "abcd efgh ijkl mnop"
# Tonight's Gmail message 1 has Gmail's id 101.
REPLY = {"per_kid": [{"kid": "Mia", "notices": [{"text": "Retki to Nuuksio on Thu 1 Oct",
                                                 "refs": ["101"]}], "action_items": []}],
         "calendar_events": [], "message_digest": "**Mia**\n- Retki to Nuuksio on Thu 1 Oct"}


@pytest.fixture
def windows_pc(harness, credential_manager):
    """A Household on the gateway whose Gmail has one message tonight, with both secrets stored
    in Credential Manager."""
    harness.config["summary_language"] = "en"
    harness.config["llm"] = {"backend": "gateway", "gateway_url": URL}
    harness.config["wilma"]["enabled"] = False
    harness.config["whatsapp"]["enabled"] = False
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    caught_up_last_night(harness)
    account = gmail.keychain_account(harness.config["gmail"]["username"])
    credential_manager.extend([account, gateway.ACCOUNT])
    secrets.put(account, APP_PASSWORD)
    secrets.put(gateway.ACCOUNT, KEY)
    harness.sources["gmail"] = gmail_messages(1)
    harness.model_reply = REPLY
    return harness


def test_a_brief_started_by_hand_is_written_through_the_gateway_and_sent(windows_pc, capsys,
                                                                         caplog):
    caplog.set_level(logging.DEBUG)

    assert windows_pc.run() == 0

    [email] = windows_pc.sent
    assert "Retki to Nuuksio on Thu 1 Oct" in email.text
    [call] = windows_pc.model_calls
    assert windows_pc.model.served_by == ["gateway"]
    assert gateway.key() == KEY  # what the gateway adapter sends, read back from Credential Manager
    assert "Message number 1." in call.prompt
    assert list(windows_pc.state()["seen_message_ids"]["gmail"]) == ["101"]
    printed = capsys.readouterr()
    assert APP_PASSWORD not in printed.out + printed.err + caplog.text
    assert_key_never_leaked(windows_pc, printed.out + printed.err, caplog)
