"""The eVaka Source in the evening run (ADR 0039, #768), against each city's fake eVaka, which
answers with its citizen API's own JSON: tomorrow's daycare events, a closed day and the
reservation deadline in the Brief, its .ics and its coverage line; a wrong password or a changed
API said there while the rest still goes out; two cities; nothing of the children kept but their
ids and names; and the daycare's messages (#786): a new one told under its Kid once, and a
sensitive one only named."""
from __future__ import annotations

import json
from datetime import timedelta

import time_machine

from conftest import (NOW, SAYS_SOMETHING, evaka_child, evaka_event, evaka_sensitive_thread, evaka_thread,
                      without_unread_note)
from fake_sources import EVAKA_PASSWORD
from fake_system import LINUX
from nights import normal_night

from family_brief.collectors import evaka

MIA, LEO = "1a2b3c4d-0000-4000-8000-000000000011", "1a2b3c4d-0000-4000-8000-000000000012"


def with_evaka(h, *more_cities: dict) -> None:
    """Mia and Leo are in a daycare in Espoo, whose eVaka sign-in the maintainers added."""
    h.source_fakes["evaka"].city("espoo").children = [evaka_child(MIA, "Mia Emilia", "Virtanen"),
                                                      evaka_child(LEO, "Leo", "Virtanen")]
    h.config["evaka"] = {"signins": [{"city": "espoo", "username": "parent@example.com", "kids": {MIA: "Mia"}},
                                     *more_cities]}


def coverage(h) -> str:
    [email] = h.sent
    return without_unread_note(email.text).rsplit("\n\n", 1)[1]


def test_tomorrow_s_daycare_events_closures_and_deadline_are_in_the_brief_and_counted(harness):
    harness.config["summary_language"] = "en"
    harness.source_fakes["evaka"].city("espoo").holidays = {"2026-10-09"}
    harness.sources = {"evaka": [
        evaka_event("Retki Nuuksioon", "2026-09-28", children=(LEO,), description="Eväät mukaan"),
        evaka_event("Kuvauspäivä", "2026-10-01", children=(MIA, LEO)),
    ]}
    with_evaka(harness)
    harness.model_reply = {"per_kid": [], "calendar_events": [],
                           "message_digest": "**Leo**\n- Mon 28.9. daycare trip to Nuuksio, pack a lunch"}

    assert harness.run() == 0

    # The model is told about the events in hand, the Kids' names as Kid A and Kid B (ADR 0013).
    [call] = harness.model_calls
    assert "Retki Nuuksioon" in call.prompt and evaka.CLOSED in call.prompt
    assert "Varaukset viikolle 41 sulkeutuvat" in call.prompt
    [email] = harness.sent
    assert "Mon 28.9. daycare trip to Nuuksio, pack a lunch" in email.text
    assert "Kuvauspäivä" in email.text
    assert coverage(harness) == ("📥 Read tonight: Gmail 0 messages · MyClub 0 events · Wilma 0 messages · "
                                 "WhatsApp 0 messages · eVaka 4 events")
    # Each goes into the .ics once: the events and the closed day all day, the deadline at its time.
    _, ics, _ = email.attachment(".ics")
    assert ics.count(b"BEGIN:VEVENT") == 4 and b"DTSTART;VALUE=DATE:20261009" in ics
    assert b"DTSTART:20260928T150000Z" in ics  # 18:00 in Helsinki


def test_a_later_daycare_event_asks_the_model_the_evening_it_is_new_and_not_after(harness):
    # #777: only the model picks out a closed day or a trip ahead to tell in the Digest.
    harness.sources = {"evaka": [evaka_event("Retki Sellosaliin", "2026-10-01", children=(MIA,),
                                             description="Lähtö päiväkodilta klo 9.15")]}
    with_evaka(harness)
    harness.source_fakes["evaka"].city("espoo").fixed_schedule = {MIA, LEO}  # no deadline tomorrow
    harness.model_reply = {"per_kid": [], "calendar_events": [],
                           "message_digest": "**Mia**\n- Thu 1.10. daycare trip, leaving at 9.15"}

    assert harness.run() == 0

    [call] = harness.model_calls
    assert "Retki Sellosaliin" in call.prompt and "Lähtö päiväkodilta klo 9.15" in call.prompt
    with time_machine.travel(NOW + timedelta(hours=1), tick=False):
        assert harness.run() == 0
    assert len(harness.model_calls) == 1


def test_a_wrong_evaka_password_is_said_on_the_coverage_line_and_the_brief_still_goes_out(harness):
    normal_night(harness, "en")
    with_evaka(harness)
    harness.secrets[evaka.secret_name("espoo", "parent@example.com")] = "changed-since"

    assert harness.run() == 0

    assert coverage(harness) == (
        "📥 Read tonight: Gmail 2 messages · MyClub 1 event · Wilma 1 message · WhatsApp 2 messages · "
        "eVaka 0 events\n⚠️ eVaka not read (login failed). Tonight's Brief may be incomplete.")


def test_a_changed_evaka_api_is_said_on_the_coverage_line_and_the_brief_still_goes_out(harness):
    normal_night(harness, "en")
    with_evaka(harness)
    harness.source_fakes["evaka"].answers[("espoo", "calendar-events")] = \
        (200, "<!doctype html><html><body>eVaka</body></html>")

    assert harness.run() == 0

    assert coverage(harness).endswith(
        "eVaka 0 events\n⚠️ eVaka not read (espoonvarhaiskasvatus.fi's calendar wasn't JSON). Tonight's "
        "Brief may be incomplete.")


def test_two_cities_are_both_read_and_one_that_fails_is_named(harness):
    harness.config["summary_language"] = "en"
    harness.model_reply = SAYS_SOMETHING
    with_evaka(harness, {"city": "tampere", "username": "parent@example.com"})
    harness.source_fakes["evaka"].city("tampere").children = [evaka_child("t-31", "Mia", "Virtanen")]
    harness.sources = {"evaka": [
        evaka_event("Retki Nuuksioon", "2026-09-28", children=(LEO,)),
        evaka_event("Teatteri", "2026-09-29", children=("t-31",), city="tampere"),
    ]}

    assert harness.run() == 0

    [email] = harness.sent
    assert "Retki Nuuksioon" in email.text and "Teatteri" in email.text
    assert coverage(harness).endswith("eVaka 4 events")  # with each city's deadline

    # The next evening the Tampere password has changed: its host is named.
    harness.sent.clear()
    harness.source_fakes["evaka"].city("tampere").accounts["parent@example.com"] = "new-one"
    harness.model_reply = SAYS_SOMETHING

    assert harness.run() == 0

    assert coverage(harness).endswith(
        "eVaka 2 events\n⚠️ eVaka partly read (varhaiskasvatus.tampere.fi: login failed). Tonight's Brief may "
        "be incomplete.")


def test_nothing_of_the_children_but_their_ids_and_names_is_kept(harness):
    harness.model_reply = SAYS_SOMETHING
    harness.sources = {"evaka": [evaka_event("Retki Nuuksioon", "2026-09-28", children=(LEO,))]}
    with_evaka(harness)

    assert harness.run() == 0

    child = evaka_child(LEO, "Leo", "Virtanen")
    private = [child["unit"]["name"], child["group"]["name"], child["imageId"]]
    kept = [p.read_text(errors="replace") for p in harness.home.rglob("*") if p.is_file()]
    kept += [json.dumps([call.prompt, call.system_prompt]) for call in harness.model_calls]
    kept += [e.text + (e.html or "") for e in harness.sent]
    assert not [value for value in private for text in kept if value in text]
    assert EVAKA_PASSWORD not in "".join(kept)


def test_a_hosted_household_reads_evaka_and_isn_t_told_the_hosted_version_doesn_t(harness):
    harness.system = LINUX  # ADR 0029: the maintainers' server
    harness.config.update(hosted=True, summary_language="en")
    harness.model_reply = SAYS_SOMETHING
    harness.sources = {"evaka": [evaka_event("Retki Nuuksioon", "2026-09-28", children=(LEO,))]}
    with_evaka(harness)

    assert harness.run() == 0

    assert coverage(harness).endswith("eVaka 2 events\nWhatsApp isn't read in the hosted version.")


BULLETIN = "Hei Kotiloisten perheet! Perjantaina 2.10. on pehmolelupäivä. Oman pehmolelun saa tuoda mukaan."


def test_a_new_daycare_message_reaches_the_model_and_the_brief_under_its_kid_once(harness):
    harness.config["summary_language"] = "en"
    with_evaka(harness)
    city = harness.source_fakes["evaka"].city("espoo")
    city.fixed_schedule = {MIA, LEO}  # no deadline: the message is tonight's only news
    city.threads = [evaka_thread("Pehmolelupäivä", BULLETIN, "2026-09-27T14:05:00+03:00", children=(MIA,))]
    harness.model_reply = {"per_kid": [], "calendar_events": [],
                           "message_digest": "**Mia**\n- Fri 2.10. cuddly toy day at daycare"}

    assert harness.run() == 0

    [call] = harness.model_calls
    assert BULLETIN in call.prompt and "Pehmolelupäivä" in call.prompt
    [message] = [m for m in json.loads(call.prompt[call.prompt.index("{"):])["messages"]
                 if m.get("source") == "evaka"]
    assert message["kid_hint"] == "Kid A"  # Mia, as the AI filter sends her (ADR 0013)
    [email] = harness.sent
    assert "Fri 2.10. cuddly toy day at daycare" in email.text
    assert coverage(harness).endswith("eVaka 1 message and 0 events")

    # The next evening it is not told again.
    harness.sent.clear()
    with time_machine.travel(NOW + timedelta(days=1), tick=False):
        assert harness.run() == 0
    assert len(harness.model_calls) == 1


def test_a_sensitive_daycare_message_is_only_named_with_its_link_and_no_model_sees_it(harness):
    harness.config["summary_language"] = "en"
    harness.config["ai_filter"] = {"enabled": False}  # held back even so: there is nothing to tell
    with_evaka(harness)
    city = harness.source_fakes["evaka"].city("espoo")
    city.fixed_schedule = {MIA, LEO}
    sensitive = evaka_sensitive_thread("Laine Maija", "2026-09-27T16:30:00+03:00")
    city.threads = [sensitive]

    assert harness.run() == 0

    assert harness.model_calls == []
    [email] = harness.sent
    link = f"https://espoonvarhaiskasvatus.fi/messages/{sensitive['id']}"
    assert "🔒 Held-back messages" in email.text
    assert f"• eVaka · Laine Maija · (no subject) · {link}" in email.text
    assert f'href="{link}"' in email.html
    assert [r for r in harness.source_fakes["evaka"].requests if r["method"] != "GET"] == \
        [r for r in harness.source_fakes["evaka"].requests if r["path"] == evaka.SIGN_IN]
