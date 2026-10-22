"""The gateway backend (ADR 0014): a Household with a Gateway Key and no Claude or ChatGPT
subscription gets its Brief from the maintainers' gateway, which the program calls itself.

The model is the harness's in-memory Backend, and a failed call is the error the gateway adapter
raises for it; what the gateway's HTTP answers mean is the adapter's own test's
(integration/test_llm_gateway.py). Assertions are on the email, the model calls, doctor's lines,
the setup step's JSON, and every place the Gateway Key must never reach (ADR 0005)."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from conftest import msg

from family_brief import gateway as api, install_record, ops
from family_brief.system import secrets

KEY = "sk-bf-fake-gateway-key"
ACCOUNT = "gateway-key"
URL = "https://gateway.example.test"
GOOD = {"per_kid": [{"kid": "Mia", "notices": [{"text": "Retki to Nuuksio on Thu 1 Oct", "refs": ["1001"]}],
                     "action_items": []}],
        "calendar_events": [], "message_digest": "**Mia**\n- Retki to Nuuksio on Thu 1 Oct"}
FAILED = "Tonight's Digest could not be written"
# What the gateway adapter raises for each answer of the gateway's.
OVERLOADED = api.GatewayError("the gateway answered HTTP 529: Overloaded", 529)
BUDGET_EXCEEDED = api.GatewayError(f"{api.SPENT_MESSAGE} (the gateway answered HTTP 402: Budget "
                                   "exceeded: VK budget exceeded: 0.1766 >= 0.1000 dollars)", 402)
UNKNOWN_KEY = api.GatewayError("the gateway answered HTTP 401: virtual key not found", 401)
NO_ANSWER = api.GatewayError("couldn't reach the gateway at gateway.example.test (ConnectionError)")


@pytest.fixture
def night(harness):
    """An English night on the gateway with one message, and the Gateway Key in the Keychain."""
    harness.config["summary_language"] = "en"
    harness.config["llm"] = {"backend": "gateway", "gateway_url": URL}
    harness.secrets[ACCOUNT] = KEY
    harness.sources = {"gmail": [msg("gmail", "1001", "2026-09-27T08:15:00+03:00",
                                     "3B goes on a retki to Nuuksio on Thursday 1.10.",
                                     sender="teacher.3b@kilo.example.fi", subject="Retki")]}
    return harness


def everything_written(harness) -> str:
    """Every file the run left in the temporary HOME: the archive, the logs, state and config."""
    return "\n".join(p.read_text(errors="replace") for p in Path(harness.home).rglob("*") if p.is_file())


def assert_key_never_leaked(harness, printed: str, caplog) -> None:
    assert KEY not in printed
    assert KEY not in caplog.text
    assert not any(KEY in arg for cmd in harness.commands for arg in cmd)
    assert not any(KEY in str(email.__dict__) for email in harness.sent)
    assert not any(KEY in call.prompt + call.system_prompt for call in harness.model_calls)
    assert KEY not in everything_written(harness)


# ── the evening run


def test_the_brief_is_written_through_the_gateway(night, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    night.model_reply = GOOD

    assert night.run() == 0

    [email] = night.sent
    assert "Retki to Nuuksio on Thu 1 Oct" in email.text and FAILED not in email.text
    [call] = night.model_calls
    assert night.model.served_by == ["gateway"]
    assert "sorts out a Household's school and hobby messages" in call.system_prompt
    assert "Retki" not in call.system_prompt and "\"messages\"" in call.prompt
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

    assert night.model.served_by == ["gateway", "gateway"]


def test_the_gateway_address_defaults_to_the_maintainers_gateway(night):
    del night.config["llm"]["gateway_url"]
    night.model_reply = GOOD

    night.run()

    assert set(night.probed) == {"gateway.parentrecap.com"}


@pytest.mark.parametrize("error", [UNKNOWN_KEY, OVERLOADED, NO_ANSWER,
                                   api.GatewayError("the model refused to answer", 200)])
def test_a_failed_call_gives_the_usual_brief(night, error):
    night.model_reply = [error]

    assert night.run() == 0

    [email] = night.sent
    assert FAILED in email.text


SPENT = "the Gateway Key has used up today's credit. It comes back within a day"


def test_a_key_out_of_credit_for_the_day_says_so(night):
    night.config["email"]["to"] = ["parent@example.com", {"address": "partner@example.com", "language": "zh"}]
    night.model_reply = [BUDGET_EXCEEDED]

    assert night.run() == 0

    assert len(night.model_calls) == 1
    en, zh = sorted(night.sent, key=lambda email: "今日" in email.text)
    assert SPENT in en.text and "tomorrow evening's Brief" in en.text
    assert "Retki" in en.text  # the raw messages still follow
    assert "Gateway Key 今天的额度已用完" in zh.text


def test_any_other_failure_does_not_say_the_credit_ran_out(night):
    night.model_reply = [UNKNOWN_KEY]

    night.run()

    assert FAILED in night.sent[0].text and "credit" not in night.sent[0].text


def test_without_a_key_the_log_says_how_to_store_one(night, caplog):
    del night.secrets[ACCOUNT]
    night.model_reply = [api.NoKey(f"no Gateway Key in the Keychain (store one with {api.SETUP})")]

    with caplog.at_level(logging.INFO):
        night.run()

    assert FAILED in night.sent[0].text
    assert "parent-recap setup gateway" in caplog.text


def test_the_claude_backend_still_runs_claude(night):
    night.config["llm"] = {"backend": "claude"}
    night.secrets["claude-oauth-token"] = "sk-ant-oat01-abc"
    night.model_reply = GOOD

    night.run()

    assert night.model.served_by == ["claude"]
    assert set(night.probed) == {"api.anthropic.com"}


# ── doctor


def doctor_lines(harness, monkeypatch, capsys) -> dict[str, str]:
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["wilma"]["enabled"] = False
    harness.config["whatsapp"]["enabled"] = False
    harness.cli("doctor")
    out = capsys.readouterr().out
    assert KEY not in out
    return {item: line for line in out.splitlines()
            for item in ("AI gateway", "Gateway Key") if f" {item}:" in line}


def test_doctor_says_the_gateway_answers_and_the_key_works(night, monkeypatch, capsys):
    lines = doctor_lines(night, monkeypatch, capsys)

    assert lines["AI gateway"].startswith(ops.OK) and URL in lines["AI gateway"]
    assert lines["Gateway Key"].startswith(ops.OK) and "works" in lines["Gateway Key"]
    assert night.model.served_by == ["gateway"] and night.model_calls[0].budget == 0
    assert not any(cmd[0].endswith(("claude", "codex")) for cmd in night.commands)


def test_doctor_says_when_the_gateway_does_not_answer(night, monkeypatch, capsys):
    night.model_reply = [NO_ANSWER]

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
    del night.secrets[ACCOUNT]
    night.model_reply = [UNKNOWN_KEY]

    lines = doctor_lines(night, monkeypatch, capsys)

    assert lines["AI gateway"].startswith(ops.OK)
    assert lines["Gateway Key"].startswith(ops.FAIL) and "parent-recap setup gateway" in lines["Gateway Key"]
    assert len(night.model_calls) == 1


def test_doctor_says_what_a_busy_gateway_said(night, monkeypatch, capsys):
    night.model_reply = [OVERLOADED]

    lines = doctor_lines(night, monkeypatch, capsys)

    assert lines["Gateway Key"].startswith(ops.FAIL) and "529" in lines["Gateway Key"]


# ── setup gateway


@pytest.fixture
def desktop(harness):
    """A desktop session, as when the family runs setup on their own Mac, with the gateway's
    address in the config."""
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

    assert desktop.cli("setup", "gateway") == 0

    res, printed = result(capsys)
    assert res == {"result": "saved", "test_call": "ok"}
    assert len(desktop.dialog.shown) == 1
    assert desktop.model.served_by == ["gateway"]
    assert desktop.secrets == {ACCOUNT: KEY}
    assert install_record.entries("keychain") == [ACCOUNT]
    # No command line ever holds the key (ADR 0005, 0009).
    assert not any(KEY in part for command in desktop.commands for part in command)
    assert_key_never_leaked(desktop, printed, caplog)


def test_a_key_the_gateway_does_not_know_is_not_stored(desktop, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    desktop.dialog.typed = KEY
    desktop.model_reply = [UNKNOWN_KEY]

    assert desktop.cli("setup", "gateway") == 1

    res, printed = result(capsys)
    assert res["result"] == "rejected" and "setup gateway" in res["next"]
    assert desktop.secrets == {}
    assert_key_never_leaked(desktop, printed, caplog)


def test_a_key_past_todays_budget_works_and_is_stored(desktop, capsys):
    desktop.dialog.typed = KEY
    desktop.model_reply = [BUDGET_EXCEEDED]

    assert desktop.cli("setup", "gateway") == 0

    assert result(capsys)[0]["result"] == "saved"
    assert desktop.secrets == {ACCOUNT: KEY}


def test_a_gateway_that_does_not_answer_stores_nothing(desktop, capsys):
    desktop.dialog.typed = KEY
    desktop.model_reply = [NO_ANSWER]

    assert desktop.cli("setup", "gateway") == 1

    res, _ = result(capsys)
    assert res["result"] == "no-connection" and "online" in res["next"]
    assert desktop.secrets == {}


def test_a_failed_test_call_says_what_the_gateway_said(desktop, capsys):
    desktop.dialog.typed = KEY
    desktop.model_reply = [api.GatewayError("the gateway answered HTTP 500: upstream error", 500)]

    assert desktop.cli("setup", "gateway") == 1

    res, _ = result(capsys)
    assert res["result"] == "test-call-failed" and "HTTP 500: upstream error" in res["error"]
    assert desktop.secrets == {}


def test_something_that_is_not_a_key_is_never_sent(desktop, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    desktop.dialog.typed = "my-mac-password"

    assert desktop.cli("setup", "gateway") == 1

    res, printed = result(capsys)
    assert res["result"] == "not-a-key"
    assert desktop.model_calls == [] and desktop.secrets == {}
    assert "my-mac-password" not in printed and "my-mac-password" not in caplog.text


def test_closing_the_dialog_stores_nothing(desktop, capsys):
    desktop.dialog.button = "cancel"

    assert desktop.cli("setup", "gateway") == 1

    assert result(capsys)[0]["result"] == "cancelled"
    assert desktop.model_calls == [] and desktop.secrets == {}


def test_a_keychain_out_of_reach_says_to_run_it_outside_tmux_or_ssh(desktop, capsys):
    desktop.secrets.refuses = secrets.SecretsError("not reachable", "not-reachable", -25308)
    desktop.dialog.typed = KEY

    assert desktop.cli("setup", "gateway") == 1

    res = result(capsys)[0]
    assert res["result"] == "keychain-not-reachable" and res["code"] == -25308
    assert "outside tmux or SSH" in res["next"]


def test_without_a_config_the_key_is_tested_and_stored(desktop, capsys, tmp_path, monkeypatch):
    import sys
    from family_brief import __main__ as cli
    desktop.dialog.typed = KEY
    monkeypatch.setattr(sys, "argv", ["parent-recap", "-c", str(tmp_path / "none.yaml"), "setup", "gateway"])

    assert cli.main() == 0

    assert desktop.model.served_by == ["gateway"] and desktop.secrets == {ACCOUNT: KEY}


def test_setup_save_takes_the_gateway_as_the_ai(desktop, monkeypatch, capsys):
    import io
    import json
    import yaml
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"ai": "gateway"})))
    desktop.config["llm"] = {"backend": "claude"}

    assert desktop.cli("setup", "save") == 0

    saved = yaml.safe_load((desktop.home / ".family" / "config.yaml").read_text())
    assert saved["llm"]["backend"] == "gateway"
