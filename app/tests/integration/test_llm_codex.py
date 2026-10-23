"""The Codex adapter of the llm package (ADR 0023): `codex exec` run isolated, found at the config's
llm.codex_path, a busy model (at capacity, rate-limited, overloaded) tried again after a pause
but one that can't work until someone acts (signed out, an expired token) not, and doctor's check
of it. The process is fake_model.ModelProcess, and the pauses are on the `pauses` fixture's clock."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from conftest import program
from fake_model import FailedCall, ModelProcess
from fake_system import MACOS, WINDOWS
from family_brief import llm
from family_brief.config import LLMConfig
from family_brief.llm import codex as codex_backend
from family_brief.system import profile_macos, profile_windows

REPLY = {"per_kid": [], "calendar_events": [], "message_digest": "Swimming on Thursday"}
AT_CAPACITY = "ERROR: Selected model is at capacity. Please try a different model.\n"


@pytest.fixture
def process(home, system, monkeypatch):
    process = ModelProcess("codex", monkeypatch)
    process.replies = REPLY
    return process


@pytest.fixture
def codex_path(process, tmp_path, monkeypatch):
    monkeypatch.setattr(codex_backend, "CODEX_BUNDLED", ())  # not this Mac's own apps
    return str(program(tmp_path / "bin", "codex"))


@pytest.fixture
def codex(codex_path):
    return llm.for_llm(LLMConfig(backend="codex", codex_path=codex_path))


@pytest.fixture
def signed_in(monkeypatch):
    """Codex signed in, as `codex login status` says, so check's first process is the model's;
    test_codex_check_says_signed_out_without_calling_it runs the status itself."""
    monkeypatch.setattr(codex_backend, "_signed_out", lambda _codex: False)


def test_codex_is_named_and_probed_as_the_brief_says(codex):
    assert (codex.name, codex.display_name, codex.probe_host) == ("codex", "Codex", "chatgpt.com")


def test_codex_runs_the_configured_program_isolated_with_the_instructions_first(codex, codex_path,
                                                                               process):
    reply = codex.run("the night's messages", "the system prompt", timeout=60, budget=0)

    assert reply.data == REPLY and reply.usage is None
    [call] = process.calls
    assert call.argv[:2] == [codex_path, "exec"]
    assert {"--ephemeral", "--ignore-user-config"} <= set(call.argv)
    assert call.argv[call.argv.index("--sandbox") + 1] == "read-only"
    # The read-only sandbox still lets commands read any file, so no command tools at all.
    disabled = {call.argv[i + 1] for i, a in enumerate(call.argv) if a == "--disable"}
    assert {"shell_tool", "unified_exec", "apps", "plugins"} <= disabled
    assert call.argv[-1] == "-"  # the prompt on stdin
    assert not any(call.stdin in a for a in call.argv)
    assert call.stdin.startswith("the system prompt\n\n---\n\nthe night's messages")
    assert not Path(call.argv[call.argv.index("-C") + 1]).exists()  # its own dir, gone after


def test_codex_passes_the_configured_model(codex_path, process):
    llm.for_llm(LLMConfig(backend="codex", codex_path=codex_path, model="gpt-6")).run(
        "p", "s", timeout=60, budget=0)

    [call] = process.calls
    assert call.argv[call.argv.index("-m") + 1] == "gpt-6"


def test_a_failed_codex_call_raises(codex, process):
    process.replies = FailedCall("stream error")

    with pytest.raises(RuntimeError, match="codex exec failed"):
        codex.run("p", "s", timeout=60, budget=0)


def test_codex_that_isnt_there_says_how_to_get_it(home, system, monkeypatch):
    monkeypatch.setattr(codex_backend, "CODEX_BUNDLED", ())
    codex = llm.for_llm(LLMConfig(backend="codex"))

    assert codex.program() is None
    with pytest.raises(RuntimeError, match=f"codex not found: {codex.install_hint()}"):
        codex.run("p", "s", timeout=60, budget=0)
    [finding] = codex.check()
    assert (finding.level, finding.key) == ("fail", "codex")
    assert codex.install_hint() in finding.detail and "llm.codex_path" in finding.detail


def test_codex_by_name_is_found_at_the_configured_path(codex_path):
    assert llm.named("codex", codex_path).program() == codex_path


def test_codex_inside_the_chatgpt_app_is_preferred(process, home, monkeypatch):
    bundled = program(home / "Applications" / "ChatGPT.app" / "Contents" / "Resources" / "codex-cli"
                      / "bin", "codex")
    monkeypatch.setattr(codex_backend, "CODEX_BUNDLED", (str(bundled),))

    llm.for_llm(LLMConfig(backend="codex")).run("p", "s", timeout=60, budget=0)

    [call] = process.calls
    assert call.argv[0] == str(bundled)


@pytest.mark.parametrize("on, line", [(MACOS, profile_macos.CODEX_INSTALL),
                                      (WINDOWS, profile_windows.CODEX_INSTALL)])
def test_codex_installs_with_openai_s_own_line_for_the_system(codex, system, on, line):
    system.profile = on

    assert codex.install_line() == line
    assert "https://github.com/openai/codex/releases/latest/download/install." in line


@pytest.mark.parametrize("on, terminal, apps", [(MACOS, "Terminal", True), (WINDOWS, "PowerShell", False)])
def test_a_missing_codex_says_the_setup_page_installs_it_and_on_a_mac_the_apps_have_one(
        codex, system, on, terminal, apps):
    system.profile = on

    hint = codex.install_hint()

    assert "the setup page" in hint and f"in {terminal} with {on.install_lines['codex']}" in hint
    assert ("ChatGPT desktop app" in hint) is apps


def test_codex_takes_no_pasted_token(codex, process):
    assert codex.test_token("sk-ant-oat01-pasted") is not None
    assert not process.calls


def test_codex_check_calls_a_signed_in_codex(codex, codex_path, process, signed_in):
    process.replies = {"ok": True}

    assert codex.check() == [llm.Finding("ok", "codex", f"call succeeded ({codex_path})")]


def test_codex_check_says_signed_out_without_calling_it(codex, codex_path, process):
    process.replies = [FailedCall("Not logged in\n")]  # `codex login status`'s exit code

    [finding] = codex.check()

    assert (finding.level, finding.key) == ("fail", "codex") and "not signed in" in finding.detail
    [status] = process.calls
    assert status.argv == [codex_path, "login", "status"]


# ── A busy model

def test_a_model_at_capacity_twice_then_free_answers(codex, process, pauses, caplog):
    process.replies = [FailedCall(AT_CAPACITY), FailedCall(AT_CAPACITY), REPLY]

    with caplog.at_level(logging.INFO):
        reply = codex.run("p", "s", timeout=60, budget=llm.CALL_BUDGET)

    assert reply.data == REPLY and reply.waited == 240
    assert len(process.calls) == 3
    assert pauses == [60, 180]
    busy = [r.getMessage() for r in caplog.records if "busy" in r.getMessage()]
    assert len(busy) == 2
    assert "at capacity" in busy[0] and "1 min" in busy[0]
    assert "3 min" in busy[1]


@pytest.mark.parametrize("stderr", [
    "ERROR: Not logged in. Run `codex login` to sign in.\n",
    "ERROR: unexpected status 401 Unauthorized: Your refresh token has expired. Please log out and sign in again.\n",
    # A plan's usage limit is a rate limit too, but it lasts hours: waiting minutes won't help.
    "ERROR: You've hit your usage limit. Upgrade to Pro or try again in 3 days.\n",
    # Out of API credit comes as a 429 from OpenAI, but it lasts until someone pays.
    "ERROR: unexpected status 429 Too Many Requests: credit_balance_exhausted: Your credit balance is exhausted\n",
    'API Error: 400 {"type":"error","error":{"type":"invalid_request_error",'
    '"message":"Your credit balance is too low to access the Anthropic API."}}\n',
])
def test_a_signed_out_model_fails_at_once(codex, process, pauses, stderr):
    process.replies = [FailedCall(stderr)]

    with pytest.raises(RuntimeError, match="codex exec failed"):
        codex.run("p", "s", timeout=60, budget=llm.CALL_BUDGET)

    assert len(process.calls) == 1
    assert pauses == []


def test_signed_out_wins_over_a_busy_word_in_the_same_error(codex, process, pauses):
    process.replies = [FailedCall("ERROR: 401 Unauthorized (rate limit headers: none). Not logged in.\n")]

    with pytest.raises(RuntimeError):
        codex.run("p", "s", timeout=60, budget=llm.CALL_BUDGET)

    assert len(process.calls) == 1 and pauses == []


def test_a_message_that_mentions_capacity_is_not_mistaken_for_a_busy_model(codex, process, pauses):
    # codex echoes the prompt on stderr, and the family's messages are in the prompt.
    process.replies = lambda prompt: FailedCall(f"user\n{prompt}\nERROR: stream disconnected\n")

    with pytest.raises(RuntimeError):
        codex.run("The hall is at capacity, 429 seats, so come early.", "s", timeout=60,
                  budget=llm.CALL_BUDGET)

    assert len(process.calls) == 1 and pauses == []


def test_a_model_busy_all_evening_stops_inside_the_budget(codex, process, pauses, caplog):
    # Each try may take the whole timeout, so the 10 minute pause would end past the budget.
    process.replies = [FailedCall(AT_CAPACITY)] * 4

    with caplog.at_level(logging.INFO), pytest.raises(RuntimeError, match="codex exec failed"):
        codex.run("p", "s", timeout=1200, budget=llm.CALL_BUDGET)

    assert len(process.calls) == 3
    assert pauses == [60, 180]
    assert any("still busy after 3 attempts" in r.getMessage() for r in caplog.records)


def test_each_pause_is_used_once_then_the_call_gives_up(codex, process, pauses):
    process.replies = [FailedCall(AT_CAPACITY)] * 4

    with pytest.raises(RuntimeError, match="codex exec failed"):
        codex.run("p", "s", timeout=60, budget=llm.CALL_BUDGET)

    assert len(process.calls) == 4
    assert pauses == [60, 180, 600]


def test_the_doctor_check_does_not_wait_for_a_busy_model(codex, process, pauses, signed_in):
    process.replies = [FailedCall(AT_CAPACITY)]

    [finding] = codex.check()

    assert len(process.calls) == 1 and pauses == []
    assert finding.level == "fail" and "at capacity" in finding.detail


@pytest.mark.parametrize("stderr, waited", [
    ("tokens used\n1,503\nERROR: stream disconnected\n", []),  # a count, not a status
    ("ERROR: unexpected status 503 Service Unavailable\n", [60]),
])
def test_a_number_is_a_busy_status_only_as_a_status(codex, process, pauses, stderr, waited):
    process.replies = [FailedCall(stderr), REPLY]

    try:
        codex.run("p", "s", timeout=60, budget=llm.CALL_BUDGET)
    except RuntimeError:
        pass

    assert pauses == waited
