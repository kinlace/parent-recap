"""Labels the Sources add to Messages are model input, so they are English whatever the Brief's
language; the summarize prompt explains them (see summarize._build_prompt)."""
from __future__ import annotations

import pytest

from family_brief.collectors import whatsapp, wilma
from family_brief.state import State


@pytest.mark.parametrize("day, label", [
    ("2026-09-27", "Sun · today"),
    ("2026-09-28", "Mon · tomorrow"),
    ("2026-09-29", "Tue · day after tomorrow"),
    ("2026-10-01", "Thu · in 4 days"),
])
def test_wilma_timetable_is_headed_with_its_date(harness, monkeypatch, day, label):
    payload = {"students": [{"student": {"name": "Virtanen Aino"}, "items": [
        {"date": day, "start": "08:15", "end": "09:45", "subject": "Liikunta", "room": "ulkokenttä"}]}]}
    monkeypatch.setattr(wilma, "_run", lambda args: payload)

    [msg] = wilma._collect_schedule(State(harness.home / "state.json"))

    assert msg.subject == f"Timetable for the next school day (from {day})"
    assert msg.body.splitlines()[:3] == [
        f"[{day} {label}]", "— Virtanen Aino —", "  08:15–09:45  Liikunta  @ ulkokenttä"]


def test_whatsapp_placeholders_are_english():
    assert [whatsapp._media_placeholder(t) for t in (0, 1, 3, 8)] == ["", "[image]", "[voice]", "[document]"]
