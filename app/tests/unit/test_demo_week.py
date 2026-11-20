"""The demo's made-up Household and its week (#1029, family_brief/demo/): each Source the Brief
covers gives something, exactly one Message is held back, and the days are counted from the
evening the demo runs."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from family_brief import demo, held_back

SUNDAY = datetime(2026, 9, 27, 21, 0, tzinfo=ZoneInfo("Europe/Helsinki"))


@pytest.mark.parametrize(("when", "expected"), [
    (0, date(2026, 9, 27)), (-1, date(2026, 9, 26)), (3, date(2026, 9, 30)),
    ("1w", date(2026, 9, 28)), ("-1w", date(2026, 9, 25)), ("6w", date(2026, 10, 5)),
    ("-3w", date(2026, 9, 23)),
])
def test_a_day_counts_days_or_weekdays_from_today(when, expected):
    assert demo.day(SUNDAY.date(), when) == expected


def test_a_day_in_a_text_is_written_as_a_finnish_or_an_english_message_writes_it():
    assert demo.written(SUNDAY.date(), "koe {+2w}, until {en:+5}") == "koe ti 29.9., until Fri 2 Oct"


def test_the_week_has_news_from_each_source_the_brief_covers():
    week = demo.week(SUNDAY)

    assert set(week.coverage()) == {"wilma", "gmail", "whatsapp", "myclub", "eepos", "evaka"}
    assert week.coverage()["evaka"] == {"count": 1, "error": None, "messages": 1}
    assert len({m.chat_name for m in week.messages if m.source == "whatsapp"}) == 2
    assert all(m.timestamp <= SUNDAY for m in week.messages)
    assert all(e.start > SUNDAY for e in week.events)
    assert not any("{" in m.body for m in week.messages)


def test_exactly_one_message_of_the_week_is_held_back_from_the_ai():
    _for_ai, held = held_back.hold_back(demo.week(SUNDAY).messages)

    assert [m.external_id for m in held] == ["7300000000000000103"]
