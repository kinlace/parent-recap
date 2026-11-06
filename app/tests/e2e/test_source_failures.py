"""A Source that fails partway through a night, or times out. The real Sources run here, as in every
test of the evening run, against their fakes (fake_sources.py) failing: Gmail's IMAP server, the
`wilma` CLI and the MyClub feed. Assertions are on what leaves the system: the
model payload, the email's coverage line and persisted state."""
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

from conftest import NOW, SAYS_SOMETHING, msg, myclub_event
from nights import caught_up_last_night


@pytest.fixture(autouse=True)
def the_model_says_something(harness):
    """So a night with new Messages sends its Brief, which these tests read the coverage line from."""
    harness.model_reply = SAYS_SOMETHING


def payload_ids(h, source: str) -> list[str]:
    return sorted(m["external_id"] for m in h.model_payload()["messages"] if m["source"] == source)


def one_new_message(h, under_test: str) -> None:
    """So each night has something new and a Brief goes out, whatever the Source under test gives:
    a Wilma message when Gmail is under test, else a Gmail one."""
    def tonight() -> list:
        n = 100 + len(h.model_calls)
        if under_test == "gmail":
            return [msg("wilma", f"message:{n}", "2026-09-27T18:00:00+03:00", "Piano moves to Wednesday",
                        sender="Opettaja Virtanen", subject="Piano", kid="Leo")]
        return [msg("gmail", f"9{n}", "2026-09-27T18:00:00+03:00", "Piano moves to Wednesday",
                    sender="anna.piano@kilo.example.fi", subject="Piano")]
    h.sources["wilma" if under_test == "gmail" else "gmail"] = tonight


def seen(h, source: str) -> list[str]:
    return sorted(h.state()["seen_message_ids"].get(source, {}))


# ── Gmail

def email_bytes(n: int, *, charset: str = "utf-8") -> bytes:
    """Gmail message `n` as raw RFC822, in `charset`."""
    m = EmailMessage()
    m["From"] = "teacher.3b@kilo.example.fi"
    m["Subject"] = f"Viesti {n}"
    m["Date"] = "Sun, 27 Sep 2026 10:00:00 +0300"
    m.set_content(f"Message number {n}.")
    raw = m.as_bytes()
    if charset != "utf-8":
        raw = raw.replace(b'charset="utf-8"', f'charset="{charset}"'.encode())
    return raw


def gmail_messages(*numbers: int) -> list:
    """Gmail messages, numbered as email_bytes numbers them: message n is X-GM-MSGID 10n."""
    return [msg("gmail", f"10{n}", "2026-09-27T10:00:00+03:00", f"Message number {n}.",
                sender="teacher.3b@kilo.example.fi", subject=f"Viesti {n}") for n in numbers]


@pytest.fixture
def gmail(harness):
    """Gmail's server, for a household already running nightly."""
    one_new_message(harness, "gmail")
    caught_up_last_night(harness)
    return harness.source_fakes["gmail"]


def test_gmail_connection_dropped_partway_brings_its_messages_back_next_night(harness, gmail):
    harness.sources["gmail"] = gmail_messages(1, 2, 3, 4)
    gmail.connections = [{"103": imaplib.IMAP4.abort("socket error: EOF")}]

    assert harness.run() == 0

    assert seen(harness, "gmail") == []
    [email] = harness.sent
    assert "Gmail 没读到（socket error: EOF）" in email.text

    with time_machine.travel(NOW + timedelta(days=1), tick=False):
        assert harness.run() == 0
    assert payload_ids(harness, "gmail") == ["101", "102", "103", "104"]


def test_gmail_in_chinese_is_read_from_its_all_mail(harness, gmail):
    """Gmail names All Mail in the account's language, so it's found by its flag (#595)."""
    harness.sources["gmail"] = gmail_messages(1, 2)
    gmail.all_mail = "[Gmail]/&YkBnCZCuTvY-"

    assert harness.run() == 0

    assert payload_ids(harness, "gmail") == ["101", "102"]


def test_gmail_unknown_charset_still_reaches_the_brief(harness, gmail):
    harness.sources["gmail"] = gmail_messages(1, 2, 3)
    gmail.connections = [{"102": email_bytes(2, charset="unknown-8bit")}]

    assert harness.run() == 0

    assert payload_ids(harness, "gmail") == ["101", "102", "103"]
    [m] = [m for m in harness.model_payload()["messages"] if m["external_id"] == "102"]
    assert "Message number 2." in m["body"]


def test_gmail_unreadable_message_is_skipped_and_the_rest_arrive(harness, gmail):
    harness.sources["gmail"] = gmail_messages(1, 2, 3)
    gmail.connections = [{"102": b""}]

    assert harness.run() == 0

    assert payload_ids(harness, "gmail") == ["101", "103"]
    [email] = harness.sent
    assert "Gmail 部分没读到（有一条消息读不出来）" in email.text
    # Not marked seen, so a later night tries it again.
    assert seen(harness, "gmail") == ["101", "103"]


# ── Wilma

def wilma_message(n: int, body: str) -> Any:
    return msg("wilma", f"message:{n}", "2026-09-27T09:00:00+03:00", body, sender="Opettaja Virtanen",
               subject=f"Viesti {n}", kid="Virtanen Mia", metadata={"student_number": "7"})


@pytest.fixture
def wilma(harness):
    """The wilma CLI, for a household already running nightly."""
    one_new_message(harness, "wilma")
    caught_up_last_night(harness)
    return harness.source_fakes["wilma"]


def test_wilma_failing_partway_brings_its_messages_back_next_night(harness, wilma):
    harness.sources["wilma"] = [wilma_message(n, body) for n, body in
                                ((1, "Yksi"), (2, "Kaksi"), (3, "Kolme"), (4, "Neljä"))]
    wilma.answers[("messages", "read", "3")] = RuntimeError("wilma crashed")

    assert harness.run() == 0

    assert seen(harness, "wilma") == []

    wilma.answers.clear()
    with time_machine.travel(NOW + timedelta(days=1), tick=False):
        assert harness.run() == 0
    assert payload_ids(harness, "wilma") == ["message:1", "message:2", "message:3", "message:4"]


@pytest.mark.parametrize("failure", [
    subprocess.TimeoutExpired(["wilma"], 60),
    # `messages read` exits non-zero, saying why on stdout as the CLI does
    (1, {"status": "error", "code": "wilma_error", "message": "Wilma HTTP 500"}),
], ids=["timeout", "read-failed"])
def test_wilma_message_that_cannot_be_read_is_not_marked_seen(harness, wilma, failure):
    harness.sources["wilma"] = [wilma_message(n, body) for n, body in ((1, "Yksi"), (2, "Kaksi"), (3, "Kolme"))]
    wilma.answers[("messages", "read", "2")] = failure

    assert harness.run() == 0

    assert payload_ids(harness, "wilma") == ["message:1", "message:3"]
    assert seen(harness, "wilma") == ["message:1", "message:3"]
    [email] = harness.sent
    assert "Wilma 部分没读到（有一条消息读不出来）" in email.text

    wilma.answers.clear()
    with time_machine.travel(NOW + timedelta(days=1), tick=False):
        assert harness.run() == 0
    assert payload_ids(harness, "wilma") == ["message:2"]
    [m] = [m for m in harness.model_payload()["messages"] if m["source"] == "wilma"]
    assert m["body"] == "Kaksi"


# ── A Source that times out once (slow DNS or network on a Mac just woken up)

@pytest.mark.parametrize("first", [
    TimeoutError("timed out"),
    {"102": TimeoutError("The read operation timed out")},
], ids=["connecting", "partway"])
def test_gmail_timing_out_once_is_read_on_the_second_try(harness, gmail, first):
    harness.sources["gmail"] = gmail_messages(1, 2)
    gmail.connections = [first]

    assert harness.run() == 0

    assert payload_ids(harness, "gmail") == ["101", "102"]
    assert seen(harness, "gmail") == ["101", "102"]
    [email] = harness.sent
    assert "Gmail 没读到" not in email.text


def test_gmail_timing_out_twice_is_reported_as_not_read(harness, gmail):
    harness.sources["gmail"] = gmail_messages(1)
    gmail.connections = [TimeoutError("timed out"), TimeoutError("timed out")]

    assert harness.run() == 0

    [email] = harness.sent
    assert "Gmail 没读到" in email.text


@pytest.mark.parametrize("command", [("messages", "list"), ("messages", "read")])
def test_wilma_command_timing_out_once_is_run_again(harness, wilma, command):
    harness.sources["wilma"] = [wilma_message(1, "Yksi"), wilma_message(2, "Kaksi")]
    timed_out: list[list[str]] = []

    def slow_the_first_time(args: list[str]) -> Any:
        if timed_out:
            return wilma.prints(args)
        timed_out.append(args)
        return subprocess.TimeoutExpired(["wilma", *args], 60)
    wilma.answers[command] = slow_the_first_time

    assert harness.run() == 0

    assert timed_out
    assert payload_ids(harness, "wilma") == ["message:1", "message:2"]
    [email] = harness.sent
    assert "Wilma 没读到" not in email.text and "Wilma 部分没读到" not in email.text


def test_wilma_timing_out_twice_is_reported_as_not_read(harness, wilma):
    harness.sources["wilma"] = [wilma_message(1, "Yksi")]
    tries: list[list[str]] = []

    def always_slow(args: list[str]) -> subprocess.TimeoutExpired:
        tries.append(args)
        return subprocess.TimeoutExpired(["wilma", *args], 60)
    wilma.answers[("messages", "list")] = always_slow

    assert harness.run() == 0

    assert len(tries) == 2
    assert payload_ids(harness, "wilma") == []
    [email] = harness.sent
    assert "Wilma 没读到" in email.text


def test_myclub_feed_timing_out_once_is_fetched_again(harness):
    one_new_message(harness, "myclub")
    harness.sources["myclub"] = [myclub_event("training-1", "Football training", "2026-09-29T15:00:00+00:00",
                                              "2026-09-29T16:30:00+00:00")]
    feed = harness.source_fakes["myclub"]
    feed.failures = [requests.ConnectTimeout("connect timed out")]

    assert harness.run() == 0

    assert len(feed.gets) == 2
    coverage = json.loads((harness.archive_dir / "2026-09-27.raw.json").read_text())["summary"]["_coverage"]
    assert coverage["myclub"] == {"count": 1, "error": None}


# ── A Kid in several MyClub clubs (#810)

SWIM_LINK = "webcal://uimaseura.myclub.fi/ical/mia-swim"


def mia_in_two_clubs(harness) -> Any:
    """Mia's football club's link and her swimming club's, with a match both feeds give."""
    football = harness.config["kids"][0]["myclub_ical_url"]
    harness.config["kids"][0]["myclub_ical_url"] = [football, SWIM_LINK]
    harness.sources["myclub"] = [
        myclub_event("football-1", "Football training", "2026-09-29T15:00:00+00:00",
                     "2026-09-29T16:30:00+00:00"),
        myclub_event("swim-1", "Swimming", "2026-09-30T15:00:00+00:00", "2026-09-30T16:00:00+00:00"),
        myclub_event("gala-1", "Club gala", "2026-10-03T09:00:00+00:00", "2026-10-03T12:00:00+00:00"),
    ]
    feed = harness.source_fakes["myclub"]
    feed.feeds = {football: ["football-1", "gala-1"], SWIM_LINK: ["swim-1", "gala-1"]}
    return feed


def queued(harness) -> list[tuple[str, str]]:
    """The events queued for the calendar, with their Kid as the model sees them: Mia is Kid A."""
    return sorted((e["title"], e["kid"]) for e in harness.model_payload()["already_queued_for_calendar"])


def test_a_kid_s_events_from_every_club_come_once_under_that_kid(harness):
    one_new_message(harness, "myclub")
    feed = mia_in_two_clubs(harness)
    harness.config["google_calendar"] = {"mode": "ics", "ics_include_myclub": True}

    assert harness.run() == 0

    assert len(feed.gets) == 2
    assert queued(harness) == [("⚽ Club gala", "Kid A"), ("⚽ Football training", "Kid A"),
                               ("⚽ Swimming", "Kid A")]
    [email] = harness.sent
    ics = next(body for name, body, _ in email.attachments if name.endswith(".ics")).decode()
    assert ics.count("Club gala") == 1 and "Swimming" in ics and "Football training" in ics


def test_one_club_s_link_failing_leaves_the_other_s_events(harness, caplog):
    one_new_message(harness, "myclub")
    feed = mia_in_two_clubs(harness)
    feed.failures = [404]  # the football club's link, read first

    assert harness.run() == 0

    assert queued(harness) == [("⚽ Club gala", "Kid A"), ("⚽ Swimming", "Kid A")]
    assert "MyClub fetch failed for Mia (link 1)" in caplog.text
    assert "mia-swim" not in caplog.text and "/ical/" not in caplog.text
