"""A Source that fails partway through a night, or times out. The real Gmail, Wilma and MyClub
Sources run here, with fakes one step further out: the IMAP server, the `wilma` CLI and the MyClub
feed. Assertions are on what leaves the system: the model payload, the email's coverage line and
persisted state."""
from __future__ import annotations

import imaplib
import json
import subprocess
from datetime import timedelta
from email.message import EmailMessage
from typing import Any

import pytest
import requests
import time_machine

from conftest import NOW, msg

from family_brief.collectors import gmail, myclub, wilma

REAL_GMAIL_COLLECT = gmail.collect  # captured before the harness fakes it
REAL_WILMA_COLLECT = wilma.collect
REAL_MYCLUB_COLLECT_EVENTS = myclub.collect_events


def payload_ids(h, source: str) -> list[str]:
    return sorted(m["external_id"] for m in h.model_payload()["messages"] if m["source"] == source)


def caught_up_last_night(h) -> None:
    """A household already running nightly, so a Source that falls behind has a night to reach back to."""
    h.state_path.parent.mkdir(parents=True, exist_ok=True)
    h.state_path.write_text(json.dumps({"caught_up_at": (NOW - timedelta(days=1)).isoformat()}))


def one_whatsapp_message(h) -> None:
    """So each night has something new and a Brief goes out, whatever the Source under test gives."""
    h.sources["whatsapp"] = lambda: [msg("whatsapp", f"wa-{len(h.model_calls)}", "2026-09-27T18:00:00+03:00",
                                         "Piano moves to Wednesday", chat="Leo piano", kid="Leo")]


def seen(h, source: str) -> list[str]:
    return sorted(h.state()["seen_message_ids"].get(source, {}))


# ── Gmail

def email_bytes(n: int, *, charset: str = "utf-8") -> bytes:
    m = EmailMessage()
    m["From"] = "teacher.3b@kilo.example.fi"
    m["Subject"] = f"Viesti {n}"
    m["Date"] = "Sun, 27 Sep 2026 10:00:00 +0300"
    m.set_content(f"Message number {n}.")
    raw = m.as_bytes()
    if charset != "utf-8":
        raw = raw.replace(b'charset="utf-8"', f'charset="{charset}"'.encode())
    return raw


class FakeImap:
    """Gmail's IMAP server as the collector sees it: one search, then per message its
    X-GM-MSGID and its RFC822 source. `bodies` maps message number to raw bytes, or to an
    exception that fetching it raises (a dropped connection)."""

    def __init__(self, bodies: dict[int, bytes | Exception]) -> None:
        self.bodies = bodies

    def __call__(self, *_a: Any, **_k: Any) -> "FakeImap":
        return self

    def __enter__(self) -> "FakeImap":
        return self

    def __exit__(self, *_a: Any) -> None:
        pass

    def login(self, *_a: Any) -> None:
        pass

    def select(self, *_a: Any, **_k: Any) -> None:
        pass

    def search(self, *_a: Any) -> tuple[str, list[bytes]]:
        return "OK", [b" ".join(str(n).encode() for n in self.bodies)]

    def fetch(self, imap_id: bytes, what: str) -> tuple[str, list[Any]]:
        n = int(imap_id)
        if what == "(X-GM-MSGID)":
            return "OK", [f"{n} (X-GM-MSGID 10{n})".encode()]
        body = self.bodies[n]
        if isinstance(body, Exception):
            raise body
        if body == b"":
            return "OK", [b"garbled response"]  # not the (envelope, bytes) pair
        return "OK", [(f"{n} (RFC822 {{{len(body)}}}".encode(), body)]


@pytest.fixture
def real_gmail(harness, monkeypatch):
    harness.config["wilma"]["enabled"] = False
    one_whatsapp_message(harness)
    caught_up_last_night(harness)
    harness.keychain["gmail-imap-parent@example.com"] = "app-password"
    monkeypatch.setattr(gmail, "collect", REAL_GMAIL_COLLECT)

    def serve(bodies: dict[int, bytes | Exception]) -> None:
        monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeImap(bodies))
    return serve


def test_gmail_connection_dropped_partway_brings_its_messages_back_next_night(harness, real_gmail):
    real_gmail({1: email_bytes(1), 2: email_bytes(2),
                3: imaplib.IMAP4.abort("socket error: EOF"), 4: email_bytes(4)})

    assert harness.run() == 0

    assert seen(harness, "gmail") == []
    [email] = harness.sent
    assert "Gmail 没读到（socket error: EOF）" in email.text

    real_gmail({n: email_bytes(n) for n in (1, 2, 3, 4)})
    with time_machine.travel(NOW + timedelta(days=1), tick=False):
        assert harness.run() == 0
    assert payload_ids(harness, "gmail") == ["101", "102", "103", "104"]


def test_gmail_unknown_charset_still_reaches_the_brief(harness, real_gmail):
    real_gmail({1: email_bytes(1), 2: email_bytes(2, charset="unknown-8bit"), 3: email_bytes(3)})

    assert harness.run() == 0

    assert payload_ids(harness, "gmail") == ["101", "102", "103"]
    [m] = [m for m in harness.model_payload()["messages"] if m["external_id"] == "102"]
    assert "Message number 2." in m["body"]


def test_gmail_unreadable_message_is_skipped_and_the_rest_arrive(harness, real_gmail):
    real_gmail({1: email_bytes(1), 2: b"", 3: email_bytes(3)})

    assert harness.run() == 0

    assert payload_ids(harness, "gmail") == ["101", "103"]
    [email] = harness.sent
    assert "Gmail 部分没读到（有一条消息读不出来）" in email.text
    # Not marked seen, so a later night tries it again.
    assert seen(harness, "gmail") == ["101", "103"]


# ── Wilma

def wilma_list(ids: list[int]) -> dict:
    return {"students": [{"student": {"name": "Virtanen Mia", "studentNumber": 7}, "items": [
        {"wilmaId": i, "subject": f"Viesti {i}", "sentAt": "2026-09-27T09:00:00+03:00",
         "sender": "Opettaja Virtanen"} for i in ids]}]}


@pytest.fixture
def real_wilma(harness, monkeypatch):
    """Runs the real Wilma collector against a fake `wilma` CLI. `reads` maps a message id to its
    body, or to what reading it does instead: an exit code (int) or an exception it raises."""
    harness.config["gmail"]["allowlist_domains"] = []  # Gmail fails fast; this is about Wilma
    one_whatsapp_message(harness)
    caught_up_last_night(harness)
    monkeypatch.setattr(wilma, "collect", REAL_WILMA_COLLECT)
    model_or_osascript = subprocess.run

    def serve(message_ids: list[int], reads: dict[int, str | int | Exception]) -> None:
        def run(cmd: list[str], *a: Any, **k: Any) -> subprocess.CompletedProcess:
            if cmd[0] != wilma.WILMA:
                return model_or_osascript(cmd, *a, **k)
            args = cmd[1:-1]  # without "--json"
            if args[:2] == ["messages", "list"]:
                out: Any = wilma_list(message_ids)
            elif args[:2] == ["messages", "read"]:
                r = reads[int(args[2])]
                if isinstance(r, Exception):
                    raise r
                if isinstance(r, int):
                    return subprocess.CompletedProcess(cmd, r, "", "session expired")
                out = {"body": r}
            else:  # news and schedule: nothing tonight
                out = {"students": []}
            return subprocess.CompletedProcess(cmd, 0, json.dumps(out), "")
        monkeypatch.setattr(subprocess, "run", run)
    return serve


def test_wilma_failing_partway_brings_its_messages_back_next_night(harness, real_wilma):
    real_wilma([1, 2, 3, 4], {1: "Yksi", 2: "Kaksi", 3: RuntimeError("wilma crashed"), 4: "Neljä"})

    assert harness.run() == 0

    assert seen(harness, "wilma") == []

    real_wilma([1, 2, 3, 4], {1: "Yksi", 2: "Kaksi", 3: "Kolme", 4: "Neljä"})
    with time_machine.travel(NOW + timedelta(days=1), tick=False):
        assert harness.run() == 0
    assert payload_ids(harness, "wilma") == ["message:1", "message:2", "message:3", "message:4"]


@pytest.mark.parametrize("failure", [
    subprocess.TimeoutExpired(["wilma"], 60),
    1,  # `messages read` exits non-zero
], ids=["timeout", "read-failed"])
def test_wilma_message_that_cannot_be_read_is_not_marked_seen(harness, real_wilma, failure):
    real_wilma([1, 2, 3], {1: "Yksi", 2: failure, 3: "Kolme"})

    assert harness.run() == 0

    assert payload_ids(harness, "wilma") == ["message:1", "message:3"]
    assert seen(harness, "wilma") == ["message:1", "message:3"]
    [email] = harness.sent
    assert "Wilma 部分没读到（有一条消息读不出来）" in email.text

    real_wilma([1, 2, 3], {1: "Yksi", 2: "Kaksi", 3: "Kolme"})
    with time_machine.travel(NOW + timedelta(days=1), tick=False):
        assert harness.run() == 0
    assert payload_ids(harness, "wilma") == ["message:2"]
    [m] = [m for m in harness.model_payload()["messages"] if m["source"] == "wilma"]
    assert m["body"] == "Kaksi"


# ── A Source that times out once (slow DNS or network on a Mac just woken up)

class ImapConnections:
    """IMAP4_SSL that opens each connection in turn from `connections`: a FakeImap, or an
    exception that connecting raises."""

    def __init__(self, connections: list[FakeImap | Exception]) -> None:
        self.connections = connections

    def __call__(self, *_a: Any, **_k: Any) -> FakeImap:
        c = self.connections.pop(0)
        if isinstance(c, Exception):
            raise c
        return c


@pytest.mark.parametrize("first", [
    TimeoutError("timed out"),
    FakeImap({1: email_bytes(1), 2: TimeoutError("The read operation timed out")}),
], ids=["connecting", "partway"])
def test_gmail_timing_out_once_is_read_on_the_second_try(harness, real_gmail, monkeypatch, first):
    monkeypatch.setattr(imaplib, "IMAP4_SSL", ImapConnections(
        [first, FakeImap({1: email_bytes(1), 2: email_bytes(2)})]))

    assert harness.run() == 0

    assert payload_ids(harness, "gmail") == ["101", "102"]
    assert seen(harness, "gmail") == ["101", "102"]
    [email] = harness.sent
    assert "Gmail 没读到" not in email.text


def test_gmail_timing_out_twice_is_reported_as_not_read(harness, real_gmail, monkeypatch):
    monkeypatch.setattr(imaplib, "IMAP4_SSL", ImapConnections(
        [TimeoutError("timed out"), TimeoutError("timed out")]))

    assert harness.run() == 0

    [email] = harness.sent
    assert "Gmail 没读到" in email.text


@pytest.mark.parametrize("command", [["messages", "list"], ["messages", "read"]])
def test_wilma_command_timing_out_once_is_run_again(harness, real_wilma, monkeypatch, command):
    real_wilma([1, 2], {1: "Yksi", 2: "Kaksi"})
    fake_wilma = subprocess.run
    timed_out: list[list[str]] = []

    def slow_the_first_time(cmd: list[str], *a: Any, **k: Any) -> subprocess.CompletedProcess:
        if cmd[0] == wilma.WILMA and cmd[1:3] == command and not timed_out:
            timed_out.append(cmd)
            raise subprocess.TimeoutExpired(cmd, k["timeout"])
        return fake_wilma(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", slow_the_first_time)

    assert harness.run() == 0

    assert timed_out
    assert payload_ids(harness, "wilma") == ["message:1", "message:2"]
    [email] = harness.sent
    assert "Wilma 没读到" not in email.text and "Wilma 部分没读到" not in email.text


def test_wilma_timing_out_twice_is_reported_as_not_read(harness, real_wilma, monkeypatch):
    real_wilma([1], {1: "Yksi"})
    fake_wilma = subprocess.run
    tries: list[list[str]] = []

    def always_slow(cmd: list[str], *a: Any, **k: Any) -> subprocess.CompletedProcess:
        if cmd[0] == wilma.WILMA and cmd[1:3] == ["messages", "list"]:
            tries.append(cmd)
            raise subprocess.TimeoutExpired(cmd, k["timeout"])
        return fake_wilma(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", always_slow)

    assert harness.run() == 0

    assert len(tries) == 2
    assert payload_ids(harness, "wilma") == []
    [email] = harness.sent
    assert "Wilma 没读到" in email.text


ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:training-1
SUMMARY:Football training
DTSTART:20260929T150000Z
DTEND:20260929T163000Z
END:VEVENT
END:VCALENDAR
"""


class IcsResponse:
    status_code, text = 200, ICS


def test_myclub_feed_timing_out_once_is_fetched_again(harness, monkeypatch):
    one_whatsapp_message(harness)
    monkeypatch.setattr(myclub, "collect_events", REAL_MYCLUB_COLLECT_EVENTS)
    gets: list[str] = []

    def get(url: str, **_k: Any) -> IcsResponse:
        gets.append(url)
        if len(gets) == 1:
            raise requests.ConnectTimeout("connect timed out")
        return IcsResponse()
    monkeypatch.setattr(myclub.requests, "get", get)

    assert harness.run() == 0

    assert len(gets) == 2
    coverage = json.loads((harness.archive_dir / "2026-09-27.raw.json").read_text())["summary"]["_coverage"]
    assert coverage["myclub"] == {"count": 1, "error": None}
