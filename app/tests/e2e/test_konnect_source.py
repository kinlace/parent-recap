"""The Konnect Source in the evening run (ADR 0049, #905), against each provider's fake parent
portal, which answers with the portal's own JSON: the daycare's new messages, news and planned
times in the Brief, its .ics and its coverage line, told once; a wrong password, a portal that
wants a new password or a changed portal said there while the rest still goes out; nothing of the
children kept but their ids and names; and nothing read but the lists. All names are made up."""
from __future__ import annotations

import json
from datetime import timedelta

import time_machine

from conftest import (NOW, SAYS_SOMETHING, konnect_child, konnect_conversation, konnect_news,
                      konnect_planned, without_unread_note)
from fake_sources import KONNECT_PASSWORD
from nights import normal_night

from family_brief.collectors import konnect

MIA, LEO = "c0ffee0000000000000000000000a001", "c0ffee0000000000000000000000a002"
TRIP = "Beste ouders, morgen gaan we naar de kinderboerderij. Graag laarzen mee!"


def with_konnect(h) -> None:
    """Mia and Leo go to a BSO whose parent portal is mijnbso.ouderportaal.nl."""
    h.source_fakes["konnect"].portal("mijnbso").children = [konnect_child(MIA, "Mia", "Testdochter"),
                                                            konnect_child(LEO, "Leo", "Testzoon")]
    h.config["konnect"] = {"signins": [{"portal": "mijnbso", "username": "parent@example.nl",
                                        "kids": {MIA: "Mia"}}]}


def coverage(h) -> str:
    [email] = h.sent
    return without_unread_note(email.text).rsplit("\n\n", 1)[1]


def test_a_new_message_news_and_planned_times_reach_the_brief_under_their_kid_once(harness):
    harness.config["summary_language"] = "en"
    with_konnect(harness)
    there = harness.source_fakes["konnect"].portal("mijnbso")
    there.conversations = [konnect_conversation("Uitje", f"<p>{TRIP}</p>", "2026-09-27T16:00:00+02:00",
                                                children=(there.children[0],))]
    there.news = [konnect_news("Sluitingsdag", "Op 9 oktober zijn we gesloten.", "2026-09-27T09:00:00+02:00")]
    harness.sources = {"konnect": [
        konnect_planned(MIA, "2026-09-28T14:00:00+02:00", "2026-09-28T18:30:00+02:00"),
        konnect_planned(LEO, "2026-09-29T14:00:00+02:00", "2026-09-29T18:00:00+02:00", code="tentative")]}
    harness.model_reply = {"per_kid": [], "calendar_events": [],
                           "message_digest": "**Mia**\n- Mon 28.9. BSO trip to the farm, bring boots"}

    assert harness.run() == 0

    [call] = harness.model_calls
    assert TRIP in call.prompt and "Op 9 oktober zijn we gesloten." in call.prompt
    messages = [m for m in json.loads(call.prompt[call.prompt.index("{"):])["messages"]
                if m.get("source") == "konnect"]
    assert {m["kid_hint"] for m in messages} == {"Kid A", None}  # Mia, as the AI filter sends her
    [email] = harness.sent
    assert "Mon 28.9. BSO trip to the farm, bring boots" in email.text
    assert coverage(harness).endswith("Konnect 2 messages and 2 events")
    _, ics, _ = email.attachment(".ics")
    assert ics.count(b"BEGIN:VEVENT") == 2 and b"Opvang (optie)" in ics
    assert b"DTSTART:20260928T120000Z" in ics  # 14:00 in Amsterdam

    # The next evening the messages aren't told again.
    harness.sent.clear()
    with time_machine.travel(NOW + timedelta(days=1), tick=False):
        harness.model_reply = SAYS_SOMETHING
        assert harness.run() == 0
    assert TRIP not in harness.model_calls[-1].prompt


def test_a_wrong_password_is_said_on_the_coverage_line_and_the_brief_still_goes_out(harness):
    normal_night(harness, "en")
    with_konnect(harness)
    harness.secrets[konnect.secret_name("mijnbso", "parent@example.nl")] = "changed-since"

    assert harness.run() == 0

    assert coverage(harness).endswith(
        "Konnect 0 events\n⚠️ Konnect not read (login failed). Tonight's Brief may be incomplete.")


def test_a_portal_that_wants_a_new_password_is_a_failed_login_on_the_coverage_line(harness):
    normal_night(harness, "en")
    with_konnect(harness)
    harness.source_fakes["konnect"].portal("mijnbso").must_reset = {"parent@example.nl"}

    assert harness.run() == 0

    assert coverage(harness).endswith(
        "Konnect 0 events\n⚠️ Konnect not read (login failed). Tonight's Brief may be incomplete.")


def test_a_changed_portal_is_said_on_the_coverage_line_and_the_brief_still_goes_out(harness):
    normal_night(harness, "en")
    with_konnect(harness)
    harness.source_fakes["konnect"].answers[("mijnbso", "restservices-parent/children/")] = \
        (200, "<!doctype html><html><body>Onderhoud</body></html>")

    assert harness.run() == 0

    assert coverage(harness).endswith(
        "Konnect 0 events\n⚠️ Konnect not read (mijnbso.ouderportaal.nl's list of children wasn't JSON). "
        "Tonight's Brief may be incomplete.")


def test_nothing_of_the_children_but_their_ids_and_names_is_kept_and_only_lists_are_read(harness):
    harness.model_reply = SAYS_SOMETHING
    with_konnect(harness)
    harness.sources = {"konnect": [konnect_planned(LEO, "2026-09-28T14:00:00+02:00", "2026-09-28T18:00:00+02:00")]}

    assert harness.run() == 0

    child = konnect_child(LEO, "Leo", "Testzoon")
    private = [child["childGroup"]["phoneNumber"], child["avatars"]["thumbUrl"], str(child["dateOfBirth"])]
    kept = [p.read_text(errors="replace") for p in harness.home.rglob("*") if p.is_file()]
    kept += [json.dumps([call.prompt, call.system_prompt]) for call in harness.model_calls]
    kept += [e.text + (e.html or "") for e in harness.sent]
    assert not [value for value in private for text in kept if value in text]
    assert KONNECT_PASSWORD not in "".join(kept)
    fake = harness.source_fakes["konnect"]
    assert not [p for p in fake.paths() if "timeline" in p]
    assert {r["method"] for r in fake.requests} == {"PUT", "GET"}
    assert [r["path"] for r in fake.requests if r["method"] == "PUT"] == ["auth-api/login"]
