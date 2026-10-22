"""How long each model call may try a busy model again (at capacity, rate-limited, overloaded):
the evening run's the whole call budget, the setup page's preview less, doctor's check not at all.
How an adapter tries again, and which errors it doesn't try again, are its own tests'
(integration/test_llm_*.py). Assertions are on the budget each call to the in-memory Backend got,
and on the Brief a model that stays busy leaves."""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import msg
from family_brief import __main__ as main, llm, run as evening

GOOD = {"per_kid": [{"kid": "Mia", "notices": [{"text": "Retki to Nuuksio on Thu 1 Oct", "refs": ["1001"]}],
                     "action_items": []}],
        "calendar_events": [], "message_digest": "**Mia**\n- Retki to Nuuksio on Thu 1 Oct"}
STILL_BUSY = RuntimeError("codex exec failed (1). stderr tail='ERROR: Selected model is at capacity.'")


@pytest.fixture
def night(harness, tmp_path: Path):
    """An English night on Codex with one message."""
    codex = tmp_path / "codex"
    codex.write_text("#!/bin/sh\n")
    codex.chmod(0o755)
    harness.config["summary_language"] = "en"
    harness.config["llm"] = {"backend": "codex", "codex_path": str(codex)}
    harness.sources = {"gmail": [msg("gmail", "1001", "2026-09-27T08:15:00+03:00",
                                     "3B goes on a retki to Nuuksio on Thursday 1.10.",
                                     sender="teacher.3b@kilo.example.fi", subject="Retki")]}
    return harness


def test_each_call_of_the_evening_run_may_wait_the_whole_budget(night):
    night.config["email"]["to"] = ["parent@example.com", {"address": "partner@example.com", "language": "fi"}]
    night.model_reply = [GOOD, {**GOOD, "message_digest": "**Mia**\n- Retki Nuuksioon to 1.10."}]

    assert night.run() == 0

    assert [call.budget for call in night.model_calls] == [llm.CALL_BUDGET] * 2


def test_a_model_busy_all_evening_gives_the_usual_brief(night):
    night.model_reply = [STILL_BUSY]

    assert night.run() == 0

    [email] = night.sent
    assert "Tonight's Digest could not be written" in email.text


def test_the_setup_page_preview_waits_less_than_the_evening_run(night, capsys):
    # The setup page gives up on its preview job after 15 minutes, collecting included.
    night.model_reply = [STILL_BUSY]

    assert night.run("--preview") == 0

    [call] = night.model_calls
    assert call.budget == evening.PREVIEW_BUDGET < llm.CALL_BUDGET
    assert "could not be written" in capsys.readouterr().out


def test_the_doctor_check_does_not_wait_for_a_busy_model(night, capsys):
    del night.config["kids"][0]["myclub_ical_url"]  # no network in tests
    night.config["wilma"]["enabled"] = False
    night.config["whatsapp"]["enabled"] = False
    night.model_reply = [STILL_BUSY]

    night.cli("doctor")

    [call] = night.model_calls
    assert call.budget == 0
    assert "at capacity" in capsys.readouterr().out
