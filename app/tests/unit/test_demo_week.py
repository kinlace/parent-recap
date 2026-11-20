"""The demo's made-up Household and its week (family_brief/demo/, #1029), read for an evening: the
Messages and events its Sources give, dated from that evening, and what the AI filter holds back."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from family_brief import demo, held_back

TZ = ZoneInfo("Europe/Helsinki")
SUNDAY_EVENING = datetime(2026, 9, 27, 21, 0, tzinfo=TZ)
WEDNESDAY_MORNING = datetime(2026, 11, 18, 7, 30, tzinfo=TZ)


@pytest.mark.parametrize("now", [SUNDAY_EVENING, WEDNESDAY_MORNING])
def test_every_message_came_before_the_evening_and_every_event_is_on_a_school_day_after_it(now):
    week = demo.week(now)

    assert all(m.timestamp < now for m in week.messages)
    assert all(e.start.date() > now.date() and e.start.weekday() < 5 for e in week.events)
    assert len({m.timestamp for m in week.messages}) == len(week.messages)


def test_dates_in_the_messages_are_the_school_days_they_name():
    week = demo.week(SUNDAY_EVENING)  # day 1 is Monday 28.9., day 4 Thursday 1.10.

    trip = next(m for m in week.messages if m.external_id == "message:4101")
    assert "torstaina 1.10. metsäretkelle" in trip.body and "viimeistään 29.9." in trip.body
    exam = next(m for m in week.messages if m.external_id == "exam:demo-yo-1")
    assert exam.body.startswith("[2026-09-30 Wed · in 3 days]")
    assert exam.metadata["exam_date"] == "2026-09-30"
    assert not any(re.search(r"\{\w+[+-]\d+\}", m.body + (m.subject or "")) for m in week.messages)


def test_the_week_has_each_source_the_brief_covers():
    week = demo.week(SUNDAY_EVENING)

    assert {m.source for m in week.messages} == {"wilma", "gmail", "whatsapp", "evaka"}
    assert {e.source for e in week.events} == {"wilma", "myclub", "eepos", "evaka"}
    assert {m.chat_name for m in week.messages if m.source == "whatsapp"} == {"2B vanhemmat", "Myyrien vanhemmat"}
    # As the WhatsApp Source gives them: the group's Kid, and whether it is summed up.
    whatsapp = [m for m in week.messages if m.source == "whatsapp"]
    assert {(m.chat_name, m.kid_hint, bool(m.metadata.get("digest"))) for m in whatsapp} == {
        ("2B vanhemmat", "Ella", True), ("Myyrien vanhemmat", "Onni", False)}
    assert demo.coverage(week) == {
        "wilma": {"count": 4, "error": None}, "gmail": {"count": 2, "error": None},
        "whatsapp": {"count": 6, "error": None}, "evaka": {"count": 1, "error": None, "messages": 1},
        "myclub": {"count": 1, "error": None}, "eepos": {"count": 1, "error": None}}


def test_one_message_of_the_week_is_held_back_from_the_ai():
    week = demo.week(SUNDAY_EVENING)

    _for_ai, held = held_back.hold_back(week.messages)

    assert [m.external_id for m in held] == ["9102"]


def test_the_household_is_made_up():
    folder = Path(demo.__file__).parent
    text = "".join(p.read_text(encoding="utf-8") for p in sorted(folder.glob("*.yaml")))

    # Not the maintainers' children, and every address on a domain nobody can have.
    assert not re.search(r"\b(Harry|Lucas)\b", text)
    domains = set(re.findall(r"[\w.-]+@([\w.-]+)", text)) | set(re.findall(r"https://([\w.-]+)", text))
    assert all(d.endswith(".example") for d in domains), domains
