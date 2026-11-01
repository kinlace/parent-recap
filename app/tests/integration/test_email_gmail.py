"""The Brief email as a Recipient receives it when the Household's own Gmail sends it (ADR 0034),
and which transport the config picks. The real email action runs here, with fakes one step
further out: Gmail's SMTP server, the gateway's relay and the `system` fixture's secrets store.
Assertions are on the message handed to the server, parsed back the way a mail client reads it."""
from __future__ import annotations

import email
import smtplib
from email.message import Message
from email.policy import default
from typing import Any

import pytest

from family_brief import gateway as api
from family_brief.actions import email as email_action
from family_brief.collectors.gmail import keychain_account
from family_brief.config import Config
from test_email_gateway import Relay

REAL_SEND = email_action.send  # captured before any test's outbox fixture fakes a transport

SENDER = "parent@gmail.com"
APP_PASSWORD = "abcdabcdabcdabcd"
RECIPIENTS = ["parent@gmail.com", "partner@example.fi"]
SUBJECT = "Iltakooste 27.9. · 家庭日报"
TEXT = "Huomenna: Leon retki Nuuksioon, eväät mukaan. 小明周五开家长会。"
HTML = "<p>Huomenna: Leon retki Nuuksioon, eväät mukaan.</p><p>小明周五开家长会。</p>"
ICS = (b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nMETHOD:PUBLISH\r\nBEGIN:VEVENT\r\n"
       b"SUMMARY:Retki Nuuksioon\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


class FakeSMTP:
    """smtplib.SMTP_SSL, recording what each connection is asked to do instead of reaching Gmail.
    `refuse_login` makes Gmail refuse the App Password, as it does a revoked one."""
    connections: list[FakeSMTP] = []
    refuse_login = False

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.address = (host, port)
        self.timeout = timeout
        self.calls: list[str] = []
        self.login_args: tuple[str, str] | None = None
        self.sent: list[tuple[str, list[str], str]] = []
        FakeSMTP.connections.append(self)

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *_a: Any) -> None:
        self.calls.append("quit")

    def close(self) -> None:
        self.calls.append("close")

    def login(self, user: str, password: str) -> None:
        self.calls.append("login")
        self.login_args = (user, password)
        if FakeSMTP.refuse_login:
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")

    def sendmail(self, from_addr: str, to_addrs: list[str], msg: str) -> dict:
        self.calls.append("sendmail")
        self.sent.append((from_addr, list(to_addrs), msg))
        return {}


@pytest.fixture
def smtp(system, monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    FakeSMTP.connections = []
    FakeSMTP.refuse_login = False
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP", None)  # never the unencrypted server
    system.secrets[keychain_account(SENDER)] = APP_PASSWORD
    return FakeSMTP


@pytest.fixture
def relay(system, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The gateway's relay, which a Household sending from its Gmail never calls, though a key it
    had before stays stored (ADR 0034)."""
    system.secrets[api.ACCOUNT] = "sk-bf-a-key-it-had-before"
    relay = Relay()
    monkeypatch.setattr(api.requests, "post", relay.post)
    return relay.requests


def household(backend: str = "claude", from_gmail: bool = True, username: str | None = SENDER) -> Config:
    return Config.model_validate({"kids": [{"name": "Mia"}], "gmail": {"username": username},
                                  "llm": {"backend": backend},
                                  "email": {"from_gmail": from_gmail, "to": RECIPIENTS}})


def send(cfg: Config | None = None, **kwargs: Any) -> None:
    args: dict[str, Any] = {"subject": SUBJECT, "body_text": TEXT, "to_addrs": RECIPIENTS, "body_html": HTML}
    REAL_SEND(cfg or household(), **{**args, **kwargs})


def received(smtp: type[FakeSMTP]) -> Message:
    [conn] = smtp.connections
    [(_from, _to, raw)] = conn.sent
    return email.message_from_string(raw, policy=default)


def parts_by_subtype(alt: Message) -> dict[str, Message]:
    return {p.get_content_subtype(): p for p in alt.iter_parts()}


# ── which transport


def test_turned_on_with_its_own_ai_the_brief_goes_from_gmail_and_the_gateway_is_never_called(smtp, relay):
    send()

    assert len(smtp.connections) == 1
    assert relay == []


@pytest.mark.parametrize("cfg", [
    household(from_gmail=False),            # the default
    household(backend="gateway"),           # the gateway writes it: never from Gmail (ADR 0034)
    household(username=None),               # no Gmail connected
], ids=["off", "gateway-ai", "no-gmail"])
def test_otherwise_the_brief_goes_through_the_gateway(smtp, relay, cfg):
    send(cfg)

    assert smtp.connections == []
    assert [c["url"] for c in relay] == [f"{cfg.llm.gateway_url}/v1/brief"]


def test_from_gmail_a_reply_reaches_the_parent_so_no_address_to_write_to_is_named():
    assert email_action.write_to(household()) is None
    assert email_action.write_to(household(from_gmail=False)) == RECIPIENTS[0]


# ── the message


def test_the_email_has_a_plain_text_and_an_html_part_in_utf8_with_chinese_and_finnish_intact(smtp):
    send()

    alt = received(smtp)
    assert alt.get_content_type() == "multipart/alternative"
    parts = parts_by_subtype(alt)
    assert list(parts) == ["plain", "html"]
    for part in parts.values():
        assert part.get_content_charset() == "utf-8"
    assert parts["plain"].get_content().strip() == TEXT
    assert parts["html"].get_content().strip() == HTML


def test_without_html_the_email_is_the_plain_text_alone_inside_the_alternative(smtp):
    send(body_html=None)

    alt = received(smtp)
    assert alt.get_content_type() == "multipart/alternative"
    assert list(parts_by_subtype(alt)) == ["plain"]


def test_a_calendar_attachment_goes_beside_the_alternative_as_a_publish_ics(smtp):
    send(attachments=[("events.ics", ICS, "text/calendar")])

    msg = received(smtp)
    assert msg.get_content_type() == "multipart/mixed"
    alt, ics = msg.iter_parts()
    assert alt.get_content_type() == "multipart/alternative"
    assert parts_by_subtype(alt)["plain"].get_content().strip() == TEXT
    assert ics.get_content_type() == "text/calendar"
    assert ics.get_param("method") == "PUBLISH"
    assert ics.get_filename() == "events.ics"
    assert ics.get_payload(decode=True) == ICS


def test_it_signs_in_over_ssl_with_the_app_password_it_reads_with_and_comes_from_that_gmail(smtp):
    send()

    [conn] = smtp.connections
    assert conn.address == ("smtp.gmail.com", 465)
    assert conn.calls == ["login", "sendmail", "quit"]
    assert conn.login_args == (SENDER, APP_PASSWORD)
    [(from_addr, to_addrs, _raw)] = conn.sent
    assert (from_addr, to_addrs) == (SENDER, RECIPIENTS)
    msg = received(smtp)
    assert msg["Subject"] == SUBJECT
    assert msg["From"] == SENDER
    assert [a.addr_spec for a in msg["To"].addresses] == RECIPIENTS
    assert msg["Date"]


# ── when it can't


def test_with_no_app_password_it_fails_clearly_and_sends_nothing(smtp, system):
    del system.secrets[keychain_account(SENDER)]

    with pytest.raises(email_action.GmailError, match=f"no Gmail App Password .* for {SENDER}") as e:
        send()

    assert e.value.reason == email_action.NO_PASSWORD
    assert smtp.connections == []


def test_a_refused_app_password_says_both_reading_and_sending_stopped_without_the_password(smtp):
    smtp.refuse_login = True

    with pytest.raises(email_action.GmailError) as e:
        send()

    assert e.value.reason == email_action.REJECTED
    assert "neither read that Gmail nor send the Brief from it" in str(e.value)
    assert APP_PASSWORD not in str(e.value)
    [conn] = smtp.connections
    assert conn.sent == [] and "close" in conn.calls


def test_gmail_out_of_reach_is_said(system, monkeypatch):
    system.secrets[keychain_account(SENDER)] = APP_PASSWORD

    def unreachable(*_a: Any, **_k: Any) -> None:
        raise OSError("Network is unreachable")
    monkeypatch.setattr(smtplib, "SMTP_SSL", unreachable)

    with pytest.raises(email_action.GmailError, match="couldn't reach Gmail") as e:
        send()
    assert e.value.reason == email_action.NO_ANSWER


def test_with_no_recipients_nothing_is_sent(smtp, system, monkeypatch):
    def read(name, default=None):
        raise AssertionError("the secrets store was read")
    monkeypatch.setattr(system.secrets, "get", read)  # not even a read of the App Password

    send(to_addrs=[])

    assert smtp.connections == []


# ── the check doctor and the setup page make


def test_the_check_signs_in_and_out_sending_nothing(smtp):
    assert email_action.check_gmail(SENDER)[0] == email_action.WORKS

    [conn] = smtp.connections
    assert conn.calls == ["login", "quit"]


def test_the_check_says_a_refused_app_password(smtp):
    smtp.refuse_login = True

    outcome, detail = email_action.check_gmail(SENDER)

    assert outcome == email_action.REJECTED
    assert "neither read that Gmail nor send the Brief from it" in detail
