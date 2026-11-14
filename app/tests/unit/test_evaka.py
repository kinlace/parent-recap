"""The eVaka Source's pure parts (ADR 0039): a city and its host, a child matched to a Kid by name,
and eVaka's calendar and reservations as events: a unit's events, discussion times, closed days
and the reservation deadline."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from conftest import evaka_event
from family_brief.config import Config
from family_brief.sources.fi.evaka import collector as evaka
from family_brief.sources.fi.evaka.config import EvakaSignin

TZ = ZoneInfo("Europe/Helsinki")
MIA, LEO = "1a2b3c4d-0000-4000-8000-000000000011", "1a2b3c4d-0000-4000-8000-000000000012"


@pytest.mark.parametrize("given, city", [
    ("espoo", "espoo"), (" Espoo ", "espoo"), ("https://espoonvarhaiskasvatus.fi/calendar", "espoo"),
    ("evaka.nokiankaupunki.fi", "nokia"), ("varhaiskasvatus.tampere.fi/", "tampere"),
    ("evaka.kouvola.fi", "evaka.kouvola.fi"), ("", None), ("helsinki", None), ("e vaka.fi", None),
])
def test_a_city_is_its_name_or_the_host_of_a_city_not_in_the_table(given, city):
    assert evaka.city_of(given) == city


def test_each_city_s_host_is_its_own():
    assert evaka.host_of("espoo") == "espoonvarhaiskasvatus.fi"
    assert evaka.host_of("evaka.kouvola.fi") == "evaka.kouvola.fi"
    assert len({host for host, _ in evaka.CITIES.values()}) == len(evaka.CITIES) == 12


def test_a_sign_in_s_secret_is_named_after_its_city_and_email_never_with_them():
    name = evaka.secret_name("espoo", "Parent@Example.fi")

    assert name.startswith("evaka-signin-") and "example" not in name.lower()
    assert name == evaka.secret_name("espoo", " parent@example.fi")  # as eVaka takes it
    assert name != evaka.secret_name("tampere", "parent@example.fi")


KIDS = Config(kids=[{"name": "Harry Zhao", "everyday_name": "Harry"}, {"name": "Mia", "aliases": ["Mimmi"]},
                    {"name": "Leo Virtanen"}]).kids


@pytest.mark.parametrize("first, last, preferred, kid", [
    ("Harry Wei", "Zhao", "", "Harry Zhao"),      # the first given name with the last name
    ("Mia Emilia", "Virtanen", "", "Mia"),        # the first given name is the Kid's name
    ("Emilia", "Virtanen", "Mimmi", "Mia"),       # the preferred name is an alias
    ("Leo", "Korhonen", "", "Leo Virtanen"),      # the first name is the first word of the Kid's
    ("Olli", "Zhao", "", None),                   # no Kid
], ids=["full-name", "first-name", "preferred-name", "first-word", "none"])
def test_a_child_is_the_kid_their_name_is(first, last, preferred, kid):
    assert evaka.kid_of(evaka.Child("1", first, last, preferred), KIDS) == kid


def test_a_child_two_kids_could_be_is_neither():
    kids = Config(kids=[{"name": "Leo A"}, {"name": "Leo B"}]).kids

    assert evaka.kid_of(evaka.Child("1", "Leo", "C", ""), kids) is None


def test_the_kid_setup_matched_a_child_to_wins_over_the_name():
    signin = EvakaSignin(city="espoo", username="p@example.fi", kids={"1": "Mia", "2": "Gone"})
    children = [evaka.Child("1", "Harry", "Zhao", ""), evaka.Child("2", "Leo", "X", "")]

    # A Kid no longer in the config is matched by name again.
    assert evaka.kids_of(signin, children, KIDS) == {"1": "Mia", "2": "Leo Virtanen"}


KIDS_BY_ID = {MIA: "Mia", LEO: "Leo Virtanen"}


def events(*entries: dict) -> list:
    return evaka.events([{k: v for k, v in e.items() if k != "city"} for e in entries], "espoo",
                        KIDS_BY_ID, "Europe/Helsinki")


def test_a_unit_s_event_is_all_day_for_its_kid_for_the_days_it_lasts():
    [trip] = events(evaka_event("Retki Nuuksioon", "2026-10-01", children=(MIA,), description="Eväät mukaan"))
    [camp] = events(evaka_event("Metsäviikko", "2026-10-05", "2026-10-07", children=(LEO,)))

    assert (trip.source, trip.title, trip.kid, trip.all_day) == ("evaka", "Retki Nuuksioon", "Mia", True)
    assert (trip.start, trip.end, trip.description) == (datetime(2026, 10, 1, tzinfo=TZ), None, "Eväät mukaan")
    assert (camp.start, camp.end, camp.kid) == (datetime(2026, 10, 5, tzinfo=TZ), datetime(2026, 10, 7, tzinfo=TZ),
                                                "Leo Virtanen")
    assert trip.external_id.startswith("evaka:")


def test_an_event_for_both_children_or_for_none_of_the_kids_is_the_household_s():
    both, other = events(evaka_event("Kuvauspäivä", "2026-10-02", children=(MIA, LEO)),
                         evaka_event("Vanhempainilta", "2026-10-06", children=("someone-else",)))

    assert both.kid is None and other.kid is None


def test_a_booked_discussion_time_is_its_kid_s_and_a_free_one_isn_t_an_event():
    [talk] = events(evaka_event("Varhaiskasvatuskeskustelu", "2026-10-01", "2026-10-09",
                                booked={MIA: ("2026-10-08", "14:00:00", "14:45:00")}))

    assert (talk.title, talk.kid, talk.all_day) == ("Varhaiskasvatuskeskustelu", "Mia", False)
    assert (talk.start, talk.end) == (datetime(2026, 10, 8, 14, 0, tzinfo=TZ), datetime(2026, 10, 8, 14, 45, tzinfo=TZ))


def test_the_same_event_gets_the_same_id_each_night():
    event = evaka_event("Retki Nuuksioon", "2026-10-01", children=(MIA,))

    assert events(event)[0].external_id == events(dict(event))[0].external_id
    assert events(event)[0].external_id != events({**event, "period": {"start": "2026-10-02", "end": "2026-10-02"}}
                                                  )[0].external_id


@pytest.mark.parametrize("change", [{"title": ""}, {"period": None}, {"period": {"start": "1.10.2026", "end": "x"}}],
                         ids=["no-title", "no-days", "days-written-otherwise"])
def test_an_event_evaka_writes_otherwise_says_evaka_has_changed(change):
    with pytest.raises(evaka.Changed):
        events({**evaka_event("Retki", "2026-10-01", children=(MIA,)), **change})


def child_day(child: str, *, reservable: bool = True, schedule: str = "RESERVATION_REQUIRED",
              holiday_period: dict | None = None) -> dict:
    return {"childId": child, "scheduleType": schedule, "holidayPeriodEffect": holiday_period,
            "reservableTimeRange": {"type": "NORMAL", "range": {"start": "06:30", "end": "17:30"}} if reservable
            else {"type": "INTERMITTENT_SHIFT_CARE", "placementUnitOperationTime": None}}


def reservations(*days: dict, start: str = "2026-10-05") -> dict:
    return {"children": [{"id": MIA}, {"id": LEO}], "days": list(days),
            "reservableRange": {"start": start, "end": "2027-08-31"}}


def test_a_holiday_none_of_the_children_has_time_on_is_closed_for_the_household():
    got = evaka.closures(reservations({"date": "2026-12-06", "holiday": True, "children": []},
                                      {"date": "2026-12-07", "holiday": False, "children": [child_day(MIA)]}),
                         "espoo", KIDS_BY_ID, "Europe/Helsinki")

    [closed] = got
    assert (closed.title, closed.kid, closed.all_day, closed.start) == \
        (evaka.CLOSED, None, True, datetime(2026, 12, 6, tzinfo=TZ))


def test_a_holiday_one_child_s_shift_care_is_open_on_is_closed_for_the_other():
    got = evaka.closures(reservations({"date": "2026-12-06", "holiday": True,
                                       "children": [child_day(MIA), child_day(LEO, reservable=False)]}),
                         "espoo", KIDS_BY_ID, "Europe/Helsinki")

    assert [(e.title, e.kid) for e in got] == [(evaka.CLOSED, "Leo Virtanen")]


def test_the_deadline_is_the_city_s_hours_before_the_first_week_still_open():
    days = reservations({"date": "2026-09-28", "holiday": False, "children": [child_day(MIA), child_day(LEO)]})

    due = evaka.deadline(days, "espoo", KIDS_BY_ID, "Europe/Helsinki")

    # Espoo closes them 150 hours before the Monday: on Monday 28 September at 18:00.
    assert (due.title, due.kid, due.all_day) == ("Varaukset viikolle 41 sulkeutuvat", None, False)
    assert due.start == datetime(2026, 9, 28, 18, 0, tzinfo=TZ)
    assert evaka.deadline(days, "tampere", KIDS_BY_ID, "Europe/Helsinki").start == datetime(2026, 9, 29, tzinfo=TZ)


def test_no_deadline_for_a_city_whose_hours_aren_t_known_or_children_who_don_t_reserve():
    days = reservations({"date": "2026-09-28", "holiday": False, "children": [child_day(MIA)]})
    fixed = reservations({"date": "2026-09-28", "holiday": False,
                          "children": [child_day(MIA, schedule="FIXED_SCHEDULE")]})

    assert evaka.deadline(days, "evaka.kouvola.fi", KIDS_BY_ID, "Europe/Helsinki") is None
    assert evaka.deadline(fixed, "espoo", KIDS_BY_ID, "Europe/Helsinki") is None
    assert evaka.deadline(days, "espoo", KIDS_BY_ID, "Europe/Helsinki").kid == "Mia"


def test_no_deadline_for_a_week_a_holiday_period_has_its_own_reservations_for():
    # As a local eVaka answers on Thursday 17 December with a Christmas holiday period from the
    # 21st, its reservations closed on 1 December: week 53 is in it, so it has no weekly deadline.
    closed = {"type": "ReservationsClosed"}
    days = reservations({"date": "2026-12-18", "holiday": False, "children": [child_day(MIA), child_day(LEO)]},
                        *({"date": f"2026-12-{d}", "holiday": False,
                           "children": [child_day(MIA, holiday_period=closed), child_day(LEO, holiday_period=closed)]}
                          for d in (28, 29, 30, 31)), start="2026-12-28")
    # One that starts on the Wednesday leaves Monday and Tuesday to the deadline, for the child
    # whose days they are.
    half = reservations({"date": "2026-12-28", "holiday": False, "children": [child_day(LEO)]},
                        {"date": "2026-12-30", "holiday": False,
                         "children": [child_day(MIA, holiday_period=closed), child_day(LEO, holiday_period=closed)]},
                        start="2026-12-28")

    assert evaka.deadline(days, "espoo", KIDS_BY_ID, "Europe/Helsinki") is None
    due = evaka.deadline(half, "espoo", KIDS_BY_ID, "Europe/Helsinki")
    assert (due.title, due.kid, due.start) == ("Varaukset viikolle 53 sulkeutuvat", "Leo Virtanen",
                                               datetime(2026, 12, 21, 18, 0, tzinfo=TZ))
