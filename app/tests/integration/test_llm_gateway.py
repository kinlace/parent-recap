"""The gateway adapter of the llm package (ADR 0023): the maintainers' gateway called with the
Gateway Key (ADR 0014), and doctor's check of it. The gateway is the harness's fake /v1/messages
(conftest.py)."""
from __future__ import annotations

import pytest

from conftest import HTTPReply
from family_brief import gateway as api, llm
from family_brief.config import LLMConfig

KEY = "sk-bf-fake-gateway-key"
URL = "https://gateway.example.test"
REPLY = {"per_kid": [], "calendar_events": [], "message_digest": "Swimming on Thursday"}
OVERLOADED = HTTPReply(529, {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})


@pytest.fixture
def gateway(harness):
    harness.secrets[api.ACCOUNT] = KEY
    harness.model_reply = REPLY
    return llm.for_llm(LLMConfig(backend="gateway", gateway_url=URL))


def test_the_gateway_is_named_and_probed_at_its_configured_host(gateway):
    assert (gateway.name, gateway.display_name, gateway.probe_host) == \
        ("gateway", "Claude", "gateway.example.test")
    assert gateway.program() is None
    assert api.SETUP in gateway.install_hint()


def test_the_gateway_is_sent_the_prompt_with_the_key_in_its_header(gateway, harness):
    reply = gateway.run("the night's messages", "the system prompt", timeout=60, budget=0)

    assert reply.data == REPLY
    assert reply.usage == {"input": 1200, "cache_write": 0, "cached_input": 0, "output": 300}
    assert reply.model == "claude-haiku-5-5"
    [call] = harness.model_calls
    assert call.argv == ["POST", f"{URL}/v1/messages"]
    assert call.stdin == "the night's messages"
    assert call.headers["x-api-key"] == KEY


def test_a_busy_gateway_is_tried_again_after_a_pause(gateway, harness):
    harness.model_reply = [OVERLOADED, REPLY]

    reply = gateway.run("p", "s", timeout=60, budget=llm.CALL_BUDGET)

    assert reply.data == REPLY and reply.waited == llm.BUSY_PAUSES[0]
    assert harness.pauses == [llm.BUSY_PAUSES[0]]


def test_an_error_that_echoes_the_key_says_key_in_its_place(gateway, harness):
    harness.model_reply = HTTPReply(400, {"type": "error", "error": {
        "type": "invalid_request_error", "message": f"bad request for {KEY}"}})

    with pytest.raises(api.GatewayError) as raised:
        gateway.run("p", "s", timeout=60, budget=0)

    assert KEY not in str(raised.value) and "<key>" in str(raised.value)


def test_the_gateway_check_says_it_answers_and_the_key_works(gateway):
    assert gateway.check() == [llm.Finding("ok", "gateway", f"answers at {URL}"),
                               llm.Finding("ok", "gateway-key", "works, test call succeeded")]


def test_the_gateway_check_fails_a_key_it_doesnt_know(gateway, harness):
    harness.model_reply = HTTPReply(401, {"type": "error", "error": {
        "type": "authentication_error", "message": "virtual key not found"}})

    answers, key = gateway.check()

    assert answers.level == "ok"
    assert (key.level, key.key) == ("fail", "gateway-key") and "revoked" in key.detail


def test_the_gateway_check_fails_without_a_key(gateway, harness):
    del harness.secrets[api.ACCOUNT]

    _, key = gateway.check()

    assert (key.level, key.key) == ("fail", "gateway-key")
    assert "the evening Brief can't call the gateway" in key.detail
