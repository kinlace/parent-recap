"""The gateway's Brief relay (ops/gateway/brief_relay.py, #525) through its request handler: the
Gateway Key checked against a fake Bifrost admin API, the daily cap, the request's shape, and the
message handed to a fake Email Delivery SMTP server, parsed back as a mail client reads it."""
from __future__ import annotations

import base64
import email
import importlib.util
import json
import logging
import smtplib
import socket
import sys
import threading
import urllib.error
import urllib.request
from email.message import EmailMessage, Message
from email.policy import default
from pathlib import Path
from typing import Any

import pytest

SOURCE = Path(__file__).resolve().parents[3] / "ops" / "gateway" / "brief_relay.py"
_spec = importlib.util.spec_from_file_location("brief_relay", SOURCE)
br = sys.modules["brief_relay"] = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(br)

KEY = "sk-bf-test-household-one"
STOP = "https://gateway.example/brief/stop"
ICS = (b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nMETHOD:PUBLISH\r\nBEGIN:VEVENT\r\n"
       b"SUMMARY:Retki Nuuksioon\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
BRIEF = {
    "to": ["parent@example.com", "partner@example.fi"],
    "subject": "Iltakooste 27.9. · 家庭日报",
    "text": "Huomenna: Leon retki Nuuksioon, eväät mukaan.",
    "html": "<p>Huomenna: Leon retki Nuuksioon, eväät mukaan.</p>",
    "attachments": [{"filename": "events.ics", "content_type": "text/calendar",
                     "data": base64.b64encode(ICS).decode()}],
}


def virtual_key(n: int, value: str, active: bool = True) -> dict:
    return {"id": f"id-{n}", "name": f"household-{n}", "value": value, "is_active": active}


class FakeBifrost:
    """Bifrost's virtual-key list as its admin API pages it: at most `limit` keys per page."""

    def __init__(self, keys: list[dict], down: bool = False) -> None:
        self.keys = keys
        self.down = down
        self.paths: list[str] = []

    def __call__(self, path: str) -> dict:
        self.paths.append(path)
        if self.down:
            raise urllib.error.URLError("connection refused")
        query = dict(p.split("=") for p in path.split("?", 1)[1].split("&"))
        limit, offset = min(int(query["limit"]), 100), int(query["offset"])
        page = self.keys[offset:offset + limit]
        return {"virtual_keys": page, "count": len(page), "limit": limit, "offset": offset,
                "total_count": len(self.keys)}


class FakeSMTP:
    """smtplib.SMTP_SSL, recording each message instead of reaching Email Delivery."""
    sent: list[tuple[str, list[str], Message]] = []
    fail: Exception | None = None

    def __init__(self, host: str, port: int, timeout: float | None = None, **_kw: Any) -> None:
        self.address = (host, port)

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *_a: Any) -> None:
        pass

    def login(self, user: str, password: str) -> None:
        if FakeSMTP.fail:
            raise FakeSMTP.fail

    def send_message(self, msg: EmailMessage, from_addr: str, to_addrs: list[str]) -> dict:
        parsed = email.message_from_bytes(msg.as_bytes(), policy=default)
        FakeSMTP.sent.append((from_addr, list(to_addrs), parsed))
        return {}


@pytest.fixture(autouse=True)
def smtp():
    FakeSMTP.sent, FakeSMTP.fail = [], None
    yield FakeSMTP


@pytest.fixture
def day() -> list[str]:
    return ["2026-10-28"]


def make_relay(tmp_path: Path, day: list[str], keys: list[dict] | None = None,
               bifrost: FakeBifrost | None = None, cap: int = 3) -> Any:
    bifrost = bifrost or FakeBifrost([virtual_key(1, KEY)] if keys is None else keys)
    return br.Relay(
        keys=br.Bifrost("http://127.0.0.1:8080", "admin", "pw", fetch=bifrost),
        cap=br.DailyCap(tmp_path / "sent.json", limit=cap, today=lambda: day[0]),
        mailer=br.Mailer("smtp.example", 465, "user", "pass", smtp=FakeSMTP),
        stop_url=STOP)


def post(relay: Any, body: Any = BRIEF, key: str | None = KEY) -> tuple[int, dict]:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return relay.handle(key, raw)


# ── The Gateway Key

@pytest.mark.parametrize("key", [None, "", "sk-bf-not-a-key"])
def test_a_missing_or_unknown_key_gets_401_and_nothing_is_sent(tmp_path, day, key):
    status, body = post(make_relay(tmp_path, day), key=key)
    assert status == 401 and body["error"]
    assert FakeSMTP.sent == []


def test_an_inactive_key_gets_401(tmp_path, day):
    relay = make_relay(tmp_path, day, keys=[virtual_key(1, KEY, active=False)])
    assert post(relay)[0] == 401
    assert FakeSMTP.sent == []


def test_a_key_past_bifrosts_first_page_is_found(tmp_path, day):
    keys = [virtual_key(n, f"sk-bf-other-{n}") for n in range(230)] + [virtual_key(230, KEY)]
    bifrost = FakeBifrost(keys)
    assert post(make_relay(tmp_path, day, bifrost=bifrost))[0] == 200
    assert len(bifrost.paths) == 3


def test_bifrost_unreachable_gets_503_so_the_program_retries_later(tmp_path, day):
    status, body = post(make_relay(tmp_path, day, bifrost=FakeBifrost([], down=True)))
    assert status == 503 and "key" in body["error"]


def test_the_key_is_checked_before_the_request_is_read(tmp_path, day):
    assert post(make_relay(tmp_path, day), body=b"not json", key="sk-bf-unknown")[0] == 401


# ── The daily cap

def test_a_key_past_its_daily_cap_gets_429_until_the_next_day(tmp_path, day):
    relay = make_relay(tmp_path, day, cap=2)
    assert [post(relay)[0] for _ in range(3)] == [200, 200, 429]
    assert len(FakeSMTP.sent) == 2
    day[0] = "2026-10-29"
    assert post(relay)[0] == 200


def test_the_cap_is_per_key(tmp_path, day):
    other = "sk-bf-test-household-two"
    relay = make_relay(tmp_path, day, keys=[virtual_key(1, KEY), virtual_key(2, other)], cap=1)
    assert post(relay)[0] == 200
    assert post(relay, key=other)[0] == 200
    assert post(relay)[0] == 429


def test_the_cap_survives_a_restart(tmp_path, day):
    post(make_relay(tmp_path, day, cap=1))
    assert post(make_relay(tmp_path, day, cap=1))[0] == 429


def test_a_message_email_delivery_didnt_take_doesnt_count(tmp_path, day, smtp):
    relay = make_relay(tmp_path, day, cap=1)
    smtp.fail = OSError("connection reset")
    assert post(relay)[0] == 502
    smtp.fail = None
    assert post(relay)[0] == 200


def test_a_bad_request_doesnt_count(tmp_path, day):
    relay = make_relay(tmp_path, day, cap=1)
    assert post(relay, body={**BRIEF, "to": []})[0] == 400
    assert post(relay)[0] == 200


# ── The request's shape

@pytest.mark.parametrize("change", [
    {"to": []},
    {"to": "parent@example.com"},
    {"to": [f"p{n}@example.com" for n in range(11)]},
    {"to": ["Parent <parent@example.com>"]},
    {"to": ["parent@example.com, spam@example.net"]},
    {"to": ["parent@example.com\r\nBcc: spam@example.net"]},
    {"subject": ""},
    {"subject": "Brief\r\nBcc: spam@example.net"},
    {"subject": "Brief \ud800"},
    {"to": ["vanhempi@esimerkki.fi", "äiti@esimerkki.fi"]},
    {"text": None},
    {"html": 7},
    {"attachments": [{"filename": "../events.ics", "content_type": "text/calendar",
                      "data": base64.b64encode(ICS).decode()}]},
    {"attachments": [{"filename": "events.ics", "content_type": "calendar",
                      "data": base64.b64encode(ICS).decode()}]},
    {"attachments": [{"filename": "events.ics", "content_type": "text/calendar",
                      "data": "not base64!"}]},
    {"attachments": "events.ics"},
])
def test_a_malformed_request_gets_400_saying_what_is_wrong(tmp_path, day, change):
    status, body = post(make_relay(tmp_path, day), body={**BRIEF, **change})
    assert status == 400 and body["error"]
    assert FakeSMTP.sent == []


@pytest.mark.parametrize("raw", [b"not json", b"[1, 2]", pytest.param(b"[" * 100_000, id="nested")])
def test_a_body_that_isnt_a_json_object_gets_400(tmp_path, day, raw):
    assert post(make_relay(tmp_path, day), body=raw)[0] == 400


def test_a_request_over_the_size_limit_gets_413(tmp_path, day):
    big = {**BRIEF, "text": "x" * (br.MAX_REQUEST_BYTES + 1)}
    assert post(make_relay(tmp_path, day), body=big)[0] == 413


# ── The message

def test_one_request_is_one_message_to_every_recipient_from_parent_recaps_address(tmp_path, day):
    status, body = post(make_relay(tmp_path, day))
    assert (status, body) == (200, {"sent": 2, "refused": 0})
    [(envelope_from, envelope_to, msg)] = FakeSMTP.sent
    assert envelope_from == "brief@parentrecap.com"
    assert envelope_to == BRIEF["to"]
    assert msg["From"] == "Parent Recap <brief@parentrecap.com>"
    assert msg["To"] == "parent@example.com, partner@example.fi"
    assert msg["Subject"] == BRIEF["subject"]
    assert msg["Reply-To"] is None
    assert msg["List-Unsubscribe"] == f"<{STOP}>"
    assert msg.get_body(("plain",)).get_content().strip() == BRIEF["text"]
    assert msg.get_body(("html",)).get_content().strip() == BRIEF["html"]


def test_the_ics_arrives_as_an_attachment_calendar_apps_offer_to_add(tmp_path, day):
    post(make_relay(tmp_path, day))
    [(_, _, msg)] = FakeSMTP.sent
    [ics] = list(msg.iter_attachments())
    assert ics.get_filename() == "events.ics"
    assert ics.get_content_type() == "text/calendar"
    assert ics.get_param("method") == "PUBLISH"
    assert ics.get_payload(decode=True) == ICS


def test_text_alone_is_enough(tmp_path, day):
    brief = {"to": ["parent@example.com"], "subject": "Brief", "text": "Nothing tonight."}
    assert post(make_relay(tmp_path, day), body=brief)[0] == 200
    [(_, _, msg)] = FakeSMTP.sent
    assert msg.get_content_type() == "text/plain"


# ── Email Delivery

@pytest.mark.parametrize("error, reason", [
    (smtplib.SMTPAuthenticationError(535, b"Authentication credentials invalid"), "login"),
    (smtplib.SMTPResponseException(451, b"Try again later"), "451"),
    (TimeoutError(), "couldn't reach"),
])
def test_an_email_delivery_failure_gets_502_with_a_short_reason(tmp_path, day, smtp, error, reason):
    smtp.fail = error
    status, body = post(make_relay(tmp_path, day))
    assert status == 502 and reason in body["error"]


def test_an_unexpected_failure_while_sending_gets_500_counts_nothing_and_logs_no_content(
        tmp_path, day, smtp, caplog):
    caplog.set_level(logging.INFO, logger="brief-relay")
    relay = make_relay(tmp_path, day, cap=1)
    smtp.fail = UnicodeEncodeError("ascii", BRIEF["subject"], 0, 1, "ordinal not in range")
    status, body = post(relay)
    assert status == 500 and body["error"]
    assert "Iltakooste" not in caplog.text
    smtp.fail = None
    assert post(relay)[0] == 200


# ── The log

def test_the_log_has_the_keys_name_the_count_the_size_and_the_outcome_and_no_content(
        tmp_path, day, caplog):
    caplog.set_level(logging.INFO, logger="brief-relay")
    raw = json.dumps(BRIEF).encode()
    post(make_relay(tmp_path, day), body=raw)
    post(make_relay(tmp_path, day), key="sk-bf-unknown")
    logged = caplog.text
    assert f"key=household-1 recipients=2 bytes={len(raw)} outcome=sent" in logged
    assert "key=- recipients=0" in logged and "outcome=401" in logged
    for secret in [KEY, "sk-bf-unknown", BRIEF["subject"], "Nuuksioon", "example.com",
                   "example.fi", "events.ics", BRIEF["attachments"][0]["data"][:20]]:
        assert secret not in logged


# ── HTTP

def test_over_http_only_post_v1_brief_is_served_and_the_key_is_x_api_key(tmp_path, day):
    server = br.ThreadingHTTPServer(("127.0.0.1", 0), br.handler(make_relay(tmp_path, day)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(path: str, key: str | None = None, data: bytes | None = None) -> int:
        headers = {"content-type": "application/json", **({"x-api-key": key} if key else {})}
        try:
            with urllib.request.urlopen(urllib.request.Request(base + path, data=data,
                                                               headers=headers), timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    try:
        raw = json.dumps(BRIEF).encode()
        assert call("/v1/brief", KEY, raw) == 200
        assert call("/v1/brief", None, raw) == 401
        assert call("/v1/messages", KEY, raw) == 404
        assert call("/v1/brief", KEY) == 404  # a GET
        assert call("/health") == 200
        assert raw_status(base, "POST /v1/brief HTTP/1.1", f"x-api-key: {KEY}",
                          "Content-Length: -1") == 400
        assert raw_status(base, "POST /v1/brief HTTP/1.1", f"x-api-key: {KEY}",
                          "Transfer-Encoding: chunked") == 411
    finally:
        server.shutdown()
        server.server_close()


def raw_status(base: str, *lines: str) -> int:
    """The status for a request written by hand, headers that urllib won't send included."""
    host, port = base.removeprefix("http://").split(":")
    with socket.create_connection((host, int(port)), timeout=5) as s:
        s.sendall(("\r\n".join([*lines, "Connection: close", "", ""])).encode())
        return int(s.recv(1024).split()[1])
