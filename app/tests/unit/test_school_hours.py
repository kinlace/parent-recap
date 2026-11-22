"""A Kid's changed school hours on their own (ADR 0054): each day's hours from its lessons, the
usual ones per weekday, the days that differ, whether the model already gave them, and the line
the Brief shows in each language."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from family_brief import render
from family_brief.brief import Brief, KidSection, Notice
from family_brief.brief_text import TEXT
from family_brief.collectors.base import Message
from family_brief.school_hours import SchoolHours, changed, day_hours, to_tell, told, usual

MONDAY = date(2026, 9, 28)
SHORT = SchoolHours("Mia", MONDAY, "08:15", "12:00", "08:15", "14:00")


def weeks(ends: dict[date, str], first: date = MONDAY - timedelta(days=14), n: int = 28) -> dict:
    """School days 08:15–14:00 over `n` days from `first`, each in `ends` ending then instead."""
    days = (first + timedelta(days=i) for i in range(n))
    return {d: ("08:15", ends.get(d, "14:00")) for d in days if d.weekday() < 5}


def test_a_day_s_hours_are_its_first_start_and_last_end_whatever_the_order():
    lessons = [("2026-09-28", "10:00", "11:30"), ("2026-09-28", "8:15", "09:45"),
               ("2026-09-28", "12:30", "14:00:00"), ("no date", "08:00", "09:00"), ("2026-09-29", "", "")]
    assert day_hours(lessons) == {MONDAY: ("08:15", "14:00")}


@pytest.mark.parametrize("ends, normally", [
    ({}, ("08:15", "14:00")),
    # One odd week among the other three Mondays doesn't change what is usual.
    ({MONDAY - timedelta(days=7): "12:00"}, ("08:15", "14:00")),
    # Each other Monday ends at another time: nothing is usual.
    ({MONDAY - timedelta(days=7): "12:00", MONDAY + timedelta(days=7): "13:00"}, None),
])
def test_the_usual_hours_are_those_most_other_days_of_the_weekday_share(ends, normally):
    assert usual(weeks(ends), MONDAY) == normally


def test_one_other_day_of_the_weekday_makes_no_usual_hours():
    """When Wilma gives only the coming two weeks, a weekday has one other day: too few to say."""
    assert usual(weeks({}, first=date(2026, 9, 27), n=14), MONDAY) is None


def test_only_a_day_whose_hours_differ_from_the_usual_ones_is_changed():
    assert changed("Mia", weeks({MONDAY: "12:00"}), MONDAY) == SHORT
    assert changed("Mia", weeks({}), MONDAY) is None
    assert changed("Mia", weeks({}), MONDAY + timedelta(days=5)) is None  # a Saturday: no lessons


@pytest.mark.parametrize("digest, notices, said", [
    ("**Mia**\n- Mon 28 Sep: 8:15–12:00 (normally 8:15–14:00)", [], True),
    # A message said 11:00 and the model gave the usual end beside it, as Finnish writes it.
    ("**Mia**\n- ma 28.9.: koulu päättyy klo 11.00 (yleensä klo 14.00)", [], True),
    ("- Tomorrow Mia has PE in the gym", [], False),
    ("", ["The day ends at 11:00, normally at 14:00"], True),
    # Leo's section names 14:00, Mia's doesn't.
    ("**Leo**\n- Ends 14:00 as usual\n\n**Mia**\n- PE in the gym", [], False),
    ("- Training at 14:005", [], False),
])
def test_the_model_gave_the_hours_when_its_text_about_the_kid_names_the_usual_time(digest, notices, said):
    brief = Brief(digest=digest, kids=[KidSection("Mia", notices=[Notice(n) for n in notices])])
    assert told(brief, SHORT) is said


def test_the_hours_to_tell_come_from_the_timetable_message_under_the_name_the_brief_uses():
    timetable = Message("wilma", "schedule:20260927", None, body="",
                        metadata={"school_hours": [SHORT.to_dict() | {"kid": "Virtanen Mia"}, {"date": "?"}]})
    other = Message("gmail", "1", None, body="")
    called = {"Virtanen Mia": "Mia"}.get

    assert to_tell([other, timetable], Brief(), called) == [SHORT]
    assert to_tell([timetable], Brief(digest="Mia: until 14:00 normally"), called) == []


@pytest.mark.parametrize("language, line", [
    ("en", "🕒 Mia, Mon 28 Sep: 8:15–12:00 (normally 8:15–14:00)"),
    ("zh", "🕒 Mia 9月28日 周一：8:15–12:00（平时 8:15–14:00）"),
    ("fi", "🕒 Mia ma 28.9.: 8.15–12.00 (yleensä 8.15–14.00)"),
])
def test_the_brief_says_the_changed_hours_in_its_own_words(language, line):
    assert render._school_lines(Brief(school_hours=[SHORT]), TEXT[language]) == [line]
