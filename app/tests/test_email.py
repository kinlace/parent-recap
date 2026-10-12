"""The Brief email as a Recipient receives it. The real email action runs here, with fakes one step
further out: the SMTP server and the Keychain (the harness's /usr/bin/security). Assertions are on
the message handed to the server, parsed back the way a mail client reads it."""
from __future__ import annotations

import email
import re
import smtplib
from email.message import Message
from email.policy import default
from typing import Any

import pytest

from family_brief.actions import email as email_action
from family_brief.collectors.gmail import keychain_account

REAL_SEND = email_action.send  # captured before the harness fakes it

SENDER = "parent@example.com"
APP_PASSWORD = "abcdabcdabcdabcd"
RECIPIENTS = ["parent@example.com", "partner@example.fi"]
SUBJECT = "Iltakooste 27.9. · 家庭日报"
TEXT = "Huomenna: Leon retki Nuuksioon, eväät mukaan. 小明周五开家长会。"
HTML = "<p>Huomenna: Leon retki Nuuksioon, eväät mukaan.</p><p>小明周五开家长会。</p>"
ICS = (b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nMETHOD:PUBLISH\r\nBEGIN:VEVENT\r\n"
       b"SUMMARY:Retki Nuuksioon\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


class FakeSMTP:
    """smtplib.SMTP, recording what each connection is asked to do instead of reaching Gmail."""
    connections: list[FakeSMTP] = []

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

    def starttls(self) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append("login")
        self.login_args = (user, password)

    def sendmail(self, from_addr: str, to_addrs: list[str], msg: str) -> None:
        self.calls.append("sendmail")
        self.sent.append((from_addr, list(to_addrs), msg))


@pytest.fixture
def smtp(harness, monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    FakeSMTP.connections = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    harness.keychain[keychain_account(SENDER)] = APP_PASSWORD
    return FakeSMTP


def send(**kwargs: Any) -> None:
    args: dict[str, Any] = {"subject": SUBJECT, "body_text": TEXT, "from_addr": SENDER,
                            "to_addrs": RECIPIENTS, "body_html": HTML}
    REAL_SEND(**{**args, **kwargs})


def received(smtp: type[FakeSMTP]) -> Message:
    [conn] = smtp.connections
    [(_from, _to, raw)] = conn.sent
    return email.message_from_string(raw, policy=default)


def parts_by_subtype(alt: Message) -> dict[str, Message]:
    return {p.get_content_subtype(): p for p in alt.iter_parts()}


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


@pytest.mark.parametrize("attachments", [None, []])
def test_without_attachments_the_email_is_the_plain_and_html_alternative_alone(smtp, attachments):
    send(attachments=attachments)

    msg = received(smtp)
    assert msg.get_content_type() == "multipart/alternative"
    assert [p.get_content_type() for p in msg.iter_parts()] == ["text/plain", "text/html"]


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
    assert ics.get_content_disposition() == "attachment"
    assert ics.get_payload(decode=True) == ICS


def test_subject_from_and_to_name_every_recipient_and_the_send_logs_in_with_the_keychain_app_password(smtp):
    send()

    [conn] = smtp.connections
    assert conn.address == ("smtp.gmail.com", 587)
    assert conn.calls == ["starttls", "login", "sendmail", "quit"]  # never logs in unencrypted
    assert conn.login_args == (SENDER, APP_PASSWORD)
    [(from_addr, to_addrs, _raw)] = conn.sent
    assert (from_addr, to_addrs) == (SENDER, RECIPIENTS)
    msg = received(smtp)
    assert msg["Subject"] == SUBJECT
    assert msg["From"] == SENDER
    assert [a.addr_spec for a in msg["To"].addresses] == RECIPIENTS
    assert msg["Date"]


def test_with_no_app_password_in_the_keychain_it_fails_clearly_and_sends_nothing(smtp, harness):
    harness.keychain.clear()

    with pytest.raises(RuntimeError, match=re.escape(f"No Gmail App Password in Keychain for {SENDER}")):
        send()

    assert smtp.connections == []


def test_with_no_recipients_nothing_is_sent(smtp, harness):
    send(to_addrs=[])

    assert smtp.connections == []
    assert not any("find-generic-password" in c for c in harness.commands)  # not even a Keychain read
