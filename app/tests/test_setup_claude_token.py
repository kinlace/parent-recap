"""The Claude token setup keeps the token out of every process's arguments, which other
accounts on the Mac can read with `ps`."""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
from fake_system import FakeSecrets

from family_brief.system import secrets

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup_claude_token.py"
TOKEN = "sk-ant-oat01-secret-token"


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch):
    """Runs the script against fake secrets (the parent types `typed` at the store's prompt) and
    a fake `claude`, recording every process started."""
    spec = importlib.util.spec_from_file_location("setup_claude_token", SCRIPT)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    store = FakeSecrets()
    store.typed = TOKEN
    monkeypatch.setattr(secrets, "_adapter", store)
    calls: list[tuple[list[str], dict | None]] = []

    def run(cmd, *_a, env=None, **_k):
        calls.append((list(cmd), env))
        assert cmd[0] == "claude", cmd
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"is_error": False, "result": "OK"}), "")

    monkeypatch.setattr(subprocess, "run", run)
    return script, store, calls


def test_the_token_never_appears_in_a_process_argument(setup):
    script, store, calls = setup

    assert script.main() == 0

    assert store == {"claude-oauth-token": TOKEN}
    assert calls and not any(TOKEN in a for cmd, _ in calls for a in cmd)
    [(claude, env)] = [(cmd, env) for cmd, env in calls if cmd[0] == "claude"]
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == TOKEN
    assert "--no-session-persistence" in claude


def test_something_that_is_not_a_claude_token_is_not_kept(setup, capsys):
    script, store, calls = setup
    store.typed = "not-a-token"

    assert script.main() == 1

    assert "claude-oauth-token" not in store
    assert "Run this again" in capsys.readouterr().err
    assert calls == []


def test_a_token_stored_before_is_replaced(setup):
    script, store, _ = setup
    store["claude-oauth-token"] = "sk-ant-oat01-stored-with-keyring"
    store.asks.add("claude-oauth-token")

    assert script.main() == 0

    assert store == {"claude-oauth-token": TOKEN} and not store.asks
