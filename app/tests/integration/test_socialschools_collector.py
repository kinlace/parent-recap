"""The Social Schools Source's collector (ADR 0047) against the fake Social Schools
(fake_sources.SocialSchools), which signs in and answers as its identity provider and API do: the
iOS app's sign-in it goes through, the requests it makes and the ones it never makes, the
announcements it tells once for the right Kid, the events it makes, and how a wrong password, an
account that signs in another way, an API that changed and a second school show. All names,
schools, posts and events are made up."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
import time_machine

from conftest import NOW, socialschools_child, socialschools_event, socialschools_post
from fake_sources import SOCIALSCHOOLS_PASSWORD
from family_brief.collectors import socialschools
from family_brief.config import Config
from family_brief.state import State

TZ = ZoneInfo("Europe/Helsinki")
USERNAME = "parent@example.nl"


@pytest.fixture(autouse=True)
def at_now():
    with time_machine.travel(NOW, tick=False):
        yield


@pytest.fixture
def portal(sources):
    """Social Schools, where Mia is in Groep 5 and Leo in Groep 3 at school 7, for the parent
    parent@example.nl."""
    fake = sources.fakes["socialschools"]
    fake.school(7).children = [socialschools_child("Mia", "Mia Virtanen", ("Groep 5",)),
                               socialschools_child("Leo", "Leo Virtanen", ("Groep 3",))]
    sources.config["socialschools"] = {"signins": [{"username": USERNAME}]}
    return fake


def read(sources):
    """What the collector reads tonight, with the state the evenings before left."""
    sources.set_up()
    cfg = Config.model_validate(sources.config)
    state = State(sources.home / "state.json")
    got = socialschools.collect(cfg, state, [])
    state.save()
    return got


def ago(hours: float) -> str:
    return (NOW - timedelta(hours=hours)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_it_signs_in_as_the_ios_app_does_and_then_only_reads(sources, portal):
    sources.tonight["socialschools"] = [socialschools_post(1, "Schoolreisje", "<p>Hallo</p>", ago(2))]

    read(sources)

    steps = [(r["method"], r["host"], r["path"]) for r in portal.requests]
    login = socialschools.LOGIN_HOST
    assert steps[:5] == [("GET", login, "/connect/authorize"), ("GET", login, "/Account/Login"),
                         ("POST", login, "/Account/Login"), ("GET", login, "/connect/authorize/callback"),
                         ("POST", login, "/connect/token")]
    authorize = portal.requests[0]["query"]
    assert authorize["client_id"] == "eu.socialschools.ios" and authorize["code_challenge_method"] == "S256"
    assert "offline_access" not in authorize["scope"].split() and portal.refresh_tokens == 0
    assert portal.requests[2]["form"]["Username"] == USERNAME
    # After the sign-in only GETs, and only of the account, the feed and the calendar: no post's own
    # page, no "seen" statistics, no conversations inbox.
    api = [(r["method"], r["path"]) for r in portal.requests[5:]]
    assert set(api) == {("GET", "/api/v1/useraccounts/current"), ("GET", "/api/v1/communityposts"),
                        ("GET", "/api/v1/events")}
    assert all(r["host"] == socialschools.API_HOST for r in portal.requests[5:])
    assert all(r["headers"]["Schoolid"] == "7" and r["headers"]["Roletypeid"] == "1"
               for r in portal.requests[5:] if r["path"] != "/api/v1/useraccounts/current")


def test_the_portal_refuses_any_other_path_before_sending_it(sources, portal):
    with socialschools.Portal() as p:
        p.token = "a-token"
        for path in ("inboxes", "communityposts/1", "../statistics"):
            with pytest.raises(ValueError):
                p.get(path, "anything")
    assert portal.requests == []


def test_each_new_announcement_is_a_message_for_the_kid_in_whose_group_it_was_posted(sources, portal):
    sources.tonight["socialschools"] = [
        socialschools_post(11, "Schoolreisje", "<p>Vrijdag gaan we naar het <b>bos</b>.</p><p>Neem laarzen mee.</p>",
                           ago(3), groups=("Groep 5",), attachments=("toestemming.pdf", "route.png")),
        socialschools_post(12, "Studiedag", "<p>Maandag is iedereen vrij.</p>", ago(5),
                           groups=("Schoolnieuws",), author="Directie"),
        socialschools_post(13, "Gym", "<p>Gym in de grote zaal.</p>", ago(6), groups=("Groep 3", "Groep 5")),
    ]

    got = read(sources)

    by_id = {m.external_id: m for m in got.messages}
    assert sorted(by_id) == ["11", "12", "13"]
    trip = by_id["11"]
    assert trip.kid_hint == "Mia" and trip.subject == "Schoolreisje" and trip.sender == "Juf Bakker"
    assert trip.body == ("Vrijdag gaan we naar het bos.\nNeem laarzen mee.\n\n"
                         "[Attachments in Social Schools: toestemming.pdf, route.png]")
    assert trip.url == "https://app.socialschools.eu/communityposts/11" and trip.chat_name == "Groep 5"
    assert "files.socialschools.eu" not in trip.body  # the attachments' links are never read
    assert by_id["12"].kid_hint is None  # the whole school's
    assert by_id["13"].kid_hint is None  # both Kids' groups: the Household's
    assert all(socialschools.sent_to_everyone(m) for m in got.messages)


def test_an_announcement_is_told_once_and_one_before_the_lookback_never(sources, portal):
    sources.tonight["socialschools"] = [socialschools_post(21, "Nieuw", "<p>Nieuw</p>", ago(2)),
                                        socialschools_post(20, "Oud", "<p>Oud</p>", ago(30))]

    assert [m.external_id for m in read(sources).messages] == ["21"]
    assert read(sources).messages == []


def test_the_feed_is_read_page_by_page_back_to_the_lookback(sources, portal):
    sources.tonight["socialschools"] = [socialschools_post(100 + n, f"Bericht {n}", "<p>x</p>", ago(n / 2))
                                        for n in range(30)]

    got = read(sources)

    assert len(got.messages) == 30
    offsets = [r["query"]["offset"] for r in portal.requests if r["path"] == "/api/v1/communityposts"]
    assert offsets == ["0", "20"]


def test_whole_days_stay_on_their_dates_and_times_come_in_the_family_s_zone(sources, portal):
    sources.tonight["socialschools"] = [
        socialschools_event(1, "Studiedag", "2026-09-30T00:00:00Z", "2026-10-01T00:00:00Z", all_day=True,
                            groups=("Schoolnieuws",)),
        socialschools_event(2, "Herfstvakantie", "2026-10-19T00:00:00Z", "2026-10-26T00:00:00Z", all_day=True),
        socialschools_event(3, "Oudergesprek", "2026-09-29T15:30:00+02:00", "2026-09-29T15:45:00+02:00",
                            groups=("Groep 3",), location="Lokaal 4", description="<p>Graag op tijd</p>"),
    ]

    events = {e.title: e for e in read(sources).events}

    day = events["Studiedag"]
    assert day.all_day and day.start == datetime(2026, 9, 30, tzinfo=TZ) and day.end is None and day.kid is None
    holiday = events["Herfstvakantie"]
    assert holiday.start.date().isoformat() == "2026-10-19" and holiday.end.date().isoformat() == "2026-10-25"
    assert holiday.kid == "Mia"
    talk = events["Oudergesprek"]
    assert not talk.all_day and talk.kid == "Leo" and talk.location == "Lokaal 4"
    assert talk.start == datetime(2026, 9, 29, 16, 30, tzinfo=TZ) and talk.description == "Graag op tijd"
    # The same each evening, so the calendar gets it once.
    assert {e.external_id for e in read(sources).events} == {e.external_id for e in events.values()}


def test_a_wrong_password_is_a_failed_login(sources, portal):
    sources.set_up()
    portal.accounts[USERNAME] = "changed-since"

    with pytest.raises(socialschools.WrongPassword, match="login failed at login.socialschools.eu"):
        read(sources)
    assert not [r for r in portal.requests if r["host"] == socialschools.API_HOST]


@pytest.mark.parametrize("way, said", [("two-step", "further step after the password"),
                                       ("external", "Google, Microsoft or Apple")])
def test_an_account_that_signs_in_another_way_is_unsupported_and_says_how(sources, portal, way, said):
    portal.ways[USERNAME] = way

    with pytest.raises(socialschools.Unsupported, match=said) as e:
        read(sources)
    assert "login failed" in str(e.value)  # the Brief says it as a failed login
    assert not [r for r in portal.requests if "google" in r["host"]]  # never followed


def test_an_api_that_changed_is_said_and_the_password_never(sources, portal):
    portal.answers["events"] = (200, "<!doctype html><html>Social Schools</html>")

    with pytest.raises(socialschools.Changed, match="api.socialschools.eu's calendar wasn't JSON") as e:
        read(sources)
    assert SOCIALSCHOOLS_PASSWORD not in str(e.value) and USERNAME not in str(e.value)


def test_announcements_that_can_t_be_read_leave_the_events(sources, portal, caplog):
    sources.tonight["socialschools"] = [socialschools_event(1, "Studiedag", "2026-09-30T00:00:00Z",
                                                            "2026-10-01T00:00:00Z", all_day=True)]
    portal.answers["communityposts"] = (500, "")

    with caplog.at_level(logging.ERROR):
        got = read(sources)

    assert [e.title for e in got.events] == ["Studiedag"] and got.messages == []
    assert "api.socialschools.eu answered HTTP 500" in caplog.text


def test_each_school_of_the_account_is_read_with_its_own_children(sources, portal):
    portal.school(8, "Kindcentrum Noord").children = [socialschools_child("Mia", "Mia Virtanen", ("Bovenbouw",))]
    sources.tonight["socialschools"] = [
        socialschools_post(31, "Muziekles", "<p>Muziek</p>", ago(1), groups=("Bovenbouw",), school=8),
        socialschools_post(32, "Bovenbouw", "<p>Elders</p>", ago(1), groups=("Bovenbouw",), school=7),
    ]

    got = read(sources)

    assert {m.external_id: m.kid_hint for m in got.messages} == {"31": "Mia", "32": None}
