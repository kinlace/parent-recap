"""The Eepos Source's collector (ADR 0038) against each school's fake portal (fake_sources.Eepos),
which answers as the portal's JSON API does, behind its `)]}',` line: the sign-in it sends, the
calendar it asks for, the events it makes of it for the right Kid, and how a wrong password, a
portal that changed and a second school show. All names and entries are made up."""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import requests
import time_machine

from conftest import NOW, eepos_entry, eepos_student
from fake_sources import EEPOS_PASSWORD
from family_brief.collectors import eepos
from family_brief.collectors.base import failed_sign_in
from family_brief.config import Config
from family_brief.state import State

TZ = ZoneInfo("Europe/Helsinki")


@pytest.fixture(autouse=True)
def at_now():
    with time_machine.travel(NOW, tick=False):
        yield


@pytest.fixture
def portal(sources):
    """The fake portals, with Mia and Leo at emo.eepos.fi for the guardian parent@example.fi."""
    fake = sources.fakes["eepos"]
    fake.school("emo").students = [eepos_student(11, "Mia", "Virtanen"), eepos_student(12, "Leo", "Virtanen")]
    sources.config["eepos"] = {"signins": [{"school": "emo", "username": "parent@example.fi"}]}
    return fake


def collect(sources) -> list:
    sources.set_up()
    cfg = Config.model_validate(sources.config)
    return eepos.collect(cfg, State(sources.home / "state.json"), []).events


def test_it_signs_in_as_the_portal_does_and_asks_for_the_next_two_weeks(sources, portal):
    collect(sources)

    login, students, calendar = portal.requests
    assert (login["method"], login["school"], login["path"]) == ("POST", "emo", "auth/login")
    assert login["body"] == {"username": "parent@example.fi", "password": EEPOS_PASSWORD}
    assert login["headers"]["X-Requested-With"] == "XMLHttpRequest"
    assert (students["path"], calendar["path"]) == ("guardian/students", "calendar-events/all-by-date-range")
    assert calendar["query"] == {"startDate": "2026-09-27", "endDate": "2026-10-11"}


def test_lessons_events_and_holidays_become_events_of_the_right_kid(sources, portal):
    sources.tonight["eepos"] = [
        eepos_entry("Virtanen Mia, Piano", "2026-09-28", "15:15:00", "16:00:00", students=(11,)),
        eepos_entry("Virtanen Leo, Viulu", "2026-09-29", "17:00:00", "17:45:00", students=(12,)),
        eepos_entry("Pianomaraton 19", "2026-10-03", "10:00:00", "14:00:00", kind="event", room="Sali"),
        eepos_entry("Pyhäinpäivä", "2026-10-10", "00:00:00", "23:59:59", kind="vacation", students=(11,)),
        eepos_entry("Pyhäinpäivä", "2026-10-10", "00:00:00", "23:59:59", kind="vacation", students=(12,)),
        eepos_entry("Virtanen Mia, Piano", "2026-10-05", students=(11,), canceled=True),
        eepos_entry("Virtanen Mia, Piano", "2026-09-27", "10:00:00", "10:45:00", students=(11,)),  # gone by
    ]

    got = collect(sources)

    assert [(e.title, e.kid, e.start, e.all_day) for e in got] == [
        ("Virtanen Mia, Piano", "Mia", datetime(2026, 9, 28, 15, 15, tzinfo=TZ), False),
        ("Virtanen Leo, Viulu", "Leo", datetime(2026, 9, 29, 17, 0, tzinfo=TZ), False),
        ("Pianomaraton 19", None, datetime(2026, 10, 3, 10, 0, tzinfo=TZ), False),
        ("Pyhäinpäivä", None, datetime(2026, 10, 10, tzinfo=TZ), True),
    ]
    assert got[2].location == "Sali"


def test_a_wrong_password_says_the_login_failed_and_never_says_the_password(sources, portal):
    sources.secrets[eepos.secret_name("emo", "parent@example.fi")] = "not-it"

    with pytest.raises(eepos.WrongPassword) as e:
        collect(sources)

    assert str(e.value) == "login failed at emo.eepos.fi"
    assert [r["path"] for r in portal.requests] == ["auth/login"]


def test_a_sign_in_without_its_password_in_the_store_is_a_failed_login(sources, portal):
    with pytest.raises(eepos.WrongPassword, match="no password"):
        collect_without_set_up(sources)


def collect_without_set_up(sources) -> list:
    cfg = Config.model_validate(sources.config)
    return eepos.collect(cfg, State(sources.home / "state.json"), []).events


@pytest.mark.parametrize("path, answer, says", [
    ("calendar-events/all-by-date-range", (200, "<!doctype html><html>Eepos</html>"), "calendar wasn't JSON"),
    ("calendar-events/all-by-date-range", (200, ")]}',\n{\"events\": []}"), "calendar isn't a list"),
    ("calendar-events/all-by-date-range", (404, "<html>Not found</html>"), "has no calendar where it was"),
    ("guardian/students", (200, ")]}',\n[{\"id\": 11}]"), "list of students has no names"),
    ("auth/login", (200, ")]}',\n{\"token\": \"abc\"}"), "sign-in answered in a way"),
    ("auth/login", (200, ")]}',\n{\"success\": false}"), "sign-in answered in a way"),
    ("auth/login", (404, "<html>Not found</html>"), "has no sign-in where it was"),
], ids=["html", "not-a-list", "moved", "no-names", "sign-in", "sign-in-not-a-success", "sign-in-moved"])
def test_a_portal_that_changed_says_so(sources, portal, path, answer, says):
    portal.answers[("emo", path)] = answer

    with pytest.raises(eepos.Changed, match=says):
        collect(sources)


@pytest.mark.parametrize("answer", [(400, '{"error": "validation"}'), (401, '{"error": "loginFailed"}'),
                                    (422, "<html>Eepos</html>")], ids=["other-error", "401", "html"])
def test_only_the_portal_s_own_login_failed_is_a_wrong_password(sources, portal, answer):
    # A changed login API mustn't tell the family their password is wrong.
    portal.answers[("emo", "auth/login")] = answer

    with pytest.raises(eepos.EeposError) as e:
        collect(sources)

    assert not isinstance(e.value, eepos.WrongPassword)


def test_a_portal_that_doesn_t_answer_says_which_school(sources, portal):
    portal.answers[("emo", "auth/login")] = requests.ConnectionError("Name or service not known")

    with pytest.raises(eepos.EeposError, match=r"^emo.eepos.fi didn't answer \(ConnectionError\)$"):
        collect(sources)


def test_a_session_the_portal_signed_out_is_no_wrong_password(sources, portal):
    portal.answers[("emo", "guardian/students")] = (401, '{"error": "noauth"}')

    with pytest.raises(eepos.EeposError) as e:
        collect(sources)

    assert not isinstance(e.value, eepos.WrongPassword)


def two_schools(sources, portal) -> None:
    portal.school("espoo").students = [eepos_student(31, "Leo", "Virtanen")]
    sources.config["eepos"]["signins"].append({"school": "espoo.eepos.fi", "username": "parent2"})
    sources.tonight["eepos"] = [
        eepos_entry("Virtanen Mia, Piano", "2026-09-28", students=(11,)),
        eepos_entry("Kuvataide 1", "2026-09-30", "16:00:00", "17:30:00", students=(31,), school="espoo"),
    ]


def test_two_schools_are_each_signed_in_to_and_read(sources, portal):
    two_schools(sources, portal)

    got = collect(sources)

    assert [(e.title, e.kid) for e in got] == [("Virtanen Mia, Piano", "Mia"), ("Kuvataide 1", "Leo")]
    assert [(r["school"], r["body"]["username"]) for r in portal.requests if r["path"] == "auth/login"] == \
        [("emo", "parent@example.fi"), ("espoo", "parent2")]


def test_a_school_that_fails_while_the_other_works_is_named(sources, portal, caplog):
    two_schools(sources, portal)
    portal.school("espoo").accounts["parent2"] = "changed"

    with caplog.at_level(logging.ERROR, logger=eepos.log.name):
        got = collect(sources)

    assert [e.title for e in got] == ["Virtanen Mia, Piano"]
    [error] = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert failed_sign_in(error) == ("espoo.eepos.fi", "login failed at espoo.eepos.fi")
    assert "changed" not in error and EEPOS_PASSWORD not in error


def test_when_no_school_can_be_read_the_first_one_s_reason_is_raised(sources, portal):
    two_schools(sources, portal)
    portal.answers[("emo", "auth/login")] = (500, "")
    portal.answers[("espoo", "auth/login")] = (500, "")

    with pytest.raises(eepos.EeposError, match="^emo.eepos.fi answered HTTP 500$"):
        collect(sources)
