"""The nightly `parent-recap run` in Google Calendar mode, end to end. Assertions are only on what
leaves the system: the email (text, HTML, attachments), the model command line, and persisted state."""
from __future__ import annotations

import dataclasses
import json
from datetime import timedelta

import pytest
import time_machine

from conftest import NOW, myclub_event

from nights import archived_summary, normal_night


# ── Google Calendar mode

def test_google_mode_writes_events_and_links_them(harness):
    normal_night(harness)
    harness.config["google_calendar"] = {"mode": "google", "invite_attendees": ["partner@example.com"]}
    harness.calendar.existing = [{"id": "x1", "summary": "Mia dentist",
                                  "start": {"dateTime": "2026-09-29T08:00:00+03:00"},
                                  "end": {"dateTime": "2026-09-29T09:00:00+03:00"}}]
    harness.authorize_google_calendar()

    assert harness.run() == 0

    # Two model events plus the MyClub match, each inviting the partner.
    titles = [e["summary"] for e in harness.calendar.inserted]
    assert titles == ["3B 远足 Nuuksio", "钢琴课（改期）", "FC Kilo P2017 vs HJK"]
    assert all(e["attendees"] == [{"email": "partner@example.com"}] for e in harness.calendar.inserted)
    assert harness.calendar.inserted[0]["start"] == {"dateTime": "2026-10-01T09:00:00+03:00",
                                                     "timeZone": "Europe/Helsinki"}
    assert harness.model_payload()["upcoming_calendar_events_next_7d"][0]["summary"] == "Kid A dentist"

    [email] = harness.sent
    assert email.attachments == []
    assert '<a href="https://calendar.google.com/event?eid=gev1">3B 远足 Nuuksio</a>' in email.html
    assert sorted(v["google_event_id"] for v in harness.state()["created_event_hashes"].values()) \
        == ["gev1", "gev2", "gev3"]


def test_a_parents_own_appointment_reaches_the_ai_as_a_busy_block(harness):
    google_mode(harness)
    harness.calendar.existing = [
        {"id": "p1", "summary": "Dr. Salo, back pain", "location": "Kilo Health Centre",
         "start": {"dateTime": "2026-09-29T08:00:00+03:00"},
         "end": {"dateTime": "2026-09-29T09:00:00+03:00"}},
        {"id": "p2", "summary": "Work trip Tampere", "location": "Tampere",
         "start": {"date": "2026-10-01"}, "end": {"date": "2026-10-03"}},
    ]

    assert harness.run() == 0

    snapshot = harness.model_payload()["upcoming_calendar_events_next_7d"]
    assert [{k: v for k, v in e.items() if k != "id"} for e in snapshot] == [
        {"summary": "busy", "start": "2026-09-29T08:00:00+03:00", "end": "2026-09-29T09:00:00+03:00"},
        {"summary": "busy", "start": "2026-10-01", "end": "2026-10-03"},
    ]
    sent = harness.model_prompt() + " ".join(harness.model_calls[-1].argv)
    for private in ("Salo", "back pain", "Kilo Health Centre", "Work trip", "Tampere"):
        assert private not in sent


def test_an_event_naming_a_kid_keeps_its_title_and_location(harness):
    google_mode(harness)
    harness.calendar.existing = [
        {"id": "k1", "summary": "MIA swimming", "location": "Leppävaara pool",
         "start": {"dateTime": "2026-09-29T17:00:00+03:00"},
         "end": {"dateTime": "2026-09-29T18:00:00+03:00"}},
        {"id": "k2", "summary": "小狮 理发", "location": "Kilo barber",  # Leo's alias
         "start": {"dateTime": "2026-09-30T15:00:00+03:00"},
         "end": {"dateTime": "2026-09-30T15:30:00+03:00"}},
    ]

    assert harness.run() == 0

    assert harness.model_payload()["upcoming_calendar_events_next_7d"] == [
        {"id": "k1", "summary": "Kid A swimming", "start": "2026-09-29T17:00:00+03:00",
         "end": "2026-09-29T18:00:00+03:00", "location": "Leppävaara pool"},
        {"id": "k2", "summary": "Kid B 理发", "start": "2026-09-30T15:00:00+03:00",
         "end": "2026-09-30T15:30:00+03:00", "location": "Kilo barber"},
    ]


def test_events_parent_recap_added_keep_their_title_and_location(harness):
    google_mode(harness)

    def added(source: str) -> dict:
        return {"private": {"family_brief": "1", "family_brief_hash": f"h-{source}",
                            "source": source, "external_id": f"{source}-1", "kid": "Mia"}}

    harness.calendar.existing = [
        {"id": "fb1", "summary": "3B 远足 Nuuksio", "location": "Nuuksio",
         "start": {"dateTime": "2026-10-01T09:00:00+03:00"},
         "end": {"dateTime": "2026-10-01T14:00:00+03:00"}, "extendedProperties": added("gmail")},
        {"id": "fb2", "summary": "⚽ FC Kilo P2017 training", "location": "Leppävaara field",
         "start": {"dateTime": "2026-10-02T17:00:00+03:00"},
         "end": {"dateTime": "2026-10-02T18:30:00+03:00"}, "extendedProperties": added("myclub")},
    ]

    assert harness.run() == 0

    assert harness.model_payload()["upcoming_calendar_events_next_7d"] == [
        {"id": "fb1", "summary": "3B 远足 Nuuksio", "start": "2026-10-01T09:00:00+03:00",
         "end": "2026-10-01T14:00:00+03:00", "location": "Nuuksio"},
        {"id": "fb2", "summary": "⚽ FC Kilo P2017 training", "start": "2026-10-02T17:00:00+03:00",
         "end": "2026-10-02T18:30:00+03:00", "location": "Leppävaara field"},
    ]


def test_a_clash_with_a_busy_block_is_cited_without_the_events_own_id(harness):
    google_mode(harness)
    # An invitation's event id can be its UID in base32hex, here appt-77@dental.example.
    google_id = "_c5o70t1d6srk0p35dpq62r1ecls62rbgdhig"
    harness.calendar.existing = [{"id": google_id, "summary": "Dentist",
                                  "start": {"dateTime": "2026-10-01T09:00:00+03:00"},
                                  "end": {"dateTime": "2026-10-01T10:00:00+03:00"}}]

    def clash(prompt: str) -> dict:
        [busy] = json.loads(prompt[prompt.index("\n{") + 1:])["upcoming_calendar_events_next_7d"]
        return {"per_kid": [{"kid": "Mia", "action_items": [], "notices": [
            {"text": "The trip on Thu 1 Oct clashes with something in the calendar at 9:00",
             "refs": [busy["id"], "g-101"]}]}],
            "calendar_events": [], "message_digest": ""}

    harness.model_reply = clash

    assert harness.run() == 0

    assert google_id not in harness.model_prompt()
    [mia] = archived_summary(harness)["per_kid"]
    assert [n["source"] for n in mia["notices"]] == [["calendar", "gmail"]]


def test_google_mode_undelivered_night_reworded_is_not_written_twice(harness):
    google_mode(harness)
    harness.email_error = OSError("SMTP connection refused")
    assert harness.run() == 0
    assert len(harness.calendar.inserted) == 3

    # The same Messages come round again, and this time the model words the titles differently.
    harness.email_error = None
    for ev in harness.model_reply["calendar_events"]:
        ev["title"] += "（再次）"
    with time_machine.travel(NOW + timedelta(days=1), tick=False):
        assert harness.run() == 0
    assert len(harness.calendar.inserted) == 3


def test_google_mode_upgrade_does_not_rewrite_events_from_before_0_4_0(harness):
    google_mode(harness)
    # Tonight's three events as written before 0.4.0, whose hashes had the title in them.
    old_hashes = ["157d8c04a7e75478d93e", "706ef145ec439b5d6883",
                  "8c9308bd46d0dfb1852b"]  # sha1 of "myclub|mc-1|2026-10-03T07:00:00+00:00|FC Kilo P2017 vs HJK"
    harness.state_path.parent.mkdir(parents=True, exist_ok=True)
    harness.state_path.write_text(json.dumps({"created_event_hashes": {
        h: {"google_event_id": "old", "created_at": "2026-09-26T18:00:00+00:00"} for h in old_hashes}}))

    assert harness.run() == 0

    assert harness.calendar.inserted == []


def test_google_mode_writes_one_event_per_kid_from_the_same_message(harness):
    google_mode(harness)
    meeting = {"title": "Parents' evening", "start": "2026-10-06T18:00:00", "refs": ["g-102"]}
    harness.model_reply["calendar_events"] = [{**meeting, "kid": "Mia"}, {**meeting, "kid": "Leo"}]

    assert harness.run() == 0

    assert [e["summary"] for e in harness.calendar.inserted] \
        == ["Parents' evening", "Parents' evening", "FC Kilo P2017 vs HJK"]


@pytest.mark.parametrize("mode", ["google", "ics"])
def test_myclub_times_are_shown_in_local_time(harness, mode):
    # The MyClub feed gives times in UTC; a winter match at 10:00 in Helsinki is 08:00 UTC.
    google_mode(harness)
    harness.config["google_calendar"] = {"mode": mode, "ics_include_myclub": True}
    harness.sources["myclub"] = [myclub_event("mc-2", "FC Kilo P2017 vs PK-35", "2026-11-07T08:00:00+00:00",
                                              "2026-11-07T09:30:00+00:00")]

    assert harness.run() == 0

    [queued] = harness.model_payload()["already_queued_for_calendar"]
    assert (queued["start"], queued["end"]) == ("2026-11-07T10:00:00+02:00", "2026-11-07T11:30:00+02:00")
    [email] = harness.sent
    assert "FC Kilo P2017 vs PK-35 — 11月7日 周六 10:00" in email.text
    assert "11月7日 周六 10:00" in email.html


def test_any_source_can_bring_events_beside_its_messages(harness):
    # Collecting doesn't single out MyClub: a Source returning both has both reach the model,
    # its events in local time, and the coverage line counts the Messages of a Source that isn't
    # an event feed.
    normal_night(harness)
    exam = dataclasses.replace(myclub_event("w-exam", "Matematiikan koe", "2026-10-05T06:00:00+00:00",
                                            "2026-10-05T07:00:00+00:00"), source="wilma")
    harness.sources["wilma"] = [*harness.sources["wilma"], exam]
    del harness.sources["myclub"]

    assert harness.run() == 0

    payload = harness.model_payload()
    assert "w-55" in {m["external_id"] for m in payload["messages"]}
    [queued] = payload["already_queued_for_calendar"]
    assert (queued["title"], queued["start"]) == ("Matematiikan koe", "2026-10-05T09:00:00+03:00")
    archived = json.loads((harness.archive_dir / "2026-09-27.raw.json").read_text())
    assert archived["summary"]["_coverage"]["wilma"] == {"count": 1, "error": None}


ICS_FALLBACK = "这些新事件已打包在邮件附件的 .ics 里，点开附件即可加入日历。"
REAUTH = "跟 Claude 说「重新授权 Google Calendar」即可修复。"


def google_mode(h, *, authorized: bool = True, error: Exception | None = None) -> None:
    normal_night(h)
    h.config["google_calendar"] = {"mode": "google"}
    if authorized:
        h.authorize_google_calendar()
    h.calendar.error = error


def assert_ics_fallback(email, note: str) -> None:
    """The Brief warns, says the events are attached, and attaches them filtered as in .ics mode."""
    assert email.text.endswith("\n\n" + note + ICS_FALLBACK)
    assert note + ICS_FALLBACK in email.html
    [(name, payload, mime)] = email.attachments
    assert (name, mime) == ("parent-recap-2026-09-27.ics", "text/calendar")
    ics = payload.decode()
    # The two model events; MyClub stays out unless ics_include_myclub is set.
    assert ics.count("BEGIN:VEVENT") == 2
    assert "3B 远足 Nuuksio" in ics and "钢琴课（改期）" in ics and "FC Kilo" not in ics


def test_google_mode_expired_token_attaches_ics(harness):
    google_mode(harness, error=RuntimeError("invalid_grant: Token has been expired or revoked."))

    assert harness.run() == 0

    [email] = harness.sent
    assert_ics_fallback(email, "⚠️ Google Calendar 授权已过期，新事件没有写入日历。" + REAUTH)
    assert harness.calendar.inserted == []
    assert sorted(v["google_event_id"] for v in harness.state()["created_event_hashes"].values()) \
        == ["ics", "ics"]


def test_google_mode_write_error_attaches_ics(harness):
    google_mode(harness, error=RuntimeError("HttpError 503: backendError"))

    assert harness.run() == 0

    [email] = harness.sent
    assert_ics_fallback(email, "⚠️ Google Calendar 写入失败：HttpError 503: backendError。")
    assert len(harness.state()["created_event_hashes"]) == 2


def test_google_mode_not_authorized_attaches_ics(harness):
    google_mode(harness, authorized=False)

    assert harness.run() == 0

    [email] = harness.sent
    assert_ics_fallback(email, "⚠️ Google Calendar 还没有授权，新事件没有写入日历。" + REAUTH)
    assert harness.calendar.inserted == []


def test_google_mode_notes_are_in_english(harness):
    google_mode(harness, error=RuntimeError("invalid_grant: Token has been expired or revoked."))
    normal_night(harness, "en")
    harness.calendar.fail_after = 1

    assert harness.run() == 0

    [email] = harness.sent
    note = ("⚠️ Google Calendar access has expired, so new events were not added to it. "
            "Tell Claude “re-authorize Google Calendar” to fix it. "
            "The new events are in the .ics attached to this email; open it to add them to your calendar.")
    assert email.text.endswith("\n\n" + note)
    assert "📅 New calendar events:\n• 3B retki to Nuuksio" in email.text
    assert ("<h3>📅 New calendar events</h3><p style='color:#666'>Linked events are in Google Calendar; "
            "the rest are in the attached .ics. Open it to add them to your calendar.</p>") in email.html


def test_google_mode_partial_write_attaches_only_the_rest(harness):
    google_mode(harness, error=RuntimeError("invalid_grant: Token has been expired or revoked."))
    harness.calendar.fail_after = 1

    assert harness.run() == 0

    [email] = harness.sent
    _, payload, _ = email.attachment(".ics")
    ics = payload.decode()
    assert ics.count("BEGIN:VEVENT") == 1 and "钢琴课（改期）" in ics
    # The event Google took before failing is still listed, linked, beside the attached one.
    assert "• 3B 远足 Nuuksio" in email.text and "• 钢琴课（改期）" in email.text
    assert '<a href="https://calendar.google.com/event?eid=gev1">3B 远足 Nuuksio</a>' in email.html
    assert "钢琴课（改期）</a>" not in email.html
    assert "带链接的已写入 Google 日历，其余已打包在邮件附件的 .ics 里" in email.html

    # Re-authorized: neither the written event nor the attached one is created again.
    harness.calendar.error = None
    with time_machine.travel(NOW + timedelta(days=1), tick=False):
        assert harness.run() == 0
    assert [e["summary"] for e in harness.calendar.inserted] == ["3B 远足 Nuuksio", "FC Kilo P2017 vs HJK"]


def test_google_mode_fallback_includes_myclub_when_configured(harness):
    google_mode(harness, authorized=False)
    harness.config["google_calendar"]["ics_include_myclub"] = True

    assert harness.run() == 0

    _, payload, _ = harness.sent[0].attachment(".ics")
    assert payload.count(b"BEGIN:VEVENT") == 3


def test_google_mode_fallback_retries_after_failed_email(harness):
    google_mode(harness, authorized=False)
    harness.email_error = OSError("SMTP connection refused")

    assert harness.run() == 0

    assert harness.state()["created_event_hashes"] == {}

    harness.email_error = None
    with time_machine.travel(NOW + timedelta(days=1), tick=False):
        assert harness.run() == 0
    [email] = harness.sent
    _, payload, _ = email.attachment(".ics")
    assert payload.count(b"BEGIN:VEVENT") == 2


def test_google_mode_does_not_recreate_events_sent_by_fallback(harness):
    google_mode(harness, error=RuntimeError("invalid_grant: Token has been expired or revoked."))
    assert harness.run() == 0

    # Still failing the next night: nothing new to attach, so no attachment sentence either.
    with time_machine.travel(NOW + timedelta(days=1), tick=False):
        assert harness.run() == 0
    assert harness.sent[1].attachments == []
    assert ICS_FALLBACK not in harness.sent[1].text

    # Re-authorized: only the MyClub match, never delivered by the fallback, is written.
    harness.calendar.error = None
    with time_machine.travel(NOW + timedelta(days=2), tick=False):
        assert harness.run() == 0
    assert [e["summary"] for e in harness.calendar.inserted] == ["FC Kilo P2017 vs HJK"]
    assert harness.sent[2].attachments == []
