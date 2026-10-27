"""The WhatsApp Source as the Linked Device's collector (ADR 0027): its records, as
`linked_device.mjs` writes them to the message store, to Messages, with a real State, the picked
groups' ids it writes for follow, and the store it prunes. All names, chats and messages are made
up."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import time_machine

from conftest import NOW

from family_brief import linked_device
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


CLASS = {"name": "3B vanhemmat 🏫", "id": "120363000000000001@g.us", "kid": "Mia", "label": "class"}
PIANO = {"name": "Leo piano", "id": "120363000000000002@g.us", "kid": "Leo", "label": "piano"}
GROUP_IDS = {CLASS["name"]: CLASS["id"], PIANO["name"]: PIANO["id"]}


def record(id: str, when: datetime, text: str | None, group: str = CLASS["name"],
           sender: str | None = "Maija Virtanen", from_me: bool = False, group_id: str | None = None) -> dict:
    """A record as the Linked Device keeps it, in the group with `group_id`, or else the one named
    `group`."""
    return {"id": id, "group_id": group_id or GROUP_IDS.get(group, f"{group}@g.us"), "group_subject": group,
            "sender": sender, "timestamp": when.timestamp(), "text": text, "from_me": from_me}


def bodies(messages: list[Message]) -> list[str]:
    return [m.body for m in messages]


def stored(*records: dict, then: str = "") -> None:
    """The store holding `records`, and `then` after them, as follow left it."""
    linked_device.folder().mkdir(parents=True, exist_ok=True)
    linked_device.store().write_text("".join(json.dumps(r) + "\n" for r in records) + then)


def test_collect_reads_the_picked_groups_records_and_writes_their_ids_for_follow(home, tmp_path):
    stored(record("1", NOW - timedelta(hours=2), "Retki perjantaina"),
           record("2", NOW - timedelta(hours=1), "Street party", group="Neighbours"),
           record("3", NOW - timedelta(minutes=5), "Piano moves", group=PIANO["name"]))

    got = whatsapp.collect(config(CLASS, PIANO, {"name": "Old group, no id"}), State(tmp_path / "state.json"), [])

    assert bodies(got.messages) == ["Retki perjantaina", "Piano moves"]  # not a group unpicked since
    assert json.loads(linked_device.picked_file().read_text()) == {"groups": [CLASS["id"], PIANO["id"]]}


def test_a_group_renamed_since_it_was_picked_is_read_by_its_id_under_its_new_name(tmp_path):
    renamed = record("1", NOW - timedelta(hours=1), "Uusi lukukausi", group="3B vanhemmat 2026–27",
                     group_id=CLASS["id"])

    [m] = whatsapp.to_messages(config(CLASS), State(tmp_path / "state.json"), [renamed])

    assert (m.chat_name, m.kid_hint) == ("3B vanhemmat 2026–27", "Mia")


def test_the_store_keeps_a_week_and_a_day_and_drops_older_records_and_broken_lines(home, tmp_path):
    # A week is the furthest a lookback widened after missed nights reaches back.
    week = 7 * 24
    kept = [record("2", NOW - timedelta(hours=week + 23), "Kept"), record("3", NOW - timedelta(hours=1), "New")]
    stored(record("1", NOW - timedelta(hours=week + 25), "Pruned"), *kept[:1])
    with linked_device.store().open("a") as f:
        f.write("not a record\n" + json.dumps(kept[1]) + "\n")
    with linked_device.store().open("a") as f:
        f.write('{"id": "4", "group_id"')  # follow is still writing it

    whatsapp.collect(config(CLASS), State(tmp_path / "state.json"), [])

    lines = linked_device.store().read_text().split("\n")
    assert [json.loads(line) for line in lines[:-1]] == kept
    assert lines[-1] == '{"id": "4", "group_id"'


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
