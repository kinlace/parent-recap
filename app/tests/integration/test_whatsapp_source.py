"""The WhatsApp Source, which reads nothing until the Linked Device's collector lands (ADR 0027),
and the mapping that collector builds on: the Linked Device's records, as `linked_device.mjs` writes
them, to Messages, with a real State. All names, chats and messages are made up."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import time_machine

from conftest import NOW

from family_brief.collectors import whatsapp
from family_brief.collectors.base import Message
from family_brief.config import Config
from family_brief.state import State


@pytest.fixture(autouse=True)
def at_now():
    with time_machine.travel(NOW, tick=False):
        yield


def config(*chats: dict, lookback_hours: int = 26) -> Config:
    return Config.model_validate({
        "kids": [{"name": "Mia", "grade": 3}, {"name": "Leo", "grade": 1}],
        "whatsapp": {"enabled": True, "chats": list(chats), "lookback_hours": lookback_hours},
    })


CLASS = {"name": "3B vanhemmat 🏫", "kid": "Mia", "label": "class"}
PIANO = {"name": "Leo piano", "kid": "Leo", "label": "piano"}


def record(id: str, when: datetime, text: str | None, group: str = CLASS["name"],
           sender: str | None = "Maija Virtanen", from_me: bool = False) -> dict:
    """A record as the Linked Device keeps it."""
    return {"id": id, "group_id": "120363000000000001@g.us", "group_subject": group,
            "sender": sender, "timestamp": when.timestamp(), "text": text, "from_me": from_me}


def bodies(messages: list[Message]) -> list[str]:
    return [m.body for m in messages]


def test_a_household_with_whatsapp_turned_on_and_groups_picked_gets_nothing_from_it(tmp_path):
    state = State(tmp_path / "state.json")

    assert whatsapp.collect(config(CLASS, PIANO), state, []).messages == []


def test_only_records_inside_the_lookback_in_time_order_with_their_real_time(tmp_path):
    at = datetime(2026, 9, 27, 15, 42, 7, tzinfo=timezone.utc)
    records = [record("4", at, "Tonight"),
               record("1", NOW - timedelta(hours=27), "Day before yesterday"),
               record("2", NOW - timedelta(hours=26, seconds=1), "Just outside"),
               record("3", NOW - timedelta(hours=26), "Exactly at the cutoff")]

    got = whatsapp.to_messages(config(CLASS), State(tmp_path / "state.json"), records)

    assert bodies(got) == ["Exactly at the cutoff", "Tonight"]
    assert got[1].timestamp == at and got[1].timestamp.tzinfo == timezone.utc


def test_a_record_seen_before_is_not_given_again_and_one_without_text_is_seen_not_given(tmp_path):
    path = tmp_path / "state.json"
    state = State(path)
    tonight = [record("1", NOW - timedelta(hours=2), "Retki perjantaina"),
               record("2", NOW - timedelta(hours=1), "  ")]
    assert bodies(whatsapp.to_messages(config(CLASS), state, tonight)) == ["Retki perjantaina"]
    assert state.has_seen_message("whatsapp", "2")
    state.save()

    # A reconnect hands tonight's messages over again, beside a new one.
    again = [*tonight, record("3", NOW - timedelta(minutes=5), "[image]")]

    assert bodies(whatsapp.to_messages(config(CLASS), State(path), again)) == ["[image]"]


def test_text_is_trimmed_and_a_long_message_is_cut(tmp_path):
    records = [record("1", NOW - timedelta(hours=2), "  Muistakaa liikuntavaatteet\n"),
               record("2", NOW - timedelta(hours=1), "x" * (whatsapp.MAX_BODY_CHARS + 50))]

    got = whatsapp.to_messages(config(CLASS), State(tmp_path / "state.json"), records)

    assert bodies(got) == ["Muistakaa liikuntavaatteet", "x" * whatsapp.MAX_BODY_CHARS]


def test_each_message_carries_its_group_sender_and_kid(tmp_path):
    records = [record("1", NOW - timedelta(hours=3), "Retki perjantaina"),
               record("2", NOW - timedelta(hours=2), "Piano moves to Wednesday", group=PIANO["name"],
                      sender=None),
               record("3", NOW - timedelta(hours=1), "We'll be there", group=PIANO["name"],
                      sender="Anna", from_me=True),
               record("4", NOW - timedelta(minutes=5), "Street party Saturday", group="Neighbours")]

    got = whatsapp.to_messages(config(CLASS, PIANO, {"name": "Neighbours"}),
                               State(tmp_path / "state.json"), records)

    assert [(m.source, m.external_id, m.chat_name, m.sender, m.kid_hint, m.metadata) for m in got] == [
        ("whatsapp", "1", CLASS["name"], "Maija Virtanen", "Mia", {"from_me": False}),
        ("whatsapp", "2", PIANO["name"], "(group member)", "Leo", {"from_me": False}),
        ("whatsapp", "3", PIANO["name"], "me", "Leo", {"from_me": True}),
        ("whatsapp", "4", "Neighbours", "Maija Virtanen", None, {"from_me": False}),
    ]
