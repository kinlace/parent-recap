"""The Social Schools Source in the evening run (ADR 0049, #904), against the fake Social Schools,
which signs in and answers as its identity provider and API do: a new announcement in the Brief
under its Kid once, through the AI filter; the coming events in the Brief, its .ics and its
coverage line; a wrong password or an account that signs in another way said there while the rest
still goes out; nothing of the children kept but their names and groups; and nothing written to
Social Schools."""
from __future__ import annotations

import json
from datetime import timedelta, timezone

import time_machine

from conftest import NOW, SAYS_SOMETHING, socialschools_child, socialschools_event, socialschools_post, \
    without_unread_note
from fake_sources import SOCIALSCHOOLS_PASSWORD
from nights import normal_night
from family_brief.collectors import socialschools

USERNAME = "parent@example.nl"


def with_socialschools(h) -> None:
    """Mia is in Groep 5 and Leo in Groep 3 at a school on Social Schools, signed in to as the
    parent."""
    h.source_fakes["socialschools"].school(7).children = [
        socialschools_child("Mia", "Mia Virtanen", ("Groep 5",)),
        socialschools_child("Leo", "Leo Virtanen", ("Groep 3",))]
    h.config["socialschools"] = {"signins": [{"username": USERNAME, "kids": {"Mia Virtanen": "Mia"}}]}


def coverage(h) -> str:
    [email] = h.sent
    return without_unread_note(email.text).rsplit("\n\n", 1)[1]


def ago(hours: float) -> str:
    return (NOW - timedelta(hours=hours)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


TRIP = ("<p>Beste ouders van groep 5,</p><p>Vrijdag 2 oktober gaan we naar de kinderboerderij. "
        "Geef uw kind laarzen mee. Vragen? Bel juf Bakker op 06-12345678.</p>")


def test_a_new_announcement_reaches_the_model_under_its_kid_through_the_ai_filter_once(harness):
    harness.config["summary_language"] = "en"
    with_socialschools(harness)
    harness.sources = {"socialschools": [
        socialschools_post(41, "Kinderboerderij", TRIP, ago(3), attachments=("toestemming.pdf",))]}
    harness.model_reply = {"per_kid": [], "calendar_events": [],
                           "message_digest": "**Mia**\n- Fri 2.10. trip to the petting farm, wellies"}

    assert harness.run() == 0

    [call] = harness.model_calls
    [message] = [m for m in json.loads(call.prompt[call.prompt.index("{"):])["messages"]
                 if m.get("source") == "socialschools"]
    assert message["kid_hint"] == "Kid A"  # Mia, as the AI filter sends her (ADR 0013)
    assert "kinderboerderij" in message["body"] and "[Attachments in Social Schools: toestemming.pdf]" in message["body"]
    assert "06-12345678" not in call.prompt  # a Third Party's phone number is a placeholder
    [email] = harness.sent
    assert "Fri 2.10. trip to the petting farm, wellies" in email.text
    assert coverage(harness).endswith("Social Schools 1 message and 0 events")

    # The next evening it is not told again.
    harness.sent.clear()
    with time_machine.travel(NOW + timedelta(days=1), tick=False):
        assert harness.run() == 0
    assert len(harness.model_calls) == 1


def test_coming_events_are_in_the_brief_its_ics_and_counted(harness):
    harness.config["summary_language"] = "en"
    with_socialschools(harness)
    harness.sources = {"socialschools": [
        socialschools_event(1, "Studiedag", "2026-09-30T00:00:00Z", "2026-10-01T00:00:00Z", all_day=True,
                            groups=("Schoolnieuws",)),
        socialschools_event(2, "Oudergesprek", "2026-09-29T15:30:00+02:00", "2026-09-29T15:45:00+02:00",
                            groups=("Groep 3",)),
    ]}
    harness.model_reply = SAYS_SOMETHING

    assert harness.run() == 0

    [call] = harness.model_calls
    assert "Studiedag" in call.prompt and "Oudergesprek" in call.prompt
    [email] = harness.sent
    assert coverage(harness).endswith("Social Schools 2 events")
    _, ics, _ = email.attachment(".ics")
    assert ics.count(b"BEGIN:VEVENT") == 2 and b"DTSTART;VALUE=DATE:20260930" in ics
    assert b"DTSTART:20260929T133000Z" in ics


def test_a_wrong_password_is_said_on_the_coverage_line_and_the_brief_still_goes_out(harness):
    normal_night(harness, "en")
    with_socialschools(harness)
    harness.secrets[socialschools.secret_name(USERNAME)] = "changed-since"

    assert harness.run() == 0

    assert coverage(harness).endswith(
        "Social Schools 0 events\n⚠️ Social Schools not read (login failed). Tonight's Brief may be incomplete.")


def test_an_account_that_signs_in_with_google_is_said_as_a_failed_login(harness):
    normal_night(harness, "en")
    with_socialschools(harness)
    harness.source_fakes["socialschools"].ways[USERNAME] = "external"

    assert harness.run() == 0

    assert "⚠️ Social Schools not read (login failed)" in coverage(harness)


def test_nothing_of_the_children_but_their_names_is_kept_and_nothing_is_written(harness):
    harness.model_reply = SAYS_SOMETHING
    with_socialschools(harness)
    harness.sources = {"socialschools": [socialschools_post(41, "Kinderboerderij", TRIP, ago(3))]}

    assert harness.run() == 0

    child = socialschools_child("Mia", "Mia Virtanen", ("Groep 5",))
    private = [child["avatarUrl"], child["yearClass"], "Ouder Testpersoon"]
    kept = [p.read_text(errors="replace") for p in harness.home.rglob("*") if p.is_file()]
    kept += [json.dumps([call.prompt, call.system_prompt]) for call in harness.model_calls]
    kept += [e.text + (e.html or "") for e in harness.sent]
    assert not [value for value in private for text in kept if value in text]
    fake = harness.source_fakes["socialschools"]
    assert SOCIALSCHOOLS_PASSWORD not in "".join(kept)
    assert fake.refresh_tokens and not [t for t in fake.refresh_tokens if t in "".join(kept)]
    assert not [n for n in harness.secrets if "refresh" in n]
    assert {r["method"] for r in fake.requests if r["host"] == socialschools.API_HOST} == {"GET"}
    assert [r["path"] for r in fake.requests if r["method"] == "POST"] == ["/Account/Login", "/connect/token"]
