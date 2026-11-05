"""The eVaka Source's collector (ADR 0039) against each city's fake eVaka (fake_sources.Evaka),
which answers as its citizen API does: the sign-in it sends, the device cookie it keeps, the
calendar, reservations and messages it asks for, the events it makes of them for the right Kid,
the daycare's new messages told once and a sensitive one only named, nothing written to eVaka, and
how a wrong password, an API that changed and a second city show. All names, events and messages
are made up."""
from __future__ import annotations

import json
import logging
import stat
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import requests
import time_machine

from conftest import NOW, evaka_child, evaka_event, evaka_sensitive_thread, evaka_thread
from fake_sources import EVAKA_PASSWORD
from family_brief.collectors import evaka
from family_brief.collectors.base import failed_sign_in
from family_brief.config import Config
from family_brief.state import State

TZ = ZoneInfo("Europe/Helsinki")
MIA, LEO = "1a2b3c4d-0000-4000-8000-000000000011", "1a2b3c4d-0000-4000-8000-000000000012"


@pytest.fixture(autouse=True)
def at_now():
    with time_machine.travel(NOW, tick=False):
        yield


@pytest.fixture
def portal(sources):
    """The fake eVakas, with Mia and Leo in Espoo's for the guardian parent@example.fi."""
    fake = sources.fakes["evaka"]
    fake.city("espoo").children = [evaka_child(MIA, "Mia Emilia", "Virtanen"), evaka_child(LEO, "Leo", "Virtanen")]
    sources.config["evaka"] = {"signins": [{"city": "espoo", "username": "parent@example.fi"}]}
    return fake


def collect(sources) -> list:
    return read(sources).events


def read(sources):
    """What the collector reads tonight, with the state the evenings before left."""
    sources.set_up()
    cfg = Config.model_validate(sources.config)
    state = State(sources.home / "state.json")
    got = evaka.collect(cfg, state, [])
    state.save()
    return got


def test_it_signs_in_as_evaka_s_app_does_and_asks_for_the_next_four_weeks(sources, portal):
    collect(sources)

    login, children, calendar, reservations, messages = portal.requests
    assert (login["method"], login["city"], login["path"]) == ("POST", "espoo", "auth/weak-login")
    assert login["body"] == {"username": "parent@example.fi", "password": EVAKA_PASSWORD}
    assert login["headers"]["x-evaka-csrf"] == "1"
    assert (children["path"], calendar["path"], reservations["path"], messages["path"]) == \
        ("children", "calendar-events", "reservations", "messages/received")
    assert messages["query"] == {"page": "1"}
    assert calendar["query"] == {"start": "2026-09-27", "end": "2026-10-25"}
    assert reservations["query"] == {"from": "2026-09-27", "to": "2026-10-25"}


def test_events_closed_days_and_the_deadline_go_to_the_right_kid(sources, portal):
    portal.city("espoo").holidays = {"2026-10-09"}
    sources.tonight["evaka"] = [
        evaka_event("Retki Nuuksioon", "2026-09-29", children=(MIA,)),
        evaka_event("Kuvauspäivä", "2026-10-01", children=(MIA, LEO)),
        evaka_event("Varhaiskasvatuskeskustelu", "2026-09-28", "2026-10-09",
                    booked={LEO: ("2026-10-06", "14:00:00", "14:45:00")}),
        evaka_event("Syysjuhla", "2026-09-27", children=(MIA,)),  # today: over by the evening
        evaka_event("Teemaviikko", "2026-09-21", "2026-09-30", children=(LEO,)),  # still going on
    ]

    got = sorted(collect(sources), key=lambda e: e.start)

    assert [(e.title, e.kid, e.start, e.all_day) for e in got] == [
        ("Teemaviikko", "Leo", datetime(2026, 9, 21, tzinfo=TZ), True),
        ("Varaukset viikolle 41 sulkeutuvat", None, datetime(2026, 9, 28, 18, 0, tzinfo=TZ), False),
        ("Retki Nuuksioon", "Mia", datetime(2026, 9, 29, tzinfo=TZ), True),
        ("Kuvauspäivä", None, datetime(2026, 10, 1, tzinfo=TZ), True),
        ("Varhaiskasvatuskeskustelu", "Leo", datetime(2026, 10, 6, 14, 0, tzinfo=TZ), False),
        (evaka.CLOSED, None, datetime(2026, 10, 9, tzinfo=TZ), True),
    ]


def test_the_device_cookie_is_kept_owner_only_and_sent_the_next_evening(sources, portal):
    collect(sources)
    devices = evaka.devices_path(Config.model_validate(sources.config))
    assert devices.parent == sources.home / ".family"  # beside the state

    assert portal.requests[0]["devices"] == {}
    kept = json.loads(devices.read_text())
    assert list(kept) == ["espoonvarhaiskasvatus.fi"]
    [name] = kept["espoonvarhaiskasvatus.fi"]
    assert name.startswith(evaka.DEVICE_COOKIE)
    assert stat.S_IMODE(devices.stat().st_mode) == 0o600
    assert EVAKA_PASSWORD not in devices.read_text()

    portal.requests.clear()
    collect(sources)

    assert portal.requests[0]["devices"] == kept["espoonvarhaiskasvatus.fi"]


def test_a_wrong_password_says_the_login_failed_and_never_says_the_password(sources, portal):
    sources.secrets[evaka.secret_name("espoo", "parent@example.fi")] = "not-it"

    with pytest.raises(evaka.WrongPassword) as e:
        collect(sources)

    assert str(e.value) == "login failed at espoonvarhaiskasvatus.fi"
    assert [r["path"] for r in portal.requests] == ["auth/weak-login"]


def test_a_sign_in_without_its_password_in_the_store_is_a_failed_login(sources, portal):
    cfg = Config.model_validate(sources.config)

    with pytest.raises(evaka.WrongPassword, match="no password"):
        evaka.collect(cfg, State(sources.home / "state.json"), [])


@pytest.mark.parametrize("path, answer, says", [
    ("calendar-events", (200, "<!doctype html><html>eVaka</html>"), "calendar wasn't JSON"),
    ("calendar-events", (200, '{"events": []}'), "calendar isn't a list"),
    ("calendar-events", (404, "Not Found"), "has no calendar where it was"),
    ("reservations", (200, '{"days": []}'), "reservations aren't as they were"),
    ("children", (200, '[{"id": "1"}]'), "list of children has no names"),
    ("auth/weak-login", (200, "OK"), "sign-in answered in a way"),  # no session cookie
    ("auth/weak-login", (404, "Not Found"), "has no sign-in where it was"),
], ids=["html", "not-a-list", "moved", "reservations", "no-names", "no-session", "sign-in-moved"])
def test_an_api_that_changed_says_so(sources, portal, path, answer, says):
    portal.answers[("espoo", path)] = answer

    with pytest.raises(evaka.Changed, match=says):
        collect(sources)


@pytest.mark.parametrize("answer", [(403, "<html>Access denied</html>"), (401, "Forbidden"), (400, "Bad Request"),
                                    (429, "Too Many Requests"), (503, '{"message": "disabled"}')],
                         ids=["403-html", "401", "400", "rate-limited", "disabled"])
def test_only_evaka_s_own_wrong_credentials_answer_is_a_wrong_password(sources, portal, answer):
    # A changed sign-in mustn't tell the family their password is wrong.
    portal.answers[("espoo", "auth/weak-login")] = answer

    with pytest.raises(evaka.EvakaError) as e:
        collect(sources)

    assert not isinstance(e.value, evaka.WrongPassword)


def test_an_evaka_that_doesn_t_answer_says_which_city(sources, portal):
    portal.answers[("espoo", "auth/weak-login")] = requests.ConnectionError("Name or service not known")

    with pytest.raises(evaka.EvakaError, match=r"^espoonvarhaiskasvatus.fi didn't answer \(ConnectionError\)$"):
        collect(sources)


def test_a_session_evaka_signed_out_is_no_wrong_password(sources, portal):
    portal.answers[("espoo", "children")] = (401, "Unauthorized")

    with pytest.raises(evaka.EvakaError) as e:
        collect(sources)

    assert not isinstance(e.value, evaka.WrongPassword)


def two_cities(sources, portal) -> None:
    portal.city("tampere").children = [evaka_child("t-31", "Leo", "Virtanen")]
    sources.config["evaka"]["signins"].append({"city": "varhaiskasvatus.tampere.fi", "username": "parent2@example.fi"})
    sources.tonight["evaka"] = [
        evaka_event("Retki Nuuksioon", "2026-09-29", children=(MIA,)),
        evaka_event("Teatteri", "2026-09-30", children=("t-31",), city="tampere"),
    ]


def test_two_cities_are_each_signed_in_to_and_read(sources, portal):
    two_cities(sources, portal)

    got = [e for e in collect(sources) if e.all_day]

    assert [(e.title, e.kid) for e in got] == [("Retki Nuuksioon", "Mia"), ("Teatteri", "Leo")]
    assert [(r["city"], r["body"]["username"]) for r in portal.requests if r["path"] == "auth/weak-login"] == \
        [("espoo", "parent@example.fi"), ("tampere", "parent2@example.fi")]


def test_a_city_that_fails_while_the_other_works_is_named(sources, portal, caplog):
    two_cities(sources, portal)
    portal.city("tampere").accounts["parent2@example.fi"] = "changed"

    with caplog.at_level(logging.ERROR, logger=evaka.log.name):
        got = collect(sources)

    assert [e.title for e in got if e.all_day] == ["Retki Nuuksioon"]
    [error] = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert failed_sign_in(error) == ("varhaiskasvatus.tampere.fi", "login failed at varhaiskasvatus.tampere.fi")
    assert "changed" not in error and EVAKA_PASSWORD not in error


def test_when_no_city_can_be_read_the_first_one_s_reason_is_raised(sources, portal):
    two_cities(sources, portal)
    portal.answers[("espoo", "auth/weak-login")] = (500, "")
    portal.answers[("tampere", "auth/weak-login")] = (500, "")

    with pytest.raises(evaka.EvakaError, match="^espoonvarhaiskasvatus.fi answered HTTP 500$"):
        collect(sources)


# ── Messages (#786)

BULLETIN = "Hei Kotiloisten perheet! Perjantaina 2.10. on pehmolelupäivä. Oman pehmolelun saa tuoda mukaan."


def test_a_new_daycare_message_is_a_message_for_the_kid_its_thread_names_and_is_told_once(sources, portal):
    portal.city("espoo").threads = [
        evaka_thread("Pehmolelupäivä", BULLETIN, "2026-09-27T14:05:00+03:00", children=(MIA,),
                     attachments=("viikkokirje.pdf",)),
        evaka_thread("Kotiloisten viikko", "Ensi viikolla retkeilemme.", "2026-09-27T09:00:00+03:00",
                     children=(MIA, LEO), sender="Laine Maija", sender_type="PERSONAL", kind="MESSAGE",
                     recipients=2),
    ]

    [bulletin, weekly] = read(sources).messages

    assert (bulletin.source, bulletin.subject, bulletin.sender, bulletin.kid_hint, bulletin.chat_name) == \
        ("evaka", "Pehmolelupäivä", "Kilonpuisto - Kotiloiset", "Mia", "bulletin")
    assert bulletin.body == BULLETIN + "\n\n[1 attachment in eVaka]"
    assert bulletin.timestamp == datetime(2026, 9, 27, 11, 5, tzinfo=ZoneInfo("UTC"))
    assert bulletin.url == f"https://espoonvarhaiskasvatus.fi/messages/{portal.city('espoo').threads[0]['id']}"
    assert evaka.sent_to_everyone(bulletin)  # to 24 guardians: never held back for its words
    # About both children: the Household's. To two guardians: one family's, so checked.
    assert (weekly.kid_hint, weekly.chat_name, evaka.sent_to_everyone(weekly)) == (None, "message", False)
    # Only how many it went to is kept, never the other guardians' names (ADR 0013).
    assert "Huoltaja" not in json.dumps([bulletin.to_dict(), weekly.to_dict()])

    assert read(sources).messages == []


def test_a_sensitive_message_is_only_named_and_its_source_withholds_it(sources, portal):
    sensitive = evaka_sensitive_thread("Laine Maija", "2026-09-27T16:30:00+03:00")
    portal.city("espoo").threads = [sensitive]

    [m] = read(sources).messages

    assert (m.sender, m.subject, m.body, m.kid_hint) == ("Laine Maija", None, "", None)
    assert m.url == f"https://espoonvarhaiskasvatus.fi/messages/{sensitive['id']}"
    assert evaka.withheld(m) and not evaka.sent_to_everyone(m)

    # A new message in the same thread is named again, the next evening.
    assert read(sources).messages == []
    sensitive["lastMessageSentAt"] = "2026-09-27T20:10:00+03:00"
    [again] = read(sources).messages
    assert again.external_id != m.external_id


def test_the_family_s_own_messages_and_deleted_ones_aren_t_told(sources, portal):
    thread = evaka_thread("Poissaolo", "Hei, Mia on huomenna poissa.", "2026-09-27T08:00:00+03:00",
                          sender="Virtanen Anna", sender_type="CITIZEN", kind="MESSAGE", children=(MIA,),
                          replies=(("Laine Maija", "PERSONAL", "2026-09-27T10:00:00+03:00"),
                                   ("Laine Maija", "PERSONAL", "2026-09-27T11:00:00+03:00")))
    thread["messages"][2]["contentDeletedAt"] = "2026-09-27T11:30:00+03:00"
    portal.city("espoo").threads = [thread]

    [reply] = read(sources).messages

    assert (reply.sender, reply.timestamp.hour) == ("Laine Maija", 7)  # 10:00 in Helsinki


def test_messages_from_before_the_lookback_are_never_told(sources, portal):
    portal.city("espoo").threads = [
        evaka_thread("Syksyn kuvaus", "Kuvaus on lokakuussa.", "2026-09-20T10:00:00+03:00"),
    ]

    assert read(sources).messages == []

    # Not even when a missed evening widens the lookback (run.py's catch-up).
    sources.config["evaka"]["lookback_hours"] = 10 * 24
    assert read(sources).messages == []


def test_older_pages_are_read_only_while_they_reach_into_the_lookback(sources, portal):
    city = portal.city("espoo")
    city.threads = [evaka_thread(f"Tiedote {n}", "Teksti", f"2026-09-27T{8 + n // 2:02d}:{n % 2 * 30:02d}:00+03:00")
                    for n in range(12)] + \
                   [evaka_thread(f"Vanha {n}", "Teksti", f"2026-09-{10 + n:02d}T10:00:00+03:00") for n in range(9)]

    got = read(sources).messages

    assert len(got) == 12
    assert [r["query"] for r in portal.requests if r["path"] == "messages/received"] == \
        [{"page": "1"}, {"page": "2"}]


def test_nothing_is_written_to_evaka(sources, portal):
    portal.city("espoo").threads = [evaka_thread("Pehmolelupäivä", BULLETIN, "2026-09-27T14:05:00+03:00"),
                                    evaka_sensitive_thread("Laine Maija", "2026-09-27T16:30:00+03:00")]

    read(sources)

    assert [(r["method"], r["path"]) for r in portal.requests if r["path"] != evaka.SIGN_IN] == [
        ("GET", "children"), ("GET", "calendar-events"), ("GET", "reservations"), ("GET", "messages/received")]


@pytest.mark.parametrize("answer, says", [
    ((200, "<!doctype html><html>eVaka</html>"), "messages wasn't JSON"),
    ((200, "[]"), "messages aren't as they were"),
    ((200, '{"data": [{"type": "Thread", "id": "x"}], "total": 1, "pages": 1}'), "thread it didn't have"),
    ((404, "Not Found"), "has no messages where it was"),
], ids=["html", "a-list", "new-type", "moved"])
def test_messages_that_changed_leave_the_events_and_say_so(sources, portal, caplog, answer, says):
    portal.answers[("espoo", "messages/received")] = answer
    sources.tonight["evaka"] = [evaka_event("Retki Nuuksioon", "2026-09-29", children=(MIA,))]

    with caplog.at_level(logging.ERROR, logger=evaka.log.name):
        got = read(sources)

    assert "Retki Nuuksioon" in [e.title for e in got.events] and got.messages == []
    [error] = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert error.startswith("espoonvarhaiskasvatus.fi") and says in error
