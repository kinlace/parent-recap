"""The Claude token setup keeps the token out of every process's arguments, which other
accounts on the Mac can read with `ps`."""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup_claude_token.py"
TOKEN = "sk-ant-oat01-secret-token"


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch):
    """Runs the script against a fake `security` (the parent types `typed` at its prompt) and
    a fake `claude`, recording every process started."""
    spec = importlib.util.spec_from_file_location("setup_claude_token", SCRIPT)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    keychain: dict[str, str] = {}
    calls: list[tuple[list[str], dict | None]] = []
    typed = {"value": TOKEN}

    def run(cmd, *_a, env=None, **_k):
        calls.append((list(cmd), env))
        account = cmd[cmd.index("-a") + 1] if "-a" in cmd else None
        if cmd[:2] == ["security", "add-generic-password"]:
            assert cmd[-1] == "-w"  # no value after -w: security prompts for it on the terminal
            keychain[account] = typed["value"]
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:2] == ["security", "find-generic-password"]:
            if account not in keychain:
                return subprocess.CompletedProcess(cmd, 44, "", "item not found")
            return subprocess.CompletedProcess(cmd, 0, keychain[account] + "\n", "")
        if cmd[:2] == ["security", "delete-generic-password"]:
            keychain.pop(account, None)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        assert cmd[0] == "claude", cmd
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"is_error": False, "result": "OK"}), "")

    monkeypatch.setattr(subprocess, "run", run)
    return script, keychain, calls, typed


def test_the_token_never_appears_in_a_process_argument(setup):
    script, keychain, calls, _ = setup

    assert script.main() == 0

    assert keychain == {"claude-oauth-token": TOKEN}
    assert calls and not any(TOKEN in a for cmd, _ in calls for a in cmd)
    [(claude, env)] = [(cmd, env) for cmd, env in calls if cmd[0] == "claude"]
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == TOKEN
    assert "--no-session-persistence" in claude


def test_something_that_is_not_a_claude_token_is_not_kept(setup, capsys):
    script, keychain, calls, typed = setup
    typed["value"] = "not-a-token"

    assert script.main() == 1

    assert "claude-oauth-token" not in keychain
    assert "Run this again" in capsys.readouterr().err
    assert not any(cmd[0] == "claude" for cmd, _ in calls)
