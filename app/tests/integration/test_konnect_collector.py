"""The Konnect Source's collector (ADR 0050) against each provider's fake parent portal
(fake_sources.Konnect), which answers as the portal's JSON does: the sign-in it sends, the list
addresses it reads and the ones it never reads (the timeline, an item's own page), the planned
times it makes events of for the right Kid, the daycare's new messages told once, and how a wrong
password, a portal that wants a new password, a portal that changed and a second provider show.
All names, times and messages are made up."""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import time_machine

from conftest import (NOW, konnect_child, konnect_conversation, konnect_news, konnect_newsletter,
                      konnect_planned)
from fake_sources import KONNECT_PASSWORD
from family_brief.sources.nl.konnect import collector as konnect
from family_brief.collectors.base import failed_sign_in
from family_brief.config import Config
from family_brief.state import State

AMS = ZoneInfo("Europe/Amsterdam")
MIA, LEO = "c0ffee0000000000000000000000a001", "c0ffee0000000000000000000000a002"
MIA_CHILD = konnect_child(MIA, "Mia", "Testdochter")
LEO_CHILD = konnect_child(LEO, "Leo", "Testdochter")
# The lists the evening reads, each once, in this order, and nothing else.
READS = ["auth-api/login", "restservices-parent/children/", "restservices-parent/parent/authorizations",
         "restservices-parent/calendar/20260927/until/20261011", "restservices-parent/logbook/overview",
         "restservices-parent/htmlnews/view", "restservices-parent/newsletter"]


@pytest.fixture(autouse=True)
def at_now():
    with time_machine.travel(NOW, tick=False):
        yield


@pytest.fixture
def portal(sources):
    """The fake portals, with Mia and Leo at a BSO on mijnbso.ouderportaal.nl for the parent
    parent@example.nl."""
    fake = sources.fakes["konnect"]
    fake.portal("mijnbso").children = [MIA_CHILD, LEO_CHILD]
    sources.config["konnect"] = {"signins": [{"portal": "mijnbso", "username": "parent@example.nl"}]}
    return fake


def read(sources):
    """What the collector reads tonight, with the state the evenings before left."""
    sources.set_up()
    cfg = Config.model_validate(sources.config)
    state = State(sources.home / "state.json")
    got = konnect.collect(cfg, state, [])
    state.save()
    return got


def test_it_signs_in_as_the_portal_s_app_does_and_reads_only_the_lists(sources, portal):
    read(sources)

    login, *rest = portal.requests
    assert (login["method"], login["portal"], login["path"]) == ("PUT", "mijnbso", "auth-api/login")
    assert login["body"] == {"username": "parent@example.nl", "password": KONNECT_PASSWORD,
                             "childMinder": False}
    assert login["headers"]["User-Agent"].startswith("Mozilla/5.0")
    assert [r["path"] for r in portal.requests] == READS
    assert {r["method"] for r in rest} == {"GET"}
    assert all(r["headers"]["Authorization"].startswith("Bearer ") for r in rest)
    # Back to the lookback, 26 hours, to tomorrow, in the portal's own days.
    assert {r["path"]: r["query"] for r in rest if r["query"]} == {
        f"restservices-parent/{p}": {"from": "20260926", "to": "20260928"}
        for p in ("logbook/overview", "htmlnews/view", "newsletter")}


def test_the_timeline_and_an_item_s_own_page_are_never_read(sources, portal):
    # The timeline has the children's photos and their day rhythm, such as medication and
    # temperature; opening an item marks it read.
    portal.portal("mijnbso").conversations = [
        konnect_conversation("Uitje", "<p>Morgen naar het bos.</p>", "2026-09-27T16:00:00+02:00",
                             children=(MIA_CHILD,))]
    portal.portal("mijnbso").news = [konnect_news("Sluitingsdag", "Op 9 oktober dicht.", "2026-09-27T09:00:00+02:00")]

    read(sources)

    assert not [p for p in portal.paths() if "timeline" in p or "photo" in p or "journal" in p]
    assert set(portal.paths()) == set(READS)


def test_planned_times_go_to_the_right_kid_and_an_absence_isn_t_one(sources, portal):
    sources.tonight["konnect"] = [
        konnect_planned(MIA, "2026-09-28T14:00:00+02:00", "2026-09-28T18:30:00+02:00"),
        konnect_planned(LEO, "2026-09-29T14:00:00+02:00", "2026-09-29T18:00:00+02:00", code="tentative"),
        konnect_planned(LEO, "2026-09-30T14:00:00+02:00", "2026-09-30T18:00:00+02:00",
                        booking_type="booking.type.extra", status="pending_approval"),
        konnect_planned(MIA, "2026-10-01T14:00:00+02:00", "2026-10-01T18:00:00+02:00", code="absent"),
        konnect_planned(MIA, "2026-10-02T14:00:00+02:00", "2026-10-02T18:00:00+02:00", status="rejected"),
    ]

    got = read(sources).events

    assert [(e.kid, e.title, e.start.astimezone(AMS).isoformat(), e.end.astimezone(AMS).isoformat())
            for e in got] == [
        ("Mia", "Opvang", "2026-09-28T14:00:00+02:00", "2026-09-28T18:30:00+02:00"),
        ("Leo", "Opvang (optie)", "2026-09-29T14:00:00+02:00", "2026-09-29T18:00:00+02:00"),
        ("Leo", "Opvang (extra dag, aangevraagd)", "2026-09-30T14:00:00+02:00", "2026-09-30T18:00:00+02:00")]
    assert {e.location for e in got} == {"BSO Locatie Testlaan"}
    assert len({e.external_id for e in got}) == 3
    # The same each evening, so a calendar gets each once.
    assert [e.external_id for e in read(sources).events] == [e.external_id for e in got]


def test_a_new_message_from_the_daycare_is_told_once_under_its_kid_and_the_family_s_own_isn_t(sources, portal):
    there = portal.portal("mijnbso")
    there.conversations = [
        konnect_conversation("Uitje naar het bos", "<p>Beste ouders,</p><p>Morgen gaan we naar het "
                             "<b>bos</b>.<br/>Laarzen mee!</p>", "2026-09-27T16:00:00+02:00",
                             children=(MIA_CHILD,), message_id="msg-1"),
        konnect_conversation("Speen", "<p>Ze gebruikt een speen.</p>", "2026-09-27T17:00:00+02:00",
                             children=(LEO_CHILD,), written="Ouder Test", from_parent=True),
    ]
    there.news = [konnect_news("Sluitingsdag", "Op 9 oktober zijn we gesloten.", "2026-09-27T09:00:00+02:00",
                               attachment=True)]
    there.newsletters = [konnect_newsletter("Tarieven 2027", "De tarieven zijn bekend.",
                                            "2026-09-27T10:00:00+02:00", service=True)]

    got = read(sources).messages

    trip, news, rates = got
    assert (trip.external_id, trip.kid_hint, trip.sender, trip.subject, trip.chat_name) == \
        ("message:msg-1", "Mia", "Groep Zonnebloem", "Uitje naar het bos", "message")
    assert trip.body == "Beste ouders,\nMorgen gaan we naar het bos.\nLaarzen mee!"
    assert trip.timestamp == datetime(2026, 9, 27, 14, 0, tzinfo=ZoneInfo("UTC"))
    assert trip.url == "https://mijnbso.ouderportaal.nl/parent/"
    assert (news.kid_hint, news.subject, news.chat_name) == (None, "Sluitingsdag", "news")
    assert news.body == "Op 9 oktober zijn we gesloten.\n\n[an attachment in Konnect]"
    assert (rates.subject, rates.chat_name) == ("Tarieven 2027", "service message")
    assert konnect.sent_to_everyone(news) and konnect.sent_to_everyone(rates)
    assert not konnect.sent_to_everyone(trip)

    # The next evening nothing is told again, but the daycare's reply in the same conversation is.
    there.conversations[0] = konnect_conversation(
        "Uitje naar het bos", "<p>Het uitje gaat niet door.</p>", "2026-09-28T12:00:00+02:00",
        children=(MIA_CHILD,), message_id="msg-2")
    with time_machine.travel(datetime(2026, 9, 28, 21, 0, tzinfo=ZoneInfo("Europe/Helsinki")), tick=False):
        [reply] = read(sources).messages
    assert (reply.external_id, reply.body) == ("message:msg-2", "Het uitje gaat niet door.")


def test_only_the_lists_the_account_may_see_are_read(sources, portal):
    portal.portal("mijnbso").authorizations = ["WEB_PARENT_LOGBOOK"]

    read(sources)

    assert portal.paths() == ["auth-api/login", "restservices-parent/children/",
                              "restservices-parent/parent/authorizations",
                              "restservices-parent/logbook/overview"]


def test_a_wrong_password_is_a_failed_login_and_nothing_else_is_asked(sources, portal):
    sources.set_up()
    portal.portal("mijnbso").accounts["parent@example.nl"] = "changed-since"

    with pytest.raises(konnect.WrongPassword, match="^login failed at mijnbso.ouderportaal.nl$"):
        read(sources)
    assert portal.paths() == ["auth-api/login"]


def test_a_portal_that_wants_a_new_password_says_so_and_nothing_else_is_asked(sources, portal):
    portal.portal("mijnbso").must_reset = {"parent@example.nl"}

    with pytest.raises(konnect.MustResetPassword) as e:
        read(sources)
    assert str(e.value) == "login failed at mijnbso.ouderportaal.nl: it asks for a new password"
    assert portal.paths() == ["auth-api/login"]


def test_a_changed_portal_is_named_and_messages_that_fail_leave_the_events(sources, portal, caplog):
    sources.tonight["konnect"] = [konnect_planned(MIA, "2026-09-28T14:00:00+02:00", "2026-09-28T18:30:00+02:00")]
    portal.answers[("mijnbso", "restservices-parent/htmlnews/view")] = (200, "<html>Onderhoud</html>")

    with caplog.at_level(logging.ERROR):
        got = read(sources)

    assert [e.title for e in got.events] == ["Opvang"] and got.messages == []
    assert "mijnbso.ouderportaal.nl's news wasn't JSON" in caplog.text

    portal.answers[("mijnbso", "restservices-parent/children/")] = (200, "<html>Onderhoud</html>")
    with pytest.raises(konnect.Changed, match="list of children wasn't JSON"):
        read(sources)


def test_two_providers_are_both_read_and_one_that_fails_is_named(sources, portal, caplog):
    sources.config["konnect"]["signins"].append({"portal": "anderebso", "username": "parent@example.nl"})
    portal.portal("anderebso").children = [konnect_child("b-2", "Leo", "Testdochter")]
    sources.tonight["konnect"] = [
        konnect_planned(MIA, "2026-09-28T14:00:00+02:00", "2026-09-28T18:30:00+02:00"),
        konnect_planned("b-2", "2026-09-29T08:00:00+02:00", "2026-09-29T12:00:00+02:00", portal="anderebso")]

    assert [(e.kid, e.start.day) for e in read(sources).events] == [("Mia", 28), ("Leo", 29)]

    portal.portal("anderebso").accounts["parent@example.nl"] = "new-one"
    with caplog.at_level(logging.ERROR):
        assert [e.kid for e in read(sources).events] == ["Mia"]
    [line] = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert failed_sign_in(line) == ("anderebso.ouderportaal.nl", "login failed at anderebso.ouderportaal.nl")
