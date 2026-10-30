"""The Brief sent through the maintainers' gateway (ADR 0028): the email Action's only transport
since ADR 0032, and doctor's check of it. The gateway's `POST /v1/brief` is a fake one
step further out, at `requests.post`, answering as ops/gateway/brief_relay.py does; the secrets
store is the `system` fixture's. Assertions are on the request the relay is sent and on what each
of its refusals is reported as."""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from typing import Any

import pytest
import requests

from fake_model import HTTPReply, _response
from family_brief import gateway as api
from family_brief.actions import email as email_action
from family_brief.config import Config

REAL_SEND = email_action.send  # captured before any test's outbox fixture fakes a transport

KEY = "sk-bf-fake-gateway-key"
URL = "https://gateway.example.test"
RECIPIENTS = ["parent@example.com", "partner@example.fi"]
SUBJECT = "Iltakooste 27.9. · 家庭日报"
TEXT = "Huomenna: Leon retki Nuuksioon, eväät mukaan. 小明周五开家长会。"
HTML = "<p>Huomenna: Leon retki Nuuksioon.</p>"
ICS = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nMETHOD:PUBLISH\r\nEND:VCALENDAR\r\n"
SENT = HTTPReply(200, {"sent": 2, "refused": 0})


@dataclass
class Relay:
    """The gateway's POST /v1/brief: records each request and answers from `replies`, one per
    request, where an exception is raised as requests raises a timeout or a refused connection."""
    replies: list[Any] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        self.requests.append({"url": url, **kwargs})
        reply = self.replies.pop(0) if self.replies else SENT
        if isinstance(reply, Exception):
            raise reply
        return _response(reply.status, reply.body)


@pytest.fixture
def relay(system, monkeypatch) -> Relay:
    system.secrets[api.ACCOUNT] = KEY
    relay = Relay()
    monkeypatch.setattr(api.requests, "post", relay.post)
    return relay


def household(**email: Any) -> Config:
    return Config.model_validate({"kids": [{"name": "Mia"}], "gmail": {"username": "parent@gmail.com"},
                                  "llm": {"backend": "claude", "gateway_url": URL},
                                  "email": email})


def send(cfg: Config | None = None, **kwargs: Any) -> None:
    args: dict[str, Any] = {"subject": SUBJECT, "body_text": TEXT, "to_addrs": RECIPIENTS, "body_html": HTML}
    REAL_SEND(cfg or household(), **{**args, **kwargs})


# ── the request


def test_the_gateway_is_sent_the_brief_with_the_key_in_its_header(relay):
    send(attachments=[("parent-recap-2026-09-27.ics", ICS, "text/calendar")])

    [request] = relay.requests
    assert request["url"] == f"{URL}/v1/brief"
    assert request["headers"] == {"x-api-key": KEY}
    assert request["json"] == {
        "to": RECIPIENTS, "subject": SUBJECT, "text": TEXT, "html": HTML,
        "attachments": [{"filename": "parent-recap-2026-09-27.ics", "content_type": "text/calendar",
                         "data": base64.b64encode(ICS).decode()}]}


def test_without_html_or_attachments_neither_is_sent(relay):
    send(body_html=None)

    assert relay.requests[0]["json"] == {"to": RECIPIENTS, "subject": SUBJECT, "text": TEXT}


@pytest.mark.parametrize("before", [{"sender": "gmail"}, {"sender": "gmail", "from_addr": "someone@gmail.com"},
                                    {"sender": "gateway"}])
def test_a_config_from_before_every_brief_went_through_the_gateway_sends_through_it(relay, system, before):
    # ADR 0032: the Household's Gmail sends nothing now, so neither its App Password nor the
    # address it sent from is used.
    send(household(**before))

    [request] = relay.requests
    assert request["url"] == f"{URL}/v1/brief" and request["headers"] == {"x-api-key": KEY}
    assert "someone@gmail.com" not in str(relay.requests)
    assert set(system.secrets) == {api.ACCOUNT}


def test_nobody_to_send_to_sends_nothing(relay):
    send(to_addrs=[])

    assert relay.requests == []


# ── refusals, reported as a failed model call is


def test_without_a_key_and_the_gateway_as_the_ai_nothing_is_sent_and_it_says_to_ask_the_team(
        relay, system):
    # A key that writes the Brief is the maintainers' to hand out (ADR 0033).
    del system.secrets[api.ACCOUNT]
    cfg = household(to=RECIPIENTS)
    cfg.llm.backend = "gateway"

    with pytest.raises(api.NoKey, match="ask the Parent Recap team for one, then store it with "
                                        "parent-recap setup gateway") as e:
        send(cfg)
    assert e.value.missing
    assert relay.requests == []


def test_without_a_key_it_asks_the_gateway_for_one_and_says_to_confirm(relay, system):
    del system.secrets[api.ACCOUNT]
    relay.replies = [HTTPReply(200, {
        "key": "sk-bf-made-on-request", "mailed": RECIPIENTS,
        "recipients": [{"address": a, "confirmed": False} for a in RECIPIENTS]})]

    with pytest.raises(api.GatewayError) as e:
        send(household(to=["parent@example.com", {"address": "partner@example.fi", "language": "fi"}]))

    [request] = relay.requests
    assert request["url"] == f"{URL}/v1/brief/key" and "x-api-key" not in request["headers"]
    assert request["json"] == {"recipients": [{"address": "parent@example.com", "language": "en"},
                                              {"address": "partner@example.fi", "language": "fi"}]}
    assert system.secrets[api.ACCOUNT] == "sk-bf-made-on-request"
    assert str(e.value).startswith("no Gateway Key was stored: parent@example.com, partner@example.fi "
                                   "have not confirmed yet")
    assert "a confirmation email was sent to parent@example.com, partner@example.fi" in str(e.value)
    assert "sk-bf-made-on-request" not in str(e.value)


def test_a_key_the_gateway_cannot_make_is_said_and_nothing_is_stored(relay, system):
    del system.secrets[api.ACCOUNT]
    relay.replies = [HTTPReply(429, {"error": "too many keys asked for today; try again tomorrow"})]

    with pytest.raises(api.GatewayError, match="the gateway couldn't make one just now: .*tomorrow"):
        send(household(to=RECIPIENTS))
    assert api.ACCOUNT not in system.secrets


def test_a_brief_to_no_confirmed_recipient_fails_saying_to_confirm(relay):
    relay.replies = [HTTPReply(403, {"error": "recipient not confirmed", "unconfirmed": RECIPIENTS})]

    with pytest.raises(api.GatewayError) as e:
        send()
    assert e.value.status == 403
    assert str(e.value).startswith(api.UNCONFIRMED_MESSAGE + " (parent@example.com, partner@example.fi)")
    assert "setup confirm --resend" in str(e.value)


def test_a_recipient_left_out_for_not_confirming_is_logged_and_the_send_succeeds(relay, caplog):
    caplog.set_level(logging.INFO)
    relay.replies = [HTTPReply(200, {"sent": 1, "refused": 0, "unconfirmed": ["partner@example.fi"]})]

    send()

    assert "not sent to partner@example.fi, who has not confirmed their address yet" in caplog.text


# ── binding the Recipients (ADR 0033)


def test_binding_sends_the_recipients_with_the_key_and_reads_who_confirmed(relay):
    relay.replies = [HTTPReply(200, {"recipients": [{"address": "parent@example.com", "confirmed": True},
                                                    {"address": "new@example.fi", "confirmed": False}],
                                     "mailed": ["new@example.fi"]})]
    to = [{"address": "parent@example.com", "language": "en"}, {"address": "new@example.fi", "language": "fi"}]

    got = api.bind(URL, KEY, to, resend=True)

    assert (got.unconfirmed, got.mailed, got.key) == (["new@example.fi"], ["new@example.fi"], None)
    [request] = relay.requests
    assert request["url"] == f"{URL}/v1/brief/recipients"
    assert request["headers"] == {"x-api-key": KEY}
    assert request["json"] == {"recipients": to, "resend": True}


@pytest.mark.parametrize("reply, says", [
    (HTTPReply(401, {"error": f"unknown key {KEY}"}), "doesn't know the Gateway Key"),
    (HTTPReply(429, {"error": "too many confirmation emails today"}), "try again tomorrow"),
    (HTTPReply(503, {"error": "down"}), "couldn't check who confirmed"),
])
def test_a_refused_binding_says_why_without_the_key(relay, reply, says):
    relay.replies = [reply]

    with pytest.raises(api.GatewayError, match=says) as e:
        api.bind(URL, KEY, [{"address": "parent@example.com", "language": "en"}])
    assert KEY not in str(e.value)


def test_a_gateway_from_before_keys_on_request_counts_everyone_as_confirmed(relay):
    relay.replies = [HTTPReply(404, "Not found")]

    assert api.bind(URL, KEY, [{"address": "parent@example.com", "language": "en"}]).unconfirmed == []


@pytest.mark.parametrize("reply, status, says", [
    (HTTPReply(401, {"error": "missing, unknown or revoked Gateway Key"}), 401,
     "the gateway doesn't know the Gateway Key, or it was revoked"),
    (HTTPReply(429, {"error": "this key has sent its 4 Briefs for today"}), 429,
     "the Gateway Key has sent all the Briefs it may send today"),
    (HTTPReply(502, {"error": "couldn't reach email delivery (OSError)"}), 502,
     "the gateway couldn't send the Brief just now"),
    (HTTPReply(503, {"error": "the gateway couldn't check the key; try again later"}), 503,
     "the gateway couldn't send the Brief just now"),
    (HTTPReply(400, {"error": "`to` has an address that isn't a plain name@domain"}), 400,
     "the gateway refused the Brief"),
])
def test_a_refused_send_fails_saying_why(relay, reply, status, says):
    relay.replies = [reply]

    with pytest.raises(api.GatewayError) as e:
        send()

    assert e.value.status == status
    assert str(e.value).startswith(says)
    assert reply.body["error"] in str(e.value)  # what the relay said, for us
    assert len(relay.requests) == 1  # never sent twice: a send that failed is tried tomorrow


def test_a_gateway_that_doesnt_answer_says_so(relay):
    relay.replies = [requests.ConnectionError("refused")]

    with pytest.raises(api.GatewayError, match="couldn't reach the gateway at gateway.example.test") as e:
        send()
    assert e.value.status is None


def test_a_send_that_times_out_says_so(relay):
    relay.replies = [requests.Timeout("slow")]

    with pytest.raises(TimeoutError, match="didn't answer"):
        send()


def test_an_error_that_echoes_the_key_says_key_in_its_place(relay):
    relay.replies = [HTTPReply(401, {"error": f"unknown key {KEY}"})]

    with pytest.raises(api.GatewayError) as e:
        send()
    assert KEY not in str(e.value) and "<key>" in str(e.value)


def test_recipients_the_mail_service_refused_are_logged_without_failing_the_send(relay, caplog):
    caplog.set_level(logging.INFO)
    relay.replies = [HTTPReply(200, {"sent": 1, "refused": 1})]

    send()

    assert "refused 1 of 2 Recipients" in caplog.text
    assert not any(a in caplog.text for a in RECIPIENTS)


# ── doctor's check, which sends nothing


def test_the_check_posts_no_message_and_a_known_key_passes(relay):
    relay.replies = [HTTPReply(400, {"error": "`to` must be a list of addresses"})]

    assert api.send_test(URL, KEY) == (api.WORKS, None)
    [request] = relay.requests
    assert request["url"] == f"{URL}/v1/brief" and request["json"] == {}


@pytest.mark.parametrize("reply, outcome", [
    (HTTPReply(401, {"error": "missing, unknown or revoked Gateway Key"}), api.UNKNOWN),
    (HTTPReply(503, {"error": "the gateway couldn't check the key; try again later"}), api.FAILED),
    (HTTPReply(404, "not found"), api.FAILED),
    (requests.ConnectionError("refused"), api.NO_ANSWER),
])
def test_the_check_tells_a_refused_key_from_a_gateway_that_is_down(relay, reply, outcome):
    relay.replies = [reply]

    got, detail = api.send_test(URL, KEY)

    assert got == outcome and detail
