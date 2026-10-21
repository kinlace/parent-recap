"""The Claude adapter of the llm package (ADR 0023): `claude -p` run isolated from the family's own
Claude Code, signed in with the stored token, and doctor's check of it. The process is the
harness's fake `claude` (conftest.py)."""
from __future__ import annotations

import shutil

import pytest

from conftest import FailedCall, assert_isolated_claude
from family_brief import llm
from family_brief.config import LLMConfig

REPLY = {"per_kid": [], "calendar_events": [], "message_digest": "Swimming on Thursday"}


@pytest.fixture
def claude(harness, monkeypatch):
    """The Claude adapter, with `claude` on PATH."""
    which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda cmd, *a, **k: "/usr/local/bin/claude"
                        if cmd == "claude" else which(cmd, *a, **k))
    harness.model_reply = REPLY
    return llm.for_llm(LLMConfig(backend="claude"))


def test_claude_is_named_probed_and_installed_as_the_brief_says(claude):
    assert (claude.name, claude.display_name, claude.probe_host) == ("claude", "Claude", "api.anthropic.com")
    assert "claude.ai/install" in claude.install_hint()


def test_claude_runs_without_tools_from_an_empty_dir(claude, harness):
    reply = claude.run("the night's messages", "the system prompt", timeout=60, budget=0)

    assert reply.data == REPLY
    [call] = harness.model_calls
    assert_isolated_claude(call)
    assert call.argv[call.argv.index("--system-prompt") + 1] == "the system prompt"
    assert call.stdin == "the night's messages"


def test_claude_runs_isolated_from_the_familys_claude_code_session(claude, harness, monkeypatch):
    # e.g. the setup skill's preview run, started from inside a Claude Code session
    for var in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_HOST_SESSION_ID",
                "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_AGENT_SDK_VERSION"):
        monkeypatch.setenv(var, "from-the-parent")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "parent-token")
    harness.secrets["claude-oauth-token"] = "keychain-token"

    claude.run("the night's messages", "the system prompt", timeout=60, budget=0)

    [call] = harness.model_calls
    assert call.env is not None
    leaked = sorted(k for k in call.env
                    if k == "CLAUDECODE" or k.startswith(("CLAUDE_CODE_", "CLAUDE_AGENT_SDK_")))
    assert leaked == ["CLAUDE_CODE_OAUTH_TOKEN"]
    assert call.env["CLAUDE_CODE_OAUTH_TOKEN"] == "keychain-token"
    assert call.env["HOME"] == str(harness.home)
    assert call.stdin_source is None  # stdin carries our prompt, not the caller's terminal


def test_claude_passes_the_configured_model(claude, harness):
    llm.for_llm(LLMConfig(backend="claude", model="opus")).run("p", "s", timeout=60, budget=0)

    [call] = harness.model_calls
    assert call.argv[call.argv.index("--model") + 1] == "opus"


def test_a_failed_claude_call_raises_with_what_it_said(claude, harness):
    harness.model_reply = FailedCall("Not logged in · Please run /login")

    with pytest.raises(RuntimeError, match="claude CLI failed.*Not logged in"):
        claude.run("p", "s", timeout=60, budget=0)


def test_claude_finds_its_program_on_path(claude):
    assert claude.program() == "/usr/local/bin/claude"


def test_claude_check_calls_it_with_the_keychain_token(claude, harness):
    harness.secrets["claude-oauth-token"] = "keychain-token"
    harness.model_reply = "OK"

    assert claude.check() == [llm.Finding("ok", "claude", "call succeeded (auth: keychain-oauth)")]
    [call] = harness.model_calls
    assert call.env["CLAUDE_CODE_OAUTH_TOKEN"] == "keychain-token"


def test_claude_check_fails_without_a_token_for_the_evening_brief(claude):
    [finding] = claude.check()

    assert (finding.level, finding.key) == ("fail", "claude")
    assert "the evening Brief can't call Claude" in finding.detail
