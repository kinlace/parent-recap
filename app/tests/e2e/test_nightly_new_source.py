"""A Source added to the registry and nowhere else (ADR 0022): the evening run reads it, the
coverage line counts it, and a catch-up widens its lookback (#407)."""
from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from conftest import NOW, msg
from nights import normal_night

from family_brief import sources
from family_brief.collectors.base import CalendarEvent, Collected


def school_app(h, monkeypatch: pytest.MonkeyPatch, counts_events: bool) -> list[int]:
    """Registers a fifth Source, SchoolApp, whose config section is its own; returns the lookback
    each of its collections ran with."""
    window = SimpleNamespace(lookback_hours=26)
    looked_back: list[int] = []

    def collect(cfg, state, kid_terms) -> Collected:
        looked_back.append(window.lookback_hours)
        if counts_events:
            return Collected(events=[CalendarEvent(
                source="schoolapp", external_id="sa-e1", title="Parents' evening",
                start=NOW + timedelta(days=3), end=NOW + timedelta(days=3, hours=1), kid="Mia")])
        return Collected(messages=[msg("schoolapp", "sa-1", "2026-09-27T09:00:00+03:00",
                                       "Parents' evening next Wednesday", sender="School office")])

    record = sources.Source("schoolapp", {"en": "SchoolApp", "fi": "SchoolApp", "zh": "SchoolApp"},
                            lambda cfg: True, skippable=True, connect_rank=9, collect=collect,
                            counts_events=counts_events, lookback=lambda cfg: window)
    monkeypatch.setattr(sources, "ALL", (*sources.ALL, record))
    return looked_back


def caught_up_hours_ago(h, source: str, hours: int) -> None:
    at = (NOW - timedelta(hours=hours)).isoformat()
    h.state_path.parent.mkdir(parents=True, exist_ok=True)
    h.state_path.write_text(json.dumps({"last_run_at": at, "caught_up_at": NOW.isoformat(),
                                        "source_caught_up_at": {source: at}}))


@pytest.mark.parametrize("counts_events, counted", [(False, "SchoolApp 1 message"),
                                                    (True, "SchoolApp 1 event")],
                         ids=["messages", "events"])
def test_a_source_added_to_the_registry_is_read_counted_and_caught_up(harness, monkeypatch,
                                                                     counts_events, counted):
    looked_back = school_app(harness, monkeypatch, counts_events)
    normal_night(harness, "en")
    caught_up_hours_ago(harness, "schoolapp", 50)  # it failed two nights ago

    assert harness.run() == 0

    [email] = harness.sent
    assert ("📥 Read tonight: Gmail 2 messages · MyClub 1 event · Wilma 1 message · WhatsApp 2 messages · "
            + counted) in email.text
    assert looked_back == [52]  # back to when it was last read, with its 2 hours' slack
    assert harness.lookback_hours["gmail"] == [26]


def test_the_lookback_override_reaches_a_source_added_to_the_registry(harness, monkeypatch):
    looked_back = school_app(harness, monkeypatch, counts_events=False)
    normal_night(harness, "en")

    assert harness.run("--lookback-hours", "72") == 0

    assert looked_back == [72]
