"""Claude, Codex, the wilma CLI and Node are found when they're installed but not on this PATH.

macOS's own Terminal, and the evening job, may not have a folder the family's own shell setup
(fish, nix, a custom npm prefix) puts on their login shell's PATH. Doctor finds the program there,
and the evening job runs the same one, without the login shell. The login shell is a fake that
adds one folder to PATH; the programs in it are the harness's fakes, or small scripts."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from conftest import REAL_RUN, msg, program

from family_brief import ops, summarize

TOKEN = "sk-ant-oat01-Abc_def-123456789"


@pytest.fixture
def terminal_path(harness, monkeypatch) -> Path:
    """macOS's Terminal, whose PATH has none of the family's own folders. Returns the folder the
    family's login shell adds."""
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["wilma"]["enabled"] = False
    harness.config["whatsapp"]["enabled"] = False
    monkeypatch.setattr(ops, "launchctl_loaded", lambda: set())
    monkeypatch.setattr(summarize, "CODEX_BUNDLED", ())  # not this Mac's own apps
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    return harness.home / "tools" / "bin"


def doctor_line(capsys, check: str) -> str:
    return next(l for l in capsys.readouterr().out.splitlines() if f"{check}:" in l)


def the_evening_job(harness, monkeypatch) -> None:
    """A night with a message to summarise. launchd starts the evening job with the PATH its
    plist names, which a schedule installed before the program was found doesn't lead to, and
    without the login shell."""
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.delenv("SHELL", raising=False)
    harness.sources = {"gmail": [msg("gmail", "g-1", "2026-09-27T09:00:00+03:00",
                                     "Vanhempainilta on 8.10.", sender="office@kilo.example.fi",
                                     subject="Vanhempainilta")]}
    harness.model_reply = {"per_kid": [], "calendar_events": [], "message_digest": ""}
    harness.model_calls.clear()


def test_doctor_finds_claude_on_the_login_shells_path_and_the_evening_job_runs_it(
        harness, terminal_path, login_shell, monkeypatch, capsys):
    claude = program(terminal_path, "claude")
    login_shell(terminal_path)
    harness.keychain["claude-oauth-token"] = TOKEN
    harness.model_reply = "OK"

    harness.cli("doctor")

    assert doctor_line(capsys, "Claude").startswith(ops.OK)
    assert harness.model_calls[-1].argv[0] == str(claude)

    the_evening_job(harness, monkeypatch)
    assert harness.run() == 0
    assert [c.argv[0] for c in harness.model_calls] == [str(claude)]


def test_doctor_finds_codex_on_the_login_shells_path_and_the_evening_job_runs_it(
        harness, terminal_path, login_shell, monkeypatch, capsys):
    harness.config["llm"]["backend"] = "codex"
    codex = program(terminal_path, "codex")
    login_shell(terminal_path)
    harness.model_reply = {"ok": True}

    harness.cli("doctor")

    assert doctor_line(capsys, "Codex").startswith(ops.OK)
    assert harness.model_calls[-1].argv[0] == str(codex)

    the_evening_job(harness, monkeypatch)
    assert harness.run() == 0
    assert [c.argv[0] for c in harness.model_calls] == [str(codex)]


def test_without_claude_anywhere_doctor_says_how_to_install_it(harness, terminal_path,
                                                               login_shell, capsys):
    login_shell(terminal_path)  # the login shell has no claude either

    harness.cli("doctor")

    line = doctor_line(capsys, "Claude")
    assert line.startswith(ops.FAIL) and "curl -fsSL https://claude.ai/install.sh | bash" in line


# A Node script that answers `wilma kids list --json` as the CLI does, once `env` finds node.
WILMA = "#!/usr/bin/env node\n"
NODE = """#!/bin/sh
echo '[{"name": "Mia"}, {"name": "Leo"}]'
"""


@pytest.fixture
def wilma_runs(harness, monkeypatch) -> list[list[str]]:
    """The wilma CLI runs for real, past the harness's fakes. Returns each command."""
    harness.config["wilma"]["enabled"] = True
    others = subprocess.run
    ran: list[list[str]] = []

    def run(cmd: list[str], *a: Any, **k: Any) -> subprocess.CompletedProcess:
        if Path(cmd[0]).name == "wilma":
            ran.append(list(cmd))
            return REAL_RUN(cmd, *a, **k)
        return others(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", run)
    return ran


def test_doctor_runs_a_wilma_cli_whose_node_only_the_login_shell_finds(
        harness, terminal_path, wilma_runs, login_shell, capsys):
    npm_global = harness.home / ".npm-global" / "bin"  # a custom npm prefix
    wilma = program(npm_global, "wilma", WILMA)
    program(terminal_path, "node", NODE)
    login_shell(terminal_path)

    harness.cli("doctor", "--skip-llm")

    line = doctor_line(capsys, "Wilma")
    assert line.startswith(ops.OK) and "Mia, Leo" in line
    assert wilma_runs[-1][0] == str(wilma)
