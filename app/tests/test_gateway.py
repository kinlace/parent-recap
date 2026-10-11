"""The gateway backend (ADR 0014): a Household with a Gateway Key and no Claude or ChatGPT
subscription gets its Brief from the maintainers' gateway, which the program calls itself.

The outside edges are faked: the gateway's /v1/messages (`requests.post`, answered from the
harness's model replies), the Keychain and the secret dialog. Assertions are on the email, the
requests the gateway got, the pauses between them, doctor's lines, the setup step's JSON, and
every place the Gateway Key must never reach (ADR 0005)."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
import requests

from conftest import FailedCall, HTTPReply, msg

from family_brief import install_record, ops

KEY = "sk-bf-fake-gateway-key"
ACCOUNT = "gateway-key"
URL = "https://gateway.example.test"
GOOD = {"per_kid": [{"kid": "Mia", "notices": [{"text": "Retki to Nuuksio on Thu 1 Oct", "refs": ["g-1"]}],
                     "action_items": []}],
        "calendar_events": [], "message_digest": "**Mia**\n- Retki to Nuuksio on Thu 1 Oct"}
FAILED = "Tonight's Digest could not be written"
OVERLOADED = HTTPReply(529, {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})
BUDGET_EXCEEDED = HTTPReply(402, {"type": "error", "error": {
    "type": "api_error", "message": "Budget exceeded: VK budget exceeded: 0.1766 >= 0.1000 dollars"}})
UNKNOWN_KEY = HTTPReply(401, {"type": "error", "error": {"type": "authentication_error",
                                                         "message": "virtual key not found"}})
REFUSAL = HTTPReply(200, {"type": "message", "role": "assistant", "content": [],
                          "stop_reason": "refusal", "usage": {"input_tokens": 900, "output_tokens": 0}})


@pytest.fixture
def night(harness):
    """An English night on the gateway with one message, and the Gateway Key in the Keychain."""
    harness.config["summary_language"] = "en"
    harness.config["llm"] = {"backend": "gateway", "gateway_url": URL}
    harness.keychain[ACCOUNT] = KEY
    harness.sources = {"gmail": [msg("gmail", "g-1", "2026-09-27T08:15:00+03:00",
                                     "3B goes on a retki to Nuuksio on Thursday 1.10.",
                                     sender="teacher.3b@kilo.example.fi", subject="Retki", kid="Mia")]}
    return harness


def everything_written(harness) -> str:
    """Every file the run left in the temporary HOME: the archive, the logs, state and config."""
    return "\n".join(p.read_text(errors="replace") for p in Path(harness.home).rglob("*") if p.is_file())


def assert_key_never_leaked(harness, printed: str, caplog) -> None:
    assert KEY not in printed
    assert KEY not in caplog.text
    assert not any(KEY in arg for cmd in harness.commands for arg in cmd)
    assert not any(KEY in str(email.__dict__) for email in harness.sent)
    assert KEY not in everything_written(harness)


# ── the evening run


def test_the_brief_is_written_through_the_gateway_with_the_key(night, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    night.model_reply = GOOD

    assert night.run() == 0

    [email] = night.sent
    assert "Retki to Nuuksio on Thu 1 Oct" in email.text and FAILED not in email.text
    [call] = night.model_calls
    assert call.argv == ["POST", f"{URL}/v1/messages"]
    assert call.headers == {"x-api-key": KEY, "anthropic-version": "2023-06-01"}
    assert call.body["model"] == "parent-recap"
    assert "sorts out a Household's school and hobby messages" in call.body["system"]
    assert "Retki" not in call.body["system"] and "\"messages\"" in call.stdin
    # No Claude Code or Codex: the program makes the call itself.
    assert not any(cmd[0].endswith(("claude", "codex")) for cmd in night.commands)
    assert night.probed and set(night.probed) == {"gateway.example.test"}
    printed = "".join(capsys.readouterr())
    assert_key_never_leaked(night, printed, caplog)


def test_a_translation_goes_through_the_gateway_too(night):
    night.config["email"]["to"] = ["parent@example.com", {"address": "partner@example.com", "language": "fi"}]
    translated = {**GOOD, "message_digest": "**Mia**\n- Retki Nuuksioon to 1.10."}
    night.model_reply = [GOOD, translated]

    assert night.run() == 0

    assert len(night.model_calls) == 2
    assert all(call.argv == ["POST", f"{URL}/v1/messages"] for call in night.model_calls)


def test_a_model_left_from_another_backend_is_not_asked_of_the_gateway(night):
    night.config["llm"]["model"] = "sonnet"
    night.model_reply = GOOD

    night.run()

    assert night.model_calls[0].body["model"] == "parent-recap"


def test_the_gateway_address_defaults_to_the_maintainers_gateway(night):
    del night.config["llm"]["gateway_url"]
    night.model_reply = GOOD

    night.run()

    assert night.model_calls[0].argv == ["POST", "https://gateway.parentrecap.com/v1/messages"]
    assert set(night.probed) == {"gateway.parentrecap.com"}


def test_a_model_overloaded_twice_then_free_writes_the_brief(night, caplog):
    night.model_reply = [OVERLOADED, HTTPReply(503, "Service Unavailable"), GOOD]

    with caplog.at_level(logging.INFO):
        assert night.run() == 0

    [email] = night.sent
    assert "Retki to Nuuksio" in email.text
    assert len(night.model_calls) == 3 and night.pauses == [60, 180]
    busy = [r.getMessage() for r in caplog.records if "busy on attempt" in r.getMessage()]
    assert "HTTP 529" in busy[0] and "1 min" in busy[0]


def test_rate_limited_is_tried_again(night):
    night.model_reply = [HTTPReply(429, {"type": "error", "error": {"type": "rate_limit_error",
                                                                    "message": "Rate limited"}}), GOOD]

    night.run()

    assert night.pauses == [60] and "Retki to Nuuksio" in night.sent[0].text


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
def test_a_reply_waiting_wont_fix_fails_at_once_with_the_usual_brief(night, reply):
    night.model_reply = [reply]

    assert night.run() == 0

    [email] = night.sent
    assert FAILED in email.text
    assert len(night.model_calls) == 1 and night.pauses == []


def test_a_refusal_is_a_failed_call_not_an_empty_brief(night, caplog):
    night.model_reply = [REFUSAL]

    with caplog.at_level(logging.INFO):
        night.run()

    assert FAILED in night.sent[0].text
    assert "refused" in caplog.text


def test_a_call_that_times_out_once_is_tried_again(night):
    night.model_reply = [requests.ReadTimeout("read timed out"), GOOD]

    night.run()

    assert len(night.model_calls) == 2
    assert "Retki to Nuuksio" in night.sent[0].text


def test_a_call_that_times_out_twice_gives_the_usual_brief(night, caplog):
    night.model_reply = [requests.ConnectTimeout("timed out")] * 2

    with caplog.at_level(logging.INFO):
        night.run()

    assert len(night.model_calls) == 2
    assert FAILED in night.sent[0].text
    assert "timed out twice" in caplog.text


def test_a_gateway_that_cannot_be_reached_gives_the_usual_brief(night, caplog):
    night.model_reply = [requests.ConnectionError("Failed to resolve gateway.example.test")]

    with caplog.at_level(logging.INFO):
        night.run()

    assert len(night.model_calls) == 1 and night.pauses == []
    assert FAILED in night.sent[0].text
    assert "couldn't reach the gateway at gateway.example.test" in caplog.text


def test_a_model_busy_all_evening_stops_inside_the_budget(night):
    night.model_reply = [OVERLOADED] * 4

    night.run()

    assert FAILED in night.sent[0].text
    assert len(night.model_calls) == 4 and night.pauses == [60, 180, 600]


def test_without_a_key_no_call_is_made_and_the_log_says_how_to_store_one(night, caplog):
    del night.keychain[ACCOUNT]

    with caplog.at_level(logging.INFO):
        night.run()

    assert night.model_calls == []
    assert FAILED in night.sent[0].text
    assert "parent-recap setup gateway" in caplog.text


def test_a_key_the_keychain_asks_about_is_said_so(night, caplog):
    night.keychain_asks.add(ACCOUNT)

    with caplog.at_level(logging.INFO):
        night.run()

    assert night.model_calls == []
    assert "asks for the Keychain password" in caplog.text


def test_the_key_never_reaches_the_output_logs_or_files_when_the_call_fails(night, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    night.model_reply = [HTTPReply(401, {"type": "error", "error": {
        "type": "authentication_error", "message": f"invalid x-api-key {KEY}"}})]

    night.run()

    assert FAILED in night.sent[0].text
    printed = "".join(capsys.readouterr())
    assert_key_never_leaked(night, printed, caplog)


def test_the_claude_backend_still_runs_claude(night):
    night.config["llm"] = {"backend": "claude"}
    night.keychain["claude-oauth-token"] = "sk-ant-oat01-abc"
    night.model_reply = GOOD

    night.run()

    [call] = night.model_calls
    assert call.argv[0] == "claude" and call.headers is None
    assert set(night.probed) == {"api.anthropic.com"}


# ── doctor


def doctor_lines(harness, monkeypatch, capsys) -> dict[str, str]:
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["wilma"]["enabled"] = False
    harness.config["whatsapp"]["enabled"] = False
    monkeypatch.setattr(ops, "launchctl_loaded", lambda: set())
    harness.cli("doctor")
    out = capsys.readouterr().out
    assert KEY not in out
    return {item: line for line in out.splitlines()
            for item in ("AI gateway", "Gateway Key") if f" {item}:" in line}


def test_doctor_says_the_gateway_answers_and_the_key_works(night, monkeypatch, capsys):
    night.model_reply = "OK"

    lines = doctor_lines(night, monkeypatch, capsys)

    assert lines["AI gateway"].startswith(ops.OK) and URL in lines["AI gateway"]
    assert lines["Gateway Key"].startswith(ops.OK) and "works" in lines["Gateway Key"]
    [call] = night.model_calls
    assert call.headers["x-api-key"] == KEY and call.argv == ["POST", f"{URL}/v1/messages"]
    assert not any(cmd[0].endswith(("claude", "codex")) for cmd in night.commands)


def test_doctor_says_when_the_gateway_does_not_answer(night, monkeypatch, capsys):
    night.model_reply = [requests.ConnectionError("Connection refused")]

    lines = doctor_lines(night, monkeypatch, capsys)

    assert lines["AI gateway"].startswith(ops.FAIL)
    assert URL in lines["AI gateway"] and "llm.gateway_url" in lines["AI gateway"]


def test_doctor_says_when_the_gateway_does_not_know_the_key(night, monkeypatch, capsys):
    night.model_reply = [UNKNOWN_KEY]

    lines = doctor_lines(night, monkeypatch, capsys)

    assert lines["AI gateway"].startswith(ops.OK)
    assert lines["Gateway Key"].startswith(ops.FAIL)
    assert "revoked" in lines["Gateway Key"] and "parent-recap setup gateway" in lines["Gateway Key"]


def test_doctor_says_a_key_past_todays_budget_works_but_waits_for_tomorrow(night, monkeypatch, capsys):
    night.model_reply = [BUDGET_EXCEEDED]

    lines = doctor_lines(night, monkeypatch, capsys)

    assert lines["Gateway Key"].startswith(ops.WARN.strip()) and "today's budget" in lines["Gateway Key"]


def test_doctor_without_a_key_still_checks_the_gateway_answers(night, monkeypatch, capsys):
    del night.keychain[ACCOUNT]
    night.model_reply = [UNKNOWN_KEY]

    lines = doctor_lines(night, monkeypatch, capsys)

    assert lines["AI gateway"].startswith(ops.OK)
    assert lines["Gateway Key"].startswith(ops.FAIL) and "parent-recap setup gateway" in lines["Gateway Key"]
    [call] = night.model_calls
    assert call.headers["x-api-key"] == ""


def test_doctor_does_not_wait_for_a_busy_gateway(night, monkeypatch, capsys):
    night.model_reply = [OVERLOADED]

    lines = doctor_lines(night, monkeypatch, capsys)

    assert lines["Gateway Key"].startswith(ops.FAIL) and "529" in lines["Gateway Key"]
    assert len(night.model_calls) == 1 and night.pauses == []


# ── setup gateway


@pytest.fixture
def desktop(harness, monkeypatch):
    """A desktop session, as when the family runs setup on their own Mac, with the gateway's
    address in the config."""
    for var in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"):
        monkeypatch.delenv(var, raising=False)
    harness.config["llm"] = {"backend": "gateway", "gateway_url": URL}
    return harness


def result(capsys) -> tuple[dict, str]:
    import json
    out, err = capsys.readouterr()
    lines = out.strip().splitlines()
    assert len(lines) == 1, f"expected one JSON line, got: {out!r}"
    return json.loads(lines[0]), out + err


def test_a_key_pasted_in_the_dialog_is_tested_and_stored(desktop, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    desktop.dialog.typed = f"  {KEY}\n"
    desktop.model_reply = "OK"

    assert desktop.cli("setup", "gateway") == 0

    res, printed = result(capsys)
    assert res == {"result": "saved", "test_call": "ok"}
    assert len(desktop.dialog.shown) == 1 and "hidden answer" in desktop.dialog.shown[0]
    [call] = desktop.model_calls
    assert call.argv == ["POST", f"{URL}/v1/messages"] and call.headers["x-api-key"] == KEY
    assert desktop.keychain == {ACCOUNT: KEY}
    assert install_record.entries("keychain") == [ACCOUNT]
    # Typed into security -i, so no command line ever holds it (ADR 0005, 0009).
    assert ["/usr/bin/security", "-i"] in desktop.commands
    assert_key_never_leaked(desktop, printed, caplog)


def test_a_key_the_gateway_does_not_know_is_not_stored(desktop, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    desktop.dialog.typed = KEY
    desktop.model_reply = [UNKNOWN_KEY]

    assert desktop.cli("setup", "gateway") == 1

    res, printed = result(capsys)
    assert res["result"] == "rejected" and "setup gateway" in res["next"]
    assert desktop.keychain == {}
    assert_key_never_leaked(desktop, printed, caplog)


def test_a_key_past_todays_budget_works_and_is_stored(desktop, capsys):
    desktop.dialog.typed = KEY
    desktop.model_reply = [BUDGET_EXCEEDED]

    assert desktop.cli("setup", "gateway") == 0

    assert result(capsys)[0]["result"] == "saved"
    assert desktop.keychain == {ACCOUNT: KEY}


def test_a_gateway_that_does_not_answer_stores_nothing(desktop, capsys):
    desktop.dialog.typed = KEY
    desktop.model_reply = [requests.ConnectionError("Connection refused")]

    assert desktop.cli("setup", "gateway") == 1

    res, _ = result(capsys)
    assert res["result"] == "no-connection" and "online" in res["next"]
    assert desktop.keychain == {}


def test_a_failed_test_call_says_what_the_gateway_said_without_the_key(desktop, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    desktop.dialog.typed = KEY
    desktop.model_reply = [FailedCall(f"upstream error for {KEY}")]

    assert desktop.cli("setup", "gateway") == 1

    res, printed = result(capsys)
    assert res["result"] == "test-call-failed" and "HTTP 500" in res["error"]
    assert "<key>" in res["error"]
    assert desktop.keychain == {}
    assert_key_never_leaked(desktop, printed, caplog)


def test_something_that_is_not_a_key_is_never_sent(desktop, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    desktop.dialog.typed = "my-mac-password"

    assert desktop.cli("setup", "gateway") == 1

    res, printed = result(capsys)
    assert res["result"] == "not-a-key"
    assert desktop.model_calls == [] and desktop.keychain == {}
    assert "my-mac-password" not in printed and "my-mac-password" not in caplog.text


def test_closing_the_dialog_stores_nothing(desktop, capsys):
    desktop.dialog.button = "cancel"

    assert desktop.cli("setup", "gateway") == 1

    assert result(capsys)[0]["result"] == "cancelled"
    assert desktop.model_calls == [] and desktop.keychain == {}


def test_a_keychain_out_of_reach_says_to_run_it_outside_tmux_or_ssh(desktop, capsys):
    desktop.keychain_refuses = -25308  # errSecInteractionNotAllowed
    desktop.dialog.typed = KEY
    desktop.model_reply = "OK"

    assert desktop.cli("setup", "gateway") == 1

    res = result(capsys)[0]
    assert res["result"] == "keychain-not-reachable" and res["code"] == -25308
    assert "outside tmux or SSH" in res["next"]


def test_without_a_config_the_key_is_tested_against_the_maintainers_gateway(desktop, capsys, tmp_path,
                                                                            monkeypatch):
    import sys
    from family_brief import __main__ as cli
    desktop.dialog.typed = KEY
    desktop.model_reply = "OK"
    monkeypatch.setattr(sys, "argv", ["parent-recap", "-c", str(tmp_path / "none.yaml"), "setup", "gateway"])

    assert cli.main() == 0

    assert desktop.model_calls[0].argv == ["POST", "https://gateway.parentrecap.com/v1/messages"]


def test_setup_save_takes_the_gateway_as_the_ai(desktop, monkeypatch, capsys):
    import io
    import json
    import yaml
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"ai": "gateway"})))
    desktop.config["llm"] = {"backend": "claude"}

    assert desktop.cli("setup", "save") == 0

    saved = yaml.safe_load((desktop.home / ".family" / "config.yaml").read_text())
    assert saved["llm"]["backend"] == "gateway"
