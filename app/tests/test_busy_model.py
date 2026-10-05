"""A model that is busy for a while (at capacity, rate-limited, overloaded) is tried again after a
pause; one that can't work until someone acts (signed out, an expired token) is not. Assertions are
on the email, the model calls made, the pauses between them, and the log."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from conftest import FailedCall, msg

from family_brief import ops

AT_CAPACITY = "ERROR: Selected model is at capacity. Please try a different model.\n"
GOOD = {"per_kid": [{"kid": "Mia", "notices": [{"text": "Retki to Nuuksio on Thu 1 Oct", "refs": ["g-1"]}],
                     "action_items": []}],
        "calendar_events": [], "message_digest": "**Mia**\n- Retki to Nuuksio on Thu 1 Oct"}


@pytest.fixture
def night(harness, tmp_path: Path):
    """An English night on Codex with one message."""
    codex = tmp_path / "codex"
    codex.write_text("#!/bin/sh\n")
    codex.chmod(0o755)
    harness.config["summary_language"] = "en"
    harness.config["llm"] = {"backend": "codex", "codex_path": str(codex)}
    harness.sources = {"gmail": [msg("gmail", "g-1", "2026-09-27T08:15:00+03:00",
                                     "3B goes on a retki to Nuuksio on Thursday 1.10.",
                                     sender="teacher.3b@kilo.example.fi", subject="Retki", kid="Mia")]}
    return harness


def test_a_model_at_capacity_twice_then_free_writes_the_brief(night, caplog):
    night.model_reply = [FailedCall(AT_CAPACITY), FailedCall(AT_CAPACITY), GOOD]

    with caplog.at_level(logging.INFO):
        assert night.run() == 0

    [email] = night.sent
    assert "Retki to Nuuksio on Thu 1 Oct" in email.text
    assert "could not be written" not in email.text
    assert len(night.model_calls) == 3
    assert night.pauses == [60, 180]
    busy = [r.getMessage() for r in caplog.records if "busy" in r.getMessage()]
    assert len(busy) == 2
    assert "at capacity" in busy[0] and "1 min" in busy[0]
    assert "3 min" in busy[1]


@pytest.mark.parametrize("stderr", [
    "ERROR: Not logged in. Run `codex login` to sign in.\n",
    "ERROR: unexpected status 401 Unauthorized: Your refresh token has expired. Please log out and sign in again.\n",
    # A plan's usage limit is a rate limit too, but it lasts hours: waiting minutes won't help.
    "ERROR: You've hit your usage limit. Upgrade to Pro or try again in 3 days.\n",
])
def test_a_signed_out_model_fails_at_once_with_the_usual_brief(night, stderr):
    night.model_reply = [FailedCall(stderr)]

    assert night.run() == 0

    [email] = night.sent
    assert "Tonight's Digest could not be written" in email.text
    assert len(night.model_calls) == 1
    assert night.pauses == []


def test_signed_out_wins_over_a_busy_word_in_the_same_error(night):
    night.model_reply = [FailedCall("ERROR: 401 Unauthorized (rate limit headers: none). Not logged in.\n")]

    night.run()

    assert len(night.model_calls) == 1 and night.pauses == []


def test_a_message_that_mentions_capacity_is_not_mistaken_for_a_busy_model(night):
    # codex echoes the prompt on stderr, and the family's messages are in the prompt.
    night.sources["gmail"][0].body = "The hall is at capacity, 429 seats, so come early."
    night.model_reply = lambda prompt: FailedCall(f"user\n{prompt}\nERROR: stream disconnected\n")

    night.run()

    assert len(night.model_calls) == 1 and night.pauses == []


def test_claude_overloaded_is_tried_again(night):
    night.config["llm"] = {"backend": "claude"}
    night.model_reply = [FailedCall('API Error: 529 {"type":"error","error":{"type":"overloaded_error"}}'),
                         GOOD]

    night.run()

    [email] = night.sent
    assert "Retki to Nuuksio" in email.text
    assert night.pauses == [60]


def test_a_model_busy_all_evening_stops_inside_the_budget_with_the_usual_brief(night, caplog):
    # Each try may take the whole timeout, so the 10 minute pause would end past the budget.
    night.config["llm"]["timeout_seconds"] = 1200
    night.model_reply = [FailedCall(AT_CAPACITY)] * 4

    with caplog.at_level(logging.INFO):
        assert night.run() == 0

    [email] = night.sent
    assert "Tonight's Digest could not be written" in email.text
    assert len(night.model_calls) == 3
    assert night.pauses == [60, 180]
    assert any("still busy after 3 attempts" in r.getMessage() for r in caplog.records)


def test_each_pause_is_used_once_then_the_night_gives_up(night):
    night.model_reply = [FailedCall(AT_CAPACITY)] * 4

    night.run()

    [email] = night.sent
    assert "Tonight's Digest could not be written" in email.text
    assert len(night.model_calls) == 4
    assert night.pauses == [60, 180, 600]


def test_the_doctor_check_does_not_wait_for_a_busy_model(night, monkeypatch, capsys):
    del night.config["kids"][0]["myclub_ical_url"]  # no network in tests
    night.config["wilma"]["enabled"] = False
    night.config["whatsapp"]["enabled"] = False
    monkeypatch.setattr(ops, "launchctl_loaded", lambda: set())
    night.model_reply = [FailedCall(AT_CAPACITY)]

    night.cli("doctor")

    assert len(night.model_calls) == 1 and night.pauses == []
    assert "at capacity" in capsys.readouterr().out


def test_the_setup_page_preview_waits_less_than_the_evening_run(night, capsys):
    # The setup page gives up on its preview job after 15 minutes, collecting included.
    night.model_reply = [FailedCall(AT_CAPACITY)] * 4

    assert night.run("--preview") == 0

    assert night.pauses == [60, 180]
    assert "could not be written" in capsys.readouterr().out


@pytest.mark.parametrize("stderr, pauses", [
    ("tokens used\n1,503\nERROR: stream disconnected\n", []),  # a count, not a status
    ("ERROR: unexpected status 503 Service Unavailable\n", [60]),
])
def test_a_number_is_a_busy_status_only_as_a_status(night, stderr, pauses):
    night.model_reply = [FailedCall(stderr), GOOD]

    night.run()

    assert night.pauses == pauses
