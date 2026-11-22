"""The Week Ahead on Sunday's evening Brief, end to end (#1037, ADR 0053): made from the archive,
tonight's Brief and tonight's Sources without a model call, each thing once, only on a Sunday,
sent on a quiet Sunday too, and translated in the translation call that already runs, with the
earlier evenings' Third Parties as placeholders. The pinned "now" (2026-09-27) is a Sunday.
Assertions are on what leaves the system: the email, the model's prompts."""
from __future__ import annotations

import json
from datetime import timedelta

import time_machine

from conftest import NOW
from nights import PARTNER_EN, caught_up_last_night
from test_source_failures import one_new_message, the_model_says_something  # noqa: F401 (autouse)
from test_wilma_source import EXAM, exams

# An evening a week ago whose Brief reached the parents: an Action Item due this Friday, a
# re-reminder of it, and the model's entry for a history exam on Thursday.
LAST_WEEK = "2026-09-20"
TEACHER = "Maija Laine"


def earlier_brief(delivered: bool = True) -> dict:
    return {
        "messages": [{"source": "gmail", "external_id": "801", "sender": f"{TEACHER} <maija.laine@kilo.example.fi>",
                      "subject": "Retki", "body": "...", "timestamp": "2026-09-20T09:00:00+03:00"}],
        "summary": {
            "per_kid": [{"kid": "Mia", "notices": [], "action_items": [
                {"what": f"回复 {TEACHER} 远足的事", "by": "2026-10-02", "refs": ["801"]},
                {"what": f"（再提醒）回复 {TEACHER} 远足的事", "by": "2026-10-02", "refs": ["801"]}]}],
            "calendar_events": [{"kid": "Mia", "title": "历史考试（Historia）", "start": "2026-10-01",
                                 "refs": ["801"], "source": "gmail", "external_id": "801"}],
            "message_digest": "",
        },
        "calendar_created": [],
        "delivered": delivered,
    }


def week_part(text: str, heading: str) -> str:
    """The Week Ahead of a plain-text Brief, from its heading to the blank line after it."""
    return text[text.index(heading):].split("\n\n")[0]


def a_quiet_sunday(h) -> None:
    """A Household already running in Chinese, with nothing new tonight and last week's Brief."""
    h.config["summary_language"] = "zh"
    caught_up_last_night(h)
    h.write_archive(LAST_WEEK, earlier_brief())


def test_a_quiet_sunday_sends_its_week_ahead_without_asking_the_model(harness, golden):
    a_quiet_sunday(harness)

    assert harness.run() == 0

    assert harness.model_calls == []
    [email] = harness.sent
    golden("week_ahead.zh.txt", week_part(email.text, "🗓️ 下周安排:"))
    # Due Friday once, though two evenings told it.
    assert email.text.count("远足的事") == 1


def test_the_week_ahead_is_only_on_sunday(harness):
    a_quiet_sunday(harness)

    with time_machine.travel(NOW + timedelta(days=1), tick=False):  # Monday
        assert harness.run() == 0

    assert harness.sent == []


def test_a_brief_that_never_reached_the_parents_adds_nothing(harness):
    a_quiet_sunday(harness)
    harness.write_archive(LAST_WEEK, earlier_brief(delivered=False))

    assert harness.run() == 0

    assert harness.sent == []


def test_an_archived_exam_and_tonights_wilma_exam_show_once_as_wilma_has_it(harness):
    a_quiet_sunday(harness)
    one_new_message(harness, "wilma")
    harness.source_fakes["wilma"].answers = {("exams",): exams({**EXAM, "date": "2026-10-01"})}

    assert harness.run() == 0

    [email] = harness.sent
    week = week_part(email.text, "🗓️ 下周安排:")
    assert "Koe: Historia – Luvut 3–5" in week
    assert "历史考试" not in week
    assert week.count("(Mia)") == 2  # the exam and the reply


def test_a_moved_wilma_exam_shows_on_its_new_day(harness):
    a_quiet_sunday(harness)
    harness.write_archive(LAST_WEEK, {**earlier_brief(), "summary": {"per_kid": [], "message_digest": ""}})
    one_new_message(harness, "wilma")
    harness.source_fakes["wilma"].answers = {("exams",): exams({**EXAM, "date": "2026-09-30"})}

    assert harness.run() == 0

    week = week_part(harness.sent[0].text, "🗓️ 下周安排:")
    assert week.splitlines()[1:] == ["9月30日 周三", "• Koe: Historia – Luvut 3–5 · Kirjan luvut 3–5 (Mia)"]


def test_the_week_is_translated_in_the_translation_call_with_earlier_people_as_placeholders(harness):
    a_quiet_sunday(harness)
    harness.config["email"]["to"] = ["parent@example.com", PARTNER_EN]

    def translation(prompt: str) -> dict:
        week = json.loads(prompt)["week_ahead"]
        return {"per_kid": [], "calendar_events": [], "message_digest": "",
                "week_ahead": [{"text": f"EN: {w['text']}"} for w in week]}
    harness.model_reply = [translation]

    assert harness.run() == 0

    [call] = harness.model_calls  # the translation alone: the Brief itself asked no model
    assert "week_ahead" in call.system_prompt
    assert TEACHER not in call.prompt and "maija.laine" not in call.prompt
    zh, en = harness.sent
    assert f"EN: 回复 {TEACHER} 远足的事 (Mia)" in week_part(en.text, "🗓️ Next week:")
    assert f"• ✅ 回复 {TEACHER} 远足的事 (Mia)" in week_part(zh.text, "🗓️ 下周安排:")
