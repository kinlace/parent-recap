"""The Local Files Source (ADR 0044, #868) in the evening run, `doctor` and `setup save`: the
messages in the folder's `.json`, `.txt` and `.md` files that are new since the last run reach the
Brief once, under their chat, through the AI filter; a broken file is skipped and named by
`doctor`; the files are never changed; a missing folder is said on the coverage line and by
`doctor`; and a Hosted Household never has it. The folder is real, in the test's HOME, written by
fake_sources.LocalFiles."""
from __future__ import annotations

import json
import os
from datetime import timedelta

import pytest
import time_machine

from conftest import NOW, SAYS_SOMETHING, local_message, msg, without_unread_note
from fake_sources import LocalFiles
from nights import caught_up_last_night, payload_message_ids
from test_doctor import nothing_loaded  # noqa: F401  (a fixture)
from test_setup_save import existing_config, result, save, saved

from family_brief import ops

TONIGHT = [
    local_message("2026-09-27T18:05:00+03:00", "Chinese school is closed next Saturday",
                  chat="Chinese school", sender="Li Wei"),
    local_message("2026-09-27T19:30:00+03:00", "Dance recital rehearsal moves to 17:00 on Wednesday",
                  chat="Dance class", sender="Teacher Anna"),
]


@pytest.fixture
def local_only(harness):
    """A Household with Local Files turned on and tonight's messages in its folder, nothing else
    read, whose model has something to say."""
    harness.config["summary_language"] = "en"
    harness.config["local_files"] = {"enabled": True}
    harness.sources = {"local_files": list(TONIGHT)}
    harness.model_reply = SAYS_SOMETHING
    return harness.source_fakes["local_files"]


def coverage_line(harness) -> str:
    [email] = harness.sent
    return without_unread_note(email.text).rsplit("\n\n", 1)[1]


def payload(harness) -> list[dict]:
    return [m for m in harness.model_payload()["messages"] if m["source"] == "local_files"]


def test_tonight_s_messages_reach_the_brief_under_their_chat(harness, local_only):
    local_only.write("Piano notice.txt", "Piano lessons start at 16:30 from next week.\n")

    assert harness.run() == 0

    assert sorted((m["chat_name"], m["body"]) for m in payload(harness)) == [
        ("Chinese school", "Chinese school is closed next Saturday"),
        ("Dance class", "Dance recital rehearsal moves to 17:00 on Wednesday"),
        ("Piano notice", "Piano lessons start at 16:30 from next week.")]
    assert coverage_line(harness).endswith("Local Files 3 messages")


def test_a_message_is_read_once_and_one_from_before_the_last_run_not_at_all(harness, local_only):
    harness.sources["local_files"] = [
        *TONIGHT, local_message("2026-09-25T18:00:00+03:00", "An old notice", chat="Dance class")]

    assert harness.run() == 0
    assert payload_message_ids(harness) == sorted(m.external_id for m in TONIGHT)

    # The next evening the same files are still there, and one new message has been added.
    later = local_message("2026-09-28T18:00:00+03:00", "Bring dance shoes", chat="Dance class")
    harness.sources["local_files"] = [*TONIGHT, later]
    with time_machine.travel(NOW + timedelta(days=1), tick=False):
        assert harness.run() == 0

    assert payload_message_ids(harness) == [later.external_id]


def test_after_a_missed_evening_the_messages_since_the_last_run_still_come(harness, local_only):
    caught_up_last_night(harness)
    missed = local_message("2026-09-27T09:00:00+03:00", "Saturday's class is in room 2", chat="Dance class")
    harness.sources["local_files"] = [missed]

    with time_machine.travel(NOW + timedelta(days=1), tick=False):
        assert harness.run() == 0

    assert payload_message_ids(harness) == [missed.external_id]
    assert harness.lookback_hours["local_files"] == [50]


def test_a_broken_file_is_skipped_and_the_brief_still_goes_out(harness, local_only):
    local_only.write("broken.json", '[{"time": "2026-09-27T18:00:00+03:00", "text": ')
    local_only.write("other.json", json.dumps([
        {"time": "2026-09-27T18:10:00+03:00", "chat": "Go club", "text": "Tournament on Sunday"},
        {"chat": "Go club", "text": "No time here"}]))
    local_only.write("latin1.txt", "Kev\xe4tjuhla".encode("latin-1"))

    assert harness.run() == 0

    assert sorted(m["body"] for m in payload(harness)) == sorted(
        [m.body for m in TONIGHT] + ["Tournament on Sunday"])
    assert coverage_line(harness).endswith("Local Files 3 messages")


def test_the_files_are_never_moved_changed_or_deleted(harness, local_only):
    harness.sources["local_files"] = []
    local_only.write("Piano notice.md", "# Piano\nLessons start at 16:30.")
    local_only.write("broken.json", "[")
    local_only.write("messages.json", json.dumps([LocalFiles.entry(m) for m in TONIGHT]))
    folder = local_only.folder()
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.iterdir()}

    assert harness.run() == 0

    assert len(payload(harness)) == 3
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.iterdir()} == before


def test_senders_and_names_go_through_the_ai_filter_and_sensitive_ones_are_held_back(harness, local_only):
    harness.sources["local_files"] = [
        local_message("2026-09-27T18:00:00+03:00", "Call Li Wei on 040 123 4567 about the trip, "
                      "or see https://chinese-school.example.fi/trip", chat="Chinese school", sender="Li Wei"),
        local_message("2026-09-27T18:30:00+03:00", "Mia has been bullied at the dance class",
                      chat="Dance class", sender="Teacher Anna")]

    assert harness.run() == 0

    prompt = harness.model_prompt()
    for real in ("Li Wei", "040 123 4567", "chinese-school.example.fi", "bullied"):
        assert real not in prompt
    [masked] = payload(harness)
    assert masked["sender"].startswith("⟦N") and "⟦P" in masked["body"] and "⟦L" in masked["body"]
    [email] = harness.sent
    assert "• Local Files · Dance class · Teacher Anna · " in email.text
    assert "bullied" not in email.text


def test_a_missing_folder_is_said_on_the_coverage_line_and_the_rest_still_goes_out(harness, local_only):
    local_only.made = False
    harness.sources = {"local_files": [], "gmail": [
        msg("gmail", "1001", "2026-09-27T18:00:00+03:00", "Retki huomenna",
            sender="teacher@kilo.example.fi", subject="Retki")]}

    assert harness.run() == 0

    assert coverage_line(harness) == (
        "📥 Read tonight: Gmail 1 message · MyClub 0 events · Wilma 0 messages · WhatsApp 0 messages · "
        "Local Files 0 messages\n⚠️ Local Files not read (the folder ~/ParentRecap/inbox does not "
        "exist). Tonight's Brief may be incomplete.")


def test_doctor_says_the_folder_is_missing(harness, nothing_loaded, capsys):  # noqa: F811
    harness.config["local_files"] = {"enabled": True}

    harness.cli("doctor", "--skip-llm")

    assert (f"{ops.FAIL} Local Files: the folder ~/ParentRecap/inbox does not exist, so the Brief has "
            "no messages from it (create the folder, or choose another one with the manage skill)"
            ) in capsys.readouterr().out.splitlines()


def test_doctor_says_how_many_messages_are_waiting(harness, nothing_loaded, capsys):  # noqa: F811
    harness.config["local_files"] = {"enabled": True}
    harness.sources = {"local_files": list(TONIGHT)}
    harness.set_up_sources()
    harness.source_fakes["local_files"].write("old.txt", "From long ago")
    os.utime(harness.source_fakes["local_files"].folder() / "old.txt",
             ((NOW - timedelta(days=3)).timestamp(),) * 2)

    harness.cli("doctor", "--skip-llm")

    assert (f"{ops.OK} Local Files: reading ~/ParentRecap/inbox, 2 new messages for the next Brief"
            ) in capsys.readouterr().out.splitlines()


def test_doctor_names_each_file_it_couldn_t_read_and_why(harness, nothing_loaded, capsys):  # noqa: F811
    harness.config["local_files"] = {"enabled": True}
    harness.set_up_sources()
    fake = harness.source_fakes["local_files"]
    fake.write("a.json", "{not json")
    fake.write("b.json", json.dumps({"time": "2026-09-27T18:00:00+03:00", "text": "hi"}))
    fake.write("c.json", json.dumps([{"time": "2026-09-27T18:00:00+03:00", "text": "Fine"},
                                     {"time": "yesterday", "text": "Not fine"}]))
    fake.write("d.txt", b"\xff\xfe\xfa")
    fake.write("notes.pdf", b"%PDF")  # not a kind it reads, so not named

    harness.cli("doctor", "--skip-llm")

    assert (f"{ops.WARN} Local Files: reading ~/ParentRecap/inbox, 1 new message for the next Brief, "
            "but skipping what couldn't be read (a.json: it isn't valid JSON (line 1: Expecting "
            "property name enclosed in double quotes); b.json: it isn't a list of messages; c.json: "
            "1 of its 2 messages couldn't be read: message 2 has a time that isn't ISO 8601; d.txt: "
            "it isn't UTF-8 text)") in capsys.readouterr().out.splitlines()


def test_doctor_says_nothing_of_it_while_it_s_off(harness, nothing_loaded, capsys):  # noqa: F811
    harness.cli("doctor", "--skip-llm")

    assert "Local Files" not in capsys.readouterr().out


def test_turning_it_on_creates_the_folder_and_turning_it_off_keeps_the_folder(harness, capsys):
    existing_config(harness)
    folder = harness.home / "Family inbox"

    assert save(harness, {"sources": {"local_files": {"enabled": True, "folder": str(folder)}}}) == 0

    assert result(capsys)[0]["result"] == "saved"
    assert saved(harness)["local_files"] == {"enabled": True, "folder": str(folder)}
    assert folder.is_dir()

    assert save(harness, {"sources": {"local_files": {"enabled": False}}}) == 0
    assert saved(harness)["local_files"] == {"enabled": False, "folder": str(folder)}
    assert folder.is_dir()


def test_the_default_folder_is_created_when_it_s_turned_on(harness, capsys):
    existing_config(harness)

    assert save(harness, {"sources": {"local_files": {"enabled": True}}}) == 0

    assert (harness.home / "ParentRecap" / "inbox").is_dir()


def test_a_hosted_household_can_t_turn_it_on_and_never_reads_it(harness, capsys):
    harness.config["hosted"] = True
    existing_config(harness)

    save(harness, {"sources": {"local_files": {"enabled": True}}})

    out, _ = result(capsys)
    assert out["result"] == "invalid-answers" and "local_files" not in saved(harness)
    assert not (harness.home / "ParentRecap" / "inbox").exists()

    harness.config["local_files"] = {"enabled": True}  # written into the config by hand
    harness.sources = {"local_files": list(TONIGHT)}
    harness.model_reply = SAYS_SOMETHING
    assert harness.run() == 0
    assert harness.model_calls == [] and "local_files" not in harness.state()["seen_message_ids"]
