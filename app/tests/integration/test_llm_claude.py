"""The Claude adapter of the llm package (ADR 0023): `claude -p` run isolated from the family's own
Claude Code, signed in with the stored token, a busy Claude tried again, and doctor's check of
it. The process is fake_model.ModelProcess, and the secrets store the `system` fixture's."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest

from fake_model import FailedCall, ModelProcess, ProcessCall
from fake_system import MACOS, WINDOWS
from family_brief import llm
from family_brief.config import LLMConfig

REPLY = {"per_kid": [], "calendar_events": [], "message_digest": "Swimming on Thursday"}


def assert_isolated_claude(call: ProcessCall) -> None:
    """Only our system prompt (not Claude Code's own), no tools, no MCP, no user-level settings,
    no saved session, the prompt on stdin rather than in argv (argv is size-capped and shows in
    `ps`), and an empty working dir that is gone once the run ends."""
    argv = call.argv
    assert "--system-prompt" in argv and "--append-system-prompt" not in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in argv and "--mcp-config" not in argv
    assert argv[argv.index("--disallowedTools") + 1] == "mcp__*"
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert "--no-session-persistence" in argv
    assert call.stdin and call.stdin_source is None
    assert not any(call.stdin in a for a in argv)
    assert call.cwd_contents == []
    assert call.cwd is not None and not Path(call.cwd).exists()


@pytest.fixture
def process(home, system, monkeypatch):
    process = ModelProcess("claude", monkeypatch)
    process.replies = REPLY
    return process


@pytest.fixture
def claude(process, monkeypatch):
    """The Claude adapter, with `claude` on PATH."""
    which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda cmd, *a, **k: "/usr/local/bin/claude"
                        if cmd == "claude" else which(cmd, *a, **k))
    return llm.for_llm(LLMConfig(backend="claude"))


def test_claude_is_named_probed_and_installed_as_the_brief_says(claude):
    assert (claude.name, claude.display_name, claude.probe_host) == ("claude", "Claude", "api.anthropic.com")
    assert "claude.ai/install" in claude.install_hint()


def test_claude_runs_without_tools_from_an_empty_dir(claude, process):
    reply = claude.run("the night's messages", "the system prompt", timeout=60, budget=0)

    assert reply.data == REPLY
    [call] = process.calls
    assert call.argv[0] == "claude"  # by name, as PATH has it
    assert_isolated_claude(call)
    assert call.argv[call.argv.index("--system-prompt") + 1] == "the system prompt"
    assert call.stdin == "the night's messages"


def test_claude_runs_isolated_from_the_familys_claude_code_session(claude, process, home, system,
                                                                   monkeypatch):
    # e.g. the setup skill's preview run, started from inside a Claude Code session
    for var in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_HOST_SESSION_ID",
                "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_AGENT_SDK_VERSION"):
        monkeypatch.setenv(var, "from-the-parent")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "parent-token")
    system.secrets["claude-oauth-token"] = "keychain-token"

    claude.run("the night's messages", "the system prompt", timeout=60, budget=0)

    [call] = process.calls
    assert call.env is not None
    leaked = sorted(k for k in call.env
                    if k == "CLAUDECODE" or k.startswith(("CLAUDE_CODE_", "CLAUDE_AGENT_SDK_")))
    assert leaked == ["CLAUDE_CODE_OAUTH_TOKEN"]
    assert call.env["CLAUDE_CODE_OAUTH_TOKEN"] == "keychain-token"
    assert call.env["HOME"] == str(home)
    assert call.stdin_source is None  # stdin carries our prompt, not the caller's terminal


def test_a_token_the_keychain_asks_about_is_said_so_rather_than_that_none_is_stored(
        claude, process, system, caplog):
    caplog.set_level(logging.INFO)
    system.secrets["claude-oauth-token"] = "keychain-token"
    system.secrets.asks.add("claude-oauth-token")

    claude.run("p", "s", timeout=60, budget=0)

    [call] = process.calls
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in call.env
    assert "auth: keychain-needs-prompt" in caplog.text


def test_claude_passes_the_configured_model(claude, process):
    llm.for_llm(LLMConfig(backend="claude", model="opus")).run("p", "s", timeout=60, budget=0)

    [call] = process.calls
    assert call.argv[call.argv.index("--model") + 1] == "opus"


def test_a_failed_claude_call_raises_with_what_it_said(claude, process):
    process.replies = FailedCall("Not logged in · Please run /login")

    with pytest.raises(RuntimeError, match="claude CLI failed.*Not logged in"):
        claude.run("p", "s", timeout=60, budget=0)


def test_an_overloaded_claude_is_tried_again_after_a_pause(claude, process, pauses):
    process.replies = [FailedCall('API Error: 529 {"type":"error","error":{"type":"overloaded_error"}}'),
                       REPLY]

    reply = claude.run("p", "s", timeout=60, budget=llm.CALL_BUDGET)

    assert reply.data == REPLY and reply.waited == llm.BUSY_PAUSES[0]
    assert len(process.calls) == 2 and pauses == [llm.BUSY_PAUSES[0]]


def test_claude_finds_its_program_on_path(claude):
    assert claude.program() == "/usr/local/bin/claude"


def test_claude_off_path_runs_by_the_full_path_it_is_found_at(process, home, monkeypatch):
    native = home / ".local" / "bin" / "claude"
    native.parent.mkdir(parents=True)
    native.write_text("#!/bin/sh\n")
    native.chmod(0o755)
    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: None)

    llm.for_llm(LLMConfig(backend="claude")).run("p", "s", timeout=60, budget=0)

    [call] = process.calls
    assert call.argv[0] == str(native)


def test_claude_by_name_is_the_adapter_setup_asks(claude):
    assert llm.named("claude").program() == claude.program() == "/usr/local/bin/claude"


@pytest.mark.parametrize("on, installer", [(MACOS, "install.sh"), (WINDOWS, "install.ps1")])
def test_claude_installs_with_its_own_installer_for_the_system(claude, system, on, installer):
    system.profile = on

    assert installer in claude.install_line() and claude.install_line() == claude.install_hint()


def test_claude_runs_setups_commands_outside_the_familys_claude_code_session(claude, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "from-the-parent")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "parent-token")

    env = claude.program_env()

    assert "CLAUDECODE" not in env and "CLAUDE_CODE_SESSION_ID" not in env
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "parent-token"  # auth is kept


def test_claude_tests_a_pasted_token_instead_of_the_stored_one(claude, process, system, monkeypatch):
    system.secrets["claude-oauth-token"] = "keychain-token"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "an-api-key")  # no other sign-in wins
    process.replies = "OK"

    assert claude.test_token("pasted-token") is None
    [call] = process.calls
    assert call.argv[0] == "/usr/local/bin/claude" and "--no-session-persistence" in call.argv
    assert call.env["CLAUDE_CODE_OAUTH_TOKEN"] == "pasted-token" and "ANTHROPIC_API_KEY" not in call.env


def test_a_pasted_token_claude_refuses_says_what_it_said(claude, process):
    process.replies = FailedCall("Invalid API key")

    assert "Invalid API key" in claude.test_token("pasted-token")


def test_claude_check_calls_it_with_the_keychain_token(claude, process, system):
    system.secrets["claude-oauth-token"] = "keychain-token"
    process.replies = "OK"

    assert claude.check() == [llm.Finding("ok", "claude", "call succeeded (auth: keychain-oauth)")]
    [call] = process.calls
    assert call.env["CLAUDE_CODE_OAUTH_TOKEN"] == "keychain-token"


def test_claude_check_fails_without_a_token_for_the_evening_brief(claude, process):
    [finding] = claude.check()

    assert (finding.level, finding.key) == ("fail", "claude")
    assert "the evening Brief can't call Claude" in finding.detail
    assert not process.calls
