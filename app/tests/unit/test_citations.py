"""The model's citations checked against what the program supplied (citations), on summaries
already in the schema's shape."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest

from family_brief.citations import for_prompt, keep_links, resolve
from family_brief.collectors.base import Message


def msg(source: str, ext_id: str, minute: int = 0, body: str = "", subject: str | None = None,
        url: str | None = None) -> Message:
    return Message(source=source, external_id=ext_id, timestamp=datetime(2026, 9, 27, 18, minute, tzinfo=timezone.utc),
                   subject=subject, body=body, url=url)


MESSAGES = [msg("wilma", "w-55"), msg("whatsapp", "wa-2"), msg("gmail", "g-101")]


def summary(notices: list | None = None, action_items: list | None = None, events: list | None = None) -> dict:
    return {"per_kid": [{"kid": "Mia", "notices": notices or [], "action_items": action_items or []}],
            "calendar_events": events or []}


def entries(s: dict) -> list[dict]:
    [kid] = s["per_kid"]
    return kid["notices"] + kid["action_items"]


# ── Notices and Action Items

def test_each_entry_gets_the_sources_of_its_resolving_refs():
    s = summary(notices=[{"text": "koe", "refs": ["w-55"]}, {"text": "made up", "refs": ["w-999"]}],
                action_items=[{"what": "sign", "refs": ["wa-2", "g-101", "nope"]}, {"what": "uncited"},
                              {"what": "odd refs", "refs": "w-55"}])

    resolve(s, MESSAGES, [], [], [])

    assert [(e["source"], e["verified"]) for e in entries(s)] == \
        [(["wilma"], True), ([], False), (["gmail", "whatsapp"], True), ([], False), ([], False)]
    assert entries(s)[2]["ref_sources"] == {"wa-2": ["whatsapp"], "g-101": ["gmail"]}
    assert [e["refs"] for e in entries(s)][3:] == [[], []]  # stored even when empty
    assert s["_citations"] == {"entries": 5, "unverified": 3, "legacy": 0}


def test_calendar_events_and_queued_events_can_be_cited():
    s = summary(notices=[{"text": "clash", "refs": ["x1", "g-101"]}, {"text": "match", "refs": ["mc-1"]},
                         {"text": "queued without a source", "refs": ["q-1"]}])

    resolve(s, MESSAGES, [{"id": "x1"}, {"id": None}],
            [{"external_id": "mc-1", "source": "myclub"}, {"external_id": "q-1"}], [])

    assert [e["source"] for e in entries(s)] == [["calendar", "gmail"], ["myclub"], ["calendar"]]


EARLIER = [{"per_kid": [{"kid": "Leo", "action_items": [
    {"what": "books", "refs": ["g-90"], "ref_sources": {"g-90": ["gmail"]}},
    {"what": "fee", "refs": ["g-404", "wa-7"], "ref_sources": {"wa-7": ["whatsapp"]}},
    {"what": "odd", "refs": [], "ref_sources": ["not", "a", "dict"]},
]}]}]


def test_a_re_reminder_borrows_only_the_refs_that_resolved_back_then():
    s = summary(action_items=[{"what": "（再提醒）books", "refs": ["g-90"]},
                              {"what": "（再提醒）fee", "refs": ["g-404"]},
                              {"what": "(Reminder) both", "refs": ["g-404", "wa-7"]}])

    resolve(s, [], [], [], EARLIER)

    assert [(e["source"], e["verified"]) for e in entries(s)] == \
        [(["gmail"], True), ([], False), (["whatsapp"], True)]
    assert s["_citations"] == {"entries": 3, "unverified": 1, "legacy": 0}


@pytest.mark.parametrize("old_item", [{"what": "books"}, "books"], ids=["without refs", "a bare string"])
def test_an_unverified_re_reminder_is_legacy_while_old_shape_briefs_are_fed_in(old_item):
    s = summary(action_items=[{"what": "（再提醒）books", "refs": []}, {"what": "new bag", "refs": []},
                              {"what": "Muistutus ei", "refs": []}])

    resolve(s, [], [], [], [{"per_kid": [{"kid": "Leo", "action_items": [old_item]}]}])

    assert [(e["verified"], e.get("legacy", False)) for e in entries(s)] == \
        [(False, True), (False, False), (False, False)]
    assert s["_citations"] == {"entries": 3, "unverified": 2, "legacy": 1}


def test_a_re_reminder_of_an_uncited_new_shape_item_is_not_legacy():
    s = summary(action_items=[{"what": "（再提醒）fee", "refs": []}])

    resolve(s, [], [], [], [{"per_kid": [{"kid": "Leo", "action_items": [{"what": "fee", "refs": []}]}]}])

    assert s["_citations"] == {"entries": 1, "unverified": 1, "legacy": 0}


# ── Calendar events

def test_an_event_citing_none_of_tonights_messages_is_dropped(caplog):
    s = summary(events=[{"title": "Payment meeting", "start": "2026-10-01T18:00", "refs": ["w-invented"]},
                        {"title": "No refs", "start": "2026-10-01T18:00"},
                        {"title": "Odd refs", "start": "2026-10-01T18:00", "refs": "w-55"}])

    with caplog.at_level(logging.WARNING):
        resolve(s, MESSAGES, [], [], [])

    assert s["calendar_events"] == []
    assert sum("no message it cites" in r.getMessage() for r in caplog.records) == 3


def test_an_event_takes_its_identity_from_the_earliest_message_it_cites():
    messages = [msg("gmail", "g-100", minute=30), msg("wilma", "w-55", minute=10), msg("whatsapp", "w-55", minute=10)]
    s = summary(events=[{"title": "Koe", "start": "2026-10-05T09:00", "refs": ["g-100", "w-55", "x"],
                         "source": "gmail", "external_id": "made-up"}])

    resolve(s, messages, [], [], [])

    [ev] = s["calendar_events"]
    assert (ev["source"], ev["external_id"]) == ("whatsapp", "w-55")  # same time: by Source name
    assert ev["ref_sources"] == {"g-100": ["gmail"], "w-55": ["whatsapp", "wilma"]}


def test_an_event_keeps_only_the_links_its_cited_messages_have():
    messages = [msg("gmail", "g-101", body="Route map: https://kilo.example.fi/retki/map.", subject="Retki"),
                msg("whatsapp", "wa-3", body="Pay at https://pay-kilo.example.com/login")]
    s = summary(events=[{"title": "Retki www.pay-kilo.example.com", "location": "Nuuksio (see pay-kilo.example.com)",
                         "description": "Map: https://kilo.example.fi/retki/map. Pay: https://pay-kilo.example.com/login",
                         "kid": None, "refs": ["g-101"]}])

    resolve(s, messages, [], [], [])

    [ev] = s["calendar_events"]
    assert (ev["title"], ev["location"]) == ("Retki ", "Nuuksio (see )")
    assert ev["description"] == "Map: https://kilo.example.fi/retki/map. Pay: "
    assert ev["kid"] is None


# ── Links

@pytest.mark.parametrize("text, allowed, kept", [
    ("See https://Kilo.example.fi/map.", "see https://kilo.example.fi/map", "See https://Kilo.example.fi/map."),
    ("Mail mailto:opettaja@koulu.fi today", "Write to MAILTO:opettaja@koulu.fi","Mail mailto:opettaja@koulu.fi today"),
    ("Mail mailto:x@evil.fi today", "", "Mail  today"),
    ("Pay at www.pay.example.com!", "", "Pay at !"),               # the trailing punctuation stays
    ("Eväät.Sadevaatteet, lupa.pdf", "", "Eväät.Sadevaatteet, lupa.pdf"),  # only looks like a domain
])
def test_keep_links_drops_links_the_allowed_text_lacks(text, allowed, kept):
    assert keep_links(text, allowed) == kept


# ── Earlier Briefs as the model sees them

def test_for_prompt_strips_what_the_program_derived_and_the_nights_people():
    earlier = [{"date": "2026-09-26", "third_parties": ["Maija Laine"], "per_kid": [
        {"kid": "Leo",
         "notices": [{"text": "closed", "refs": ["g-9"], "source": ["gmail"], "verified": True}, "a bare string"],
         "action_items": [{"what": "books", "refs": ["g-90"], "ref_sources": {"g-90": ["gmail"]},
                           "legacy": True}]},
        {"kid": "Mia"},
    ]}, {"date": "2026-09-25"}]

    assert for_prompt(earlier) == [
        {"date": "2026-09-26", "per_kid": [
            {"kid": "Leo", "notices": [{"text": "closed", "refs": ["g-9"]}, "a bare string"],
             "action_items": [{"what": "books", "refs": ["g-90"]}]},
            {"kid": "Mia", "notices": [], "action_items": []}]},
        {"date": "2026-09-25", "per_kid": []},
    ]
