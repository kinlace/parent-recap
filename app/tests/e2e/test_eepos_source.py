"""The Eepos Source in the evening run (ADR 0038, #755), against each school's fake portal, which
answers with the portal's own JSON behind its `)]}',` line: tomorrow's lessons in the Brief and on
its coverage line, a wrong password or a changed portal said there while the rest still goes out,
two schools, and nothing of the students kept but their ids and names."""
from __future__ import annotations

import json

from conftest import SAYS_SOMETHING, eepos_entry, eepos_student, without_unread_note
from fake_sources import EEPOS_PASSWORD
from fake_system import LINUX
from nights import normal_night

from family_brief.sources.fi.eepos import collector as eepos


def with_eepos(h, *more_schools: dict) -> None:
    """Mia and Leo take lessons at emo.eepos.fi, whose guardian sign-in the maintainers added."""
    h.source_fakes["eepos"].school("emo").students = [eepos_student(11, "Mia", "Virtanen"),
                                                       eepos_student(12, "Leo", "Virtanen", nickname="Leo")]
    h.config["eepos"] = {"signins": [{"school": "emo", "username": "parent@example.com", "kids": {"11": "Mia"}},
                                     *more_schools]}


def coverage(h) -> str:
    [email] = h.sent
    return without_unread_note(email.text).rsplit("\n\n", 1)[1]


def test_tomorrow_s_eepos_lessons_are_in_the_brief_and_counted(harness):
    harness.config["summary_language"] = "en"
    harness.sources = {"eepos": [
        eepos_entry("Virtanen Leo, Piano", "2026-09-28", "17:00:00", "17:45:00", students=(12,)),
        eepos_entry("Virtanen Mia, Viulu", "2026-09-30", "15:15:00", "16:00:00", students=(11,)),
        eepos_entry("Syysloma", "2026-10-05", "00:00:00", "23:59:59", kind="vacation", students=(11,)),
        eepos_entry("Syysloma", "2026-10-05", "00:00:00", "23:59:59", kind="vacation", students=(12,)),
    ]}
    with_eepos(harness)
    harness.model_reply = {"per_kid": [], "calendar_events": [],
                           "message_digest": "**Leo**\n- Mon 28.9. 17:00 piano lesson at Ahertajantie"}

    assert harness.run() == 0

    # #734: an event tomorrow asks the model, which is told about the events already in hand, the
    # Kids' names in them as Kid A and Kid B (ADR 0013).
    [call] = harness.model_calls
    assert ", Piano" in call.prompt and "Syysloma" in call.prompt and "Virtanen Leo" not in call.prompt
    [email] = harness.sent
    assert "Mon 28.9. 17:00 piano lesson at Ahertajantie" in email.text
    assert "Virtanen Mia, Viulu" in email.text
    assert coverage(harness) == ("📥 Read tonight: Gmail 0 messages · MyClub 0 events · Wilma 0 messages · "
                                 "WhatsApp 0 messages · Eepos 3 events")
    # Each goes into the .ics once, the holiday as one all-day event.
    _, ics, _ = email.attachment(".ics")
    assert ics.count(b"BEGIN:VEVENT") == 3 and b"DTSTART;VALUE=DATE:20261005" in ics
    assert b"DTSTART:20260928T140000Z" in ics  # 17:00 in Helsinki


def test_a_wrong_eepos_password_is_said_on_the_coverage_line_and_the_brief_still_goes_out(harness):
    normal_night(harness, "en")
    with_eepos(harness)
    harness.secrets[eepos.secret_name("emo", "parent@example.com")] = "changed-since"

    assert harness.run() == 0

    assert coverage(harness) == (
        "📥 Read tonight: Gmail 2 messages · MyClub 1 event · Wilma 1 message · WhatsApp 2 messages · "
        "Eepos 0 events\n⚠️ Eepos not read (login failed). Tonight's Brief may be incomplete.")


def test_a_changed_eepos_portal_is_said_on_the_coverage_line_and_the_brief_still_goes_out(harness):
    normal_night(harness, "en")
    with_eepos(harness)
    harness.source_fakes["eepos"].answers[("emo", "calendar-events/all-by-date-range")] = \
        (200, "<!doctype html><html><body>Eepos</body></html>")

    assert harness.run() == 0

    assert coverage(harness).endswith(
        "Eepos 0 events\n⚠️ Eepos not read (emo.eepos.fi's calendar wasn't JSON). Tonight's Brief may "
        "be incomplete.")


def test_two_eepos_schools_are_both_read_and_one_that_fails_is_named(harness):
    harness.config["summary_language"] = "en"
    harness.model_reply = SAYS_SOMETHING
    with_eepos(harness, {"school": "espoo", "username": "parent@example.com"})
    harness.source_fakes["eepos"].school("espoo").students = [eepos_student(31, "Mia", "Virtanen")]
    harness.sources = {"eepos": [
        eepos_entry("Virtanen Leo, Piano", "2026-09-28", students=(12,)),
        eepos_entry("Kuvataide 1", "2026-09-29", "16:00:00", "17:30:00", students=(31,), school="espoo"),
    ]}

    assert harness.run() == 0

    [email] = harness.sent
    assert "Virtanen Leo, Piano" in email.text and "Kuvataide 1" in email.text
    assert coverage(harness).endswith("Eepos 2 events")

    # The next evening the art school's password has changed: its school is named.
    harness.sent.clear()
    harness.source_fakes["eepos"].school("espoo").accounts["parent@example.com"] = "new-one"
    harness.sources = {"eepos": [eepos_entry("Virtanen Leo, Piano", "2026-09-28", students=(12,))]}
    harness.model_reply = SAYS_SOMETHING

    assert harness.run() == 0

    assert coverage(harness).endswith(
        "Eepos 1 event\n⚠️ Eepos partly read (espoo.eepos.fi: login failed). Tonight's Brief may be incomplete.")


def test_nothing_of_the_students_but_their_ids_and_names_is_kept(harness):
    harness.model_reply = SAYS_SOMETHING
    harness.sources = {"eepos": [eepos_entry("Virtanen Leo, Piano", "2026-09-28", students=(12,))]}
    with_eepos(harness)

    assert harness.run() == 0

    student = eepos_student(12, "Leo", "Virtanen")["person"]
    private = [student[k] for k in ("address", "phone", "birthdate", "ssn_start", "email")]
    kept = [p.read_text(errors="replace") for p in harness.home.rglob("*") if p.is_file()]
    kept += [json.dumps([call.prompt, call.system_prompt]) for call in harness.model_calls]
    kept += [e.text + (e.html or "") for e in harness.sent]
    assert not [value for value in private for text in kept if value in text]
    assert EEPOS_PASSWORD not in "".join(kept)


def test_a_hosted_household_reads_eepos_and_isn_t_told_the_hosted_version_doesn_t(harness):
    harness.system = LINUX  # ADR 0029: the maintainers' server
    harness.config.update(hosted=True, summary_language="en")
    harness.model_reply = SAYS_SOMETHING
    harness.sources = {"eepos": [eepos_entry("Virtanen Leo, Piano", "2026-09-28", students=(12,))]}
    with_eepos(harness)

    assert harness.run() == 0

    assert coverage(harness).endswith("Eepos 1 event\nWhatsApp isn't read in the hosted version.")
