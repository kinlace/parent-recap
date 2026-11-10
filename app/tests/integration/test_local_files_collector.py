"""The Local Files collector (ADR 0044) on a real folder: what each kind of file gives, what it leaves
alone, the problems it names, the since-last-run window and messages given once."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import time_machine

from family_brief.collectors import local_files
from family_brief.config import Config
from family_brief.state import State

NOW = datetime(2026, 9, 27, 21, 0, tzinfo=ZoneInfo("Europe/Helsinki"))


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    folder = tmp_path / "inbox"
    folder.mkdir()
    return folder


@pytest.fixture
def cfg(folder: Path) -> Config:
    return Config(kids=[{"name": "Mia"}], timezone="Europe/Helsinki",
                  local_files={"enabled": True, "folder": str(folder)})


@pytest.fixture(autouse=True)
def now():
    with time_machine.travel(NOW, tick=False):
        yield


def changed_at(path: Path, when: datetime) -> None:
    os.utime(path, (when.timestamp(), when.timestamp()))


def test_a_json_file_s_messages_each_in_its_chat_or_the_file_s(cfg, folder):
    (folder / "Chinese school.json").write_text(json.dumps([
        {"time": "2026-09-27T18:00:00+03:00", "chat": "Dance", "sender": "Anna", "text": " Rehearsal at 17 "},
        {"time": "2026-09-27T17:00:00", "text": "Closed on Saturday"},  # no offset: the Household's zone
        {"time": "2026-09-27T14:30:00Z", "chat": "", "sender": None, "text": "Bring a pencil"},
    ], ensure_ascii=False), encoding="utf-8")

    found = local_files.read(cfg)

    assert found.problems == []
    assert [(m.timestamp, m.chat_name, m.sender, m.body) for m in found.messages] == [
        (datetime(2026, 9, 27, 14, 0, tzinfo=timezone.utc), "Chinese school", None, "Closed on Saturday"),
        (datetime(2026, 9, 27, 14, 30, tzinfo=timezone.utc), "Chinese school", None, "Bring a pencil"),
        (datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc), "Dance", "Anna", "Rehearsal at 17")]
    assert all(m.source == "local_files" for m in found.messages)


def test_a_text_or_markdown_file_is_one_message_named_by_the_file_at_its_last_change(cfg, folder):
    (folder / "Piano.txt").write_text("﻿Lessons start at 16:30\n", encoding="utf-8")
    (folder / "Go club.MD").write_bytes("# Tournament\r\nSunday 10:00".encode("utf-8"))  # Windows line ends
    (folder / "empty.txt").write_text("  \n")
    changed_at(folder / "Piano.txt", NOW - timedelta(hours=2))
    changed_at(folder / "Go club.MD", NOW - timedelta(hours=1))

    found = local_files.read(cfg)

    assert [(m.chat_name, m.body, m.timestamp) for m in found.messages] == [
        ("Piano", "Lessons start at 16:30", NOW - timedelta(hours=2)),
        ("Go club", "# Tournament\nSunday 10:00", NOW - timedelta(hours=1))]
    assert found.problems == []


def test_other_files_hidden_files_and_subfolders_are_left_alone(cfg, folder):
    (folder / ".notes.txt").write_text("hidden")
    (folder / "photo.jpg").write_bytes(b"\xff\xd8")
    (folder / "old").mkdir()
    (folder / "old" / "more.txt").write_text("in a subfolder")

    assert local_files.read(cfg) == local_files.Folder()


def test_each_file_that_can_t_be_read_is_named_with_why_and_the_rest_are_read(cfg, folder):
    (folder / "a.json").write_text('[{"time": ')
    (folder / "b.json").write_text('{"time": "2026-09-27T18:00:00+03:00", "text": "hi"}')
    (folder / "c.json").write_text(json.dumps([
        {"time": "2026-09-27T18:00:00+03:00", "text": "Fine"}, "just text", {"text": "no time"},
        {"time": "2026-09-27T18:00:00+03:00", "text": ""},
        {"time": "2026-09-27T18:00:00+03:00", "text": "x", "sender": 5}]))
    (folder / "d.txt").write_bytes("Kevätjuhla".encode("latin-1"))
    (folder / "e.txt").write_bytes(b"x" * (local_files.MAX_FILE_BYTES + 1))

    found = local_files.read(cfg)

    assert [m.body for m in found.messages] == ["Fine"]
    assert found.problems == [
        local_files.Problem("a.json", "it isn't valid JSON (line 1: Expecting value)"),
        local_files.Problem("b.json", "it isn't a list of messages"),
        local_files.Problem("c.json", "4 of its 5 messages couldn't be read: message 2 isn't an object"),
        local_files.Problem("d.txt", "it isn't UTF-8 text"),
        local_files.Problem("e.txt", "it is larger than 5 MB")]


def test_a_missing_folder_is_said(cfg, folder):
    folder.rmdir()
    with pytest.raises(local_files.FolderMissing, match="does not exist"):
        local_files.read(cfg)
    folder.write_text("")
    with pytest.raises(local_files.FolderMissing, match="is a file, not a folder"):
        local_files.read(cfg)


def test_only_messages_since_the_lookback_and_not_seen_before_are_given_once(cfg, folder, tmp_path):
    state = State(tmp_path / "state.json")
    (folder / "chat.json").write_text(json.dumps([
        {"time": (NOW - timedelta(hours=27)).isoformat(), "text": "Before the last run"},
        {"time": (NOW - timedelta(hours=3)).isoformat(), "text": "Tonight"}]))
    # The same messages without a chat of their own, so in another chat: the file's.
    (folder / "chat copy.json").write_text((folder / "chat.json").read_text())

    first = local_files.collect(cfg, state, [])

    assert sorted((m.chat_name, m.body) for m in first.messages) == [("chat", "Tonight"),
                                                                      ("chat copy", "Tonight")]
    assert local_files.collect(cfg, state, []).messages == []
    # A lookback widened over a missed night reaches the older one.
    cfg.local_files.lookback_hours = 50
    assert [m.body for m in local_files.collect(cfg, state, []).messages] == ["Before the last run"] * 2


def test_the_same_message_in_two_files_is_given_once(cfg, folder, tmp_path):
    message = [{"time": "2026-09-27T18:00:00+03:00", "chat": "Dance", "text": "Rehearsal at 17"}]
    (folder / "export 1.json").write_text(json.dumps(message))
    (folder / "export 2.json").write_text(json.dumps(message))

    assert len(local_files.collect(cfg, State(tmp_path / "state.json"), []).messages) == 1


def test_a_text_file_saved_again_unchanged_gives_nothing_new_and_one_changed_does(cfg, folder, tmp_path):
    state = State(tmp_path / "state.json")
    notice = folder / "Piano.txt"
    notice.write_text("Lessons start at 16:30")
    assert len(local_files.collect(cfg, state, []).messages) == 1

    notice.write_text("Lessons start at 16:30")
    assert local_files.collect(cfg, state, []).messages == []

    notice.write_text("Lessons start at 17:00")
    assert [m.body for m in local_files.collect(cfg, state, []).messages] == ["Lessons start at 17:00"]


def test_reading_changes_nothing_in_the_folder(cfg, folder, tmp_path):
    (folder / "chat.json").write_text(json.dumps([{"time": NOW.isoformat(), "text": "Hi"}]))
    (folder / "broken.json").write_text("[")
    (folder / "Piano.txt").write_text("Lessons")
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.iterdir()}

    local_files.collect(cfg, State(tmp_path / "state.json"), [])

    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.iterdir()} == before


def test_a_hosted_household_never_has_it(cfg):
    assert local_files.connected(cfg)
    assert not local_files.connected(cfg.model_copy(update={"hosted": True}))
    assert not local_files.connected(cfg.model_copy(update={"local_files": cfg.local_files.model_copy(
        update={"enabled": False})}))
