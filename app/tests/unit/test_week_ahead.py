"""The Week Ahead on its own (ADR 0053): which evenings have one, which items it takes from
tonight's Sources, tonight's Brief and the earlier Briefs, each thing once, in day order, and the
days it shows."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from family_brief.brief import ActionItem, Brief, Event, KidSection
from family_brief.collectors.base import CalendarEvent
from family_brief.summarize import extract_calendar_events
from family_brief.week_ahead import PER_DAY, EarlierNight, WeekItem, by_day, items, week_after

TZ = "Europe/Helsinki"
SUNDAY = date(2026, 9, 27)
WEEK = week_after(SUNDAY)
REMINDER = "（再提醒）"


def at(day: str, time: str | None = None) -> datetime:
    return datetime.fromisoformat(f"{day}T{time or '00:00'}").replace(tzinfo=ZoneInfo(TZ))


def exam(start: str, title: str = "Koe: Historia – Luvut 3–5", kid: str = "Mia") -> CalendarEvent:
    """A Wilma exam as tonight's Wilma gives it: all day, known by its exam id (ADR 0040)."""
    return CalendarEvent("wilma", "exam:1", title, at(start), kid=kid, all_day=True,
                         description="Luvut 3–5, sivut 40–62", same_when_moved=True)


def brief(actions: list[tuple[str, str, str]] = (), events: list[Event] = ()) -> Brief:
    """A Brief whose Kids' Action Items are (kid, what, by)."""
    kids: dict[str, KidSection] = {}
    for kid, what, by in actions:
        kids.setdefault(kid, KidSection(kid)).action_items.append(ActionItem(what, by))
    return Brief(kids=list(kids.values()), events=list(events))


def week(source=(), tonight: Brief | None = None, earlier: list[EarlierNight] = (), days=WEEK) -> list[WeekItem]:
    tonight = tonight or Brief()
    model = lambda b: extract_calendar_events(b, TZ, "(untitled)")  # noqa: E731
    return items(days, source, tonight, model(tonight), list(earlier),
                 kid_called=lambda k: {"mia": "Mia"}.get((k or "").casefold(), k),
                 household=["家庭", "Household"], reminders=[REMINDER, "(Reminder) "], model_events=model)


def lines(got: list[WeekItem]) -> list[tuple]:
    return [(i.day.isoformat(), i.kid, i.text, i.time, i.due) for i in got]


@pytest.mark.parametrize("evening, days", [
    (SUNDAY, ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"]),
    (date(2026, 9, 26), []),  # Saturday
    (date(2026, 9, 28), []),  # Monday
])
def test_only_a_sunday_evening_has_a_week_ahead_and_it_is_monday_to_friday(evening, days):
    assert [d.isoformat() for d in week_after(evening)] == days


def test_no_days_no_items():
    assert week([exam("2026-09-29")], days=[]) == []


def test_items_come_from_tonights_sources_tonights_brief_and_earlier_briefs_in_day_order():
    tonight = brief([("Mia", "签 reissuvihko", "2026-09-28")],
                    [Event(kid="Leo", title="钢琴课（改期）", start="2026-09-30T17:30:00", description="本周改到周三")])
    earlier = [EarlierNight("2026-09-20", brief([("Leo", "交郊游费", "2026-10-02")],
                                                [Event(kid="家庭", title="学校关门", start="2026-09-29")]))]

    got = week([exam("2026-09-29")], tonight, earlier)

    assert lines(got) == [
        ("2026-09-28", "Mia", "签 reissuvihko", None, True),
        ("2026-09-29", "Mia", "Koe: Historia – Luvut 3–5", None, False),
        ("2026-09-29", None, "学校关门", None, False),  # the Household's: no Kid
        ("2026-09-30", "Leo", "钢琴课（改期）", "17:30", False),
        ("2026-10-02", "Leo", "交郊游费", None, True),
    ]
    assert [i.from_source for i in got] == [False, True, False, False, False]
    assert got[1].detail == "Luvut 3–5, sivut 40–62"


def test_nothing_outside_monday_to_friday():
    tonight = brief([("Mia", "Saturday thing", "2026-10-03"), ("Mia", "Past thing", "2026-09-27")])
    assert week([exam("2026-10-05")], tonight) == []


def test_an_archived_event_and_tonights_source_event_for_the_same_thing_show_once_as_the_sources():
    earlier = [EarlierNight("2026-09-15", brief(events=[Event(kid="mia", title="Historian koe", start="2026-09-29")]))]

    got = week([exam("2026-09-29")], earlier=earlier)

    assert lines(got) == [("2026-09-29", "Mia", "Koe: Historia – Luvut 3–5", None, False)]
    assert got[0].from_source


def test_a_moved_exam_shows_on_its_new_day_only():
    # An earlier Brief's model event put it on Tuesday; Wilma has it on Thursday tonight.
    earlier = [EarlierNight("2026-09-15", brief(events=[Event(kid="Mia", title="Historian koe", start="2026-09-29")]))]

    got = week([exam("2026-10-01")], earlier=earlier)

    # The model's Tuesday stays: nothing says it moved but the Source, whose event is another day's.
    assert lines(got) == [("2026-09-29", "Mia", "Historian koe", None, False),
                          ("2026-10-01", "Mia", "Koe: Historia – Luvut 3–5", None, False)]


def test_the_same_action_item_from_several_evenings_and_its_re_reminder_show_once():
    first = EarlierNight("2026-09-20", brief([("Mia", "签 reissuvihko", "2026-09-28")]))
    again = EarlierNight("2026-09-25", brief([("Mia", f"{REMINDER}签 reissuvihko", "2026-09-28")]))
    tonight = brief([("Mia", f"{REMINDER}签 reissuvihko", "2026-09-28")])

    assert lines(week(tonight=tonight, earlier=[first, again])) == [
        ("2026-09-28", "Mia", "签 reissuvihko", None, True)]


def test_the_same_event_written_twice_by_the_model_shows_once_with_the_latest_words():
    old = EarlierNight("2026-09-20", brief(events=[Event(kid="Mia", title="3B retki Nuuksioon", start="2026-10-01T09:00:00")]))
    new = EarlierNight("2026-09-24", brief(events=[Event(kid="Mia", title="Retki Nuuksioon (3B)", start="2026-10-01T09:00:00")]))

    assert lines(week(earlier=[old, new])) == [("2026-10-01", "Mia", "Retki Nuuksioon (3B)", "09:00", False)]


@pytest.mark.parametrize("other", [
    Event(kid="Mia", title="Koe: Matematiikka", start="2026-09-29"),       # another subject
    Event(kid="Leo", title="Historian koe", start="2026-09-29"),           # another Kid
    Event(kid="Mia", title="Historian koe", start="2026-09-30"),           # another day
])
def test_different_things_are_all_listed(other):
    got = week([exam("2026-09-29")], earlier=[EarlierNight("2026-09-20", brief(events=[other]))])
    assert len(got) == 2


def test_two_different_deadlines_on_one_day_are_both_listed():
    tonight = brief([("Mia", "Pay the trip", "2026-09-29"), ("Mia", "Sign the slip", "2026-09-29")])
    assert len(week(tonight=tonight)) == 2


def test_a_day_shows_its_first_items_and_counts_the_rest():
    many = [WeekItem(date(2026, 9, 29), "Mia", f"Item {n}", due=True) for n in range(PER_DAY + 2)]
    [(day, shown, more)] = by_day(many)
    assert (day, len(shown), more) == (date(2026, 9, 29), PER_DAY, 2)
