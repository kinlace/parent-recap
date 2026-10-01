"""Weekend Picks: the ranking call goes through the same model process as the Brief."""
from __future__ import annotations

import json
from datetime import timedelta
from html import escape

import pytest

from conftest import NOW, FailedCall, assert_isolated_claude, system_prompt_of

from family_brief import weekend_pipeline
from family_brief.collectors.weekend_events import Candidate
from family_brief.config import Config
from family_brief.state import State


def candidate() -> Candidate:
    start = NOW + timedelta(days=6)
    return Candidate(ext_id="le-1", title="Puppet theatre", description="For kids 4-10",
                     start=start, end=start + timedelta(hours=1), price_eur=None, is_free=True,
                     location_name="Sello library", locality="Espoo", address="Leppävaarantori 1",
                     url="https://example.fi/le-1")


def test_rank_runs_claude_without_tools_from_an_empty_dir(harness):
    harness.model_reply = {"picks": [{"ext_id": "le-1", "rank": 1, "why": "free"}]}

    picks = weekend_pipeline.rank(Config.model_validate(harness.config), [candidate()])

    assert [p["ext_id"] for p in picks] == ["le-1"]
    [call] = harness.model_calls
    assert_isolated_claude(call)


# ── Weekend Picks in their first Recipient's language (summary_language for a plain address)

def run_weekend_picks(harness, monkeypatch, language: str, candidates: list[Candidate] | None = None,
                      dry_run: bool = False) -> None:
    harness.config["summary_language"] = language
    harness.config["weekend_events"] = {"enabled": True}
    harness.authorize_google_calendar()
    monkeypatch.setattr(weekend_pipeline.we, "collect", lambda cfg: candidates or [candidate()])
    assert weekend_pipeline.run(Config.model_validate(harness.config), dry_run=dry_run) == 0


@pytest.mark.parametrize("language, subject, heading, when, free, note", [
    ("en", "Weekend Picks · 2026-09-26 ~ 2026-09-27", "🎪 Weekend Picks (2026-09-26 ~ 2026-09-27)",
     "Sat 10/03 21:00", "Free", "[From FamilyBrief Weekend Picks. Delete it if you don't want it"),
    ("zh", "周末活动推荐 · 2026-09-26 ~ 2026-09-27", "🎪 周末活动推荐（2026-09-26 ~ 2026-09-27）",
     "周六 10/03 21:00", "免费", "[本条来自 FamilyBrief 周末活动推荐"),
    ("fi", "Viikonlopun vinkit · 2026-09-26 ~ 2026-09-27", "🎪 Viikonlopun vinkit (2026-09-26 ~ 2026-09-27)",
     "la 3.10. klo 21.00", "Maksuton", "[FamilyBriefin viikonlopun vinkki"),
])
def test_weekend_picks_follow_summary_language(harness, monkeypatch, language, subject, heading, when, free, note):
    harness.model_reply = {"picks": [{"ext_id": "le-1", "rank": 1, "why": "puppets"}]}

    run_weekend_picks(harness, monkeypatch, language)

    [email] = harness.sent
    assert email.subject == subject
    assert email.text.startswith(heading + "\n")
    assert f"⏰ {when}" in email.text
    assert f"💰 {free}" in email.text and f"💰 {free}" in email.html
    assert heading in email.html
    [event] = harness.calendar.inserted
    assert note in event["description"]
    [call] = harness.model_calls
    system = system_prompt_of(call)
    name = {"en": "English", "zh": "Simplified Chinese", "fi": "Finnish"}[language]
    assert f"one-sentence reason, in {name}" in system


@pytest.mark.parametrize("language, why", [
    ("en", "(Ranking failed, so these are the first few in order)"),
    ("zh", "（LLM 排序失败，按顺序展示前几条）"),
    ("fi", "(Vinkkien lajittelu epäonnistui, joten tässä ovat ensimmäiset vinkit alkuperäisessä järjestyksessä)"),
])
def test_failed_ranking_explains_itself_in_summary_language(harness, monkeypatch, language, why):
    harness.model_error = "boom"

    run_weekend_picks(harness, monkeypatch, language)

    assert f"💡 {why}" in harness.sent[0].text


# ── Each Weekend Picks Recipient in their own language (ADR 0004)

PICKS_ZH = {"picks": [{"ext_id": "le-1", "rank": 1, "why": "免费的木偶剧，适合小的"}]}
PICKS_EN = {"picks": [{"ext_id": "le-1", "rank": 1, "why": "A free puppet show for the younger one"}]}


def test_each_weekend_picks_recipient_gets_them_in_their_language(harness, monkeypatch):
    harness.config["email"]["weekend_to"] = ["parent@example.com",
                                             {"address": "partner@example.com", "language": "en"}]
    harness.model_reply = [PICKS_ZH, PICKS_EN]  # written once in Chinese, then translated

    run_weekend_picks(harness, monkeypatch, "zh")

    zh, en = harness.sent
    assert (zh.to, en.to) == (["parent@example.com"], ["partner@example.com"])
    assert zh.subject == "周末活动推荐 · 2026-09-26 ~ 2026-09-27"
    assert "周六 10/03 21:00" in zh.text and "💡 免费的木偶剧，适合小的" in zh.text
    assert en.subject == "Weekend Picks · 2026-09-26 ~ 2026-09-27"
    assert en.text.startswith("🎪 Weekend Picks (2026-09-26 ~ 2026-09-27)\n")
    assert "Sat 10/03 21:00" in en.text and "💰 Free" in en.html
    assert "💡 A free puppet show for the younger one" in en.text and "A free puppet show" in en.html
    rank, translate = harness.model_calls
    assert "one-sentence reason, in Simplified Chinese" in system_prompt_of(rank)
    assert "from Simplified Chinese into English" in system_prompt_of(translate)
    assert json.loads(harness.model_prompt(1)) == PICKS_ZH
    # The shared calendar and the archive get the picks once, as written.
    [event] = harness.calendar.inserted
    assert event["description"].startswith("免费的木偶剧，适合小的")
    assert "[本条来自 FamilyBrief 周末活动推荐" in event["description"]
    assert "免费的木偶剧" in (harness.home / "FamilyBrief" / "weekend_events" / "2026-09-26.md").read_text()


def test_weekend_picks_for_one_language_make_no_extra_call(harness, monkeypatch):
    harness.config["email"]["to"] = [{"address": "parent@example.com", "language": "zh"}, "partner@example.com"]
    harness.model_reply = PICKS_ZH

    run_weekend_picks(harness, monkeypatch, "zh")

    [email] = harness.sent
    assert email.to == ["parent@example.com", "partner@example.com"]
    assert len(harness.model_calls) == 1


def second_candidate() -> Candidate:
    start = NOW + timedelta(days=7)
    return Candidate(ext_id="le-2", title="Family floorball", description="Try floorball, ages 6-12",
                     start=start, end=start + timedelta(hours=1), price_eur=5.0, is_free=False,
                     location_name="Tapiola sports hall", locality="Espoo", address="Tapiolantie 1",
                     url="https://example.fi/le-2")


TWO_PICKS_ZH = {"picks": [{"ext_id": "le-1", "rank": 1, "why": "免费的木偶剧"},
                          {"ext_id": "le-2", "rank": 2, "why": "试试室内曲棍球"}]}


def two_language_weekend(h, monkeypatch, translation) -> None:
    h.config["email"]["weekend_to"] = ["parent@example.com", {"address": "partner@example.com", "language": "en"}]
    h.model_reply = [TWO_PICKS_ZH, translation]
    run_weekend_picks(h, monkeypatch, "zh", [candidate(), second_candidate()])


@pytest.mark.parametrize("translation", [
    FailedCall("Error: 529 overloaded_error"),
    {"picks": [{"ext_id": "le-1", "rank": 1, "why": "A free puppet show"}]},  # drops a pick
    {"picks": [{"ext_id": "le-2", "rank": 1, "why": "Try floorball"},
               {"ext_id": "le-1", "rank": 2, "why": "A free puppet show"}]},  # reorders them
    {"picks": [{"ext_id": "le-1", "rank": 1, "why": "A free puppet show"},
               {"ext_id": "le-9", "rank": 2, "why": "Try floorball"}]},  # changes one
], ids=["model fails", "drops a pick", "reorders the picks", "changes a pick"])
def test_failed_weekend_picks_translation_sends_the_original_with_a_note(harness, monkeypatch, translation):
    two_language_weekend(harness, monkeypatch, translation)

    zh, en = harness.sent
    note = "⚠️ This week's Weekend Picks couldn't be translated, so here they are as they were written."
    heading, rest = zh.text.split("\n", 1)
    assert en.text == f"{heading}\n{note}\n\n{rest}"
    assert en.html == zh.html.replace("</h2>", f"</h2><p style='color:#a33'>{escape(note)}</p>", 1)
    assert en.subject == zh.subject
    assert len(harness.model_calls) == 2  # checking the translation takes no model call


def test_without_the_model_each_recipient_gets_the_fallback_picks_in_their_language(harness, monkeypatch):
    harness.config["email"]["weekend_to"] = ["parent@example.com", {"address": "partner@example.com", "language": "en"}]
    harness.model_error = "boom"

    run_weekend_picks(harness, monkeypatch, "zh")

    zh, en = harness.sent
    assert "💡 （LLM 排序失败，按顺序展示前几条）" in zh.text
    assert "💡 (Ranking failed, so these are the first few in order)" in en.text
    assert len(harness.model_calls) == 1  # no translation call to a model that just failed


def test_dry_run_translates_no_weekend_picks(harness, monkeypatch):
    harness.config["email"]["weekend_to"] = ["parent@example.com", {"address": "partner@example.com", "language": "en"}]
    harness.model_reply = [PICKS_ZH]

    run_weekend_picks(harness, monkeypatch, "zh", dry_run=True)

    assert len(harness.model_calls) == 1 and harness.sent == []


def test_picks_without_reasons_need_no_translation(harness, monkeypatch):
    harness.config["email"]["weekend_to"] = ["parent@example.com", {"address": "partner@example.com", "language": "en"}]
    harness.model_reply = [{"picks": [{"ext_id": "le-1", "rank": 1}]}]

    run_weekend_picks(harness, monkeypatch, "zh")

    zh, en = harness.sent
    assert en.subject == "Weekend Picks · 2026-09-26 ~ 2026-09-27" and "💡" not in en.text
    assert len(harness.model_calls) == 1


# ── Odd picks from the model

def test_a_pick_for_an_invented_event_is_dropped_and_the_rest_keep_their_own_event(harness, monkeypatch):
    harness.model_reply = {"picks": [{"ext_id": "le-9", "rank": 1, "why": "Made up"},
                                     {"ext_id": "le-2", "rank": 2, "why": "Try floorball"}]}

    run_weekend_picks(harness, monkeypatch, "en", [candidate(), second_candidate()])

    [event] = harness.calendar.inserted
    assert event["summary"] == "🎪 Family floorball"
    private = event["extendedProperties"]["private"]
    assert (private["candidate_ext_id"], private["pick_rank"]) == ("le-2", "2")
    [email] = harness.sent
    assert "Made up" not in email.text and "💡 Try floorball" in email.text
    archived = json.loads((harness.home / "FamilyBrief" / "weekend_events" / "2026-09-26.json").read_text())
    assert [p["ext_id"] for p in archived["picks"]] == ["le-2"]


def test_each_calendar_event_carries_its_own_pick(harness):
    harness.authorize_google_calendar()
    cfg = Config.model_validate(harness.config)
    picks = [{"ext_id": "le-9", "rank": 1, "why": "Made up"}, {"ext_id": "le-2", "rank": 2, "why": "Try floorball"}]

    weekend_pipeline._write_picks_to_calendar(cfg, State(cfg.resolved_state_path()), {"le-2": second_candidate()},
                                              picks, (NOW.date(), NOW.date() + timedelta(days=1)))

    [event] = harness.calendar.inserted
    private = event["extendedProperties"]["private"]
    assert (private["candidate_ext_id"], private["pick_rank"]) == ("le-2", "2")


@pytest.mark.parametrize("odd", [
    {"ext_id": "le-1", "rank": None, "why": "No rank"},
    {"ext_id": "le-1", "why": "No rank"},
    {"ext_id": "le-1", "rank": "first", "why": "No rank"},
    {"ext_id": "le-1", "rank": "nan", "why": "No rank"},
    "le-1",
    ["le-1", 1],
    None,
], ids=["null rank", "missing rank", "rank that isn't a number", "NaN rank", "a string", "a list", "null"])
def test_an_odd_pick_is_skipped_and_the_email_still_goes_out(harness, monkeypatch, odd):
    harness.model_reply = {"picks": [odd, {"ext_id": "le-2", "rank": 1, "why": "Try floorball"}]}

    run_weekend_picks(harness, monkeypatch, "en", [candidate(), second_candidate()])

    [email] = harness.sent
    assert "No rank" not in email.text and "Puppet theatre" not in email.text
    assert "1. Family floorball" in email.text
    [event] = harness.calendar.inserted
    assert event["extendedProperties"]["private"]["candidate_ext_id"] == "le-2"


def test_picks_that_arent_a_list_send_an_empty_email_rather_than_crash(harness, monkeypatch):
    harness.model_reply = {"picks": {"ext_id": "le-1", "rank": 1}}

    run_weekend_picks(harness, monkeypatch, "en")

    [email] = harness.sent
    assert "Puppet theatre" not in email.text


def test_picks_are_ordered_by_rank_whether_written_as_numbers_or_strings(harness, monkeypatch):
    harness.model_reply = {"picks": [{"ext_id": "le-2", "rank": "2", "why": "Try floorball"},
                                     {"ext_id": "le-1", "rank": 1, "why": "Puppets"}]}

    run_weekend_picks(harness, monkeypatch, "en", [candidate(), second_candidate()])

    text = harness.sent[0].text
    assert text.index("Puppet theatre") < text.index("Family floorball")


def test_weekend_picks_translation_keeps_finnish_names(harness, monkeypatch):
    harness.config["email"]["weekend_to"] = ["parent@example.com", {"address": "partner@example.com", "language": "en"}]
    harness.model_reply = [PICKS_ZH, PICKS_EN]

    run_weekend_picks(harness, monkeypatch, "zh")

    instructions = system_prompt_of(harness.model_calls[1])
    assert "2. Keep Finnish event, venue and place names as written" in instructions
    assert "Brief" not in instructions
