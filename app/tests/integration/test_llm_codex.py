"""The Codex adapter of the llm package (ADR 0023): `codex exec` run isolated, found at the config's
llm.codex_path, and doctor's check of it. The process is the harness's fake `codex` (conftest.py)."""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import FailedCall, program
from fake_system import MACOS, WINDOWS
from family_brief import llm
from family_brief.config import LLMConfig
from family_brief.llm import codex as codex_backend
from family_brief.system import profile_windows

REPLY = {"per_kid": [], "calendar_events": [], "message_digest": "Swimming on Thursday"}


@pytest.fixture
def codex_path(harness, tmp_path, monkeypatch):
    monkeypatch.setattr(codex_backend, "CODEX_BUNDLED", ())  # not this Mac's own apps
    harness.model_reply = REPLY
    return str(program(tmp_path / "bin", "codex"))


@pytest.fixture
def codex(codex_path):
    return llm.for_llm(LLMConfig(backend="codex", codex_path=codex_path))


def test_codex_is_named_and_probed_as_the_brief_says(codex):
    assert (codex.name, codex.display_name, codex.probe_host) == ("codex", "Codex", "chatgpt.com")


def test_codex_runs_the_configured_program_isolated_with_the_instructions_first(codex, codex_path,
                                                                               harness):
    reply = codex.run("the night's messages", "the system prompt", timeout=60, budget=0)

    assert reply.data == REPLY and reply.usage is None
    [call] = harness.model_calls
    assert call.argv[:2] == [codex_path, "exec"]
    assert {"--ephemeral", "--ignore-user-config"} <= set(call.argv)
    assert call.argv[call.argv.index("--sandbox") + 1] == "read-only"
    disabled = {call.argv[i + 1] for i, a in enumerate(call.argv) if a == "--disable"}
    assert {"shell_tool", "unified_exec", "apps", "plugins"} <= disabled
    assert call.argv[-1] == "-"  # the prompt on stdin
    assert call.stdin.startswith("the system prompt\n\n---\n\nthe night's messages")
    assert not Path(call.argv[call.argv.index("-C") + 1]).exists()  # its own dir, gone after


def test_codex_passes_the_configured_model(codex_path, harness):
    llm.for_llm(LLMConfig(backend="codex", codex_path=codex_path, model="gpt-6")).run(
        "p", "s", timeout=60, budget=0)

    [call] = harness.model_calls
    assert call.argv[call.argv.index("-m") + 1] == "gpt-6"


def test_a_failed_codex_call_raises(codex, harness):
    harness.model_reply = FailedCall("stream error")

    with pytest.raises(RuntimeError, match="codex exec failed"):
        codex.run("p", "s", timeout=60, budget=0)


def test_codex_that_isnt_there_says_how_to_get_it(harness, monkeypatch):
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


@pytest.mark.parametrize("system, line", [(MACOS, None), (WINDOWS, profile_windows.CODEX_INSTALL)])
def test_codex_installs_with_a_line_only_on_windows(codex, harness, system, line):
    # On a Mac it comes with the Codex or ChatGPT desktop app.
    harness.system = system

    assert codex.install_line() == line


def test_codex_takes_no_pasted_token(codex, harness):
    assert codex.test_token("sk-ant-oat01-pasted") is not None
    assert not harness.model_calls


def test_codex_check_calls_a_signed_in_codex(codex, codex_path, harness):
    harness.model_reply = {"ok": True}

    assert codex.check() == [llm.Finding("ok", "codex", f"call succeeded ({codex_path})")]


def test_codex_check_says_signed_out_without_calling_it(codex, harness):
    harness.signed_in["codex"] = False

    [finding] = codex.check()

    assert (finding.level, finding.key) == ("fail", "codex") and "not signed in" in finding.detail
    assert not harness.model_calls
