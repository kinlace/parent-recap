"""The gateway's Brief relay (ops/gateway/brief_relay.py, #525) through its request handler: the
Gateway Key checked against a fake Bifrost admin API, the daily cap, the request's shape, and the
message handed to a fake Email Delivery SMTP server, parsed back as a mail client reads it. And
keys made on request (ADR 0033, #618): asking for one, its limits, the confirmation email and
page, binding new addresses, and the relay sending only to the confirmed ones."""
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
CONFIRM = "https://gateway.example/brief/confirm"
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
    """Bifrost's virtual-key list as its admin API pages it: at most `limit` keys per page. A POST
    makes a key, as its admin API does."""

    def __init__(self, keys: list[dict], down: bool = False) -> None:
        self.keys = keys
        self.down = down
        self.paths: list[str] = []
        self.made: list[dict] = []

    def __call__(self, path: str, body: dict | None = None) -> dict:
        self.paths.append(path)
        if self.down:
            raise urllib.error.URLError("connection refused")
        if body is not None:
            assert path == "/api/governance/virtual-keys"
            key = {**body, "id": f"id-made-{len(self.made)}",
                   "value": f"sk-bf-made-{len(self.made)}"}
            self.made.append(body)
            self.keys.append(key)
            return {"message": "created", "virtual_key": key}
        query = dict(p.split("=") for p in path.split("?", 1)[1].split("&"))
        limit, offset = min(int(query["limit"]), 100), int(query["offset"])
        page = self.keys[offset:offset + limit]
        return {"virtual_keys": page, "count": len(page), "limit": limit, "offset": offset,
                "total_count": len(self.keys)}


class FakeSMTP:
    """smtplib.SMTP_SSL, recording each message instead of reaching Email Delivery."""
    sent: list[tuple[str, list[str], Message]] = []
    fail: Exception | None = None
    # Addresses Email Delivery refuses, as it does one it has suppressed.
    refuse: set[str] = set()

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
        return {a: (550, b"suppressed") for a in to_addrs if a in FakeSMTP.refuse}


@pytest.fixture(autouse=True)
def smtp():
    FakeSMTP.sent, FakeSMTP.fail, FakeSMTP.refuse = [], None, set()
    yield FakeSMTP


@pytest.fixture
def day() -> list[str]:
    return ["2026-10-28"]


def make_relay(tmp_path: Path, day: list[str], keys: list[dict] | None = None,
               bifrost: FakeBifrost | None = None, cap: int = 3, warn_at: int = 70) -> Any:
    bifrost = bifrost or FakeBifrost([virtual_key(1, KEY)] if keys is None else keys)
    return br.Relay(
        keys=br.Bifrost("http://127.0.0.1:8080", "admin", "pw", fetch=bifrost),
        cap=br.DailyCap(tmp_path / "sent.json", limit=cap, today=lambda: day[0]),
        mailer=br.Mailer("smtp.example", 465, "user", "pass", smtp=FakeSMTP,
                         quota=br.Quota(tmp_path / "quota.json", warn_at=warn_at,
                                        today=lambda: day[0])),
        stop_url=STOP,
        bindings=br.Bindings(tmp_path / "bindings.json", today=lambda: day[0]),
        confirm_url=CONFIRM)


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


# ── Email Delivery's quota

def quota_warnings(caplog: Any) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if r.levelno == logging.WARNING and r.getMessage().startswith("quota ")]


def test_the_maintainers_are_warned_once_when_a_days_mail_passes_the_mark(tmp_path, day, caplog):
    caplog.set_level(logging.INFO, logger="brief-relay")
    relay = make_relay(tmp_path, day, cap=10, warn_at=3)
    post(relay)
    assert quota_warnings(caplog) == []
    post(relay)
    assert quota_warnings(caplog) == ["quota day=2026-10-28 sent=4 of 100: past 3, "
                                      "see ops/gateway/README.md \"Email Delivery's quota\""]
    post(relay)
    assert len(quota_warnings(caplog)) == 1


def test_the_count_starts_again_each_utc_day(tmp_path, day, caplog):
    caplog.set_level(logging.INFO, logger="brief-relay")
    relay = make_relay(tmp_path, day, cap=10, warn_at=3)
    post(relay)
    day[0] = "2026-10-29"
    post(relay)
    assert quota_warnings(caplog) == []
    post(relay)
    assert len(quota_warnings(caplog)) == 1 and "day=2026-10-29 sent=4" in quota_warnings(caplog)[0]


def test_a_restart_keeps_the_count_and_doesnt_warn_again(tmp_path, day, caplog):
    caplog.set_level(logging.INFO, logger="brief-relay")
    post(make_relay(tmp_path, day, warn_at=3))
    post(make_relay(tmp_path, day, warn_at=3))
    post(make_relay(tmp_path, day, warn_at=3))
    assert len(quota_warnings(caplog)) == 1


def test_only_messages_email_delivery_took_count(tmp_path, day, smtp, caplog):
    caplog.set_level(logging.INFO, logger="brief-relay")
    relay = make_relay(tmp_path, day, cap=10, warn_at=1)
    smtp.fail = OSError("connection reset")
    post(relay)
    smtp.fail = None
    smtp.refuse = {"partner@example.fi"}
    post(relay)
    assert quota_warnings(caplog) == []
    post(relay)
    assert quota_warnings(caplog)


def test_a_warning_reaches_the_journal_as_one_so_journalctl_p_warning_lists_it():
    def line(level: int) -> str:
        return br.JournalFormatter("%(message)s").format(
            logging.LogRecord("brief-relay", level, __file__, 1, "quota day=…", None, None))
    assert line(logging.WARNING) == "<4>quota day=…"
    assert line(logging.INFO) == "quota day=…"


def test_confirmation_emails_count_too(tmp_path, day, caplog):
    caplog.set_level(logging.INFO, logger="brief-relay")
    relay = make_relay(tmp_path, day, warn_at=1)
    assert ask_key(relay)[0] == 200
    assert quota_warnings(caplog)


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


# ── Keys made on request (ADR 0033)

ASKED = [{"address": "Parent@Example.com", "language": "fi"},
         {"address": "partner@example.fi", "language": "zh"}]


def ask_key(relay: Any, recipients: Any = ASKED, caller: str = "203.0.113.7") -> tuple[int, dict]:
    return relay.request_key(caller, json.dumps({"recipients": recipients}).encode())


def link_in(msg: Message) -> str:
    [link] = [w for w in msg.get_body(("plain",)).get_content().split() if w.startswith(CONFIRM)]
    return link


def open_link(relay: Any, link: str, post: bool = False) -> tuple[int, str]:
    return relay.confirm(link.split("?", 1)[1], post=post)


def test_a_key_request_makes_a_send_only_key_and_mails_each_address_a_link_in_its_language(
        tmp_path, day):
    bifrost = FakeBifrost([])
    relay = make_relay(tmp_path, day, bifrost=bifrost)
    status, body = ask_key(relay)
    assert status == 200
    assert body["key"] == "sk-bf-made-0"
    assert body["recipients"] == [{"address": "parent@example.com", "confirmed": False},
                                  {"address": "partner@example.fi", "confirmed": False}]
    assert body["mailed"] == ["parent@example.com", "partner@example.fi"]
    [made] = bifrost.made
    assert made["name"].startswith("on-request-") and made["is_active"] is True
    assert "made on request" in made["description"]
    assert "provider_configs" not in made and "budgets" not in made  # no model behind it
    (frm, [to_fi], fi), (_, [to_zh], zh) = FakeSMTP.sent
    assert frm == "brief@parentrecap.com" and fi["From"] == "Parent Recap <brief@parentrecap.com>"
    assert (to_fi, to_zh) == ("parent@example.com", "partner@example.fi")
    assert fi["Subject"] == "Vahvista, että haluat Parent Recapin koosteen"
    assert zh["Subject"] == "请确认接收 Parent Recap 日报"
    # Who asked, and how to ignore it.
    assert "parent@example.com otti Parent Recapin käyttöön" in fi.get_body(("plain",)).get_content()
    assert "parent@example.com 设置了 Parent Recap" in zh.get_body(("plain",)).get_content()
    assert "忽略这封邮件" in zh.get_body(("plain",)).get_content()
    assert link_in(fi) != link_in(zh)


def test_a_language_without_its_own_text_gets_english(tmp_path, day):
    ask_key(make_relay(tmp_path, day), [{"address": "far@example.com", "language": "sv"}])
    [(_, _, msg)] = FakeSMTP.sent
    assert msg["Subject"] == "Confirm that you want the Parent Recap Brief"


@pytest.mark.parametrize("recipients", [
    [], "parent@example.com", [{"address": "Parent <parent@example.com>", "language": "en"}],
    [{"address": "parent@example.com"}], [{"address": "parent@example.com", "language": "<b>"}],
    [{"address": f"p{n}@example.com", "language": "en"} for n in range(7)],
    [{"address": "parent@example.com", "language": "en"}, {"address": "PARENT@example.com",
                                                           "language": "fi"}],
])
def test_a_malformed_key_request_gets_400_and_makes_no_key(tmp_path, day, recipients):
    bifrost = FakeBifrost([])
    status, body = ask_key(make_relay(tmp_path, day, bifrost=bifrost), recipients)
    assert status == 400 and body["error"]
    assert bifrost.made == [] and FakeSMTP.sent == []


def test_one_caller_gets_three_keys_a_day(tmp_path, day):
    relay = make_relay(tmp_path, day, bifrost=FakeBifrost([]))
    statuses = [ask_key(relay, [{"address": f"p{n}@example.com", "language": "en"}])[0]
                for n in range(4)]
    assert statuses == [200, 200, 200, 429]
    assert ask_key(relay, [{"address": "p9@example.com", "language": "en"}],
                   caller="198.51.100.1")[0] == 200
    day[0] = "2026-10-29"
    assert ask_key(relay, [{"address": "p8@example.com", "language": "en"}])[0] == 200


def test_one_address_gets_three_confirmation_emails_a_day_whoever_asks(tmp_path, day):
    bifrost = FakeBifrost([])
    relay = make_relay(tmp_path, day, bifrost=bifrost)
    one = [{"address": "parent@example.com", "language": "en"}]
    statuses = [ask_key(relay, one, caller=f"198.51.100.{n}")[0] for n in range(4)]
    assert statuses == [200, 200, 200, 429]
    assert len(bifrost.made) == 3 and len(FakeSMTP.sent) == 3


def test_confirmation_emails_stop_at_the_days_total(tmp_path, day):
    relay = make_relay(tmp_path, day, bifrost=FakeBifrost([]))
    sent = 0
    for n in range(br.MAILS_PER_DAY):
        status, _ = ask_key(relay, [{"address": f"p{n}@example.com", "language": "en"}],
                            caller=f"198.51.100.{n}")
        sent += status == 200
    assert sent == br.KEYS_PER_DAY  # the key total comes first
    assert len(FakeSMTP.sent) == br.KEYS_PER_DAY <= br.MAILS_PER_DAY


def test_bifrost_unreachable_while_making_a_key_gets_503(tmp_path, day):
    status, body = ask_key(make_relay(tmp_path, day, bifrost=FakeBifrost([], down=True)))
    assert status == 503 and "key" in body["error"]
    assert FakeSMTP.sent == []


def test_the_link_asks_first_and_confirms_only_when_its_button_posts(tmp_path, day):
    relay = make_relay(tmp_path, day, bifrost=FakeBifrost([]))
    ask_key(relay)
    (_, _, fi), (_, _, zh) = FakeSMTP.sent
    status, page = open_link(relay, link_in(fi))
    assert status == 200 and '<form method="post">' in page and "Vahvista" in page
    assert 'lang="fi"' in page and "parent@example.com" in page
    status, page = open_link(relay, link_in(fi), post=True)
    assert status == 200 and "Vahvistettu" in page and "<form" not in page
    status, page = open_link(relay, link_in(zh), post=True)
    assert "已确认" in page
    status, page = open_link(relay, f"{CONFIRM}?t={'x' * 32}", post=True)
    assert status == 404 and "doesn't work" in page
    assert open_link(relay, f"{CONFIRM}?t=<script>")[0] == 404


def test_the_key_sends_only_to_its_confirmed_addresses(tmp_path, day):
    relay = make_relay(tmp_path, day, bifrost=FakeBifrost([]))
    _, made = ask_key(relay)
    (_, _, fi), _ = FakeSMTP.sent
    FakeSMTP.sent = []
    brief = {**BRIEF, "to": ["parent@example.com", "partner@example.fi"]}
    status, body = post(relay, brief, key=made["key"])
    assert (status, body) == (403, {"error": "recipient not confirmed",
                                    "unconfirmed": ["parent@example.com", "partner@example.fi"]})
    assert FakeSMTP.sent == []
    open_link(relay, link_in(fi), post=True)
    status, body = post(relay, brief, key=made["key"])
    assert (status, body) == (200, {"sent": 1, "refused": 0, "unconfirmed": ["partner@example.fi"]})
    [(_, to, msg)] = FakeSMTP.sent
    assert to == ["parent@example.com"] and msg["To"] == "parent@example.com"
    # An address never bound is refused as one not confirmed.
    status, body = post(relay, {**BRIEF, "to": ["stranger@example.net"]}, key=made["key"])
    assert status == 403 and body["unconfirmed"] == ["stranger@example.net"]


def test_a_key_made_by_hand_still_sends_to_any_address(tmp_path, day):
    assert post(make_relay(tmp_path, day), {**BRIEF, "to": ["stranger@example.net"]})[0] == 200


def test_a_key_made_on_request_has_its_own_daily_cap(tmp_path, day):
    relay = make_relay(tmp_path, day, bifrost=FakeBifrost([virtual_key(1, KEY)]))
    relay.cap.on_request_limit = 1
    _, made = ask_key(relay, [{"address": "parent@example.com", "language": "en"}])
    open_link(relay, link_in(FakeSMTP.sent[0][2]), post=True)
    brief = {**BRIEF, "to": ["parent@example.com"]}
    assert [post(relay, brief, key=made["key"])[0] for _ in range(2)] == [200, 429]
    assert post(relay)[0] == 200  # a key made by hand keeps its own


def bind(relay: Any, key: str, recipients: list[dict], resend: bool = False) -> tuple[int, dict]:
    return relay.rebind(key, json.dumps({"recipients": recipients, "resend": resend}).encode())


def test_binding_a_new_address_mails_only_it_and_keeps_who_confirmed(tmp_path, day):
    relay = make_relay(tmp_path, day, bifrost=FakeBifrost([]))
    _, made = ask_key(relay, [{"address": "parent@example.com", "language": "en"}])
    open_link(relay, link_in(FakeSMTP.sent[0][2]), post=True)
    FakeSMTP.sent = []
    now = [{"address": "parent@example.com", "language": "en"},
           {"address": "new@example.fi", "language": "fi"}]
    status, body = bind(relay, made["key"], now)
    assert status == 200
    assert body == {"recipients": [{"address": "parent@example.com", "confirmed": True},
                                   {"address": "new@example.fi", "confirmed": False}],
                    "mailed": ["new@example.fi"]}
    [(_, [to], msg)] = FakeSMTP.sent
    assert to == "new@example.fi" and "parent@example.com otti" in msg.get_body(("plain",)).get_content()
    # The same list again only answers.
    assert bind(relay, made["key"], now)[1]["mailed"] == []
    # An address left out is unbound: the key no longer sends to it.
    bind(relay, made["key"], now[1:])
    assert post(relay, {**BRIEF, "to": ["parent@example.com"]}, key=made["key"])[0] == 403


def test_resend_mails_each_unconfirmed_address_again_within_its_limit(tmp_path, day):
    relay = make_relay(tmp_path, day, bifrost=FakeBifrost([]))
    one = [{"address": "parent@example.com", "language": "en"}]
    _, made = ask_key(relay, one)
    first = link_in(FakeSMTP.sent[0][2])
    statuses = [bind(relay, made["key"], one, resend=True)[0] for _ in range(3)]
    assert statuses == [200, 200, 429]  # three emails a day to one address
    assert link_in(FakeSMTP.sent[-1][2]) == first  # the same link, so any of them works
    open_link(relay, first, post=True)
    status, body = bind(relay, made["key"], one, resend=True)
    assert (status, body["mailed"]) == (200, [])  # nothing left to confirm


def test_binding_with_a_key_made_by_hand_changes_nothing_and_says_all_are_confirmed(tmp_path, day):
    status, body = bind(make_relay(tmp_path, day), KEY, ASKED)
    assert status == 200 and all(r["confirmed"] for r in body["recipients"])
    assert FakeSMTP.sent == []
    assert bind(make_relay(tmp_path, day), "sk-bf-unknown", ASKED)[0] == 401


def test_the_bindings_survive_a_restart(tmp_path, day):
    bifrost = FakeBifrost([])
    _, made = ask_key(make_relay(tmp_path, day, bifrost=bifrost))
    open_link(make_relay(tmp_path, day, bifrost=bifrost), link_in(FakeSMTP.sent[0][2]), post=True)
    relay = make_relay(tmp_path, day, bifrost=bifrost)
    assert post(relay, {**BRIEF, "to": ["parent@example.com"]}, key=made["key"])[0] == 200


def test_requests_for_keys_log_no_address_link_or_key(tmp_path, day, caplog):
    caplog.set_level(logging.INFO, logger="brief-relay")
    relay = make_relay(tmp_path, day, bifrost=FakeBifrost([]))
    _, made = ask_key(relay)
    link = link_in(FakeSMTP.sent[0][2])
    open_link(relay, link, post=True)
    bind(relay, made["key"], ASKED, resend=True)
    assert "key-request key=on-request-" in caplog.text and "outcome=made, 2 mailed" in caplog.text
    for secret in [made["key"], link.split("=")[1], "example.com", "example.fi", "203.0.113.7"]:
        assert secret not in caplog.text
    # The day's counts keep no address and no caller as they are either.
    stored = (tmp_path / "bindings.json").read_text()
    assert "203.0.113.7" not in stored


def test_print_keys_lists_every_key_with_what_it_can_do_and_never_its_value(
        tmp_path, day, capsys):
    ai = {**virtual_key(1, KEY), "name": "ai-household-1",
          "provider_configs": [{"provider": "anthropic"}],
          "budgets": [{"max_limit": 0.10, "current_usage": 0.03}]}
    off = {**virtual_key(2, "sk-bf-test-send-only", active=False), "name": "send-household-2"}
    bifrost = FakeBifrost([ai, off])
    relay = make_relay(tmp_path, day, bifrost=bifrost)
    ask_key(relay)
    open_link(relay, link_in(FakeSMTP.sent[0][2]), post=True)
    br.print_keys(br.Bifrost("http://127.0.0.1:8080", "admin", "pw", fetch=bifrost),
                  tmp_path / "bindings.json")
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "ai-household-1  id=id-1  writes+sends  on  today $0.03 of $0.10"
    assert lines[1] == "send-household-2  id=id-2  sends only  off  today -"
    # A key made on request: what it can do, then who confirmed.
    assert lines[2].startswith("on-request-") and "sends only  on" in lines[2]
    assert lines[2].endswith("made 2026-10-28")
    assert lines[3].startswith("  parent@example.com (fi): confirmed ")
    assert lines[4:] == ["  partner@example.fi (zh): not confirmed"]
    for k in bifrost.keys:
        assert k["value"] not in "\n".join(lines)


def test_over_http_a_key_is_asked_for_and_confirmed_and_the_caller_is_caddys(tmp_path, day):
    relay = make_relay(tmp_path, day, bifrost=FakeBifrost([]))
    server = br.ThreadingHTTPServer(("127.0.0.1", 0), br.handler(relay))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(path: str, data: bytes | None = None, headers: dict | None = None) -> tuple[int, bytes]:
        request = urllib.request.Request(base + path, data=data, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    try:
        ask = json.dumps({"recipients": ASKED[:1]}).encode()
        for n in range(3):
            assert call("/v1/brief/key", ask, {"X-Forwarded-For": "203.0.113.7"})[0] == 200 or n
        assert call("/v1/brief/key", ask, {"X-Forwarded-For": "203.0.113.7"})[0] == 429
        link = link_in(FakeSMTP.sent[0][2]).removeprefix(CONFIRM)
        status, page = call("/brief/confirm" + link)
        assert status == 200 and b"<form" in page
        status, page = call("/brief/confirm" + link, data=b"")
        assert status == 200 and "Vahvistettu".encode() in page
        assert call("/v1/brief/recipients", ask, {"x-api-key": "sk-bf-made-0"})[0] == 200
    finally:
        server.shutdown()
        server.server_close()
