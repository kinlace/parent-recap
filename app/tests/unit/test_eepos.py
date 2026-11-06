"""The Eepos Source's pure parts (ADR 0038): a school's address, a student matched to a Kid by
name, and the portal's calendar entries as events."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from conftest import eepos_entry
from family_brief.collectors import eepos
from family_brief.config import Config, EeposSignin

TZ = ZoneInfo("Europe/Helsinki")


@pytest.mark.parametrize("given, school", [
    ("emo", "emo"), ("EMO", "emo"), ("emo.eepos.fi", "emo"), ("https://emo.eepos.fi/#/portal/calendar", "emo"),
    (" espoo.eepos.fi/ ", "espoo"), ("", None), ("emo.example.fi", None), ("e mo", None), ("-emo", None),
])
def test_a_school_is_the_part_of_its_address_before_eepos_fi(given, school):
    assert eepos.school_of(given) == school


@pytest.mark.parametrize("given, website", [
    ("www.espoonkuvis.fi", True), ("https://www.espoonkuvis.fi/", True), ("emo.example.fi", True),
    ("espoo", False), ("xyz", False), ("e mo", False), ("espoo.eepos.fi", False),
    ("https://espoo.eepos.fi/#/splash", False), ("-emo.eepos.fi", False), ("", False),
])
def test_an_address_elsewhere_than_eepos_is_a_website(given, website):
    assert eepos.is_website(given) is website


def test_a_sign_in_s_secret_is_named_after_its_school_and_username_never_with_them():
    name = eepos.secret_name("emo", "Parent@Example.fi")

    assert name.startswith("eepos-signin-") and "example" not in name.lower()
    assert name == eepos.secret_name("emo", " parent@example.fi")  # as Eepos takes it
    assert name != eepos.secret_name("espoo", "parent@example.fi")


KIDS = Config(kids=[{"name": "Harry Zhao", "everyday_name": "Harry"}, {"name": "Mia", "aliases": ["Mimmi"]},
                    {"name": "Leo Virtanen"}]).kids


@pytest.mark.parametrize("first, last, nickname, kid", [
    ("Harry", "Zhao", None, "Harry Zhao"),        # the full name, as the config has it
    ("Mia", "Virtanen", None, "Mia"),             # the first name is the Kid's name
    ("Emilia", "Virtanen", "Mimmi", "Mia"),       # the nickname is an alias
    ("Leo", "Korhonen", None, "Leo Virtanen"),    # the first name is the first word of the Kid's
    ("Olli", "Zhao", None, None),                 # no Kid
], ids=["full-name", "first-name", "nickname", "first-word", "none"])
def test_a_student_is_the_kid_their_name_is(first, last, nickname, kid):
    assert eepos.kid_of(eepos.Student("1", first, last, nickname or ""), KIDS) == kid


def test_a_student_two_kids_could_be_is_neither():
    kids = Config(kids=[{"name": "Leo A"}, {"name": "Leo B"}]).kids

    assert eepos.kid_of(eepos.Student("1", "Leo", "C", ""), kids) is None


def test_the_kid_setup_matched_a_student_to_wins_over_the_name():
    signin = EeposSignin(school="emo", username="p", kids={1: "Mia", 2: "Gone"})
    students = [eepos.Student("1", "Harry", "Zhao", ""), eepos.Student("2", "Leo", "X", "")]

    # A Kid no longer in the config is matched by name again.
    assert eepos.kids_of(signin, students, KIDS) == {"1": "Mia", "2": "Leo Virtanen"}


KIDS_BY_ID = {"11": "Harry Zhao", "12": "Mia"}


def events(*entries: dict) -> list:
    return eepos.events(list(entries), "emo", KIDS_BY_ID, "Europe/Helsinki")


def test_a_lesson_is_its_kid_s_event_in_the_household_s_time():
    [e] = events(eepos_entry("Zhao Harry, Piano", "2026-09-28", "15:15:00", "16:00:00", students=(11,)))

    assert (e.source, e.title, e.kid, e.all_day) == ("eepos", "Zhao Harry, Piano", "Harry Zhao", False)
    assert (e.start, e.end) == (datetime(2026, 9, 28, 15, 15, tzinfo=TZ), datetime(2026, 9, 28, 16, 0, tzinfo=TZ))
    assert e.location == "Ahertajantie, luokka 12"
    assert e.external_id.startswith("eepos:")


def test_a_holiday_that_comes_twice_is_one_all_day_event_for_the_household():
    got = events(eepos_entry("Pyhäinpäivä", "2026-10-31", "00:00:00", "23:59:59", kind="vacation", students=(11,)),
                 eepos_entry("Pyhäinpäivä", "2026-10-31", "00:00:00", "23:59:59", kind="vacation", students=(12,)))

    [e] = got
    assert (e.all_day, e.kid, e.start, e.end) == (True, None, datetime(2026, 10, 31, tzinfo=TZ), None)


def test_an_entry_of_none_of_the_kids_or_of_two_is_the_household_s():
    concert, group = events(eepos_entry("Pianomaraton 19", "2026-10-03", kind="event"),
                            eepos_entry("Musiikin hahmottaminen 2", "2026-10-01", students=(11, 12)))

    assert concert.kid is None and group.kid is None


def test_a_cancelled_lesson_is_no_event():
    assert events(eepos_entry("Zhao Harry, Piano", "2026-09-28", students=(11,), canceled=True)) == []


def test_the_same_entry_gets_the_same_id_each_night():
    entry = eepos_entry("Zhao Harry, Piano", "2026-09-28", students=(11,))

    assert events(entry)[0].external_id == events(dict(entry))[0].external_id
    assert events(entry)[0].external_id != events({**entry, "startTime": "15:30:00"})[0].external_id


@pytest.mark.parametrize("change", [{"date": None}, {"title": ""}, {"date": "28.9.2026"}, {"startTime": "kello 3"}],
                         ids=["no-day", "no-title", "day-written-otherwise", "time-written-otherwise"])
def test_an_entry_the_portal_writes_otherwise_says_eepos_has_changed(change):
    with pytest.raises(eepos.Changed):
        events({**eepos_entry("Zhao Harry, Piano", "2026-09-28", students=(11,)), **change})
