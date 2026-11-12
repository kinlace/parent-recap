"""Social Schools (ADR 0047): a post's HTML as text, its times, a whole day kept on its dates, a
child as a Kid and a post or an event as a Kid's by its groups. All names are made up."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from family_brief.collectors import socialschools as ss
from family_brief.config import Kid

TZ = "Europe/Helsinki"
MIA = ss.Child("Mia", "Mia Virtanen", frozenset({"groep 5"}))
LEO = ss.Child("Leo", "Leo Virtanen", frozenset({"groep 3"}))
SCHOOL = ss.School(7, "Basisschool De Kilo", (MIA, LEO))
KIDS = {MIA: "Mia", LEO: "Leo"}


@pytest.mark.parametrize("html, text", [
    ("<p>Vrijdag naar het <b>bos</b>.</p><p>Laarzen mee.</p>", "Vrijdag naar het bos.\nLaarzen mee."),
    ("<p>Mee:</p><ul><li>laarzen</li><li>jas</li></ul>", "Mee:\n- laarzen\n- jas"),
    ("Tot<br>dan &amp; doei", "Tot\ndan & doei"),
    ("", ""), (None, ""),
], ids=["paragraphs", "list", "break", "empty", "none"])
def test_html_becomes_plain_lines(html, text):
    assert ss.plain(html) == text


@pytest.mark.parametrize("given, at", [
    ("2026-11-26T08:30:00Z", datetime(2026, 11, 26, 8, 30, tzinfo=timezone.utc)),
    ("2026-11-26T09:30:00.000+01:00", datetime(2026, 11, 26, 8, 30, tzinfo=timezone.utc)),
    ("2026-11-26T09:30:00", datetime(2026, 11, 26, 8, 30, tzinfo=timezone.utc)),  # the Netherlands'
    ("2026-11-26", None), ("soon", None), (None, None),
], ids=["utc", "offset", "no-offset", "date-only", "words", "none"])
def test_a_time_without_an_offset_is_in_the_netherlands(given, at):
    got = ss._at(given)
    assert (got.astimezone(timezone.utc) if got else None) == at


def event(**k):
    return {"id": 1, "title": "Studiedag", "communities": [{"name": "Groep 5"}], **k}


@pytest.mark.parametrize("start, end, first, last", [
    ("2026-11-26T00:00:00Z", "2026-11-27T00:00:00Z", "2026-11-26", None),
    ("2026-12-21T00:00:00Z", "2027-01-04T00:00:00Z", "2026-12-21", "2027-01-03"),
    ("2026-11-26T00:00:00", "2026-11-26T23:59:00", "2026-11-26", None),
    ("2026-11-26T00:00:00Z", None, "2026-11-26", None),
], ids=["one-day", "holiday", "inclusive-end", "no-end"])
def test_a_whole_day_stays_on_its_dates_as_written(start, end, first, last):
    [e] = ss.events_of([event(start=start, end=end, allDay=True)], SCHOOL, KIDS, TZ)

    assert e.all_day and e.start.isoformat() == f"{first}T00:00:00+02:00"
    assert (e.end.date().isoformat() if e.end else None) == last


def test_a_timed_event_is_in_the_family_s_zone_and_an_end_before_its_start_is_none():
    [e] = ss.events_of([event(start="2026-11-26T15:30:00+01:00", end="2026-11-26T15:00:00+01:00")],
                       SCHOOL, KIDS, TZ)

    assert e.start == datetime(2026, 11, 26, 16, 30, tzinfo=ZoneInfo(TZ)) and e.end is None


def test_an_event_without_its_title_or_start_is_skipped():
    assert ss.events_of([event(title="", start="2026-11-26T00:00:00Z"), event(start="morgen")],
                        SCHOOL, KIDS, TZ) == []


@pytest.mark.parametrize("groups, kid", [
    (["Groep 5"], "Mia"), (["groep  5 "], "Mia"), (["Groep 3", "Groep 5"], None),
    (["Schoolnieuws"], None), ([], None), (["Groep 5A"], None),
], ids=["own-group", "spacing-and-case", "both", "whole-school", "none", "look-alike"])
def test_a_post_is_a_kid_s_when_it_is_in_that_kid_s_group_only(groups, kid):
    assert ss._whose(groups, SCHOOL, KIDS) == kid


@pytest.mark.parametrize("child, kid", [
    (ss.Child("Mia", "Mia Virtanen", frozenset()), "Mia Virtanen"),
    (ss.Child("", "Mia Virtanen", frozenset()), "Mia Virtanen"),
    (ss.Child("Mila", "Mila de Vries", frozenset()), "Mia Virtanen"),  # her alias
    (ss.Child("Olli", "Olli Virtanen", frozenset()), None),
], ids=["first-name", "display-name-only", "alias", "no-kid"])
def test_a_child_is_the_kid_its_name_is(child, kid):
    kids = [Kid(name="Mia Virtanen", aliases=["Mila"]), Kid(name="Leo Virtanen")]

    assert ss.kid_of(child, kids) == kid


def test_the_secret_s_name_has_no_email_in_it_and_is_the_same_whatever_its_case():
    name = ss.secret_name("Parent@Example.nl ")

    assert name == ss.secret_name("parent@example.nl") and name.startswith("socialschools-signin-")
    assert "@" not in name and "example" not in name
