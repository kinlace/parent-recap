"""The evening run reads WhatsApp from the Linked Device's message store (ADR 0027): the picked
groups' messages reach the Brief once, the store is pruned, the picked groups' ids are written for
the always-running job, and a link that needs a new pairing is in the coverage line. The Linked
Device is faked as the files it leaves (fake_sources.WhatsApp)."""
from __future__ import annotations

import json
import stat
from datetime import timedelta
from pathlib import Path

import pytest
import time_machine

from conftest import CLASS_GROUP, NOW, PIANO_GROUP, SAYS_SOMETHING, msg
from nights import caught_up_last_night, payload_message_ids

from family_brief import linked_device

TONIGHT = [
    msg("whatsapp", "3EB0A1", "2026-09-27T18:02:00+03:00", "Piano moves to Wednesday 17:30",
        sender="Anna (piano)", chat="Leo piano", kid="Leo"),
    msg("whatsapp", "3EB0A2", "2026-09-27T19:45:00+03:00", "Remember reissuvihko signatures",
        sender="Parent rep", chat="3B parents", kid="Mia"),
    msg("whatsapp", "3EB0A3", "2026-09-27T20:10:00+03:00", "[image]", sender="me", chat="3B parents",
        kid="Mia"),
]


@pytest.fixture
def whatsapp_only(harness):
    """A Household whose WhatsApp has its groups picked and nothing else to read tonight, whose
    model has something to say."""
    harness.sources = {"whatsapp": list(TONIGHT)}
    harness.model_reply = SAYS_SOMETHING
    harness.config["summary_language"] = "en"
    return harness.source_fakes["whatsapp"]


def whatsapp_archived(harness) -> list[dict]:
    """Tonight's WhatsApp Messages as the archive keeps them: the model sees them with placeholders."""
    raw = json.loads((harness.archive_dir / "2026-09-27.raw.json").read_text())
    return [m for m in raw["messages"] if m["source"] == "whatsapp"]


def store_ids() -> list[str]:
    """The ids of the records in the store, as it is on disk."""
    return [json.loads(line)["id"] for line in linked_device.store().read_text().splitlines()]


def one_gmail_message(harness) -> None:
    harness.sources["gmail"] = [msg("gmail", "1001", "2026-09-27T18:00:00+03:00", "Retki huomenna",
                                    sender="teacher@kilo.example.fi", subject="Retki")]


def coverage_line(harness) -> str:
    [email] = harness.sent
    return email.text.rsplit("\n\n", 1)[1]


def test_the_picked_groups_messages_reach_the_brief_once(harness, whatsapp_only):
    assert harness.run() == 0

    assert [(m["external_id"], m["chat_name"], m["kid_hint"], m["sender"]) for m in whatsapp_archived(harness)] == [
        ("3EB0A1", "Leo piano", "Leo", "Anna (piano)"),
        ("3EB0A2", "3B parents", "Mia", "Parent rep"),
        ("3EB0A3", "3B parents", "Mia", "me")]
    assert payload_message_ids(harness) == ["3EB0A1", "3EB0A2", "3EB0A3"]
    assert coverage_line(harness).endswith("WhatsApp 3 messages")

    # A reconnect hands the same messages over again: the next run has nothing new.
    with time_machine.travel(NOW + timedelta(hours=1), tick=False):
        assert harness.run() == 0

    assert len(harness.model_calls) == 1 and len(harness.sent) == 1
    assert store_ids() == [m.external_id for m in TONIGHT] * 2  # stored twice, read once


def test_a_group_no_longer_picked_is_not_read(harness, whatsapp_only):
    harness.config["whatsapp"]["chats"] = [c for c in harness.config["whatsapp"]["chats"]
                                           if c["id"] == CLASS_GROUP]

    assert harness.run() == 0

    assert payload_message_ids(harness) == ["3EB0A2", "3EB0A3"]
    assert json.loads(linked_device.picked_file().read_text()) == {"groups": [CLASS_GROUP]}


def test_a_group_renamed_on_whatsapp_but_picked_by_id_reaches_the_brief_under_its_new_name(harness, whatsapp_only):
    whatsapp_only.group_ids["3B parents 2026–27"] = CLASS_GROUP
    harness.sources["whatsapp"] = [msg("whatsapp", "3EB0B1", "2026-09-27T19:00:00+03:00",
                                       "New term, new group name", sender="Parent rep",
                                       chat="3B parents 2026–27", kid="Mia")]

    assert harness.run() == 0

    [m] = whatsapp_archived(harness)
    assert (m["chat_name"], m["kid_hint"]) == ("3B parents 2026–27", "Mia")


def test_records_past_the_longest_lookback_and_a_margin_are_pruned(harness, whatsapp_only):
    # The longest lookback is a week, as far as one widened after missed nights reaches back.
    week_and_a_day = NOW - timedelta(days=8)
    old = [whatsapp_only.record(msg("whatsapp", f"OLD{n}", (week_and_a_day - timedelta(minutes=n)).isoformat(),
                                    "Last week's news", sender="Parent rep", chat="3B parents", kid="Mia"))
           for n in (1, 2)]
    kept = whatsapp_only.record(msg("whatsapp", "KEPT", (week_and_a_day + timedelta(minutes=5)).isoformat(),
                                    "A week ago", sender="Parent rep", chat="3B parents", kid="Mia"))
    whatsapp_only.keep(*old, kept)

    assert harness.run() == 0

    assert store_ids() == ["KEPT", "3EB0A1", "3EB0A2", "3EB0A3"]


@pytest.mark.parametrize("language, line", [
    ("en", "⚠️ WhatsApp not read since Sat 26 Sep 08:30 (needs a new pairing). Tonight's Brief may be incomplete."),
    ("zh", "⚠️ WhatsApp 自 9月26日 周六 08:30 起没读到（需要重新关联设备），今天的日报可能缺这一块。"),
])
def test_a_link_whatsapp_ended_is_not_read_since_then(harness, language, line):
    harness.config["summary_language"] = language
    harness.model_reply = SAYS_SOMETHING
    one_gmail_message(harness)
    harness.sources["whatsapp"] = list(TONIGHT)
    harness.source_fakes["whatsapp"].status(paired=False, connected_since=None,
                                            last_connected="2026-09-26T05:30:00.000Z",
                                            link_ended_at="2026-09-26T05:30:00.000Z")

    assert harness.run() == 0

    assert payload_message_ids(harness) == ["1001"]
    assert coverage_line(harness).endswith("\n" + line)
    assert "whatsapp" not in harness.lookback_hours  # never read


def test_groups_picked_without_a_pairing_are_not_read_since_whatsapp_last_was(harness):
    # As a Household that read WhatsApp for Mac's database has it, the night after the update.
    caught_up_last_night(harness)
    harness.config["summary_language"] = "en"
    harness.model_reply = SAYS_SOMETHING
    one_gmail_message(harness)
    for chat in harness.config["whatsapp"]["chats"]:
        del chat["id"]

    assert harness.run() == 0

    assert coverage_line(harness).endswith(
        "\n⚠️ WhatsApp not read since Sat 26 Sep 21:00 (needs a new pairing). Tonight's Brief may be incomplete.")
    # Still behind, so the first run after the pairing reaches back to then.
    assert harness.state()["source_caught_up_at"]["whatsapp"] == (NOW - timedelta(days=1)).isoformat()


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


@pytest.mark.macos
def test_the_linked_device_folder_is_owner_only_and_not_in_the_archive(harness, whatsapp_only):
    harness.config["archive"] = {"dir": "~/Documents/Briefs"}
    folder = linked_device.folder()
    whatsapp_only.keep()  # the store follow made, opened up some other way
    for p in (folder / "auth", folder / "auth" / "creds.json"):
        p.mkdir() if p.suffix == "" else p.write_text("{}")
    for p in (folder, folder / "auth"):
        p.chmod(0o755)
    for p in (linked_device.store(), folder / "auth" / "creds.json"):
        p.chmod(0o644)

    assert harness.run() == 0

    assert folder == harness.home / "ParentRecap" / "linked-device"
    assert not folder.is_relative_to(harness.home / "Documents")
    for p in (folder, folder / "auth"):
        assert _mode(p) == 0o700, p
    for p in (linked_device.store(), linked_device.status_file(), linked_device.picked_file(),
              folder / "auth" / "creds.json"):
        assert _mode(p) == 0o600, p
    assert json.loads(linked_device.picked_file().read_text()) == {"groups": [CLASS_GROUP, PIANO_GROUP]}
    assert sorted(p.name for p in (harness.home / "Documents" / "Briefs").iterdir()) == \
        ["2026-09-27.md", "2026-09-27.raw.json"]
