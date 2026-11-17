"""Whether the evening run sends its Run Report (ADR 0048): where the setting starts when the config
says nothing, which follows how the Brief is sent, and that a stated setting holds either way. A
Hosted Household sends none."""
from __future__ import annotations

import pytest

from family_brief.config import Config

THROUGH_THE_GATEWAY = {}
FROM_ITS_OWN_GMAIL = {"email": {"from_gmail": True}, "gmail": {"username": "parent@example.com"}}
# Asked to send from its Gmail, but its AI is the gateway, so the Brief goes through it (ADR 0034).
GMAIL_ASKED_ON_THE_GATEWAY = {**FROM_ITS_OWN_GMAIL, "llm": {"backend": "gateway"}}


def config(sending: dict, **more: object) -> Config:
    return Config.model_validate({"kids": [], **sending, **more})


@pytest.mark.parametrize("sending, sends", [
    (THROUGH_THE_GATEWAY, True),
    (FROM_ITS_OWN_GMAIL, False),
    (GMAIL_ASKED_ON_THE_GATEWAY, True),
])
def test_unset_it_follows_how_the_brief_is_sent(sending, sends):
    assert config(sending).sends_run_report() is sends


@pytest.mark.parametrize("sending", [THROUGH_THE_GATEWAY, FROM_ITS_OWN_GMAIL])
@pytest.mark.parametrize("enabled", [True, False])
def test_a_stated_setting_holds_whichever_way_the_brief_is_sent(sending, enabled):
    assert config(sending, run_report={"enabled": enabled}).sends_run_report() is enabled


@pytest.mark.parametrize("enabled", [None, True])
def test_a_hosted_household_sends_none(enabled):
    assert config(THROUGH_THE_GATEWAY, hosted=True, run_report={"enabled": enabled}).sends_run_report() is False
