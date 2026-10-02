"""A Recipient may read any language. `en` and `zh` have reviewed program text; for any other
the model translates the English program text once, and it is kept on the Mac (ADR 0004)."""
from __future__ import annotations

import copy
import dataclasses
import json
from datetime import timedelta

import pytest
import time_machine
from pydantic import ValidationError

from conftest import NOW, FailedCall, system_prompt_of
from test_nightly_run import ENGLISH_REPLY, FINNISH_REPLY, model_call_for_golden, normal_night
from test_weekend_picks import run_weekend_picks

from family_brief import ops
from family_brief.brief_text import EN, WEEKEND_TEXT

PARTNER_SV = {"address": "partner@example.com", "language": "sv"}


def swedish(prompt: str) -> dict:
    """A faked one-time translation of the program text: every text marked `sv:`."""
    def mark(value):
        if isinstance(value, str):
            return f"sv:{value}"
        if isinstance(value, list):
            return [mark(v) for v in value]
        return {k: mark(v) for k, v in value.items()}
    return {**mark(json.loads(prompt)), "language_name": "Swedish", "quotes": "”så här”"}


def swedish_partner(h) -> None:
    """The normal night; the first Recipient reads Chinese and the partner Swedish."""
    normal_night(h)
    h.config["email"]["to"] = ["parent@example.com", PARTNER_SV]
    # The program text, the Chinese Brief, and its translation (the English stands in for Swedish).
    h.model_reply = [swedish, h.model_reply, copy.deepcopy(ENGLISH_REPLY)]


def next_night(chinese_reply: dict) -> list[dict]:
    """The Chinese Brief and its translation the night after: no new Messages, so the same Brief
    without its calendar events, which would cite nothing read tonight."""
    return [{**chinese_reply, "calendar_events": []}, {**copy.deepcopy(ENGLISH_REPLY), "calendar_events": []}]


def test_recipient_without_a_reviewed_language_gets_program_text_in_it(harness, golden):
    swedish_partner(harness)

    assert harness.run() == 0

    zh, sv = harness.sent
    assert sv.to == ["partner@example.com"]
    assert "sv:✅ Action Items\n• " in sv.text
    assert "sv:📥 Read tonight: Gmail sv:2 messages · MyClub sv:1 event" in sv.text
    assert "<h3>sv:✅ Action Items</h3>" in sv.html
    # Dates come in the translated format, built from its own weekday and month names.
    assert "Sign the reissuvihko<small style='color:#666'> (Mia) · sv:by sv:sv:Mon 28 sv:Sep · sv:Mom · WhatsApp" \
           "</small>" in sv.html
    assert "Parent Recap sv:sv:Sun 27 sv:Sep" in sv.text and "sv:sv:Thu 1 sv:Oct 09:00" in sv.text
    assert "✅ 待办\n• " in zh.text
    program_text, _, translate = harness.model_calls
    golden("program_text.sv.model.txt", model_call_for_golden(program_text.argv, program_text.stdin))
    assert "from Simplified Chinese into Swedish" in system_prompt_of(translate)
    assert (harness.home / ".family" / "languages" / "sv.json").exists()


def numeric_dates(prompt: str) -> dict:
    """Program text whose dates write the month as a number and leave out the weekday."""
    return {**swedish(prompt), "date": "{day}/{month}", "date_time": "{day}/{month} kl. {hour}.{minute:02}"}


def test_translated_program_text_may_write_dates_with_other_parts_than_english(harness):
    swedish_partner(harness)
    harness.model_reply[0] = numeric_dates

    assert harness.run() == 0

    _, sv = harness.sent
    assert "Parent Recap 27/9" in sv.text and "1/10 kl. 9.00" in sv.text
    assert "sv:by 28/9" in sv.html


def test_stored_program_text_is_reused_without_another_model_call(harness):
    swedish_partner(harness)
    chinese_reply = harness.model_reply[1]
    assert harness.run() == 0

    harness.model_reply = next_night(chinese_reply)
    with time_machine.travel(NOW + timedelta(days=1), tick=False):
        assert harness.run() == 0

    assert len(harness.model_calls) == 3 + 2  # the second night only summarizes and translates
    assert "sv:✅ Action Items\n• " in harness.sent[-1].text


def unfillable_placeholder(prompt: str) -> dict:
    table = swedish(prompt)
    table["due"] = "sv:senast {date:d}"  # a date is text, not a number: this breaks on the night
    return table


def dropped_placeholder(prompt: str) -> dict:
    table = swedish(prompt)
    table["read_tonight"] = "sv:📥 Read tonight"  # lost {counts}
    return table


def dropped_time(prompt: str) -> dict:
    return {**swedish(prompt), "date_time": "{day}/{month}"}  # an event's start without its time


@pytest.mark.parametrize("program_text", [FailedCall("Error: 529 overloaded_error"), dropped_placeholder,
                                          unfillable_placeholder, dropped_time],
                         ids=["model fails", "loses a placeholder", "can't be filled in", "loses the time"])
def test_failed_program_text_translation_is_english_tonight_and_tried_again(harness, program_text):
    swedish_partner(harness)
    chinese_reply = harness.model_reply[1]
    harness.model_reply[0] = program_text

    assert harness.run() == 0

    zh, sv = harness.sent
    assert "✅ Action Items\n• " in sv.text and "sv:" not in sv.text
    assert "Parent Recap Sun 27 Sep" in sv.text and "by Mon 28 Sep" in sv.html  # English dates too
    assert not (harness.home / ".family" / "languages" / "sv.json").exists()
    # The translation still asks for Swedish, by its code.
    assert "from Simplified Chinese into the language whose code is sv" in system_prompt_of(harness.model_calls[2])

    harness.model_reply = [swedish, *next_night(chinese_reply)]
    with time_machine.travel(NOW + timedelta(days=1), tick=False):
        assert harness.run() == 0
    assert len(harness.model_calls) == 3 + 3
    assert "sv:✅ Action Items\n• " in harness.sent[-1].text


def test_a_stored_table_from_an_older_version_is_translated_again(harness):
    # Stored before the table gained entries such as `due`: it no longer checks out.
    old = swedish(json.dumps(dataclasses.asdict(EN)))
    del old["due"]
    stored = harness.home / ".family" / "languages" / "sv.json"
    stored.parent.mkdir(parents=True)
    stored.write_text(json.dumps(old, ensure_ascii=False))
    swedish_partner(harness)

    assert harness.run() == 0

    assert len(harness.model_calls) == 3  # the program text again, then the Brief and its translation
    assert json.loads(stored.read_text())["due"] == "sv:by {date}"
    assert "sv:✅ Action Items\n• " in harness.sent[1].text


def test_brief_text_stored_under_the_old_name_is_translated_again(harness):
    # Translated before the product was renamed: its fix lines still say FamilyBrief.
    old = swedish(json.dumps(dataclasses.asdict(EN)))
    old["fix_permission"] = "sv:Tell {assistant} “FamilyBrief can't read {source}” to fix it."
    old["fix_other"] = "sv:If it happens again, tell {assistant} “check FamilyBrief”."
    stored = harness.home / ".family" / "languages" / "sv.json"
    stored.parent.mkdir(parents=True)
    stored.write_text(json.dumps(old, ensure_ascii=False))
    swedish_partner(harness)

    assert harness.run() == 0

    assert len(harness.model_calls) == 3  # the program text again, then the Brief and its translation
    assert "keep the emoji, the Markdown (## and **) and the names Gmail" in system_prompt_of(harness.model_calls[0])
    assert "Parent Recap" in system_prompt_of(harness.model_calls[0])
    again = json.loads(stored.read_text())
    assert again["fix_permission"] == "sv:Tell {assistant} “Parent Recap can't read {source}” to fix it."
    assert "FamilyBrief can't" not in stored.read_text() and "check FamilyBrief" not in stored.read_text()


def test_the_model_writes_the_brief_in_a_first_recipient_language_without_a_review(harness):
    normal_night(harness)
    harness.config["email"]["to"] = [PARTNER_SV, "parent@example.com"]
    harness.model_reply = [swedish, copy.deepcopy(ENGLISH_REPLY), harness.model_reply]

    assert harness.run() == 0

    _, summarize, translate = harness.model_calls
    assert "Write every text value in the JSON in Swedish, but keep the key Finnish words" in system_prompt_of(summarize)
    assert "sv:Mon" in harness.model_prompt(1)  # the date reference uses its weekdays
    assert "from Swedish into Simplified Chinese" in system_prompt_of(translate)
    sv, zh = harness.sent
    assert "sv:📥 Read tonight" in sv.text and "📥 今晚读取" in zh.text
    assert "sv:## Digest" in (harness.archive_dir / "2026-09-27.md").read_text().splitlines()


def test_reviewed_finnish_replaces_a_stored_best_effort_table(harness):
    # A Mac that used Finnish before it was reviewed has a translated table of its own.
    stored = harness.home / ".family" / "languages" / "fi.json"
    stored.parent.mkdir(parents=True)
    stored.write_text(json.dumps(swedish(json.dumps(dataclasses.asdict(EN))), ensure_ascii=False))
    normal_night(harness, "fi")

    assert harness.run() == 0

    [email] = harness.sent
    assert "✅ Hoidettavat\n• " in email.text and "sv:" not in email.text
    assert len(harness.model_calls) == 1


def test_a_finnish_brief_has_no_finnish_words_to_keep(harness):
    normal_night(harness)
    harness.config["email"]["to"] = [{"address": "parent@example.com", "language": "fi"}, "partner@example.com"]
    # Finnish is reviewed, so there is no program text to translate.
    harness.model_reply = [copy.deepcopy(FINNISH_REPLY), harness.model_reply]

    assert harness.run() == 0

    summarize, translate = harness.model_calls
    assert "Write every text value in the JSON in Finnish\n" in system_prompt_of(summarize)
    assert "Keep the key Finnish words" in system_prompt_of(translate)  # the Chinese translation keeps them


def test_a_translation_into_finnish_has_no_finnish_words_to_keep(harness):
    normal_night(harness)
    harness.config["email"]["to"] = ["parent@example.com", {"address": "partner@example.com", "language": "fi"}]
    harness.model_reply = [harness.model_reply, copy.deepcopy(ENGLISH_REPLY)]

    assert harness.run() == 0

    summarize, translate = harness.model_calls
    assert "keep the key Finnish words as written" in system_prompt_of(summarize)  # the Chinese Brief keeps them
    instructions = system_prompt_of(translate)
    assert "into Finnish" in instructions and "Finnish words" not in instructions
    assert "\n2. Keep names, times" in instructions


def test_language_command_translates_the_program_text_once(harness, capsys):
    harness.model_reply = [swedish, swedish]  # the Brief's and Weekend Picks' text

    assert harness.cli("language", "sv") == 0
    assert harness.cli("language", "sv") == 0  # already there: no second call
    assert harness.cli("language", "zh") == 0

    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("sv: program text translated once, kept in ")
    assert out[2] == "zh: reviewed program text"
    assert len(harness.model_calls) == 2
    assert (harness.home / ".family" / "languages" / "sv.weekend.json").exists()


def test_language_command_fails_when_the_translation_does(harness):
    harness.model_reply = [FailedCall("Error: 529 overloaded_error")]

    assert harness.cli("language", "sv") == 1
    assert not (harness.home / ".family" / "languages" / "sv.json").exists()


def test_a_language_code_is_read_in_any_case_and_without_a_region(harness):
    normal_night(harness)
    harness.config["email"]["to"] = [{"address": "parent@example.com", "language": " ZH "}, "partner@example.com"]

    assert harness.run() == 0

    [email] = harness.sent  # the reviewed zh, the same as summary_language
    assert email.to == ["parent@example.com", "partner@example.com"]
    assert len(harness.model_calls) == 1

    harness.config["email"]["to"][0]["language"] = "en-GB"
    with pytest.raises(ValidationError, match="language code|pattern"):
        harness.run()


def test_language_command_refuses_what_is_not_a_language_code(harness):
    with pytest.raises(SystemExit):
        harness.cli("language", "../../evil")
    assert harness.model_calls == []


def test_doctor_says_which_languages_have_their_own_program_text(harness, monkeypatch, capsys):
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["wilma"]["enabled"] = harness.config["whatsapp"]["enabled"] = False
    harness.config["email"]["to"] = ["parent@example.com", PARTNER_SV]
    monkeypatch.setattr(ops, "launchctl_loaded", lambda: set())

    harness.cli("doctor", "--skip-llm")
    harness.model_reply = [swedish, swedish]
    assert harness.cli("language", "sv") == 0
    harness.cli("doctor", "--skip-llm")

    out = capsys.readouterr().out.splitlines()
    assert "✅ Language zh: reviewed program text" in out
    assert "⚠️  Language sv: English program text until its translation works (family-brief language sv)" in out
    assert any(line.startswith("✅ Language sv: program text translated once, kept in ") for line in out)


def test_doctor_counts_weekend_picks_text_once_they_are_on(harness, monkeypatch, capsys):
    swedish_partner(harness)
    assert harness.run() == 0  # the night translates the Brief's text only
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["wilma"]["enabled"] = harness.config["whatsapp"]["enabled"] = False
    harness.config["weekend_events"] = {"enabled": True}
    monkeypatch.setattr(ops, "launchctl_loaded", lambda: set())

    harness.cli("doctor", "--skip-llm")

    assert "⚠️  Language sv: English program text until its translation works (family-brief language sv)" \
        in capsys.readouterr().out.splitlines()


def test_weekend_picks_come_in_a_language_without_a_review(harness, monkeypatch, golden):
    # Weekend Picks' own text is a table of its own, translated once like the Brief's.
    harness.model_reply = [swedish, swedish, {"picks": [{"ext_id": "le-1", "rank": 1, "why": "dockteater"}]}]

    run_weekend_picks(harness, monkeypatch, "sv")

    [email] = harness.sent
    assert email.subject == "sv:Weekend Picks · 2026-09-26 ~ 2026-09-27"
    assert "sv:Sat 10/03 21:00" in email.text and "💡 dockteater" in email.text
    _, weekend_text, rank = harness.model_calls
    golden("program_text.sv.weekend.model.txt", model_call_for_golden(weekend_text.argv, weekend_text.stdin))
    assert "one-sentence reason, in Swedish" in system_prompt_of(rank)
    assert (harness.home / ".family" / "languages" / "sv.weekend.json").exists()


def test_weekend_picks_text_stored_under_the_old_name_is_translated_again(harness, monkeypatch):
    # Translated before the product was renamed: its calendar note still says FamilyBrief.
    folder = harness.home / ".family" / "languages"
    folder.mkdir(parents=True)
    (folder / "sv.json").write_text(json.dumps(swedish(json.dumps(dataclasses.asdict(EN))), ensure_ascii=False))
    old = swedish(json.dumps(dataclasses.asdict(WEEKEND_TEXT["en"])))
    old["calendar_note"] = "sv:[From FamilyBrief Weekend Picks.]"
    (folder / "sv.weekend.json").write_text(json.dumps(old, ensure_ascii=False))
    harness.model_reply = [swedish, {"picks": [{"ext_id": "le-1", "rank": 1, "why": "dockteater"}]}]

    run_weekend_picks(harness, monkeypatch, "sv")

    assert len(harness.model_calls) == 2  # the Weekend Picks text again, then the ranking
    [event] = harness.calendar.inserted
    assert "sv:[From Parent Recap Weekend Picks." in event["description"]
    assert "FamilyBrief" not in (folder / "sv.weekend.json").read_text()
