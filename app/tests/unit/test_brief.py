"""The Brief and its run notes read from the archive and written back (brief.from_archive,
to_archive), and earlier Briefs as the model sees them. Archives written before the Brief was a
typed object load, and render as they did then, against golden files made by the version that
wrote them."""
from __future__ import annotations

import json
from datetime import timezone
from pathlib import Path

import pytest

from conftest import NOW, TZ, html_for_golden, ics_for_golden

from family_brief import render
from family_brief.actions import archive
from family_brief.brief import Brief, Citations, EarlierBrief, KidSection, Notice, RunNotes, from_archive, to_archive
from family_brief.brief_text import TEXT
from family_brief.collectors.base import Message
from family_brief.summarize import extract_calendar_events

DATA = Path(__file__).parent / "data"
STAMP = NOW.astimezone(timezone.utc)


def loaded(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


# ── Archives from before

@pytest.mark.parametrize("version", ["0.6", "0.2"])
def test_an_old_archive_renders_as_it_did(golden, version):
    data = loaded(f"archive-{version}.raw.json")
    brief, notes = from_archive(data["summary"])
    t = TEXT["zh"]
    events = extract_calendar_events(brief, TZ, t.untitled)
    night = render.Night(date="2026-09-27", tz=TZ, t=t, assistant="Claude", brief=brief,
                         coverage=notes.coverage or {}, created=data["calendar_created"],
                         ics_events=events, stamp=STAMP, incomplete=notes.incomplete)
    [mail] = render.brief(night, [render.Version(t, ["parent@example.com"], brief)], "parent@example.com", [])

    golden(f"archive_{version}.zh.txt", mail.text)
    golden(f"archive_{version}.zh.html", html_for_golden(mail.html))
    if events:
        golden(f"archive_{version}.zh.ics", ics_for_golden(mail.attachments[0][1]))
    messages = [Message.from_dict(m) for m in data["messages"]]
    golden(f"archive_{version}.zh.md",
           archive._render_markdown(t, "2026-09-27", brief, data["calendar_created"], messages))


def test_an_archive_is_written_back_with_the_keys_it_was_read_with():
    summary = loaded("archive-0.6.raw.json")["summary"]

    assert to_archive(*from_archive(summary)) == summary


def test_the_run_notes_are_read_from_their_underscored_keys():
    _, notes = from_archive(loaded("archive-0.6.raw.json")["summary"])

    assert (notes.error, notes.incomplete, notes.held_back) == (None, True, ["1103", "message:812", "1:3"])
    assert notes.citations == Citations(entries=6, unverified=0, legacy=0)
    assert notes.coverage is not None and notes.coverage["wilma"] == {"count": 2, "error": None}


def test_the_rule_based_fallback_keeps_its_error():
    brief, notes = from_archive({"per_kid": [], "calendar_events": [], "message_digest_cn": "⚠️ 今日 LLM 总结失败",
                                 "_llm_error": "boom"})

    assert (brief, notes) == (Brief("⚠️ 今日 LLM 总结失败"), RunNotes(error="boom"))
    assert to_archive(Brief("raw list"), RunNotes(error="boom", coverage={})) == {
        "per_kid": [], "calendar_events": [], "message_digest": "raw list", "_coverage": {}, "_llm_error": "boom"}


@pytest.mark.parametrize("summary", [None, [], {}], ids=["null", "a list", "empty"])
def test_a_summary_with_nothing_in_it_is_an_empty_brief(summary):
    assert from_archive(summary) == (Brief(), RunNotes())


# ── Earlier Briefs as the model sees them

def test_an_earlier_brief_for_the_prompt_lacks_what_the_program_derived_and_the_nights_people():
    earlier = [EarlierBrief.from_json(day) for day in [{"date": "2026-09-26", "third_parties": ["Maija Laine"], "per_kid": [
        {"kid": "Leo",
         "notices": [{"text": "closed", "refs": ["g-9"], "source": ["gmail"], "verified": True}, "a bare string"],
         "action_items": [{"what": "books", "refs": ["g-90"], "ref_sources": {"g-90": ["gmail"]},
                           "legacy": True}]},
        {"kid": "Mia"},
    ]}, {"date": "2026-09-25"}]]

    assert earlier[0].third_parties == ["Maija Laine"]
    assert earlier[0].kids[0].notices[0] == Notice("closed", refs=["g-9"], source=["gmail"], verified=True)
    assert [day.for_prompt() for day in earlier] == [
        {"date": "2026-09-26", "per_kid": [
            {"kid": "Leo", "notices": [{"text": "closed", "refs": ["g-9"]}, {"text": "a bare string"}],
             "action_items": [{"what": "books", "refs": ["g-90"]}]},
            {"kid": "Mia", "notices": [], "action_items": []}]},
        {"date": "2026-09-25", "per_kid": []},
    ]


def test_a_kid_section_without_a_kid_is_written_without_one():
    brief = Brief(kids=[KidSection(None, [Notice("closed")])])

    assert to_archive(brief, RunNotes())["per_kid"] == [{"notices": [{"text": "closed"}], "action_items": []}]
