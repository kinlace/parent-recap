"""The Claude token setup keeps the token out of every process's arguments, which other
accounts on the Mac can read with `ps`."""
from __future__ import annotations

import getpass
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup_claude_token.py"
TOKEN = "sk-ant-oat01-secret-token"


@pytest.fixture
def setup(harness, monkeypatch: pytest.MonkeyPatch):
    """Runs the script against the harness's Keychain (the parent types `typed` at its hidden
    prompt) and a fake `claude`, recording every process started."""
    spec = importlib.util.spec_from_file_location("setup_claude_token", SCRIPT)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    calls: list[tuple[list[str], dict | None]] = []
    typed = {"value": TOKEN}
    others = subprocess.run

    def run(cmd, *a, env=None, **k):
        calls.append((list(cmd), env))
        if cmd[0] != "claude":
            return others(cmd, *a, env=env, **k)
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"is_error": False, "result": "OK"}), "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(getpass, "getpass", lambda *_a, **_k: typed["value"])
    return script, harness.keychain, calls, typed


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


def test_a_token_stored_before_is_replaced_so_the_new_one_gets_this_access_list(setup, harness):
    script, keychain, calls, _ = setup
    keychain["claude-oauth-token"] = "sk-ant-oat01-stored-with-keyring"
    harness.keychain_asks.add("claude-oauth-token")  # it trusts only an earlier version's Python

    assert script.main() == 0

    assert keychain == {"claude-oauth-token": TOKEN}
    assert "claude-oauth-token" not in harness.keychain_asks
