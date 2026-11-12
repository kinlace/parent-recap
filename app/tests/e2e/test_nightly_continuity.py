"""The nightly `parent-recap run` across nights, end to end: quiet nights and what carries over, a
Gmail with no sender ticked, and catching up a missed evening, at sign-in with `run --if-missed` or
when the scheduler starts the run late, only within 10 hours and in the Household's zone.
Assertions are only on what leaves the system: the email (text, HTML, attachments), the model
command line, persisted state, and the log line a skipped catch-up leaves."""
from __future__ import annotations

import json
import time
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
import time_machine

from conftest import NOW, html_for_golden, msg, myclub_event, without_unread_note
from fake_system import WINDOWS

from family_brief.actions import archive
from family_brief.system import jobs

from nights import normal_night, payload_message_ids, two_languages


# ── Quiet nights and continuity

def test_nothing_new_and_nothing_due_sends_nothing(harness):
    assert harness.run() == 0

    assert harness.sent == []
    assert harness.model_calls == []
    assert harness.state()["last_run_at"] is not None


def only_the_timetable(h) -> None:
    """A school evening whose only new Message is the next school day's ordinary timetable."""
    h.sources = {}
    h.source_fakes["wilma"].answers[("schedule", "list")] = {"students": [{"student": {"name": "Mia"}, "lessons": [
        {"date": "2026-09-28", "start": "08:15", "end": "09:45", "subject": "Historia", "room": "204"}]}]}


def test_night_the_model_finds_nothing_to_say_about_sends_nothing(harness):
    # #385: the model returns no Digest, Notices, Action Items or events, so it counts as quiet.
    only_the_timetable(harness)
    harness.config["imessage"] = {"enabled": True, "recipients": ["+358401234567"]}

    assert harness.run() == 0

    assert len(harness.model_calls) == 1
    assert harness.sent == []
    assert harness.imessages == []
    assert harness.state()["last_run_at"] is not None
    # Its Messages still count as read, so a run later that evening doesn't send them to the model
    # again (the next evening has the next day's timetable).
    with time_machine.travel(NOW + timedelta(hours=1), tick=False):
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


def test_night_with_only_a_myclub_event_tomorrow_tells_it_in_the_digest(harness):
    # #734: the rule that tells tomorrow's Source events is the model's, so such a night asks it.
    harness.sources = {"myclub": [myclub_event("mc-1", "Football training", "2026-09-28T15:15:00+00:00",
                                               "2026-09-28T16:45:00+00:00")]}
    harness.model_reply = {"per_kid": [], "calendar_events": [],
                           "message_digest": "**Mia**\n- 9月28日 周一 18:15 足球训练，Leppävaara field"}

    assert harness.run() == 0

    [call] = harness.model_calls
    assert "Football training" in call.prompt
    [email] = harness.sent
    assert "9月28日 周一 18:15 足球训练，Leppävaara field" in email.text


def test_night_with_only_later_myclub_events_asks_no_model(harness):
    harness.sources = {"myclub": [myclub_event("mc-1", "Football training", "2026-09-29T15:15:00+00:00",
                                               "2026-09-29T16:45:00+00:00")]}

    assert harness.run() == 0

    assert harness.model_calls == []


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="time_machine sets the computer's zone with tzset")
def test_the_model_s_tomorrow_is_the_household_s_on_a_computer_in_another_zone(harness):
    # A hosted computer keeps UTC (a ZoneInfo destination sets it): 01:00 on the 28th in Helsinki
    # is still the 27th there.
    harness.sources = {"myclub": [myclub_event("mc-1", "Football training", "2026-09-29T15:15:00+00:00",
                                               "2026-09-29T16:45:00+00:00")]}
    with time_machine.travel((NOW + timedelta(hours=4)).astimezone(ZoneInfo("UTC")), tick=False):
        assert harness.run() == 0

    assert harness.model_payload()["date_reference"]["tomorrow"].startswith("2026-09-29")


def test_setup_preview_shows_a_night_the_model_finds_nothing_to_say_about(harness, capsys):
    only_the_timetable(harness)

    assert harness.run("--preview") == 0

    assert '"preview": "made"' in capsys.readouterr().out


def every_source_fails(h) -> None:
    h.sources = {
        "gmail": RuntimeError("b'[AUTHENTICATIONFAILED] Invalid credentials (Failure)'"),
        "myclub": OSError("[Errno 8] nodename nor servname provided, or not known"),
        "wilma": RuntimeError("wilma: request timed out"),
        "whatsapp": PermissionError(1, "Operation not permitted"),
    }


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
        msg("gmail", "1201", "2026-09-29T09:00:00+03:00", "Vanhempainilta on 8.10.",
            sender="office@kilo.example.fi", subject="Vanhempainilta")]}
    with time_machine.travel(NOW + timedelta(days=4), tick=False):
        assert harness.run() == 0

    for source in ("gmail", "wilma", "whatsapp"):
        # Each outage night keeps reaching back to night 0, the last one every Source was read.
        assert harness.lookback_hours[source] == [26, 26, 50, 74, 98]
    assert payload_message_ids(harness) == ["1201"]


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

def test_gmail_with_no_sender_beside_another_source_is_not_read(harness):
    normal_night(harness, "en")
    harness.config["gmail"]["allowlist_domains"] = []

    assert harness.run() == 0

    assert "gmail" not in harness.lookback_hours  # never read
    [email] = harness.sent
    coverage = "📥 Read tonight: MyClub 1 event · Wilma 1 message · WhatsApp 2 messages"
    assert without_unread_note(email.text).endswith("\n\n" + coverage)


def test_gmail_with_no_sender_and_no_other_source_still_fails(harness):
    harness.config["summary_language"] = "en"
    harness.config["gmail"]["allowlist_domains"] = []
    harness.config["wilma"]["enabled"] = False
    harness.config["whatsapp"]["enabled"] = False
    del harness.config["kids"][0]["myclub_ical_url"]

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

    # The model gets them in today's shape: a bare string is an entry with that text.
    assert harness.model_payload()["earlier_briefs"] == [
        {"date": "2026-09-24", "per_kid": [
            {"kid": "Kid A", "notices": [{"text": "足球训练改到周五"}],
             "action_items": [{"what": "交班费", "by": "本周"}, {"what": "带手工材料"}]}]},
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

CAUGHT_UP = ("💤 Your computer was off at the last Brief time, so this Brief was sent when it came "
             "back on. It covers everything since then.")


def signing_in_after_the_brief_time(h, last_run_hours_ago: float | None, hours: float = 9) -> None:
    """NOW is the family signing in, `hours` after the Brief's time on the PC's own clock, which
    Task Scheduler keeps; the last run ended, caught up, `last_run_hours_ago`, if there was one."""
    normal_night(h, "en")
    evening = (NOW - timedelta(hours=hours)).astimezone()
    h.config["schedule"] = {"daily_hour": evening.hour, "daily_minute": evening.minute}
    if last_run_hours_ago is not None:
        at = (NOW - timedelta(hours=last_run_hours_ago)).isoformat()
        h.state_path.parent.mkdir(parents=True, exist_ok=True)
        h.state_path.write_text(json.dumps({"last_run_at": at, "caught_up_at": at}))


def test_signing_in_after_the_evening_run_was_missed_sends_a_brief_that_says_it_catches_up(harness):
    signing_in_after_the_brief_time(harness, last_run_hours_ago=33)

    assert harness.run("--if-missed") == 0

    [email] = harness.sent
    assert email.subject == "Parent Recap · 2026-09-27"
    assert email.text.split("\n")[2] == CAUGHT_UP  # under the heading, before the Brief
    assert harness.state()["last_run_at"] == "2026-09-27T18:00:00+00:00"


def test_one_catch_up_covers_several_missed_evenings(harness):
    signing_in_after_the_brief_time(harness, last_run_hours_ago=81)

    assert harness.run("--if-missed") == 0

    assert len(harness.sent) == 1
    assert harness.lookback_hours["gmail"] == [83]  # back to the last run, with its 2 hours' slack


def test_signing_in_ten_hours_after_a_missed_evening_leaves_it_to_the_next_evening_run(harness, caplog):
    signing_in_after_the_brief_time(harness, last_run_hours_ago=34, hours=10)
    before = harness.state_path.read_text()

    with caplog.at_level("INFO", logger="family_brief"):
        assert harness.run("--if-missed") == 0

    assert harness.sent == [] and harness.model_calls == []
    assert harness.state_path.read_text() == before
    assert not harness.state_path.with_name("last-run.json").exists()  # not a run that failed
    assert [r.getMessage() for r in caplog.records if "left to the next evening run" in r.getMessage()] == [
        "The evening run was missed 10 hours ago, too long ago to catch up (10 hours or more), so it "
        "is left to the next evening run, which covers it"]


def test_signing_in_after_the_evening_run_ran_sends_nothing(harness):
    signing_in_after_the_brief_time(harness, last_run_hours_ago=8.9)
    before = harness.state_path.read_text()

    assert harness.run("--if-missed") == 0

    assert harness.sent == [] and harness.model_calls == []
    assert harness.state_path.read_text() == before


def scheduled_on_windows(h, hours_ago: float) -> None:
    """Setup's Finish, or `schedule install`, on a Windows PC `hours_ago`, with no run since."""
    h.wake.settable = False
    with pytest.MonkeyPatch.context() as mp, time_machine.travel(NOW - timedelta(hours=hours_ago),
                                                                 tick=False):
        mp.setattr(h, "system", WINDOWS)  # only for the install
        assert h.cli("schedule", "install") == 0


def test_signing_in_after_missing_the_first_evening_since_setup_sends_its_brief(harness):
    signing_in_after_the_brief_time(harness, last_run_hours_ago=None)
    scheduled_on_windows(harness, hours_ago=12)

    assert harness.run("--if-missed") == 0

    [email] = harness.sent
    assert email.subject == "Parent Recap · 2026-09-27"


def test_signing_in_before_the_first_evening_since_setup_sends_nothing(harness):
    signing_in_after_the_brief_time(harness, last_run_hours_ago=None)
    scheduled_on_windows(harness, hours_ago=8)

    assert harness.run("--if-missed") == 0

    assert harness.sent == [] and harness.model_calls == []


# ── A Brief sent once, and caught up in the Household's zone (#776)

def last_run_hours_before_now(h, hours: float) -> None:
    at = (NOW - timedelta(hours=hours)).isoformat()
    h.state_path.parent.mkdir(parents=True, exist_ok=True)
    h.state_path.write_text(json.dumps({"last_run_at": at, "caught_up_at": at}))


def test_a_run_that_sent_the_brief_and_then_failed_isnt_caught_up_the_next_day(harness, monkeypatch):
    normal_night(harness, "en")
    last_run_hours_before_now(harness, 24)
    def disk_full(*_a: object) -> None:
        raise OSError("disk full")
    monkeypatch.setattr(archive, "write", disk_full)

    with pytest.raises(OSError, match="disk full"):
        harness.run()

    assert len(harness.sent) == 1
    assert json.loads(harness.state_path.with_name("last-run.json").read_text())["exit"] == 1
    # Signing in on Windows the next day at 19:00, before that evening's run.
    with time_machine.travel(NOW + timedelta(hours=22), tick=False):
        assert harness.run("--if-missed") == 0
    assert len(harness.sent) == 1 and len(harness.model_calls) == 1


# On a computer set to Karachi's zone, 21:00 there is 19:00 in the Household's, Helsinki.
EARLY_IN_KARACHI = NOW.replace(tzinfo=ZoneInfo("Asia/Karachi"))
needs_tzset = pytest.mark.skipif(not hasattr(time, "tzset"),
                                 reason="time_machine sets the computer's zone with tzset")


@needs_tzset
def test_a_missed_evening_is_caught_up_within_ten_hours_of_it_in_the_household_s_zone(harness):
    # Last night's 21:00 in Helsinki went by with no run. Signing in at 06:30 there, 08:30 on the
    # computer's own clock, is 9.5 hours after it, and 11.5 after 21:00 in Karachi.
    normal_night(harness, "en")
    last_run_hours_before_now(harness, 24)

    with time_machine.travel(EARLY_IN_KARACHI + timedelta(hours=11, minutes=30), tick=False):
        assert harness.run("--if-missed") == 0

    [email] = harness.sent
    assert CAUGHT_UP in email.text


@needs_tzset
def test_signing_in_early_on_a_computer_in_another_zone_after_the_evening_ran_sends_nothing(harness):
    # 21:00 on its own clock has gone by, but the Household's evening is still two hours away.
    normal_night(harness, "en")
    last_run_hours_before_now(harness, 0)

    with time_machine.travel(EARLY_IN_KARACHI + timedelta(days=1, minutes=1), tick=False):
        assert harness.run("--if-missed") == 0

    assert harness.sent == [] and harness.model_calls == []


# ── A late start by the scheduler catches up only within 10 hours (#894): launchd's after a wake
# on macOS, and on Windows Task Scheduler's start of a missed task (jobs.SCHEDULED)

CAUGHT_UP_IN = {
    "en": CAUGHT_UP,
    "zh": "💤 上次日报时间电脑没开，所以这份是开机后补发的，包含从那以后的消息。",
    "fi": "💤 Tietokone ei ollut päällä edellisen koosteen aikaan, joten tämä kooste lähetettiin, kun se "
          "taas käynnistyi. Siinä on kaikki sen jälkeen tullut.",
}


@pytest.fixture
def by_the_scheduler(monkeypatch):
    """The run is the evening job's, which the system's scheduler started."""
    monkeypatch.setenv(jobs.SCHEDULED, "1")


def started_late(h, hours: float, last_run_hours_ago: float, language: str = "en") -> None:
    """NOW is `hours` after the Brief's time, which went by with no run: the last one ended
    `last_run_hours_ago`."""
    signing_in_after_the_brief_time(h, last_run_hours_ago, hours)
    normal_night(h, language)


@pytest.mark.parametrize("language", ["en", "zh", "fi"])
def test_a_late_start_by_the_scheduler_sends_a_brief_that_says_it_catches_up(harness, by_the_scheduler,
                                                                             language):
    started_late(harness, hours=9.9, last_run_hours_ago=34, language=language)

    assert harness.run() == 0

    [email] = harness.sent
    assert email.text.split("\n")[2] == CAUGHT_UP_IN[language]  # under the heading, before the Brief
    assert CAUGHT_UP_IN[language] in email.html


def test_a_catch_up_says_so_in_each_recipient_s_language(harness, by_the_scheduler):
    started_late(harness, hours=3, last_run_hours_ago=27)
    two_languages(harness)

    assert harness.run() == 0

    by_address = {email.to[0]: email for email in harness.sent}
    assert by_address["parent@example.com"].text.split("\n")[2] == CAUGHT_UP_IN["zh"]
    assert by_address["partner@example.com"].text.split("\n")[2] == CAUGHT_UP_IN["en"]


def test_a_start_by_the_scheduler_ten_hours_late_sends_nothing_and_the_next_evening_covers_it(
        harness, by_the_scheduler, caplog):
    started_late(harness, hours=10, last_run_hours_ago=34)
    before = harness.state_path.read_text()

    with caplog.at_level("INFO", logger="family_brief"):
        assert harness.run() == 0

    assert harness.sent == [] and harness.model_calls == []
    assert harness.state_path.read_text() == before
    assert not harness.state_path.with_name("last-run.json").exists()  # not a run that failed
    assert sum("left to the next evening run" in r.getMessage() for r in caplog.records) == 1
    # The next evening's run, on time, reaches back to the last run, over the missed evening.
    with time_machine.travel(NOW + timedelta(hours=14), tick=False):
        assert harness.run() == 0
    [email] = harness.sent
    assert "💤" not in email.text
    assert harness.lookback_hours["gmail"] == [50]  # 48 hours back to the last run, and 2 of slack


def test_several_missed_evenings_get_one_catch_up_brief_that_covers_them(harness, by_the_scheduler):
    started_late(harness, hours=2, last_run_hours_ago=74)

    assert harness.run("--if-missed") == 0  # Windows' sign-in task
    # Task Scheduler's own late start of the missed task finds the evening caught up.
    assert harness.run() == 0

    [email] = harness.sent
    assert CAUGHT_UP in email.text
    assert harness.lookback_hours["gmail"] == [76]  # back to the last run, with its 2 hours' slack


def test_an_on_time_start_by_the_scheduler_says_nothing_about_catching_up(harness, by_the_scheduler):
    normal_night(harness, "en")
    last_run_hours_before_now(harness, 24)

    assert harness.run() == 0

    [email] = harness.sent
    assert "💤" not in email.text


def test_a_run_by_hand_runs_at_any_hour_and_says_nothing_about_catching_up(harness):
    started_late(harness, hours=15, last_run_hours_ago=39)

    assert harness.run() == 0

    [email] = harness.sent
    assert "💤" not in email.text


def test_the_evening_job_started_by_hand_runs_at_any_hour_and_says_nothing_about_catching_up(
        harness, by_the_scheduler):
    started_late(harness, hours=15, last_run_hours_ago=39)
    before = harness.state_path.read_text()
    harness.jobs.add(jobs.DAILY)
    assert harness.cli("schedule", "run-now") == 0

    assert harness.run() == 0  # the evening job, which run-now started

    [email] = harness.sent
    assert "💤" not in email.text
    # Its note that it was started by hand is used up: the scheduler's next late start is its own.
    harness.state_path.write_text(before)
    assert harness.run() == 0
    assert len(harness.sent) == 1


def test_a_hosted_household_s_run_is_never_a_catch_up(harness, by_the_scheduler):
    started_late(harness, hours=15, last_run_hours_ago=39)
    harness.config["hosted"] = True

    assert harness.run() == 0

    [email] = harness.sent
    assert "💤" not in email.text
