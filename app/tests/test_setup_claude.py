"""Storing the Claude token with `family-brief setup claude`: Claude's sign-in opens in Terminal,
the token goes from macOS's dialog to one test call and the Keychain, and nowhere else (ADR 0005).

`claude setup-token` draws its sign-in on Ink's interactive screen, which needs a real terminal,
so the step opens it in a Terminal window and asks for the token it shows in the secret dialog.
The outside edges are faked: Terminal, the dialog, the Keychain and the `claude` test call. The
assistant reads only the command's one-line JSON result, so assertions are on that result, on
what the Keychain holds, and on every place the token must never reach."""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

import pytest
from conftest import keychain_refusing, msg, program

from family_brief import install_record

TOKEN = "sk-ant-oat01-Abc_def-123456789"
ACCOUNT = "claude-oauth-token"


@pytest.fixture(autouse=True)
def desktop(monkeypatch):
    """A desktop session, as when the family runs setup on their own Mac."""
    for var in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"):
        monkeypatch.delenv(var, raising=False)


class Terminal:
    """The Terminal window `claude setup-token` is opened in."""

    def __init__(self) -> None:
        self.opens = True             # False: macOS won't open Terminal
        self.scripts: list[str] = []  # each script opened, as it read then


@pytest.fixture
def terminal(harness, tmp_path, monkeypatch) -> Terminal:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text("#!/bin/sh\nexit 0\n")
    claude.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    t = Terminal()
    others = subprocess.run  # the harness's fakes

    def run(cmd: list[str], *a: Any, **k: Any) -> subprocess.CompletedProcess:
        if cmd[:3] == ["open", "-a", "Terminal"]:
            harness.commands.append(list(cmd))
            if not t.opens:
                return subprocess.CompletedProcess(cmd, 1, "", "Unable to find application")
            t.scripts.append(Path(cmd[3]).read_text())
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return others(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", run)
    return t


def result(capsys) -> tuple[dict[str, Any], str]:
    out, err = capsys.readouterr()
    lines = out.strip().splitlines()
    assert len(lines) == 1, f"expected one JSON line, got: {out!r}"
    return json.loads(lines[0]), out + err


def assert_never_leaked(harness, terminal: Terminal, printed: str, caplog,
                        secret: str = TOKEN) -> None:
    assert secret not in printed
    assert secret not in caplog.text
    assert not any(secret in arg for cmd in harness.commands for arg in cmd)
    assert not any(secret in script for script in terminal.scripts)


# ── the dialog


def test_a_token_pasted_in_the_dialog_is_tested_and_stored(harness, terminal, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    harness.dialog.typed = f"  {TOKEN}\n"

    assert harness.cli("setup", "claude") == 0

    res, printed = result(capsys)
    assert res == {"result": "saved", "test_call": "ok"}
    [script] = terminal.scripts
    assert "setup-token" in script
    assert "\\033[3J" in script  # the window's scrollback is cleared of the token afterwards
    assert len(harness.dialog.shown) == 1 and "hidden answer" in harness.dialog.shown[0]
    [call] = harness.model_calls
    assert call.env["CLAUDE_CODE_OAUTH_TOKEN"] == TOKEN
    assert "--no-session-persistence" in call.argv
    assert harness.keychain == {ACCOUNT: TOKEN}
    assert install_record.entries("keychain") == [ACCOUNT]
    assert_never_leaked(harness, terminal, printed, caplog)


def test_a_claude_only_the_login_shell_finds_signs_in(harness, terminal, login_shell, monkeypatch,
                                                      capsys):
    # macOS's own Terminal: the family's shell setup puts claude's folder on their login
    # shell's PATH, not on this one's.
    folder = harness.home / "tools" / "bin"
    claude = program(folder, "claude")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    login_shell(folder)
    harness.dialog.typed = TOKEN

    assert harness.cli("setup", "claude") == 0

    assert result(capsys)[0] == {"result": "saved", "test_call": "ok"}
    [script] = terminal.scripts
    assert str(claude) in script
    [call] = harness.model_calls
    assert call.argv[0] == str(claude)


def test_a_token_copied_across_wrapped_lines_is_put_back_together(harness, terminal, capsys):
    # Claude's screen wraps the token to the window, so a copy can have line breaks in it.
    harness.dialog.typed = f"{TOKEN[:15]}\n  {TOKEN[15:]}"

    assert harness.cli("setup", "claude") == 0

    assert harness.keychain == {ACCOUNT: TOKEN}


def test_the_test_call_ignores_an_api_key_in_the_environment(harness, terminal, monkeypatch,
                                                            capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-other")
    harness.dialog.typed = TOKEN

    assert harness.cli("setup", "claude") == 0

    [call] = harness.model_calls
    assert "ANTHROPIC_API_KEY" not in call.env


def test_a_failed_test_call_stores_nothing_and_says_why(harness, terminal, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    harness.model_error = "Invalid bearer token"
    harness.dialog.typed = TOKEN

    assert harness.cli("setup", "claude") == 1

    res, printed = result(capsys)
    assert res["result"] == "test-call-failed"
    assert "Invalid bearer token" in res["error"]
    assert "setup claude" in res["next"]
    assert harness.keychain == {}
    assert_never_leaked(harness, terminal, printed, caplog)


def test_something_that_is_not_a_claude_token_is_never_sent_to_claude(harness, terminal, capsys,
                                                                      caplog):
    caplog.set_level(logging.DEBUG)
    harness.dialog.typed = "my-claude-password"

    assert harness.cli("setup", "claude") == 1

    res, printed = result(capsys)
    assert res["result"] == "not-a-token" and "sk-ant-oat01-" in res["next"]
    assert harness.model_calls == [] and harness.keychain == {}
    assert_never_leaked(harness, terminal, printed, caplog, "my-claude-password")


def test_closing_the_dialog_stores_nothing(harness, terminal, capsys):
    harness.dialog.button = "cancel"

    assert harness.cli("setup", "claude") == 1

    assert result(capsys)[0]["result"] == "cancelled"
    assert harness.model_calls == [] and harness.keychain == {}


def test_a_keychain_that_refuses_says_so(harness, terminal, monkeypatch, capsys):
    import keyring.errors
    from family_brief.utils import keychain

    def refuse(_key: str, _value: str) -> None:
        raise keyring.errors.PasswordSetError("User interaction is not allowed.")
    monkeypatch.setattr(keychain, "set_", refuse)
    harness.dialog.typed = TOKEN

    assert harness.cli("setup", "claude") == 1

    assert result(capsys)[0]["result"] == "keychain-failed"


def test_another_keychain_error_says_so_with_its_code(harness, terminal, monkeypatch, capsys):
    from family_brief.utils import keychain
    monkeypatch.setattr(keychain, "set_", keychain_refusing(-25293))
    harness.dialog.typed = TOKEN

    assert harness.cli("setup", "claude") == 1

    res = result(capsys)[0]
    assert res["result"] == "keychain-failed" and res["code"] == -25293
    assert "click Allow" in res["next"]


def test_a_keychain_out_of_reach_says_to_run_it_outside_tmux_or_ssh(harness, terminal,
                                                                   monkeypatch, capsys):
    from family_brief.utils import keychain
    monkeypatch.setattr(keychain, "set_", keychain_refusing(-25308))  # errSecInteractionNotAllowed
    harness.dialog.typed = TOKEN

    assert harness.cli("setup", "claude") == 1

    res = result(capsys)[0]
    assert res["result"] == "keychain-not-reachable" and res["code"] == -25308
    assert "outside tmux or SSH" in res["next"] and "Shell → New Command…" in res["next"]
    assert "setup claude --no-open" in res["next"]
    assert "Allow" not in res["next"]  # macOS shows no prompt there


@pytest.mark.parametrize("var", ["TMUX", "SSH_CONNECTION"])
def test_in_tmux_or_ssh_it_warns_before_the_token_is_asked_for(harness, terminal, monkeypatch,
                                                              capsys, var):
    import getpass
    monkeypatch.setenv(var, "/private/tmp/tmux-501/default,1234,0" if var == "TMUX"
                       else "10.0.0.2 52000 10.0.0.1 22")
    prompts: list[str] = []
    monkeypatch.setattr(getpass, "getpass", lambda p="": prompts.append(p) or TOKEN)  # over SSH
    harness.dialog.typed = TOKEN

    harness.cli("setup", "claude")

    out, err = capsys.readouterr()
    assert len(out.strip().splitlines()) == 1  # still one JSON line on stdout
    assert err.count("outside tmux or SSH") == 1
    [asked] = harness.dialog.shown + prompts
    assert "outside tmux or SSH" in asked


def test_outside_tmux_and_ssh_there_is_no_warning(harness, terminal, capsys):
    harness.dialog.typed = TOKEN

    assert harness.cli("setup", "claude") == 0

    assert "tmux" not in capsys.readouterr().err
    assert "tmux" not in harness.dialog.shown[0]


# ── Claude's sign-in in Terminal


def test_without_the_claude_cli_it_says_how_to_install_it(harness, terminal, monkeypatch, capsys):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    assert harness.cli("setup", "claude") == 1

    res, _ = result(capsys)
    assert res["result"] == "not-installed" and "claude" in res["next"]
    assert "curl -fsSL https://claude.ai/install.sh | bash" in res["next"]
    assert "npm" not in res["next"]
    assert terminal.scripts == [] and harness.dialog.shown == []


def test_when_terminal_does_not_open_it_says_what_to_run(harness, terminal, capsys):
    terminal.opens = False

    assert harness.cli("setup", "claude") == 1

    res, _ = result(capsys)
    assert res["result"] == "no-terminal"
    assert "claude setup-token" in res["next"] and "--no-open" in res["next"]
    assert harness.dialog.shown == []


def test_no_open_only_asks_for_the_token(harness, terminal, capsys):
    harness.dialog.typed = TOKEN

    assert harness.cli("setup", "claude", "--no-open") == 0

    assert terminal.scripts == []
    assert harness.keychain == {ACCOUNT: TOKEN}


def test_with_neither_a_dialog_nor_a_terminal_it_says_where_to_run_it(harness, terminal,
                                                                     monkeypatch, capsys):
    import getpass
    harness.dialog.button = "fails"

    def no_terminal(_prompt: str = "") -> str:
        raise EOFError()
    monkeypatch.setattr(getpass, "getpass", no_terminal)

    assert harness.cli("setup", "claude") == 1

    res, _ = result(capsys)
    assert res["result"] == "no-prompt" and "setup claude --no-open" in res["next"]
    assert harness.keychain == {}


# ── doctor


def test_doctor_points_to_the_setup_step_when_no_token_is_stored(harness, terminal, monkeypatch,
                                                                 capsys):
    from family_brief import ops
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["wilma"]["enabled"] = False
    harness.config["whatsapp"]["enabled"] = False
    monkeypatch.setattr(ops, "launchctl_loaded", lambda: set())

    harness.cli("doctor")

    out = capsys.readouterr().out
    assert "family-brief setup claude" in out and "setup_claude_token" not in out


# ── Claude Code from its native installer, in ~/.local/bin
#
# The native installer puts `claude` in ~/.local/bin and adds that folder to the shell's PATH,
# which a shell started earlier, the agent's own process or the nightly job may not have.


@pytest.fixture
def native_claude(harness, monkeypatch) -> Path:
    """A fake `claude` where the native installer puts it, on no PATH this process sees."""
    bin_dir = harness.home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    claude = bin_dir / "claude"
    claude.write_text("#!/bin/sh\nexit 0\n")
    claude.chmod(0o755)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    return claude


def test_setup_claude_finds_claude_from_the_native_installer(harness, terminal, native_claude,
                                                             capsys):
    harness.dialog.typed = TOKEN

    assert harness.cli("setup", "claude") == 0

    res, _ = result(capsys)
    assert res["result"] == "saved"
    assert str(native_claude) in terminal.scripts[0]
    assert harness.model_calls[0].argv[0] == str(native_claude)


def test_doctor_finds_claude_from_the_native_installer(harness, native_claude, monkeypatch, capsys):
    from family_brief import ops
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["wilma"]["enabled"] = False
    harness.config["whatsapp"]["enabled"] = False
    monkeypatch.setattr(ops, "launchctl_loaded", lambda: set())
    harness.keychain[ACCOUNT] = TOKEN
    harness.model_reply = "OK"

    harness.cli("doctor")

    out = capsys.readouterr().out
    assert "Claude: call succeeded" in out and "not found" not in out
    assert harness.model_calls[0].argv[0] == str(native_claude)


def test_the_nightly_run_finds_claude_from_the_native_installer(harness, native_claude):
    harness.keychain[ACCOUNT] = TOKEN
    harness.sources["gmail"] = [msg("gmail", "g-1", "2026-09-27T08:15:00+03:00",
                                    "Parents' evening is on 8.10.", sender="teacher@kilo.example.fi")]

    assert harness.run() == 0

    assert harness.model_calls and harness.model_calls[0].argv[0] == str(native_claude)
