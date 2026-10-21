"""Claude, Codex and Node are found when they're installed but not on this PATH.

macOS's own Terminal, and the evening job, may not have a folder the family's own shell setup
(fish, nix, a custom npm prefix) puts on their login shell's PATH. Doctor finds the program there,
and the evening job runs the same one, without the login shell. The login shell is a fake that
adds one folder to PATH; the programs in it are the harness's fakes, or small scripts."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from conftest import msg, program

from family_brief import ops, tools
from family_brief.llm import codex as codex_backend

TOKEN = "sk-ant-oat01-Abc_def-123456789"


@pytest.fixture
def terminal_path(harness, monkeypatch) -> Path:
    """macOS's Terminal, whose PATH has none of the family's own folders. Returns the folder the
    family's login shell adds."""
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["wilma"]["enabled"] = False
    harness.config["whatsapp"]["enabled"] = False
    monkeypatch.setattr(codex_backend, "CODEX_BUNDLED", ())  # not this Mac's own apps
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    return harness.home / "tools" / "bin"


def doctor_line(capsys, check: str) -> str:
    return next(l for l in capsys.readouterr().out.splitlines() if f"{check}:" in l)


def the_evening_job(harness, monkeypatch) -> None:
    """A night with a message to summarise. The system starts the evening job with the PATH its
    job names, which a schedule installed before the program was found doesn't lead to, and
    without the login shell."""
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.delenv("SHELL", raising=False)
    harness.sources = {"gmail": [msg("gmail", "1001", "2026-09-27T09:00:00+03:00",
                                     "Vanhempainilta on 8.10.", sender="office@kilo.example.fi",
                                     subject="Vanhempainilta")]}
    harness.model_reply = {"per_kid": [], "calendar_events": [], "message_digest": ""}
    harness.model_calls.clear()


@pytest.mark.macos
def test_doctor_finds_claude_on_the_login_shells_path_and_the_evening_job_runs_it(
        harness, terminal_path, login_shell, monkeypatch, capsys):
    claude = program(terminal_path, "claude")
    login_shell(terminal_path)
    harness.secrets["claude-oauth-token"] = TOKEN
    harness.model_reply = "OK"

    harness.cli("doctor")

    assert doctor_line(capsys, "Claude").startswith(ops.OK)
    assert harness.model_calls[-1].argv[0] == str(claude)

    the_evening_job(harness, monkeypatch)
    assert harness.run() == 0
    assert [c.argv[0] for c in harness.model_calls] == [str(claude)]


@pytest.mark.macos
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


@pytest.mark.windows
def test_on_windows_claude_from_its_own_installer_is_found_off_path_and_remembered(
        harness, terminal_path, monkeypatch):
    claude = harness.home / ".local" / "bin" / "claude.exe"
    claude.parent.mkdir(parents=True)
    claude.write_bytes(b"")
    monkeypatch.setenv("PATH", str(harness.home / "Windows" / "System32"))

    assert tools.find("claude") == str(claude)
    assert str(claude.parent) in os.environ["PATH"].split(os.pathsep)
    assert json.loads(tools.remembered_path().read_text()) == {"claude": str(claude)}


@pytest.mark.windows
def test_on_windows_codex_installed_with_npm_is_found_off_path(harness, terminal_path,
                                                               monkeypatch):
    npm = harness.home / "AppData" / "Roaming" / "npm"
    npm.mkdir(parents=True)
    (npm / "codex").write_text("#!/bin/sh\n")  # npm's wrapper for Git Bash, which Windows can't run
    (npm / "codex.cmd").write_text("@node codex.js %*\r\n")
    monkeypatch.setenv("APPDATA", str(harness.home / "AppData" / "Roaming"))
    monkeypatch.setenv("PATH", str(harness.home / "Windows" / "System32"))

    assert tools.find("codex") == str(npm / "codex.cmd")


def test_on_windows_codex_from_openai_s_installer_is_found_off_path(harness, windows_programs):
    codex = program(windows_programs / "Programs" / "OpenAI" / "Codex" / "bin", "codex.exe")

    assert tools.find("codex") == str(codex)


@pytest.mark.windows  # the run itself, with Windows' own run lock
def test_on_windows_the_evening_job_runs_the_codex_openai_s_installer_put_in_place(
        harness, terminal_path, windows_programs, monkeypatch):
    codex = program(windows_programs / "Programs" / "OpenAI" / "Codex" / "bin", "codex.exe")
    harness.config["llm"]["backend"] = "codex"

    the_evening_job(harness, monkeypatch)
    # Task Scheduler's PATH, which the installer's folder isn't on.
    monkeypatch.setenv("PATH", str(harness.home / "Windows" / "System32"))
    assert harness.run() == 0
    assert [c.argv[0] for c in harness.model_calls] == [str(codex)]
    assert json.loads(tools.remembered_path().read_text()) == {"codex": str(codex)}


@pytest.mark.windows  # the run itself, with Windows' own run lock and Credential Manager
def test_on_windows_the_evening_job_runs_the_claude_its_own_installer_put_in_place(
        harness, terminal_path, windows_programs, monkeypatch):
    claude = program(harness.home / ".local" / "bin", "claude.exe")
    harness.secrets["claude-oauth-token"] = TOKEN

    the_evening_job(harness, monkeypatch)
    # Task Scheduler's PATH, which the installer's folder isn't on.
    monkeypatch.setenv("PATH", str(harness.home / "Windows" / "System32"))
    assert harness.run() == 0
    [call] = harness.model_calls
    assert call.argv[0] == str(claude) and call.env["CLAUDE_CODE_OAUTH_TOKEN"] == TOKEN
    assert json.loads(tools.remembered_path().read_text()) == {"claude": str(claude)}


@pytest.mark.macos  # Windows' own line is in test_doctor.py
def test_without_claude_anywhere_doctor_says_how_to_install_it(harness, terminal_path,
                                                               login_shell, capsys):
    login_shell(terminal_path)  # the login shell has no claude either

    harness.cli("doctor")

    line = doctor_line(capsys, "Claude")
    assert line.startswith(ops.FAIL) and "curl -fsSL https://claude.ai/install.sh | bash" in line


@pytest.mark.macos
def test_codex_inside_the_chatgpt_app_is_one_of_the_bundled_ones():
    """The ChatGPT app keeps its Codex in codex-cli/bin, and a Mac may have only that app (#203)."""
    assert "/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex" in codex_backend.CODEX_BUNDLED
    assert codex_backend.CODEX_BUNDLED.index("/Applications/Codex.app/Contents/Resources/codex") < \
        codex_backend.CODEX_BUNDLED.index("/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex")


def test_the_evening_job_runs_the_codex_inside_the_chatgpt_app(harness, terminal_path, monkeypatch):
    app = harness.home / "Applications" / "ChatGPT.app"
    codex = program(app / "Contents" / "Resources" / "codex-cli" / "bin", "codex")
    monkeypatch.setattr(codex_backend, "CODEX_BUNDLED", (str(codex),))
    harness.config["llm"]["backend"] = "codex"

    the_evening_job(harness, monkeypatch)
    assert harness.run() == 0
    assert [c.argv[0] for c in harness.model_calls] == [str(codex)]

