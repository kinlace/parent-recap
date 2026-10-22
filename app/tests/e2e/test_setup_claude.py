"""Storing the Claude token with `parent-recap setup claude`: Claude's sign-in opens in a terminal
window, the token goes from macOS's dialog to one test call and the secrets store, and nowhere
else (ADR 0005).

`claude setup-token` draws its sign-in on Ink's interactive screen, which needs a real terminal,
so the step opens it in a terminal window and asks for the token it shows in the secret dialog.
The outside edges are faked: the desktop's windows, the dialog, the secrets store and the
model that answers the test call. The assistant reads only the command's one-line JSON result, so assertions
are on that result, on what the store holds, and on every place the token must never reach."""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import msg, program

from family_brief import install_record
from family_brief.system import secrets

TOKEN = "sk-ant-oat01-Abc_def-123456789"
ACCOUNT = "claude-oauth-token"


@pytest.fixture(autouse=True)
def desktop(monkeypatch):
    """A desktop session, as when the family runs setup on their own Mac."""
    for var in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def terminal(harness, tmp_path, monkeypatch):
    """The desktop, whose terminal windows `claude setup-token` is opened in, with a `claude`
    on the PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text("#!/bin/sh\nexit 0\n")
    claude.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    return harness.desktop


def result(capsys) -> tuple[dict[str, Any], str]:
    out, err = capsys.readouterr()
    lines = out.strip().splitlines()
    assert len(lines) == 1, f"expected one JSON line, got: {out!r}"
    return json.loads(lines[0]), out + err


def assert_never_leaked(harness, terminal, printed: str, caplog,
                        secret: str = TOKEN) -> None:
    assert secret not in printed
    assert secret not in caplog.text
    assert not any(secret in arg for cmd in harness.commands for arg in cmd)
    assert not any(secret in part for window in terminal.windows
                   for part in [*window.argv, *window.env.values()])


# ── the dialog


@pytest.mark.macos
def test_a_token_pasted_in_the_dialog_is_tested_and_stored(harness, terminal, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    harness.dialog.typed = f"  {TOKEN}\n"

    assert harness.cli("setup", "claude") == 0

    res, printed = result(capsys)
    assert res == {"result": "saved", "test_call": "ok"}
    assert len(terminal.windows) == 1  # what it shows, the next test
    assert len(harness.dialog.shown) == 1
    assert len(harness.model_calls) == 1  # how it signs in with the token: test_llm_claude.py
    assert harness.secrets == {ACCOUNT: TOKEN}
    assert install_record.entries("keychain") == [ACCOUNT]
    assert_never_leaked(harness, terminal, printed, caplog)


@pytest.mark.macos
def test_the_window_clears_the_token_once_the_family_presses_enter(harness, terminal, tmp_path,
                                                                  capsys):
    (tmp_path / "bin" / "claude").write_text(f"#!/bin/sh\n[ \"$1\" = setup-token ] && echo {TOKEN}\n")
    harness.dialog.typed = TOKEN
    assert harness.cli("setup", "claude") == 0
    [opened] = harness.desktop.windows

    # The terminal runs the window's command in the home folder; the family presses Enter once
    # the token is pasted.
    window = subprocess.Popen(opened.argv, cwd=harness.home, stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    shown, _ = window.communicate("\n", timeout=10)

    before, after = shown.split("\x1b[H\x1b[2J\x1b[3J")
    assert TOKEN in before and "paste it in the Parent Recap dialog" in before
    assert after == "You can close this window.\n"


@pytest.mark.macos
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
    [window] = terminal.windows
    assert str(claude) in window.argv
    assert harness.model.reached == [str(claude)]


@pytest.mark.macos
def test_a_token_copied_across_wrapped_lines_is_put_back_together(harness, terminal, capsys):
    # Claude's screen wraps the token to the window, so a copy can have line breaks in it.
    harness.dialog.typed = f"{TOKEN[:15]}\n  {TOKEN[15:]}"

    assert harness.cli("setup", "claude") == 0

    assert harness.secrets == {ACCOUNT: TOKEN}


@pytest.mark.macos
def test_a_failed_test_call_stores_nothing_and_says_why(harness, terminal, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    harness.model_error = "Invalid bearer token"
    harness.dialog.typed = TOKEN

    assert harness.cli("setup", "claude") == 1

    res, printed = result(capsys)
    assert res["result"] == "test-call-failed"
    assert "Invalid bearer token" in res["error"]
    assert "setup claude" in res["next"]
    assert harness.secrets == {}
    assert_never_leaked(harness, terminal, printed, caplog)


@pytest.mark.macos
def test_something_that_is_not_a_claude_token_is_never_sent_to_claude(harness, terminal, capsys,
                                                                      caplog):
    caplog.set_level(logging.DEBUG)
    harness.dialog.typed = "my-claude-password"

    assert harness.cli("setup", "claude") == 1

    res, printed = result(capsys)
    assert res["result"] == "not-a-token" and "sk-ant-oat01-" in res["next"]
    assert harness.model_calls == [] and harness.secrets == {}
    assert_never_leaked(harness, terminal, printed, caplog, "my-claude-password")


@pytest.mark.macos
def test_closing_the_dialog_stores_nothing(harness, terminal, capsys):
    harness.dialog.button = "cancel"

    assert harness.cli("setup", "claude") == 1

    assert result(capsys)[0]["result"] == "cancelled"
    assert harness.model_calls == [] and harness.secrets == {}


@pytest.mark.macos
def test_a_keychain_that_refuses_says_so(harness, terminal, capsys):
    harness.secrets.refuses = secrets.SecretsError("the store refused", "failed", -128)
    harness.dialog.typed = TOKEN

    assert harness.cli("setup", "claude") == 1

    assert result(capsys)[0]["result"] == "keychain-failed"


@pytest.mark.macos
def test_another_keychain_error_says_so_with_its_code(harness, terminal, capsys):
    harness.secrets.refuses = secrets.SecretsError("the store refused", "failed", -25293)
    harness.dialog.typed = TOKEN

    assert harness.cli("setup", "claude") == 1

    res = result(capsys)[0]
    assert res["result"] == "keychain-failed" and res["code"] == -25293
    assert "click Allow" in res["next"]


@pytest.mark.macos
def test_a_keychain_out_of_reach_says_to_run_it_outside_tmux_or_ssh(harness, terminal, capsys):
    harness.secrets.refuses = secrets.SecretsError("the store can't be reached", "not-reachable",
                                                   -25308)
    harness.dialog.typed = TOKEN

    assert harness.cli("setup", "claude") == 1

    res = result(capsys)[0]
    assert res["result"] == "keychain-not-reachable" and res["code"] == -25308
    assert "outside tmux or SSH" in res["next"] and "Shell → New Command…" in res["next"]
    assert "setup claude --no-open" in res["next"]
    assert "Allow" not in res["next"]  # macOS shows no prompt there


@pytest.mark.macos
@pytest.mark.parametrize("var", ["TMUX", "SSH_CONNECTION"])
def test_in_tmux_or_ssh_it_warns_before_the_token_is_asked_for(harness, terminal, monkeypatch,
                                                              capsys, var):
    import getpass
    harness.secrets.reachable_here = False
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


@pytest.mark.macos
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
    assert terminal.windows == [] and harness.dialog.shown == []


@pytest.mark.macos
def test_when_terminal_does_not_open_it_says_what_to_run(harness, terminal, capsys):
    terminal.opens = False

    assert harness.cli("setup", "claude") == 1

    res, _ = result(capsys)
    assert res["result"] == "no-terminal"
    assert "claude setup-token" in res["next"] and "--no-open" in res["next"]
    assert harness.dialog.shown == []


@pytest.mark.macos
def test_no_open_only_asks_for_the_token(harness, terminal, capsys):
    harness.dialog.typed = TOKEN

    assert harness.cli("setup", "claude", "--no-open") == 0

    assert terminal.windows == []
    assert harness.secrets == {ACCOUNT: TOKEN}


@pytest.mark.macos
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
    assert harness.secrets == {}


@pytest.mark.macos
def test_storing_the_token_again_replaces_one_the_keychain_asks_about(harness, terminal, capsys):
    # An earlier version stored it with keyring, so only that Python may read it without a prompt.
    harness.secrets[ACCOUNT] = "sk-ant-oat01-old"
    harness.secrets.asks.add(ACCOUNT)
    harness.dialog.typed = TOKEN

    assert harness.cli("setup", "claude") == 0

    assert result(capsys)[0]["result"] == "saved"
    assert harness.secrets == {ACCOUNT: TOKEN} and not harness.secrets.asks


# ── doctor


@pytest.mark.macos
def test_doctor_points_to_the_setup_step_when_no_token_is_stored(harness, terminal, monkeypatch,
                                                                 capsys):
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["wilma"]["enabled"] = False
    harness.config["whatsapp"]["enabled"] = False

    harness.cli("doctor")

    out = capsys.readouterr().out
    assert "parent-recap setup claude" in out and "setup_claude_token" not in out


# ── Claude Code from its native installer, in ~/.local/bin
#
# The native installer puts `claude` in ~/.local/bin and adds that folder to the shell's PATH,
# which a shell started earlier, the agent's own process or the nightly job may not have.


@pytest.fixture
def native_claude(harness, monkeypatch) -> Path:
    """A fake `claude` where the native installer puts it, on no PATH this process sees."""
    bin_dir = harness.home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    # claude.exe on Windows, where a program without its extension can't be run.
    claude = bin_dir / ("claude.exe" if sys.platform == "win32" else "claude")
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
    assert str(native_claude) in terminal.windows[0].argv
    assert harness.model.reached == [str(native_claude)]


def test_doctor_finds_claude_from_the_native_installer(harness, native_claude, monkeypatch, capsys):
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["wilma"]["enabled"] = False
    harness.config["whatsapp"]["enabled"] = False
    harness.secrets[ACCOUNT] = TOKEN

    harness.cli("doctor")

    out = capsys.readouterr().out
    assert "Claude: call succeeded" in out and "not found" not in out
    assert harness.model.reached == [str(native_claude)]


def test_the_nightly_run_finds_claude_from_the_native_installer(harness, native_claude):
    harness.secrets[ACCOUNT] = TOKEN
    harness.sources["gmail"] = [msg("gmail", "1001", "2026-09-27T08:15:00+03:00",
                                    "Parents' evening is on 8.10.", sender="teacher@kilo.example.fi")]

    assert harness.run() == 0

    assert harness.model.reached == [str(native_claude)]


def doctor_claude_line(harness, monkeypatch, capsys) -> str:
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["wilma"]["enabled"] = False
    harness.config["whatsapp"]["enabled"] = False
    harness.cli("doctor")
    return next(l for l in capsys.readouterr().out.splitlines() if "Claude:" in l)


@pytest.mark.macos
def test_doctor_tells_a_token_the_keychain_asks_about_from_a_missing_one(harness, terminal,
                                                                         monkeypatch, capsys):
    from family_brief import ops
    harness.secrets[ACCOUNT] = TOKEN
    harness.secrets.asks.add(ACCOUNT)

    line = doctor_claude_line(harness, monkeypatch, capsys)

    assert line.startswith(ops.FAIL)
    assert "no claude-oauth-token" not in line
    assert "asks for the Keychain password" in line and "evening" in line
    assert "parent-recap setup claude" in line


@pytest.mark.macos
def test_doctor_reads_a_stored_token(harness, terminal, monkeypatch, capsys):
    from family_brief import ops
    harness.secrets[ACCOUNT] = TOKEN

    line = doctor_claude_line(harness, monkeypatch, capsys)

    assert line.startswith(ops.OK) and "auth: keychain-oauth" in line
