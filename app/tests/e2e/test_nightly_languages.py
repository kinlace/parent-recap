"""The nightly `parent-recap run` for Recipients in their own language, end to end. Assertions are
only on what leaves the system: the email (text, HTML, attachments), the model command line, and
persisted state."""
from __future__ import annotations

import copy
import json
from html import escape

import pytest

from conftest import FailedCall, assert_isolated_claude, html_for_golden, ics_for_golden

from nights import (ENGLISH_REPLY, FEEDBACK, PARTNER_EN, archived_summary, feedback_links,
                    model_call_for_golden, normal_night, two_languages)


# ── Recipients in their own language


def test_recipients_sharing_the_brief_language_get_one_email_and_no_extra_call(harness, golden):
    normal_night(harness)
    harness.config["email"]["to"] = [{"address": "parent@example.com", "language": "zh"},
                                     "partner@example.com"]  # a plain address uses summary_language

    assert harness.run() == 0

    [email] = harness.sent
    assert email.to == ["parent@example.com", "partner@example.com"]
    golden("normal_night.zh.txt", email.text)
    assert len(harness.model_calls) == 1


def test_second_recipient_gets_the_brief_translated_into_their_language(harness, golden):
    two_languages(harness)

    assert harness.run() == 0

    zh, en = harness.sent
    assert (zh.to, en.to) == (["parent@example.com"], ["partner@example.com"])
    # The first Recipient gets exactly the Brief a Chinese-only Household gets.
    golden("normal_night.zh.txt", zh.text)
    golden("normal_night.zh.html", html_for_golden(zh.html))
    golden("normal_night.zh.ics", ics_for_golden(zh.attachment(".ics")[1]))
    assert en.subject == zh.subject
    golden("two_languages.en.txt", en.text)
    golden("two_languages.en.html", html_for_golden(en.html))
    name, payload, _ = en.attachment(".ics")
    assert name == "parent-recap-2026-09-27.ics"
    golden("two_languages.en.ics", ics_for_golden(payload))

    summarize, translate = harness.model_calls
    golden("normal_night.zh.masked.model.txt", model_call_for_golden(summarize.argv, summarize.stdin))
    golden("two_languages.translate.masked.model.txt", model_call_for_golden(translate.argv, translate.stdin))
    assert_isolated_claude(translate)


def test_translation_shares_calendar_archive_and_feedback_text_with_the_original(harness):
    two_languages(harness)
    harness.config["google_calendar"] = {"mode": "google"}
    harness.authorize_google_calendar()
    harness.config["feedback"] = FEEDBACK

    assert harness.run() == 0

    # Each event is written to the shared calendar once, in the first Recipient's language.
    assert [e["summary"] for e in harness.calendar.inserted] == \
        ["3B 远足 Nuuksio", "钢琴课（改期）", "⚽ FC Kilo P2017 vs HJK"]
    zh, en = harness.sent
    assert '<a href="https://calendar.google.com/event?eid=gev1">3B 远足 Nuuksio</a>' in zh.html
    assert '<a href="https://calendar.google.com/event?eid=gev1">3B retki to Nuuksio</a>' in en.html
    assert '<a href="https://calendar.google.com/event?eid=gev3">⚽ FC Kilo P2017 vs HJK</a>' in en.html
    assert en.attachments == []
    # The archive, which feeds tomorrow's prompt, keeps only the original.
    summary = archived_summary(harness)
    assert summary["message_digest"].startswith("**Mia**\n- 周四远足，周二前在 Wilma 签同意书")
    assert summary["per_kid"][0]["action_items"][0]["what"] == "在 Wilma 签远足同意书"
    # Feedback from the English Brief reaches the Form in the original's words.
    en_links, zh_links = feedback_links(en.html), feedback_links(zh.html)
    assert [label for label, _, _ in en_links[:3]] == \
        ["❌ The Digest has a mistake", "⭐ Glad this was here", "❌ This is wrong"]
    assert [answers for _, answers, _ in en_links] == [answers for _, answers, _ in zh_links]
    assert en_links[1][1]["item_text"] == "在 Wilma 签远足同意书"


def test_translation_adds_no_link_to_an_event(harness):
    two_languages(harness)
    harness.model_reply[1]["calendar_events"][0]["description"] += " Book at https://pay-kilo.example.com"

    assert harness.run() == 0

    _zh, en = harness.sent
    ics = en.attachment(".ics")[1].decode().replace("\r\n ", "")  # unfolded
    assert "rain gear (gmail) Book at" in ics
    assert "pay-kilo" not in ics


FAILED_EN = "⚠️ Tonight's Brief couldn't be translated, so here it is as it was written."
FAILED_ZH = "⚠️ 今晚的日报没能翻译成功，下面是原文。"


def with_note(email, note: str) -> tuple[str, str]:
    """An email's text and HTML with the failed-translation note under the title."""
    title, rest = email.text.split("\n\n", 1)
    return (f"{title}\n\n{note}\n\n{rest}",
            email.html.replace("</h2>", f"</h2><p style='color:#a33'>{escape(note)}</p>", 1))


def edited_translation(edit) -> dict:
    """The English translation of the normal night, with one thing changed that a translation must keep."""
    reply = copy.deepcopy(ENGLISH_REPLY)
    edit(reply)
    return reply


@pytest.mark.parametrize("translation", [
    FailedCall("Error: stream disconnected before completion"),
    "Sorry, I can't translate this.",
    edited_translation(lambda r: r["per_kid"][0]["action_items"].pop(1)),
    edited_translation(lambda r: r["per_kid"][1]["action_items"][0].update(by="2026-10-01")),
    edited_translation(lambda r: r["per_kid"][1].update(kid="Mia")),
    edited_translation(lambda r: r["per_kid"][0]["notices"][1].update(refs=["1101"])),
    edited_translation(lambda r: r["calendar_events"][1].update(start="2026-10-01T17:30:00+03:00")),
], ids=["model fails", "no json", "drops an action item", "changes a due date", "changes a kid",
        "changes a ref", "moves an event"])
def test_failed_translation_sends_that_recipient_the_original(harness, golden, translation):
    two_languages(harness)
    harness.model_reply[1] = translation

    assert harness.run() == 0

    assert len(harness.model_calls) == 2  # checking the translation takes no model call
    zh, en = harness.sent
    assert en.to == ["partner@example.com"]
    # The original, with a line in English at the top saying why.
    golden("failed_translation.en.txt", en.text)
    golden("failed_translation.en.html", html_for_golden(en.html))
    golden("normal_night.zh.ics", ics_for_golden(en.attachment(".ics")[1]))
    assert (en.text, en.html) == with_note(zh, FAILED_EN)
    # The first Recipient's Brief and the archive are as on a night the translation works.
    golden("normal_night.zh.txt", zh.text)
    golden("normal_night.zh.html", html_for_golden(zh.html))
    assert archived_summary(harness)["per_kid"][0]["action_items"][1]["what"] == "签 reissuvihko"
    assert harness.state()["caught_up_at"] is not None


def test_failed_translation_leaves_the_shared_calendar_and_feedback_text_alone(harness):
    two_languages(harness)
    harness.model_reply[1] = edited_translation(lambda r: r["per_kid"][0]["action_items"].pop(1))
    harness.config["google_calendar"] = {"mode": "google"}
    harness.authorize_google_calendar()
    harness.config["feedback"] = FEEDBACK

    assert harness.run() == 0

    assert [e["summary"] for e in harness.calendar.inserted] == \
        ["3B 远足 Nuuksio", "钢琴课（改期）", "⚽ FC Kilo P2017 vs HJK"]
    zh, en = harness.sent
    assert (en.text, en.html) == with_note(zh, FAILED_EN)  # feedback links and Google links included
    assert FAILED_EN not in zh.text


def test_failed_translation_note_is_in_the_recipients_language(harness):
    normal_night(harness)
    harness.config["email"]["to"] = [PARTNER_EN, "parent@example.com"]
    harness.model_reply = [copy.deepcopy(ENGLISH_REPLY), FailedCall("Error: stream disconnected before completion")]

    assert harness.run() == 0

    en, zh = harness.sent
    assert (zh.text, zh.html) == with_note(en, FAILED_ZH)


def test_the_model_writes_in_the_first_recipients_language(harness, golden):
    normal_night(harness)  # summary_language zh, which the plain second address reads
    chinese_reply = harness.model_reply
    harness.config["email"]["to"] = [PARTNER_EN, "parent@example.com"]
    harness.model_reply = [copy.deepcopy(ENGLISH_REPLY), chinese_reply]

    assert harness.run() == 0

    en, zh = harness.sent
    assert (en.to, zh.to) == (["partner@example.com"], ["parent@example.com"])
    summarize, translate = harness.model_calls
    golden("normal_night.en.masked.model.txt", model_call_for_golden(summarize.argv, summarize.stdin))
    golden("normal_night.en.txt", en.text)
    golden("normal_night.en.html", html_for_golden(en.html))
    assert "from English into Simplified Chinese" in translate.argv[translate.argv.index("--system-prompt") + 1]
    # Translated back, it reads as the Chinese Brief the model would have written.
    golden("normal_night.zh.txt", zh.text)
    golden("normal_night.zh.html", html_for_golden(zh.html))
    # The archive keeps the original, so its headings are in the original's language too.
    assert "## Digest" in (harness.archive_dir / "2026-09-27.md").read_text().splitlines()


def test_recipients_sharing_a_language_share_one_translation(harness):
    two_languages(harness)
    harness.config["email"]["to"].append({"address": "grandma@example.com", "language": "en"})

    assert harness.run() == 0

    zh, en = harness.sent
    assert (zh.to, en.to) == (["parent@example.com"], ["partner@example.com", "grandma@example.com"])
    assert len(harness.model_calls) == 2


def test_dry_run_translates_nothing(harness):
    two_languages(harness)

    assert harness.run("--dry-run") == 0

    assert len(harness.model_calls) == 1 and harness.sent == []


def test_translation_translates_finnish_words_but_keeps_terms_in_brackets(harness):
    two_languages(harness)

    assert harness.run() == 0

    translate = harness.model_calls[1]
    instructions = translate.argv[translate.argv.index("--system-prompt") + 1]
    assert "write an everyday one (such as eväät, vaatetus, retki, kota) only in translation" in instructions
    assert "such as reissuvihko or vanhempainilta), keeps its Finnish in brackets" in instructions
    zh, en = harness.sent
    assert "签 reissuvihko" in zh.html and "Sign the reissuvihko" in en.html


def test_without_the_model_each_recipient_gets_the_raw_list_in_their_language(harness):
    two_languages(harness)
    harness.model_reply = [FailedCall("Error: authentication_error: OAuth token has expired")]

    assert harness.run() == 0

    zh, en = harness.sent
    assert zh.text.startswith("👨‍👩‍👧‍👦 Parent Recap 9月27日 周日\n\n⚠️ 今日 LLM 总结失败")
    assert en.text.startswith("👨‍👩‍👧‍👦 Parent Recap Sun 27 Sep\n\n⚠️ Tonight's Digest could not be written")
    assert "Read tonight: Gmail 2 messages" in en.text
    assert len(harness.model_calls) == 1  # no translation call to a model that just failed


@pytest.mark.parametrize("household_in_reply", ["全家", "Household"])  # kept, or translated anyway
def test_translation_puts_the_programs_own_words_in_the_target_language(harness, household_in_reply):
    # The Household label, the assignee and the re-reminder prefix come from the text table.
    two_languages(harness)
    harness.model_reply[0]["per_kid"].append({"kid": "全家", "notices": [], "action_items": [
        {"what": "（再提醒）交班费", "by": "2026-09-28", "who": "任一", "refs": []}]})
    translation = copy.deepcopy(ENGLISH_REPLY)
    translation["per_kid"].append({"kid": household_in_reply, "notices": [], "action_items": [
        {"what": "Pay the class fee", "by": "2026-09-28", "refs": []}]})
    harness.model_reply[1] = translation

    assert harness.run() == 0

    [*_, household] = json.loads(harness.model_prompt(1))["per_kid"]
    assert household == {"kid": "全家", "notices": [],
                         "action_items": [{"what": "交班费", "by": "2026-09-28", "refs": []}]}
    zh, en = harness.sent
    assert "（再提醒）交班费<small style='color:#666'> (全家) · by 9月28日 周一 · 任一</small>" in zh.html
    assert "(Reminder) Pay the class fee<small style='color:#666'> (Household) · by Mon 28 Sep · Either</small>" \
        in en.html
