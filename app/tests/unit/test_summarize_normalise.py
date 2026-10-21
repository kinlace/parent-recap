"""The model's reply put in the schema's shape (summarize.normalise), its calendar events read
(summarize.extract_calendar_events), and the rest of what summarize reads from a backend's output."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from family_brief import ai_filter, kid_placeholders
from family_brief.languages import FINNISH_TERMS
from family_brief.summarize import _busy_reason, _claude_reply, extract_calendar_events, normalise, system_prompt

HELSINKI = ZoneInfo("Europe/Helsinki")
COMPLETE = {"per_kid": [], "calendar_events": [], "message_digest": "ok"}


# ── A reply cut off by the model

@pytest.mark.parametrize("reply, repaired, incomplete", [
    pytest.param({"per_kid": []}, True, True, id="repaired-and-missing-keys"),
    pytest.param({"per_kid": []}, False, False, id="missing-keys-but-not-repaired"),
    pytest.param(COMPLETE, True, False, id="repaired-but-complete"),
    pytest.param({"per_kid": [], "calendar_events": [], "message_digest_cn": "旧"}, True, False,
                 id="digest-under-its-old-name"),
])
def test_a_repaired_reply_missing_a_top_level_key_is_incomplete(reply, repaired, incomplete):
    assert normalise(dict(reply), repaired).get("_incomplete", False) is incomplete


# ── The Digest

@pytest.mark.parametrize("digest, text", [
    pytest.param({"Mia": "- 周四远足", "Leo": "- 周三拍班级照", "Eero": " "}, "**Mia**\n- 周四远足\n\n**Leo**\n- 周三拍班级照",
                 id="dict-by-kid"),
    pytest.param(["**Mia**\n- 周四远足", "**Leo**\n- 周三拍班级照"], "**Mia**\n- 周四远足\n\n**Leo**\n- 周三拍班级照",
                 id="list-of-sections"),
    pytest.param(["- 周四远足", "", "- 周三拍班级照"], "- 周四远足\n- 周三拍班级照", id="list-of-lines"),
    pytest.param(None, "", id="null"),
    pytest.param(42, "42", id="number"),
])
def test_a_digest_in_any_shape_is_one_text(digest, text):
    assert normalise({**COMPLETE, "message_digest": digest}, False)["message_digest"] == text


def test_a_digest_under_its_old_name_is_read():
    assert normalise({"message_digest_cn": "旧"}, False)["message_digest"] == "旧"


# ── per_kid

@pytest.mark.parametrize("per_kid, kids", [
    pytest.param(None, [], id="null"),
    pytest.param([{"kid": "Mia"}, "Leo: nothing today", {"kid": "Leo"}], ["Mia", "Leo"], id="stray-string"),
    pytest.param({"Mia": {"notices": []}, "Leo": {}, "Eero": "nothing"}, ["Mia", "Leo"], id="keyed-by-kid"),
    pytest.param({"kid": "Mia", "notices": []}, ["Mia"], id="a-lone-entry"),
    pytest.param([{"kid": None}], [None], id="no-kid"),
])
def test_per_kid_is_a_list_of_kid_entries(per_kid, kids):
    out = normalise({**COMPLETE, "per_kid": per_kid}, False)["per_kid"]

    assert [k.get("kid") for k in out] == kids
    assert all(k["notices"] == [] and k["action_items"] == [] for k in out)


def test_empty_and_odd_items_are_dropped_and_the_rest_kept():
    kid = {"kid": "Mia",
           "notices": ["周四远足", {"text": "  "}, None, 42, {"text": ["a", "b"], "refs": ["w-1"]}],
           "action_items": [{"what": ""}, {"what": "交班费", "by": None, "who": None, "refs": ["g-1"]},
                            {"what": "签字", "by": "2026-09-28", "who": "妈妈"}]}

    [out] = normalise({**COMPLETE, "per_kid": [kid]}, False)["per_kid"]

    assert out["notices"] == [{"text": "周四远足"}, {"text": "42"}, {"text": "a\nb", "refs": ["w-1"]}]
    assert out["action_items"] == [{"what": "交班费", "refs": ["g-1"]},
                                   {"what": "签字", "by": "2026-09-28", "who": "妈妈"}]


def test_a_lone_item_stands_for_a_list_of_it():
    [out] = normalise({**COMPLETE, "per_kid": [{"kid": "Mia", "notices": "带雨衣", "action_items": None}]},
                      False)["per_kid"]

    assert (out["notices"], out["action_items"]) == ([{"text": "带雨衣"}], [])


def test_calendar_events_are_objects_with_text_fields():
    events = ["周五家长会", {"title": None, "start": "2026-10-01T09:00", "kid": "Mia", "location": ["Nuuksio"],
                         "refs": ["g-1"]}]

    assert normalise({**COMPLETE, "calendar_events": events}, False)["calendar_events"] == \
        [{"start": "2026-10-01T09:00", "kid": "Mia", "location": "Nuuksio", "refs": ["g-1"]}]


# ── Calendar events

def events(*evs: dict) -> list:
    return extract_calendar_events({"calendar_events": list(evs)}, "Europe/Helsinki", "(untitled)")


@pytest.mark.parametrize("start, local", [
    pytest.param("2026-11-05T09:00:00+03:00", datetime(2026, 11, 5, 9, 0), id="summer-offset-in-winter"),
    pytest.param("2026-11-05T09:00:00+02:00", datetime(2026, 11, 5, 9, 0), id="winter-offset"),
    pytest.param("2026-11-05T09:00:00", datetime(2026, 11, 5, 9, 0), id="no-offset"),
    pytest.param("2026-11-05T07:00:00Z", datetime(2026, 11, 5, 9, 0), id="utc"),
    pytest.param("2026-07-05T09:00:00+01:00", datetime(2026, 7, 5, 11, 0), id="another-zone"),
])
def test_an_events_wall_clock_time_is_kept_unless_another_zone_was_meant(start, local):
    [ev] = events({"title": "Koe", "start": start})

    assert ev.start == local.replace(tzinfo=HELSINKI) and ev.start.tzinfo == HELSINKI
    assert not ev.all_day


def test_an_event_gets_its_end_and_the_defaults_for_what_it_lacks():
    [ev] = events({"start": "2026-11-05T09:00", "end": "2026-11-05T10:30"})

    assert ev.end == datetime(2026, 11, 5, 10, 30, tzinfo=HELSINKI)
    assert (ev.title, ev.source, ev.external_id, ev.description, ev.location, ev.kid) == \
        ("(untitled)", "llm", "", "", None, None)


@pytest.mark.parametrize("end, last", [
    pytest.param(None, None, id="no-end"),
    pytest.param("2026-10-09", None, id="same-day"),
    pytest.param("2026-10-09T13:00:00", None, id="same-day-with-a-time"),
    pytest.param("2026-10-13T13:00:00", datetime(2026, 10, 13), id="later-day-with-a-time"),
])
def test_a_date_alone_is_an_all_day_event_ending_on_its_last_day(end, last):
    [ev] = events({"title": "Ulkoilupäivä", "start": " 2026-10-09 ", "end": end, "source": "wilma",
                   "external_id": 702, "kid": "Mia"})

    assert ev.all_day and ev.start == datetime(2026, 10, 9, tzinfo=HELSINKI)
    assert ev.end == (last.replace(tzinfo=HELSINKI) if last else None)
    assert (ev.source, ev.external_id, ev.kid) == ("wilma", "702", "Mia")


@pytest.mark.parametrize("bad", [{"title": "No start"}, {"start": "Thursday"},
                                 {"start": "2026-10-09T09:00", "end": "soon"}])
def test_an_event_with_a_bad_date_is_skipped(bad):
    assert events(bad, {"start": "2026-10-09"})[0].all_day
    assert len(events(bad)) == 0


def test_no_calendar_events_are_none():
    assert extract_calendar_events({"calendar_events": None}, "Europe/Helsinki", "") == []


# ── What a backend's output says

def test_claude_reply_gives_the_text_the_tokens_and_the_model_that_wrote_most():
    envelope = {"result": "{}", "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": None},
                "modelUsage": {"haiku": {"outputTokens": 1}, "opus": {"outputTokens": 4}, "sonnet": None}}

    assert _claude_reply(json.dumps(envelope)) == \
        ("{}", {"input": 10, "cache_write": 0, "cached_input": 0, "output": 5}, "opus")


def test_claude_reply_without_usage_has_none_and_an_object_result_is_its_json():
    assert _claude_reply(json.dumps({"result": {"a": "好"}})) == ('{"a": "好"}', None, None)


@pytest.mark.parametrize("returncode, stdout, stderr, prompt, busy", [
    pytest.param(0, "overloaded", "", None, None, id="succeeded"),
    pytest.param(1, "", "Overloaded, try again", None, "Overloaded", id="overloaded"),
    pytest.param(1, "", "error: 503 Service Unavailable", None, "error: 503", id="status"),
    pytest.param(1, "Rate limit hit; usage limit reached", "", None, None, id="wont-pass"),
    pytest.param(1, "", "prompt: we are at capacity tonight", "we are at capacity tonight", None,
                 id="the-prompt-echoed"),
    pytest.param(1, "", "Something else", None, None, id="other-failure"),
])
def test_busy_reason_says_when_waiting_may_help(returncode, stdout, stderr, prompt, busy):
    proc = subprocess.CompletedProcess([], returncode, stdout, stderr)

    assert _busy_reason(proc, prompt) == busy


# ── The instructions

@pytest.mark.parametrize("language, keeps_finnish", [("en", True), ("zh", True), ("fi", False)])
def test_the_instructions_ask_for_the_brief_in_its_language(language, keeps_finnish):
    prompt = system_prompt(language)

    assert "Write every text value in the JSON in " in prompt
    assert (FINNISH_TERMS in prompt) is keeps_finnish
    assert ai_filter.INSTRUCTION not in prompt


@pytest.mark.parametrize("masked, kids_masked, placeholders, kids", [
    (True, False, True, False), (True, True, True, True), (False, True, False, False)])
def test_the_instructions_explain_the_placeholders_the_payload_has(masked, kids_masked, placeholders, kids):
    prompt = system_prompt("en", masked=masked, kids_masked=kids_masked)

    assert (ai_filter.INSTRUCTION in prompt) is placeholders
    assert (kid_placeholders.INSTRUCTION in prompt) is kids
