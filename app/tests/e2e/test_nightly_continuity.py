"""The nightly `parent-recap run` across nights, end to end: quiet nights and what carries over, a
Gmail with no sender ticked, and catching up at sign-in with `run --if-missed`. Assertions are only
on what leaves the system: the email (text, HTML, attachments), the model command line, and
persisted state."""
from __future__ import annotations

import json
import logging
import sys
from datetime import timedelta

import pytest
import time_machine

from conftest import NOW, html_for_golden, msg, myclub_event

from nights import normal_night, payload_message_ids


# ── Quiet nights and continuity

def test_nothing_new_and_nothing_due_sends_nothing(harness):
    assert harness.run() == 0

    assert harness.sent == []
    assert harness.model_calls == []
    assert harness.state()["last_run_at"] is not None


def only_the_timetable(h) -> None:
    """A school evening whose only new Message is the next school day's ordinary timetable."""
    h.sources = {"wilma": [
        msg("wilma", "schedule:20260928", "2026-09-27T21:00:00+03:00",
            "[2026-09-28 Mon · tomorrow]\n— Mia —\n  08:15–09:45  Historia  @ 204",
            subject="Timetable for the next school day (from 2026-09-28)", chat="schedule"),
    ]}


def test_night_the_model_finds_nothing_to_say_about_sends_nothing(harness):
    # #385: the model returns no Digest, Notices, Action Items or events, so it counts as quiet.
    only_the_timetable(harness)
    harness.config["imessage"] = {"enabled": True, "recipients": ["+358401234567"]}

    assert harness.run() == 0

    assert len(harness.model_calls) == 1
    assert harness.sent == []
    assert harness.imessages == []
    assert harness.state()["last_run_at"] is not None
    # Its Messages still count as read, so the next evening doesn't send them to the model again.
    with time_machine.travel(NOW + timedelta(days=1), tick=False):
        assert harness.run() == 0
    assert len(harness.model_calls) == 1
    assert harness.sent == []


def test_dry_run_of_a_night_the_model_finds_nothing_to_say_about_sends_nothing(harness):
    only_the_timetable(harness)

    assert harness.run("--dry-run") == 0

    assert harness.sent == []
    assert not harness.state_path.exists()


def test_night_the_model_finds_nothing_to_say_about_still_sends_its_events(harness):
    only_the_timetable(harness)
    harness.sources["myclub"] = [myclub_event("mc-1", "FC Kilo P2017 vs HJK", "2026-10-03T07:00:00+00:00",
                                              "2026-10-03T08:30:00+00:00")]
    harness.config["google_calendar"] = {"mode": "ics", "ics_include_myclub": True}

    assert harness.run() == 0

    [email] = harness.sent
    assert "FC Kilo P2017 vs HJK" in email.text


def test_setup_preview_shows_a_night_the_model_finds_nothing_to_say_about(harness, capsys):
    only_the_timetable(harness)

    assert harness.run("--preview") == 0

    assert '"preview": "made"' in capsys.readouterr().out


def every_source_fails(h) -> None:
    h.sources = {
        "gmail": RuntimeError("b'[AUTHENTICATIONFAILED] Invalid credentials (Failure)'"),
        "myclub": OSError("[Errno 8] nodename nor servname provided, or not known"),
        "wilma": RuntimeError("wilma: request timed out"),
    }

    def whatsapp_without_permission():
        logging.getLogger("family_brief.collectors.whatsapp").error(
            "Cannot open ChatStorage.sqlite: [Errno 1] Operation not permitted")
        return []
    h.sources["whatsapp"] = whatsapp_without_permission


@pytest.mark.parametrize("language", ["en", "zh", "fi"])
def test_night_when_no_source_could_be_read_tells_the_parents(harness, golden, language):
    harness.config["summary_language"] = language
    every_source_fails(harness)

    assert harness.run() == 0

    [email] = harness.sent
    assert email.subject == "Parent Recap · 2026-09-27"
    assert email.to == ["parent@example.com", "partner@example.com"]
    golden(f"nothing_read.{language}.txt", email.text)
    golden(f"nothing_read.{language}.html", html_for_golden(email.html))
    assert harness.model_calls == []  # nothing to summarize


def test_night_when_no_source_could_be_read_reaches_imessage(harness):
    every_source_fails(harness)
    harness.config["imessage"] = {"enabled": True, "recipients": ["+358401234567"]}

    assert harness.run() == 0

    [(to, text)] = harness.imessages
    assert to == "+358401234567"
    assert "今晚所有信息源都没读到" in text


def test_night_when_no_source_could_be_read_speaks_each_recipients_language(harness):
    harness.config["summary_language"] = "zh"
    harness.config["email"]["to"] = ["parent@example.com", {"address": "partner@example.com", "language": "en"}]
    every_source_fails(harness)

    assert harness.run() == 0

    zh, en = harness.sent
    assert (zh.to, en.to) == (["parent@example.com"], ["partner@example.com"])
    assert "今晚所有信息源都没读到" in zh.text
    assert "No Source could be read tonight" in en.text


def test_night_when_no_source_could_be_read_says_so_even_if_the_model_finds_nothing_to_say(harness):
    # A near deadline asks the model, which drops it: silence would still look like a quiet night.
    harness.write_archive("2026-09-26", {"summary": {"per_kid": [
        {"kid": "Leo", "action_items": [{"what": "交图书馆的书", "by": "2026-09-28", "who": "爸爸"}]}]}})
    every_source_fails(harness)

    assert harness.run() == 0

    assert len(harness.model_calls) == 1
    [email] = harness.sent
    assert "Gmail" in email.text and "WhatsApp" in email.text


def test_dry_run_when_no_source_could_be_read_sends_nothing(harness):
    every_source_fails(harness)

    assert harness.run("--dry-run") == 0

    assert harness.sent == []
    assert not harness.state_path.exists()


def test_outage_over_three_nights_is_told_each_night_and_caught_up_after(harness):
    normal_night(harness)
    # Nothing due soon, which would bring a normal Brief with its coverage warning instead.
    harness.model_reply = {"per_kid": [], "calendar_events": [], "message_digest": "**Mia**\n- 周四远足"}
    night_0 = harness.sources
    assert harness.run() == 0

    for day in (1, 2, 3):
        every_source_fails(harness)
        with time_machine.travel(NOW + timedelta(days=day), tick=False):
            assert harness.run() == 0
    assert [e.subject for e in harness.sent[1:]] == [
        "Parent Recap · 2026-09-28", "Parent Recap · 2026-09-29", "Parent Recap · 2026-09-30"]
    assert all("今晚所有信息源都没读到" in e.text for e in harness.sent[1:])

    harness.sources = {**night_0, "gmail": night_0["gmail"] + [
        msg("gmail", "g-201", "2026-09-29T09:00:00+03:00", "Vanhempainilta on 8.10.",
            sender="office@kilo.example.fi", subject="Vanhempainilta")]}
    with time_machine.travel(NOW + timedelta(days=4), tick=False):
        assert harness.run() == 0

    for source in ("gmail", "wilma", "whatsapp"):
        # Each outage night keeps reaching back to night 0, the last one every Source was read.
        assert harness.lookback_hours[source] == [26, 26, 50, 74, 98]
    assert payload_message_ids(harness) == ["g-201"]


def test_run_of_some_sources_that_all_fail_sends_nothing(harness):
    # A run by hand with --sources didn't try the others, so it can't say nothing could be read.
    every_source_fails(harness)

    assert harness.run("--sources", "gmail,wilma") == 0

    assert harness.sent == []


def test_quiet_source_beside_a_failed_one_sends_nothing(harness):
    # Only a night where every Source failed says so on its own; the failed one catches up later.
    harness.sources["gmail"] = RuntimeError("socket error: EOF")

    assert harness.run() == 0

    assert harness.sent == []


# ── A Gmail with no sender ticked (#361)

# What the real Gmail collector says when it has no sender to search for, rather than read the
# whole inbox.
NO_SENDER = RuntimeError("gmail.allowlist_domains or gmail.allowlist_senders is empty — "
                         "refusing to scan all inbox.")


def test_gmail_with_no_sender_beside_another_source_only_sends_the_brief(harness):
    normal_night(harness, "en")
    harness.config["gmail"]["allowlist_domains"] = []
    harness.sources["gmail"] = NO_SENDER

    assert harness.run() == 0

    assert "gmail" not in harness.lookback_hours  # never read
    [email] = harness.sent
    coverage = "📥 Read tonight: MyClub 1 event · Wilma 1 message · WhatsApp 2 messages"
    assert email.text.endswith("\n\n" + coverage)


def test_gmail_with_no_sender_and_no_other_source_still_fails(harness):
    harness.config["summary_language"] = "en"
    harness.config["gmail"]["allowlist_domains"] = []
    harness.config["wilma"]["enabled"] = False
    harness.config["whatsapp"]["enabled"] = False
    del harness.config["kids"][0]["myclub_ical_url"]
    harness.sources["gmail"] = NO_SENDER

    assert harness.run() == 0

    [email] = harness.sent
    assert "No Source could be read tonight" in email.text and "Gmail" in email.text


def test_old_shape_archives_feed_continuity(harness):
    # Briefs archived by earlier versions, or on nights the model failed, have looser shapes.
    harness.write_archive("2026-09-24", {  # 0.2-era: no _coverage, message_digest key, odd action items
        "generated_at": "2026-09-24T21:03:11",
        "messages": [],
        "summary": {"per_kid": [
            {"kid": "Mia", "notices": ["足球训练改到周五"],
             "action_items": [{"what": "交班费", "by": "本周"}, "带手工材料"]},
            {"kid": "Leo"},
        ], "message_digest": "旧格式摘要"},
        "calendar_created": [],
    })
    harness.write_archive("2026-09-25", {  # the rule-based fallback on a failed model night
        "summary": {"per_kid": [], "calendar_events": [], "message_digest_cn": "⚠️ 今日 LLM 总结失败",
                    "_llm_error": "boom"},
    })
    harness.write_archive("2026-09-26", {  # due tomorrow, with a null summary alongside
        "summary": {"per_kid": [
            {"kid": "Leo", "notices": None,
             "action_items": [{"what": "交图书馆的书", "by": "2026-09-28T00:00:00", "who": "爸爸"},
                              {"what": "没写日期"}]},
        ]},
    })
    harness.write_archive("2026-09-23", "{not json")  # outside the window, and broken anyway
    harness.model_reply = {"per_kid": [{"kid": "Leo", "notices": [], "action_items": [
        {"what": "$reminder 交图书馆的书", "by": "2026-09-28", "who": "爸爸", "refs": []}]}],
        "calendar_events": [], "message_digest": ""}

    assert harness.run() == 0  # no new messages: the near deadline alone triggers a Brief

    assert harness.model_payload()["earlier_briefs"] == [
        {"date": "2026-09-24", "per_kid": [
            {"kid": "Kid A", "notices": ["足球训练改到周五"],
             "action_items": [{"what": "交班费", "by": "本周"}, "带手工材料"]}]},
        {"date": "2026-09-26", "per_kid": [
            {"kid": "Kid B", "notices": [],
             "action_items": [{"what": "交图书馆的书", "by": "2026-09-28T00:00:00", "who": "爸爸"},
                              {"what": "没写日期"}]}]},
    ]
    assert len(harness.sent) == 1


def test_broken_archive_is_skipped(harness):
    normal_night(harness)
    harness.write_archive("2026-09-26", "{not json")
    harness.write_archive("2026-09-25", {"summary": None})

    assert harness.run() == 0

    assert harness.model_payload()["earlier_briefs"] == []
    assert len(harness.sent) == 1


# ── Catching up at sign-in: Windows' sign-in task runs `run --if-missed` (#386)

def signing_in_ten_hours_after_the_brief_time(h, last_run_hours_ago: float | None) -> None:
    """NOW is the family signing in, ten hours after the Brief's time on the PC's own clock, which
    Task Scheduler keeps; the last run ended, caught up, `last_run_hours_ago`, if there was one."""
    normal_night(h, "en")
    evening = (NOW - timedelta(hours=10)).astimezone()
    h.config["schedule"] = {"daily_hour": evening.hour, "daily_minute": evening.minute}
    if last_run_hours_ago is not None:
        at = (NOW - timedelta(hours=last_run_hours_ago)).isoformat()
        h.state_path.parent.mkdir(parents=True, exist_ok=True)
        h.state_path.write_text(json.dumps({"last_run_at": at, "caught_up_at": at}))


def test_signing_in_after_the_evening_run_was_missed_sends_that_evenings_brief(harness):
    signing_in_ten_hours_after_the_brief_time(harness, last_run_hours_ago=34)

    assert harness.run("--if-missed") == 0

    [email] = harness.sent
    assert email.subject == "Parent Recap · 2026-09-27"
    assert harness.state()["last_run_at"] == "2026-09-27T18:00:00+00:00"


def test_one_catch_up_covers_several_missed_evenings(harness):
    signing_in_ten_hours_after_the_brief_time(harness, last_run_hours_ago=82)

    assert harness.run("--if-missed") == 0

    assert len(harness.sent) == 1
    assert harness.lookback_hours["gmail"] == [84]  # back to the last run, with its 2 hours' slack


def test_signing_in_after_the_evening_run_ran_sends_nothing(harness):
    signing_in_ten_hours_after_the_brief_time(harness, last_run_hours_ago=9.9)
    before = harness.state_path.read_text()

    assert harness.run("--if-missed") == 0

    assert harness.sent == [] and harness.model_calls == []
    assert harness.state_path.read_text() == before


def scheduled_on_windows(h, hours_ago: float) -> None:
    """Setup's Finish, or `schedule install`, on a Windows PC `hours_ago`, with no run since."""
    h.wake.settable = False
    with pytest.MonkeyPatch.context() as mp, time_machine.travel(NOW - timedelta(hours=hours_ago),
                                                                 tick=False):
        mp.setattr(sys, "platform", "win32")  # only for the install
        assert h.cli("schedule", "install") == 0


def test_signing_in_after_missing_the_first_evening_since_setup_sends_its_brief(harness):
    signing_in_ten_hours_after_the_brief_time(harness, last_run_hours_ago=None)
    scheduled_on_windows(harness, hours_ago=12)

    assert harness.run("--if-missed") == 0

    [email] = harness.sent
    assert email.subject == "Parent Recap · 2026-09-27"


def test_signing_in_before_the_first_evening_since_setup_sends_nothing(harness):
    signing_in_ten_hours_after_the_brief_time(harness, last_run_hours_ago=None)
    scheduled_on_windows(harness, hours_ago=8)

    assert harness.run("--if-missed") == 0

    assert harness.sent == [] and harness.model_calls == []
