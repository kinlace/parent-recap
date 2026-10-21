"""The WhatsApp Source reads tonight's messages from WhatsApp for Mac's database, run for real
against a small ChatStorage.sqlite in the temporary HOME, built by the WhatsApp Source's fake
(fake_sources.py) with the tables and columns WhatsApp's Core Data store uses. The Mac has given the
Source's Python Full Disk Access. All names, chats and messages are made up."""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import time_machine

from conftest import NOW
from fake_sources import TEXT, WhatsAppDb

from family_brief.collectors import whatsapp
from family_brief.collectors.base import Message
from family_brief.config import Config
from family_brief.state import State

# WhatsApp for Mac's database.
pytestmark = pytest.mark.macos

# ZWAMESSAGE.ZMESSAGETYPE values the Source treats differently.
IMAGE, VOICE, SYSTEM, URL = 1, 3, 6, 7
NEW_TYPE = 99  # a type a later WhatsApp adds, which has no placeholder


@pytest.fixture
def mac(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WhatsAppDb:
    """A Mac with WhatsApp signed in and Full Disk Access for this Python, at NOW."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    tcc = whatsapp.full_disk_access_file()
    tcc.parent.mkdir(parents=True)
    tcc.write_bytes(b"")
    db_dir = home / "Library" / "Group Containers" / "group.net.whatsapp.WhatsApp.shared"
    monkeypatch.setattr(whatsapp, "DB_DIR", db_dir)
    monkeypatch.setattr(whatsapp, "DB_FILE", db_dir / "ChatStorage.sqlite")
    db = WhatsAppDb(db_dir / "ChatStorage.sqlite")
    with time_machine.travel(NOW, tick=False):
        yield db
    db.close()


def config(*chats: dict, lookback_hours: int = 26) -> Config:
    return Config.model_validate({
        "kids": [{"name": "Mia", "grade": 3}, {"name": "Leo", "grade": 1}],
        "whatsapp": {"enabled": True, "chats": list(chats), "lookback_hours": lookback_hours},
    })


CLASS = {"name": "3B vanhemmat 🏫", "kid": "Mia", "label": "class"}
PIANO = {"name": "Leo piano", "kid": "Leo", "label": "piano"}
BOTH = {"name": "Kilo School parents", "kid": "both"}


def collect(cfg: Config, state: State) -> list[Message]:
    return whatsapp.collect(cfg, state, []).messages


def bodies(messages: list[Message]) -> list[str]:
    return [m.body for m in messages]


def test_only_configured_chats_are_read_and_a_missing_one_is_a_warning(mac, tmp_path, caplog):
    cls, piano, other = mac.chat(CLASS["name"]), mac.chat(PIANO["name"]), mac.chat("Neighbours")
    mac.message(cls, NOW - timedelta(hours=2), "Retki perjantaina")
    mac.message(piano, NOW - timedelta(hours=1), "Piano moves to Wednesday")
    mac.message(other, NOW - timedelta(hours=1), "Anyone selling a bike?")
    cfg = config(CLASS, {"name": "Mia football"}, PIANO)

    with caplog.at_level(logging.WARNING, logger="family_brief.collectors.whatsapp"):
        got = collect(cfg, State(tmp_path / "state.json"))

    assert sorted(bodies(got)) == ["Piano moves to Wednesday", "Retki perjantaina"]
    [warning] = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert "Mia football" in warning.getMessage()
    assert CLASS["name"] not in warning.getMessage()


def test_chat_names_match_exactly(mac, tmp_path):
    # Config matching is exact: a name WhatsApp writes with a trailing space is another chat.
    mac.message(mac.chat("Leo piano "), NOW - timedelta(hours=1), "Not this one")
    mac.message(mac.chat(PIANO["name"]), NOW - timedelta(hours=1), "This one")

    assert bodies(collect(config(PIANO), State(tmp_path / "state.json"))) == ["This one"]


def test_only_messages_inside_the_lookback_window_with_their_real_time(mac, tmp_path):
    chat = mac.chat(CLASS["name"])
    mac.message(chat, NOW - timedelta(hours=27), "Day before yesterday")
    mac.message(chat, NOW - timedelta(hours=26, seconds=1), "Just outside")
    mac.message(chat, NOW - timedelta(hours=26), "Exactly at the cutoff")
    at = datetime(2026, 9, 27, 15, 42, 7, tzinfo=timezone.utc)
    mac.message(chat, at, "Tonight")

    got = collect(config(CLASS), State(tmp_path / "state.json"))

    assert bodies(got) == ["Exactly at the cutoff", "Tonight"]
    assert got[1].timestamp == at
    assert got[1].timestamp.tzinfo == timezone.utc


def test_a_shorter_lookback_reads_less(mac, tmp_path):
    chat = mac.chat(CLASS["name"])
    mac.message(chat, NOW - timedelta(hours=5), "This afternoon")
    mac.message(chat, NOW - timedelta(hours=1), "This evening")

    got = collect(config(CLASS, lookback_hours=2), State(tmp_path / "state.json"))

    assert bodies(got) == ["This evening"]


def test_messages_come_in_time_order(mac, tmp_path):
    chat = mac.chat(CLASS["name"])
    mac.message(chat, NOW - timedelta(hours=1), "Second")
    mac.message(chat, NOW - timedelta(hours=3), "First")

    assert bodies(collect(config(CLASS), State(tmp_path / "state.json"))) == ["First", "Second"]


def test_system_events_are_skipped(mac, tmp_path):
    chat = mac.chat(CLASS["name"])
    mac.message(chat, NOW - timedelta(hours=3), "Opettaja Saarinen added Huoltaja Virtanen",
                msg_type=SYSTEM)
    mac.message(chat, NOW - timedelta(hours=2), "A row from before WhatsApp kept the type", msg_type=None)
    mac.message(chat, NOW - timedelta(hours=1), "https://kilo.example.fi/menu", msg_type=URL)

    got = collect(config(CLASS), State(tmp_path / "state.json"))

    assert bodies(got) == ["A row from before WhatsApp kept the type", "https://kilo.example.fi/menu"]


def test_media_without_text_gets_a_placeholder_and_empty_rows_are_seen_not_returned(mac, tmp_path):
    chat = mac.chat(CLASS["name"])
    mac.message(chat, NOW - timedelta(hours=5), None, msg_type=IMAGE)
    mac.message(chat, NOW - timedelta(hours=4), "  ", msg_type=VOICE)
    mac.message(chat, NOW - timedelta(hours=3), "Kuva retkeltä", msg_type=IMAGE)
    empty = mac.message(chat, NOW - timedelta(hours=2), None, msg_type=TEXT)
    unknown = mac.message(chat, NOW - timedelta(hours=1), "   ", msg_type=NEW_TYPE)
    state = State(tmp_path / "state.json")

    got = collect(config(CLASS), state)

    assert bodies(got) == ["[image]", "[voice]", "Kuva retkeltä"]
    assert state.has_seen_message("whatsapp", empty)
    assert state.has_seen_message("whatsapp", unknown)


def test_a_long_message_is_cut_and_text_is_trimmed(mac, tmp_path):
    chat = mac.chat(CLASS["name"])
    mac.message(chat, NOW - timedelta(hours=2), "  Muistakaa liikuntavaatteet\n")
    mac.message(chat, NOW - timedelta(hours=1), "x" * (whatsapp.MAX_BODY_CHARS + 50))

    got = collect(config(CLASS), State(tmp_path / "state.json"))

    assert got[0].body == "Muistakaa liikuntavaatteet"
    assert got[1].body == "x" * whatsapp.MAX_BODY_CHARS


def test_a_message_returned_one_night_is_not_returned_the_next(mac, tmp_path):
    chat = mac.chat(CLASS["name"])
    path = tmp_path / "state.json"
    first = mac.message(chat, NOW - timedelta(hours=1), "Tonight")
    mac.message(chat, NOW - timedelta(hours=2), None, msg_type=TEXT)
    state = State(path)
    assert [m.external_id for m in collect(config(CLASS), state)] == [first]
    state.save()

    tomorrow = NOW + timedelta(days=1)
    mac.message(chat, tomorrow - timedelta(hours=1), "Tomorrow night")
    with time_machine.travel(tomorrow, tick=False):
        got = collect(config(CLASS), State(path))

    # Tonight's message is still inside tomorrow's 26-hour lookback, and is not read again.
    assert bodies(got) == ["Tomorrow night"]


def test_each_message_carries_its_chat_sender_and_kid(mac, tmp_path):
    cls, piano, school = mac.chat(CLASS["name"]), mac.chat(PIANO["name"]), mac.chat(BOTH["name"])
    contact = mac.member(cls, "358401110001@s.whatsapp.net", contact="Maija Virtanen", first="Maija")
    first_only = mac.member(cls, "358401110002@s.whatsapp.net", first="Pekka")
    pushed = mac.member(piano, "358401110003@s.whatsapp.net")
    mac.push_name("358401110003@s.whatsapp.net", "Opettaja Saarinen")
    mac.push_name("358401110004@s.whatsapp.net", "Rehtori Niemi")
    unknown = mac.member(school, "358401110005@s.whatsapp.net")
    sent = [
        mac.message(cls, NOW - timedelta(hours=6), "Retki perjantaina", member=contact),
        mac.message(cls, NOW - timedelta(hours=5), "Kiitos!", member=first_only),
        mac.message(piano, NOW - timedelta(hours=4), "Piano moves to Wednesday", member=pushed),
        mac.message(school, NOW - timedelta(hours=3), "Koulu alkaa klo 9",
                    from_jid="358401110004@s.whatsapp.net"),
        mac.message(school, NOW - timedelta(hours=2), "Hei kaikki", member=unknown),
        mac.message(piano, NOW - timedelta(hours=1), "We'll be there", from_me=True,
                    from_jid="358409999999@s.whatsapp.net"),
    ]

    got = collect(config(CLASS, PIANO, BOTH), State(tmp_path / "state.json"))

    by_id = {m.external_id: m for m in got}
    assert set(by_id) == set(sent)
    assert [(by_id[i].chat_name, by_id[i].sender, by_id[i].kid_hint) for i in sent] == [
        (CLASS["name"], "Maija Virtanen", "Mia"),
        (CLASS["name"], "Pekka", "Mia"),
        (PIANO["name"], "Opettaja Saarinen", "Leo"),
        (BOTH["name"], "Rehtori Niemi", "both"),
        (BOTH["name"], "(group member)", "both"),
        (PIANO["name"], "me", "Leo"),
    ]
    assert all(m.source == "whatsapp" for m in got)
    assert by_id[sent[5]].metadata == {"from_me": True, "msg_type": TEXT}
    assert by_id[sent[0]].metadata == {"from_me": False, "msg_type": TEXT}


def test_a_chat_without_a_kid_has_no_kid_hint(mac, tmp_path):
    mac.message(mac.chat("Neighbours"), NOW - timedelta(hours=1), "Street party Saturday")

    [got] = collect(config({"name": "Neighbours"}), State(tmp_path / "state.json"))

    assert got.kid_hint is None and got.chat_name == "Neighbours"


def test_the_snapshot_is_removed_and_the_live_database_is_untouched(mac, tmp_path, monkeypatch):
    mac.message(mac.chat(CLASS["name"]), NOW - timedelta(hours=1), "Tonight")
    copied_to: list[Path] = []
    copy = shutil.copy2

    def copy2(src: Path, dst: Path, **k) -> Path:
        copied_to.append(Path(dst))
        return copy(src, dst, **k)
    monkeypatch.setattr(shutil, "copy2", copy2)
    before = whatsapp.DB_FILE.read_bytes()

    assert bodies(collect(config(CLASS), State(tmp_path / "state.json"))) == ["Tonight"]

    [snapshot] = copied_to
    assert snapshot.parent != whatsapp.DB_DIR and not snapshot.parent.exists()
    assert whatsapp.DB_FILE.read_bytes() == before
