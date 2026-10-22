"""The gateway adapter of the llm package (ADR 0023): the maintainers' gateway called with the
Gateway Key (ADR 0014), what its HTTP answers mean, a busy gateway tried again, doctor's check
and setup's test call. The gateway is fake_model.GatewayServer, the secrets store the `system`
fixture's, and the pauses are on the `pauses` fixture's clock."""
from __future__ import annotations

import json
import logging

import pytest
import requests

from fake_model import FailedCall, GatewayServer, HTTPReply
from family_brief import gateway as api, llm, setup_ai
from family_brief.config import LLMConfig

KEY = "sk-bf-fake-gateway-key"
URL = "https://gateway.example.test"
REPLY = {"per_kid": [], "calendar_events": [], "message_digest": "Swimming on Thursday"}
OVERLOADED = HTTPReply(529, {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})
BUDGET_EXCEEDED = HTTPReply(402, {"type": "error", "error": {
    "type": "api_error", "message": "Budget exceeded: VK budget exceeded: 0.1766 >= 0.1000 dollars"}})
UNKNOWN_KEY = HTTPReply(401, {"type": "error", "error": {"type": "authentication_error",
                                                         "message": "virtual key not found"}})
REFUSAL = HTTPReply(200, {"type": "message", "role": "assistant", "content": [],
                          "stop_reason": "refusal", "usage": {"input_tokens": 900, "output_tokens": 0}})


@pytest.fixture
def server(home, system, monkeypatch):
    system.secrets[api.ACCOUNT] = KEY
    server = GatewayServer(monkeypatch)
    server.replies = REPLY
    return server


@pytest.fixture
def gateway(server):
    return llm.for_llm(LLMConfig(backend="gateway", gateway_url=URL))


def test_the_gateway_is_named_and_probed_at_its_configured_host(gateway):
    assert (gateway.name, gateway.display_name, gateway.probe_host) == \
        ("gateway", "Claude", "gateway.example.test")
    assert gateway.program() is None
    assert api.SETUP in gateway.install_hint()


def test_the_gateway_address_defaults_to_the_maintainers_gateway(server):
    gateway = llm.for_llm(LLMConfig(backend="gateway"))

    gateway.run("p", "s", timeout=60, budget=0)

    assert gateway.probe_host == "gateway.parentrecap.com"
    assert server.requests[0].url == "https://gateway.parentrecap.com/v1/messages"


def test_the_gateway_is_sent_the_prompt_with_the_key_in_its_header(gateway, server):
    reply = gateway.run("the night's messages", "the system prompt", timeout=60, budget=0)

    assert reply.data == REPLY
    assert reply.usage == {"input": 1200, "cache_write": 0, "cached_input": 0, "output": 300}
    assert reply.model == "claude-haiku-5-5"
    [request] = server.requests
    assert request.url == f"{URL}/v1/messages"
    assert request.prompt == "the night's messages"
    assert request.headers == {"x-api-key": KEY, "anthropic-version": "2023-06-01"}
    assert request.body["model"] == "parent-recap" and request.body["system"] == "the system prompt"


def test_a_model_left_from_another_backend_is_not_asked_of_the_gateway(server):
    llm.for_llm(LLMConfig(backend="gateway", gateway_url=URL, model="sonnet")).run(
        "p", "s", timeout=60, budget=0)

    assert server.requests[0].body["model"] == "parent-recap"


def test_without_a_key_no_call_is_made_and_it_says_how_to_store_one(gateway, server, system):
    del system.secrets[api.ACCOUNT]

    with pytest.raises(api.NoKey, match="parent-recap setup gateway"):
        gateway.run("p", "s", timeout=60, budget=0)

    assert server.requests == []


def test_a_key_the_keychain_asks_about_is_said_so(gateway, server, system):
    system.secrets.asks.add(api.ACCOUNT)

    with pytest.raises(api.NoKey, match="asks for the Keychain password"):
        gateway.run("p", "s", timeout=60, budget=0)

    assert server.requests == []


def test_an_error_that_echoes_the_key_says_key_in_its_place(gateway, server):
    server.replies = HTTPReply(400, {"type": "error", "error": {
        "type": "invalid_request_error", "message": f"bad request for {KEY}"}})

    with pytest.raises(api.GatewayError) as raised:
        gateway.run("p", "s", timeout=60, budget=0)

    assert KEY not in str(raised.value) and "<key>" in str(raised.value)


# ── What the gateway answers

def test_a_busy_gateway_is_tried_again_after_a_pause(gateway, server, pauses):
    server.replies = [OVERLOADED, REPLY]

    reply = gateway.run("p", "s", timeout=60, budget=llm.CALL_BUDGET)

    assert reply.data == REPLY and reply.waited == llm.BUSY_PAUSES[0]
    assert pauses == [llm.BUSY_PAUSES[0]]


def test_a_model_overloaded_twice_then_free_answers(gateway, server, pauses, caplog):
    server.replies = [OVERLOADED, HTTPReply(503, "Service Unavailable"), REPLY]

    with caplog.at_level(logging.INFO):
        assert gateway.run("p", "s", timeout=60, budget=llm.CALL_BUDGET).data == REPLY

    assert len(server.requests) == 3 and pauses == [60, 180]
    busy = [r.getMessage() for r in caplog.records if "busy on attempt" in r.getMessage()]
    assert "HTTP 529" in busy[0] and "1 min" in busy[0]


def test_rate_limited_is_tried_again(gateway, server, pauses):
    server.replies = [HTTPReply(429, {"type": "error", "error": {"type": "rate_limit_error",
                                                                 "message": "Rate limited"}}), REPLY]

    assert gateway.run("p", "s", timeout=60, budget=llm.CALL_BUDGET).data == REPLY

    assert pauses == [60]


@pytest.mark.parametrize("reply", [
    UNKNOWN_KEY,
    BUDGET_EXCEEDED,
    HTTPReply(403, "Forbidden"),
    HTTPReply(400, {"type": "error", "error": {"type": "invalid_request_error",
                                               "message": "prompt is too long"}}),
    # The gateway's own credit run out, said with a busy status: waiting won't fix it.
    HTTPReply(500, {"type": "error", "error": {"type": "api_error", "message":
                    "Your credit balance is too low to access the Anthropic API."}}),
    REFUSAL,
    HTTPReply(200, "<html>not the gateway</html>"),
])
def test_a_reply_waiting_wont_fix_fails_at_once(gateway, server, pauses, reply):
    server.replies = [reply]

    with pytest.raises(api.GatewayError):
        gateway.run("p", "s", timeout=60, budget=llm.CALL_BUDGET)

    assert len(server.requests) == 1 and pauses == []


@pytest.mark.parametrize("reply", [
    BUDGET_EXCEEDED,
    # Said with a busy status instead, it still won't pass by waiting minutes.
    HTTPReply(429, {"type": "error", "error": {"type": "rate_limit_error", "message":
                    "Budget exceeded: VK budget exceeded: 0.1766 >= 0.1000 dollars"}}),
])
def test_a_key_out_of_credit_for_the_day_fails_at_once_and_says_so(gateway, server, pauses, reply):
    server.replies = [reply]

    with pytest.raises(api.GatewayError) as raised:
        gateway.run("p", "s", timeout=60, budget=llm.CALL_BUDGET)

    assert api.spent(str(raised.value))
    assert len(server.requests) == 1 and pauses == []


def test_a_reply_that_mentions_a_budget_exceeded_is_not_taken_for_one(gateway, server):
    digest = "**Mia**\n- Budget exceeded: the class trip costs more this year"
    server.replies = [HTTPReply(200, {"content": [{"type": "text", "text": json.dumps(
        {"message_digest": digest, "per_kid": [], "calendar_events": []})}], "stop_reason": "end_turn"})]

    assert gateway.run("p", "s", timeout=60, budget=0).data["message_digest"] == digest


def test_any_other_failure_does_not_say_the_credit_ran_out(gateway, server):
    server.replies = [UNKNOWN_KEY]

    with pytest.raises(api.GatewayError) as raised:
        gateway.run("p", "s", timeout=60, budget=0)

    assert not api.spent(str(raised.value))


def test_a_refusal_is_a_failed_call_not_an_empty_reply(gateway, server):
    server.replies = [REFUSAL]

    with pytest.raises(api.GatewayError, match="refused"):
        gateway.run("p", "s", timeout=60, budget=0)


def test_a_call_that_times_out_once_is_tried_again(gateway, server, pauses):
    server.replies = [requests.ReadTimeout("read timed out"), REPLY]

    assert gateway.run("p", "s", timeout=60, budget=llm.CALL_BUDGET).data == REPLY

    assert len(server.requests) == 2


def test_a_call_that_times_out_twice_fails(gateway, server, pauses):
    server.replies = [requests.ConnectTimeout("timed out")] * 2

    with pytest.raises(RuntimeError, match="timed out twice"):
        gateway.run("p", "s", timeout=60, budget=llm.CALL_BUDGET)

    assert len(server.requests) == 2


def test_a_gateway_that_cannot_be_reached_fails_at_once_saying_where(gateway, server, pauses):
    server.replies = [requests.ConnectionError("Failed to resolve gateway.example.test")]

    with pytest.raises(api.GatewayError, match="couldn't reach the gateway at gateway.example.test"):
        gateway.run("p", "s", timeout=60, budget=llm.CALL_BUDGET)

    assert len(server.requests) == 1 and pauses == []


def test_a_model_busy_all_evening_stops_inside_the_budget(gateway, server, pauses):
    server.replies = [OVERLOADED] * 4

    with pytest.raises(api.GatewayError):
        gateway.run("p", "s", timeout=60, budget=llm.CALL_BUDGET)

    assert len(server.requests) == 4 and pauses == [60, 180, 600]


# ── doctor's check

def test_the_gateway_check_says_it_answers_and_the_key_works(gateway, server):
    assert gateway.check() == [llm.Finding("ok", "gateway", f"answers at {URL}"),
                               llm.Finding("ok", "gateway-key", "works, test call succeeded")]
    [request] = server.requests
    assert request.headers["x-api-key"] == KEY


def test_the_gateway_check_says_when_it_does_not_answer(gateway, server):
    server.replies = [requests.ConnectionError("Connection refused")]

    [answers] = gateway.check()  # nothing to say of the key

    assert answers.level == "fail" and URL in answers.detail and "llm.gateway_url" in answers.detail


def test_the_gateway_check_fails_a_key_it_doesnt_know(gateway, server):
    server.replies = UNKNOWN_KEY

    answers, key = gateway.check()

    assert answers.level == "ok"
    assert (key.level, key.key) == ("fail", "gateway-key") and "revoked" in key.detail


def test_the_gateway_check_warns_of_a_key_past_todays_budget(gateway, server):
    server.replies = [BUDGET_EXCEEDED]

    _, key = gateway.check()

    assert key.level == "warn" and "today's budget" in key.detail


def test_the_gateway_check_fails_without_a_key_and_calls_with_none(gateway, server, system):
    del system.secrets[api.ACCOUNT]
    server.replies = UNKNOWN_KEY

    answers, key = gateway.check()

    assert answers.level == "ok"
    assert (key.level, key.key) == ("fail", "gateway-key")
    assert "the evening Brief can't call the gateway" in key.detail
    [request] = server.requests
    assert request.headers["x-api-key"] == ""


def test_the_gateway_check_does_not_wait_for_a_busy_gateway(gateway, server, pauses):
    server.replies = [OVERLOADED]

    _, key = gateway.check()

    assert key.level == "fail" and "529" in key.detail
    assert len(server.requests) == 1 and pauses == []


# ── setup's test call

def test_setup_tests_the_pasted_key_and_stores_it(server, system):
    del system.secrets[api.ACCOUNT]

    assert setup_ai.gateway_sign_in(URL, f"  {KEY}\n") == ("saved", {})

    [request] = server.requests
    assert request.url == f"{URL}/v1/messages" and request.headers["x-api-key"] == KEY
    assert system.secrets == {api.ACCOUNT: KEY}


def test_a_failed_test_call_says_what_the_gateway_said_without_the_key(server, system):
    del system.secrets[api.ACCOUNT]
    server.replies = [FailedCall(f"upstream error for {KEY}")]

    result, said = setup_ai.gateway_sign_in(URL, KEY)

    assert result == "test-call-failed" and "HTTP 500" in said["error"]
    assert KEY not in said["error"] and "<key>" in said["error"]
    assert system.secrets == {}
