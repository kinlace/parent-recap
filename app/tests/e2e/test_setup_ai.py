"""The setup page's AI sign-in for the evening Brief (ADR 0006): Claude, ChatGPT (Codex) and the
maintainers' gateway for an invited Household (ADR 0014), and switching the AI or the Gateway Key
once setup is done (ADR 0017).

The server runs in-process on a free port, inside the harness, as `setup_page.py` starts it, so the
config, the progress record, the secrets, the jobs, the wake and the desktop are the harness's.
Assertions are on what the page and the family see: the responses, the config and progress written,
and what was opened."""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import requests
import yaml

from family_brief import install_record, ops, setup_ai, setup_server, setup_steps
from family_brief.config import Config
from family_brief.setup_server.ai import AISignIn
from conftest import FailedCall, HTTPReply, program
from setup_page import (PAGE_DIR, Response, assert_never_leaked, at_finish, call, chat_installed, check_ai,
                        config_file, inside, phase, progress_file, refuse_secrets, save_progress, statuses,
                        wait_for, welcome)
from setup_page import _listening, ai, clock, page  # noqa: F401


# ── Connect: the AI sign-in for the evening Brief

CLAUDE_TOKEN = "sk-ant-oat01-Abc_123-xyzXYZ0987654321abcdefghijklmnopqrstuvwxyz-AA"

# `claude setup-token` as setup runs it: it opens Anthropic's Authorize page, and once the family
# has clicked Authorize it draws the token on Ink's screen, with its escape sequences, and ends.
# Without a terminal it stops, as Ink's does.
FAKE_CLAUDE = """#!{python}
import json, os, pathlib, sys, time
here = pathlib.Path(os.path.realpath(__file__)).parent
ctl = json.loads((here / "claude.json").read_text())
if sys.argv[1:] != ["setup-token"]:
    sys.exit(2)
(here / "setup-token.ran").write_text("")
out = sys.stdout
if ctl["terminal"] and not os.isatty(0):
    out.write("Error: Raw mode is not supported on the current process.stdin\\n")
    sys.exit(1)
out.write("\\x1b[?25l\\x1b[2K\\x1b[1GOpening browser to sign in\\u2026\\r\\n")
out.write("Browser didn't open? Use the url below to sign in:\\r\\n\\r\\n"
          "https://claude.ai/oauth/authorize?code=true&client_id=x&state=y\\r\\n")
out.flush()
if ctl["hangs"]:
    time.sleep(60)
if ctl["token"] is None:
    out.write("\\x1b[31mOAuth error: Request failed with status code 400\\x1b[39m\\r\\n")
    sys.exit(1)
out.write("\\x1b[2K\\x1b[32m\\u2713\\x1b[39m Long-lived authentication token created "
          "successfully!\\r\\n\\r\\nYour OAuth token (valid for 1 year):\\r\\n\\r\\n")
token = ctl["token"]
out.write("\\x1b[1m" + token[:20])
out.flush()
time.sleep(0.2)  # drawn in two parts: the first alone isn't the token
if ctl["stays"]:  # nothing but escape sequences after it, and it keeps running
    out.write(token[20:] + "\\x1b[22m\\x1b[?25h")
    out.flush()
    time.sleep(60)
out.write(token[20:] + "\\x1b[22m\\r\\n\\r\\nStore this token securely. You won't be able to "
          "see it again.\\r\\n\\x1b[?25h")
out.flush()
"""


def _can_open_a_pty() -> bool:
    try:
        for fd in os.openpty():
            os.close(fd)
        return True
    except (OSError, AttributeError):  # AttributeError: Windows has no os.openpty
        return False


CAN_PTY = _can_open_a_pty()


class FakeClaude:
    """The Mac's `claude`: `setup-token` runs for real, in the pseudo-terminal setup gives it;
    its test call is the harness's. Not installed until `install()`."""

    def __init__(self, bin_dir: Path) -> None:
        self.bin_dir = bin_dir
        self.token: str | None = CLAUDE_TOKEN  # what Authorize gives; None if it fails
        self.hangs = False                      # the family never clicks Authorize
        self.stays = False                      # it keeps running once the token is shown
        self.started: list[list[str]] = []      # each process started in the background

    @property
    def ran(self) -> bool:
        return (self.bin_dir / "setup-token.ran").exists()

    def install(self) -> None:
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        (self.bin_dir / "claude").write_text(FAKE_CLAUDE.format(python=sys.executable))
        (self.bin_dir / "claude").chmod(0o755)
        (self.bin_dir / "claude.json").write_text(json.dumps(
            {"token": self.token, "hangs": self.hangs, "stays": self.stays, "terminal": CAN_PTY}))


def record_background_processes(harness, monkeypatch, started: list[list[str]]) -> None:
    popen = subprocess.Popen

    def recorded(cmd: list[str], *a: Any, **k: Any) -> Any:
        harness.commands.append(list(cmd))  # each command line, with the harness's
        started.append(list(cmd))
        return popen(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "Popen", recorded)


@pytest.fixture
def claude(harness, ai, monkeypatch) -> FakeClaude:
    fake = FakeClaude(harness.home / "bin")
    if not CAN_PTY:  # a sandbox: the same reading and writing, through a socket pair
        def pair() -> tuple[int, int]:
            ours, theirs = socket.socketpair()
            return ours.detach(), theirs.detach()
        monkeypatch.setattr(setup_ai, "open_terminal", pair)
    record_background_processes(harness, monkeypatch, fake.started)
    return fake


def claude_result(url: str) -> dict[str, Any]:
    out: dict[str, Any] = {}

    def done() -> bool:
        out.clear()
        out.update(call(url, "api/claude/check", method="POST", body={}).json())
        return out["result"] != "waiting"
    wait_for(done, 10)
    return out


def at_the_ai_step(harness, url: str, ai_name: str = "claude") -> None:
    welcome(url, ai=ai_name)
    save_progress(harness, {**json.loads(progress_file(harness).read_text()), "source": "ai",
                            "sources": {"wilma": "done", "gmail": "done"}})


@pytest.mark.macos
def test_claude_s_token_is_read_tested_and_stored_with_nothing_copied(harness, page, claude,
                                                                       caplog, capsys):
    caplog.set_level(logging.DEBUG)
    claude.install()
    at_the_ai_step(harness, page.url)

    r = call(page.url, "api/claude", method="POST", body={})

    assert r.status == 200 and r.json() == {"result": "waiting"}
    out = claude_result(page.url)
    assert out["result"] == "saved"
    assert claude.ran and claude.started == [[str(claude.bin_dir / "claude"), "setup-token"]]
    assert harness.secrets == {"claude-oauth-token": CLAUDE_TOKEN}
    [test_call] = harness.model_calls  # with the token in its environment
    assert test_call.env["CLAUDE_CODE_OAUTH_TOKEN"] == CLAUDE_TOKEN
    assert out["progress"]["sources"]["ai"] == "done" and out["progress"]["source"] == "whatsapp"
    assert not harness.desktop.windows
    assert_never_leaked(harness, [r, call(page.url, "api/claude/check", method="POST", body={}),
                                  call(page.url, "api/state")], caplog, capsys, CLAUDE_TOKEN)


@pytest.mark.macos
def test_a_token_at_the_end_of_the_screen_is_read_once_the_screen_is_still(harness, page, claude):
    claude.stays = True
    claude.install()

    call(page.url, "api/claude", method="POST", body={})

    assert claude_result(page.url)["result"] == "saved"
    assert harness.secrets == {"claude-oauth-token": CLAUDE_TOKEN}


@pytest.mark.macos
def test_a_claude_sign_in_already_running_is_not_started_twice(harness, page, claude):
    claude.hangs = True
    claude.install()

    for _ in range(2):
        assert call(page.url, "api/claude", method="POST", body={}).json() == {"result": "waiting"}

    wait_for(lambda: claude.ran)
    assert len(claude.started) == 1


@pytest.mark.macos
@pytest.mark.parametrize("how, result", [("no token", "sign-in-failed"), ("hangs", "timeout")])
def test_a_claude_sign_in_without_a_token_offers_the_terminal_window(harness, page, claude, how,
                                                                    result):
    page.context.timeouts.claude_sign_in = 1
    claude.token = None
    claude.hangs = how == "hangs"
    claude.install()

    call(page.url, "api/claude", method="POST", body={})

    assert claude_result(page.url) == {"result": result}
    assert harness.secrets == {} and statuses(page.url)["ai"] == "to-do"


@pytest.mark.macos
def test_a_token_claude_does_not_accept_is_not_kept(harness, page, claude):
    harness.model_error = "Invalid bearer token"
    claude.install()

    call(page.url, "api/claude", method="POST", body={})

    assert claude_result(page.url) == {"result": "test-call-failed"}
    assert harness.secrets == {} and statuses(page.url)["ai"] == "to-do"


@pytest.mark.macos
def test_the_fallback_opens_the_terminal_window_and_takes_the_pasted_token(harness, page, claude,
                                                                          caplog, capsys):
    caplog.set_level(logging.DEBUG)
    claude.install()
    at_the_ai_step(harness, page.url)

    r = call(page.url, "api/claude/terminal", method="POST", body={})

    assert r.status == 200 and r.json() == {"result": "opened"}
    [window] = harness.desktop.windows
    assert window.title == setup_steps.CLAUDE_WINDOW
    assert window.argv == setup_steps.setup_token_window(str(claude.bin_dir / "claude"),
                                                          "the field on the setup page")

    # Copied from the window, across the lines Terminal wrapped it on.
    pasted = f" {CLAUDE_TOKEN[:30]}\n{CLAUDE_TOKEN[30:]} "
    r = call(page.url, "api/claude/token", method="POST", body={"token": pasted})

    assert r.status == 200 and r.json()["result"] == "saved"
    assert harness.secrets == {"claude-oauth-token": CLAUDE_TOKEN}
    assert r.json()["progress"]["sources"]["ai"] == "done"
    assert harness.desktop.forgotten == [setup_steps.CLAUDE_WINDOW]  # once it has done its work
    assert_never_leaked(harness, [r], caplog, capsys, CLAUDE_TOKEN)


@pytest.mark.macos
@pytest.mark.parametrize("pasted, result", [("my-claude-password", "not-a-token"),
                                            (CLAUDE_TOKEN, "test-call-failed")])
def test_a_pasted_token_that_does_not_work_is_explained_and_not_kept(harness, page, claude, caplog,
                                                                     capsys, pasted, result):
    caplog.set_level(logging.DEBUG)
    harness.model_error = f"Invalid bearer token {pasted}"
    claude.install()

    r = call(page.url, "api/claude/token", method="POST", body={"token": pasted})

    assert r.status == 200 and r.json() == {"result": result}
    assert harness.secrets == {}
    if result == "not-a-token":  # most likely a password: never sent to Claude
        assert not harness.model_calls
    assert_never_leaked(harness, [r], caplog, capsys, pasted)


@pytest.mark.macos
def test_a_keychain_that_refuses_the_claude_token_says_so(harness, page, claude):
    refuse_secrets(harness, -128)  # errSecUserCanceled
    claude.install()

    r = call(page.url, "api/claude/token", method="POST", body={"token": CLAUDE_TOKEN})

    assert r.json() == {"result": "keychain-failed", "code": -128}
    assert statuses(page.url)["ai"] == "to-do"


@pytest.mark.macos
def test_a_keychain_out_of_reach_of_the_claude_token_says_so_with_its_code(harness, page, claude):
    refuse_secrets(harness, -25308)  # errSecInteractionNotAllowed
    claude.install()

    r = call(page.url, "api/claude/token", method="POST", body={"token": CLAUDE_TOKEN})

    assert r.json() == {"result": "keychain-not-reachable", "code": -25308}
    assert statuses(page.url)["ai"] == "to-do"


@pytest.mark.macos  # Windows installs it on the page, and has no sign-in read from a pty
def test_without_claude_code_its_sign_in_says_how_to_install_it(harness, page, claude):
    for path, body in [("api/claude", {}), ("api/claude/terminal", {}),
                       ("api/claude/token", {"token": CLAUDE_TOKEN})]:
        assert call(page.url, path, method="POST", body=body).json() == {
            "result": "not-installed", "install": "curl -fsSL https://claude.ai/install.sh | bash"}
    assert not harness.secrets


def test_the_claude_calls_take_only_what_they_need(harness, page, claude):
    claude.install()
    for path in ("api/claude", "api/claude/check", "api/claude/terminal"):
        r = call(page.url, path, method="POST", body={"token": CLAUDE_TOKEN})
        assert r.status == 400 and CLAUDE_TOKEN.encode() not in r.body, path
    for body in [{}, {"token": 1}, {"token": CLAUDE_TOKEN, "x": 1}]:
        r = call(page.url, "api/claude/token", method="POST", body=body)
        assert r.status == 400 and CLAUDE_TOKEN.encode() not in r.body, body
    assert call(page.url, "api/claude/check", method="POST", body={}).json() == \
        {"result": "no-sign-in"}
    assert not claude.started and not harness.secrets


# `login` stays open as the real one does while the sign-in is open in the browser, unless
# FAKE_CODEX_LOGIN_EXIT ends it at once: one that ended signed out is the page's login-failed.
FAKE_CODEX = """#!{python}
import os, pathlib, sys, time
if sys.argv[1:] == ["login"]:
    (pathlib.Path(os.path.realpath(__file__)).parent / "codex-login.ran").write_text("")
    if "FAKE_CODEX_LOGIN_EXIT" in os.environ:
        sys.exit(int(os.environ["FAKE_CODEX_LOGIN_EXIT"]))
    time.sleep(30)
    sys.exit(0)
sys.exit(2)
"""


@pytest.fixture
def codex(harness, ai, monkeypatch) -> Path:
    """The Mac's `codex`: `login` runs for real, and its status is the harness's."""
    bin_dir = harness.home / "bin"
    (bin_dir / "codex").write_text(FAKE_CODEX.format(python=sys.executable))
    (bin_dir / "codex").chmod(0o755)
    record_background_processes(harness, monkeypatch, [])
    return bin_dir


def codex_check(url: str) -> dict[str, Any]:
    return call(url, "api/codex", method="POST", body={}).json()


@pytest.mark.macos
def test_a_signed_in_codex_ticks_the_entry_itself(harness, page, codex):
    at_the_ai_step(harness, page.url, "codex")

    out = codex_check(page.url)

    assert out["result"] == "signed-in"
    assert out["progress"]["sources"]["ai"] == "done" and out["progress"]["source"] == "whatsapp"
    assert not (codex / "codex-login.ran").exists()  # no sign-in needed


@pytest.mark.macos
def test_a_signed_out_codex_signs_in_and_the_entry_ticks_itself(harness, page, codex):
    at_the_ai_step(harness, page.url, "codex")
    harness.signed_in["codex"] = False
    assert codex_check(page.url) == {"result": "signed-out"}

    r = call(page.url, "api/codex/login", method="POST", body={})

    assert r.status == 200 and r.json() == {"result": "waiting"}
    assert [str(codex / "codex"), "login"] in harness.commands
    wait_for(lambda: (codex / "codex-login.ran").exists())
    harness.signed_in["codex"] = True  # the family signs in with ChatGPT in the browser
    out = codex_check(page.url)
    assert out["result"] == "signed-in" and out["progress"]["sources"]["ai"] == "done"
    assert statuses(page.url)["ai"] == "done"


@pytest.mark.macos
def test_a_codex_sign_in_that_ends_signed_out_says_so(harness, page, codex, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_LOGIN_EXIT", "1")
    harness.signed_in["codex"] = False

    assert call(page.url, "api/codex/login", method="POST", body={}).json() == {"result": "waiting"}

    wait_for(lambda: phase(page, AISignIn)._codex.poll() is not None)
    assert codex_check(page.url) == {"result": "login-failed"}
    assert codex_check(page.url) == {"result": "signed-out"}  # said once; it can be started again
    assert statuses(page.url)["ai"] == "to-do"


def test_without_codex_its_sign_in_says_so(harness, page, ai):
    assert codex_check(page.url) == {"result": "not-installed"}
    assert call(page.url, "api/codex/login", method="POST", body={}).json() == \
        {"result": "not-installed"}
    for path in ("api/codex", "api/codex/login", "api/codex/install"):
        assert call(page.url, path, method="POST", body={"x": 1}).status == 400


def same_file(path: str | Path) -> str:
    """`path` as the system compares it: Windows' `which` names codex.cmd codex.CMD."""
    return os.path.normcase(str(path))


def fake_codex(folder: Path) -> Path:
    """A `codex` the system can run, made as Windows' own installer would put it: `login` runs
    for real, and its status is the harness's. On Windows a `.cmd` runs the script."""
    folder.mkdir(parents=True, exist_ok=True)
    script = FAKE_CODEX.format(python=sys.executable)
    if os.name != "nt":
        return program(folder, "codex.cmd", script)  # found by its Windows name, run by its #!
    (folder / "codex.py").write_text(script)
    (folder / "codex.cmd").write_text(f'@"{sys.executable}" "%~dp0codex.py" %*\r\n')
    return folder / "codex.cmd"


@pytest.fixture
def codex_installer(harness, ai, windows_programs, monkeypatch) -> dict[str, Any]:
    """A Windows PC without Codex, and OpenAI's installer, which PowerShell runs: the fake puts
    `codex` where the real one puts codex.exe, under %LOCALAPPDATA%, unless `fails`. The PC's
    PATH doesn't lead there: the installer adds it only for windows opened after it ran."""
    installer = {"fails": False, "ran": [],
                 "folder": windows_programs / "Programs" / "OpenAI" / "Codex" / "bin"}
    record_background_processes(harness, monkeypatch, [])
    run = subprocess.run

    def powershell(cmd: list[str], *a: Any, **k: Any) -> subprocess.CompletedProcess:
        if cmd[0] != "powershell.exe":
            return run(cmd, *a, **k)
        installer["ran"].append(cmd)
        if installer["fails"]:
            return subprocess.CompletedProcess(cmd, 1, "", "Invoke-WebRequest: no network")
        fake_codex(installer["folder"])
        return subprocess.CompletedProcess(cmd, 0, "Codex CLI installed successfully.\n", "")
    monkeypatch.setattr(subprocess, "run", powershell)
    return installer


def codex_installed(url: str) -> dict[str, Any]:
    out: dict[str, Any] = {}

    def done() -> bool:
        out.clear()
        out.update(codex_check(url))
        return out["result"] != "installing"
    wait_for(done)
    return out


def test_on_windows_the_page_installs_codex_with_openai_s_installer_and_opens_its_sign_in(
        harness, page, codex_installer):
    at_the_ai_step(harness, page.url, "codex")
    harness.signed_in["codex"] = False
    assert codex_check(page.url) == {"result": "not-installed"}

    r = call(page.url, "api/codex/install", method="POST", body={})

    assert r.status == 200 and r.json() == {"result": "installing"}
    assert codex_installed(page.url) == {"result": "waiting"}  # the sign-in is open in the browser
    [cmd] = codex_installer["ran"]
    assert cmd[-1] == "irm https://github.com/openai/codex/releases/latest/download/install.ps1 | iex"
    codex = codex_installer["folder"] / "codex.cmd"
    assert [same_file(codex), "login"] in [[same_file(c[0]), *c[1:]] for c in harness.commands]
    wait_for(lambda: (codex.parent / "codex-login.ran").exists())
    harness.signed_in["codex"] = True  # the family signs in with ChatGPT in the browser
    out = codex_check(page.url)
    assert out["result"] == "signed-in" and out["progress"]["sources"]["ai"] == "done"
    status = harness.commands[-1]
    assert [same_file(status[0]), *status[1:3]] == [same_file(codex), "login", "status"]


def test_on_windows_an_install_of_codex_that_fails_says_so_and_can_be_tried_again(
        harness, page, codex_installer):
    at_the_ai_step(harness, page.url, "codex")
    codex_installer["fails"] = True

    assert call(page.url, "api/codex/install", method="POST", body={}).json() == \
        {"result": "installing"}

    assert codex_installed(page.url) == {"result": "install-failed"}
    assert codex_check(page.url) == {"result": "not-installed"}  # said once
    codex_installer["fails"] = False
    assert call(page.url, "api/codex/install", method="POST", body={}).json() == \
        {"result": "installing"}
    assert codex_installed(page.url)["result"] == "signed-in"
    assert len(codex_installer["ran"]) == 2


def test_on_windows_installing_codex_that_is_there_already_only_opens_its_sign_in(
        harness, page, codex_installer):
    fake_codex(codex_installer["folder"])
    harness.signed_in["codex"] = False

    r = call(page.url, "api/codex/install", method="POST", body={})

    assert r.status == 200 and r.json() == {"result": "waiting"}
    assert codex_installer["ran"] == []


@pytest.mark.macos
def test_on_a_mac_the_page_doesn_t_install_codex_since_it_comes_with_the_chatgpt_app(harness,
                                                                                    page, ai):
    r = call(page.url, "api/codex/install", method="POST", body={})

    assert r.status == 400
    assert harness.commands == []


@pytest.fixture
def claude_installer(harness, ai, windows_programs, monkeypatch) -> dict[str, Any]:
    """A Windows PC without Claude Code, and its own Windows installer, which PowerShell runs:
    the fake puts claude.exe where the real one does, in %USERPROFILE%\\.local\\bin, unless
    `fails`. The PC's PATH doesn't lead there. The test call is the harness's."""
    installer = {"fails": False, "ran": [], "claude": harness.home / ".local" / "bin" / "claude.exe"}
    run = subprocess.run

    def powershell(cmd: list[str], *a: Any, **k: Any) -> subprocess.CompletedProcess:
        if cmd[0] != "powershell.exe":
            return run(cmd, *a, **k)
        installer["ran"].append(cmd)
        if installer["fails"]:
            return subprocess.CompletedProcess(cmd, 1, "", "Invoke-RestMethod: no network")
        program(installer["claude"].parent, installer["claude"].name)
        return subprocess.CompletedProcess(cmd, 0, "Claude Code successfully installed!\n", "")
    monkeypatch.setattr(subprocess, "run", powershell)
    return installer


def claude_installed(url: str) -> dict[str, Any]:
    out: dict[str, Any] = {}

    def done() -> bool:
        out.clear()
        out.update(call(url, "api/claude/check", method="POST", body={}).json())
        return out["result"] != "installing"
    wait_for(done)
    return out


def test_on_windows_the_page_installs_claude_code_opens_its_window_and_stores_the_pasted_token(
        harness, page, claude_installer, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    at_the_ai_step(harness, page.url)
    assert call(page.url, "api/claude/terminal", method="POST", body={}).json() == \
        {"result": "not-installed"}  # the page offers to install it, not a line to paste

    r = call(page.url, "api/claude/install", method="POST", body={})

    assert r.status == 200 and r.json() == {"result": "installing"}
    assert claude_installed(page.url) == {"result": "opened"}
    [cmd] = claude_installer["ran"]
    assert cmd[-1] == "irm https://claude.ai/install.ps1 | iex"
    claude = str(claude_installer["claude"])
    [window] = harness.desktop.windows  # `claude setup-token`, in a PowerShell window
    assert window.title == setup_steps.CLAUDE_WINDOW
    assert window.argv == setup_steps.setup_token_window(claude, "the field on the setup page")

    r = call(page.url, "api/claude/token", method="POST", body={"token": CLAUDE_TOKEN})

    assert r.status == 200 and r.json()["result"] == "saved"
    assert r.json()["progress"]["sources"]["ai"] == "done"
    assert harness.secrets == {"claude-oauth-token": CLAUDE_TOKEN}
    [test_call] = harness.model_calls
    assert same_file(test_call.argv[0]) == same_file(claude)  # Windows' `which` says claude.EXE
    assert test_call.env["CLAUDE_CODE_OAUTH_TOKEN"] == CLAUDE_TOKEN
    assert_never_leaked(harness, [r], caplog, capsys, CLAUDE_TOKEN)


def test_on_windows_an_install_of_claude_code_that_fails_says_so_and_can_be_tried_again(
        harness, page, claude_installer):
    at_the_ai_step(harness, page.url)
    claude_installer["fails"] = True

    assert call(page.url, "api/claude/install", method="POST", body={}).json() == \
        {"result": "installing"}

    assert claude_installed(page.url) == {"result": "install-failed"}
    assert call(page.url, "api/claude/check", method="POST", body={}).json() == \
        {"result": "no-sign-in"}  # said once
    assert not harness.desktop.windows
    claude_installer["fails"] = False
    call(page.url, "api/claude/install", method="POST", body={})
    assert claude_installed(page.url) == {"result": "opened"}
    assert len(claude_installer["ran"]) == 2


def test_on_windows_installing_claude_code_that_is_there_already_only_opens_its_window(
        harness, page, claude_installer):
    program(claude_installer["claude"].parent, claude_installer["claude"].name)

    r = call(page.url, "api/claude/install", method="POST", body={})

    assert r.status == 200 and r.json() == {"result": "opened"}
    assert claude_installer["ran"] == [] and len(harness.desktop.windows) == 1


def test_on_windows_claude_signs_in_only_in_its_window_without_a_pseudo_terminal(
        harness, page, claude_installer):
    program(claude_installer["claude"].parent, claude_installer["claude"].name)

    r = call(page.url, "api/claude", method="POST", body={})

    assert r.status == 400
    assert not harness.commands and phase(page, AISignIn)._claude is None


@pytest.mark.macos
def test_on_a_mac_the_page_doesn_t_install_claude_code_and_gives_the_line_instead(harness, page,
                                                                                 ai):
    r = call(page.url, "api/claude/install", method="POST", body={})

    assert r.status == 400
    assert harness.commands == []


def test_every_ai_sign_in_result_is_explained_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        for result in setup_server.ai.CLAUDE_RESULTS:
            assert table.get(f"claude.{result}", "").strip(), (language, result)
        for result in setup_server.ai.CLAUDE_WINDOW_RESULTS:
            assert table.get(f"claude.window.{result}", "").strip(), (language, result)
        for result in setup_server.ai.CODEX_RESULTS:
            assert table.get(f"codex.{result}", "").strip(), (language, result)


# Where each AI's consumer plans let the family turn off training on their chats, named as the
# app names it (ADR 0013).
TRAINING_SETTINGS = {"claude": ("Settings > Privacy", "Help improve Claude"),
                     "codex": ("Settings > Data controls", "Improve the model for everyone")}


def test_the_ai_sign_in_says_where_to_turn_off_training_on_the_chats():
    # Both AIs may train on the family's chats by default, and Parent Recap can't check the
    # setting, so the sign-in for the AI the family chose names it, as a hint beside the step.
    page_html = (PAGE_DIR / "index.html").read_text()
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for name, (menu, setting) in TRAINING_SETTINGS.items():
        assert f'<p class="hint" data-text="{name}.training"></p>' in inside(page_html, f"ai-{name}")
        for language, table in text.items():
            assert menu in table[f"{name}.training"], (language, name)
            assert setting in table[f"{name}.training"], (language, name)


def test_the_claude_token_field_is_the_page_s_own(page):
    html = call(page.url).body.decode()

    field = re.search(r'<input[^>]*id="claude-token"[^>]*>', html).group(0)
    assert 'type="password"' in field and 'autocomplete="off"' in field


# ── Welcome and Connect: the maintainers' gateway, for an invited Household (ADR 0014)


GATEWAY_KEY = "sk-bf-fake-gateway-key"
GATEWAY_UNKNOWN_KEY = HTTPReply(401, {"type": "error", "error": {
    "type": "authentication_error", "message": "virtual key not found"}})


def save_gateway_key(url: str, key: str) -> Response:
    return call(url, "api/gateway", method="POST", body={"key": key})


def test_the_gateway_is_ready_with_nothing_installed_and_no_chat_to_install(harness, page, ai):
    r = check_ai(page.url, "gateway")

    assert r.status == 200 and r.json() == {"result": "ready", "ai": "gateway"}
    assert welcome(page.url, ai="gateway").status == 200
    assert Config.load(config_file(harness)).llm.backend == "gateway"
    state = call(page.url, "api/state").json()
    assert state["welcome"]["ai"] == "gateway"
    # Neither Claude Code nor Codex is looked for, run or given the plugin: there's no chat.
    assert not any(Path(c[0]).name in ("claude", "codex") for c in harness.commands)
    assert harness.plugin_calls == [] and state["chat"] == {}


@pytest.mark.macos
def test_a_gateway_key_pasted_on_the_page_is_tested_stored_and_ticks_the_ai(harness, page, caplog,
                                                                            capsys):
    caplog.set_level(logging.DEBUG)
    at_the_ai_step(harness, page.url, "gateway")
    harness.model_reply = "OK"

    r = save_gateway_key(page.url, f" {GATEWAY_KEY}\n")  # as copied from a message

    assert r.status == 200 and r.json()["result"] == "saved"
    assert r.json()["progress"]["sources"]["ai"] == "done"
    assert r.json()["progress"]["source"] == "whatsapp"
    [test_call] = harness.model_calls
    assert test_call.argv == ["POST", "https://gateway.parentrecap.com/v1/messages"]
    assert test_call.headers["x-api-key"] == GATEWAY_KEY
    assert harness.secrets == {"gateway-key": GATEWAY_KEY}
    assert install_record.entries("keychain") == ["gateway-key"]  # so uninstall removes it
    assert Config.load(config_file(harness)).llm.backend == "gateway"
    assert_never_leaked(harness, [r, call(page.url, "api/state")], caplog, capsys, GATEWAY_KEY)


def test_the_key_is_tested_with_the_gateway_the_config_names(harness, page):
    at_the_ai_step(harness, page.url, "gateway")
    data = yaml.safe_load(config_file(harness).read_text())
    data["llm"]["gateway_url"] = "https://gateway.example.test"
    config_file(harness).write_text(yaml.safe_dump(data))
    harness.model_reply = "OK"

    assert save_gateway_key(page.url, GATEWAY_KEY).json()["result"] == "saved"

    assert harness.model_calls[0].argv == ["POST", "https://gateway.example.test/v1/messages"]


@pytest.mark.parametrize("pasted, reply, result", [
    ("my-mac-password", None, "not-a-key"),
    (GATEWAY_KEY, GATEWAY_UNKNOWN_KEY, "rejected"),
    (GATEWAY_KEY, requests.ConnectionError("Connection refused"), "no-connection"),
    (GATEWAY_KEY, FailedCall(f"upstream error for {GATEWAY_KEY} in /Users/mum"), "test-call-failed"),
])
def test_a_gateway_key_that_does_not_work_is_explained_and_not_kept(harness, page, caplog, capsys,
                                                                    pasted, reply, result):
    caplog.set_level(logging.DEBUG)
    at_the_ai_step(harness, page.url, "gateway")
    harness.model_reply = [reply]

    r = save_gateway_key(page.url, pasted)

    # Only the result: what the gateway said can name the Mac's files, or echo the key.
    assert r.status == 200 and r.json() == {"result": result}
    assert harness.secrets == {} and statuses(page.url)["ai"] == "to-do"
    if result == "not-a-key":  # most likely a password: never sent to the gateway
        assert not harness.model_calls
    assert_never_leaked(harness, [r], caplog, capsys, pasted)


@pytest.mark.parametrize("code, result", [(-128, "keychain-failed"),
                                          (-25308, "keychain-not-reachable")])
def test_a_keychain_that_refuses_the_gateway_key_says_so_with_its_code(harness, page, code, result):
    at_the_ai_step(harness, page.url, "gateway")
    refuse_secrets(harness, code)
    harness.model_reply = "OK"

    r = save_gateway_key(page.url, GATEWAY_KEY)

    assert r.json() == {"result": result, "code": code}
    assert statuses(page.url)["ai"] == "to-do"


def test_the_gateway_call_takes_only_the_key(harness, page):
    for body in [{}, {"key": 1}, {"key": GATEWAY_KEY, "x": 1}]:
        r = call(page.url, "api/gateway", method="POST", body=body)
        assert r.status == 400 and GATEWAY_KEY.encode() not in r.body, body
    assert not harness.model_calls and not harness.secrets


def test_every_gateway_result_is_explained_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        assert table.get("ai.gateway.ready", "").strip(), language
        for result in setup_server.ai.GATEWAY_RESULTS:
            assert table.get(f"gateway.{result}", "").strip(), (language, result)


# How each language names the maintainers' gateway, and the EU it runs in.
GATEWAY_WORDS = {"en": ("gateway", "EU"), "fi": ("yhdyskäytävä", "EU"), "zh": ("网关", "欧盟")}


def test_the_page_says_the_messages_go_through_the_maintainers_gateway():
    # ADR 0014: before a Household uses the gateway, the page says what passes through it.
    page_html = (PAGE_DIR / "index.html").read_text()
    text = json.loads((PAGE_DIR / "text.json").read_text())

    assert '<p class="hint" data-text="gateway.privacy"></p>' in inside(page_html, "ai-gateway")
    for language, (gateway, eu) in GATEWAY_WORDS.items():
        for key in ("ai.gateway.ready", "gateway.privacy"):
            assert gateway in text[language][key] and eu in text[language][key], (language, key)


# How each language says the AI filter applies only while it's on, as SECURITY.md does.
FILTER_ON_WORDS = {"en": "when it's on", "fi": "kun se on päällä", "zh": "开着时"}


def test_the_gateway_text_says_the_ai_filter_applies_only_when_it_s_on():
    # ADR 0013: a Household can turn the AI filter off, so the page doesn't promise it always runs.
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, words in FILTER_ON_WORDS.items():
        for key in ("ai.gateway.ready", "gateway.privacy"):
            assert words in text[language][key], (language, key)


def test_the_gateway_key_field_is_the_page_s_own(page):
    html = call(page.url).body.decode()

    field = re.search(r'<input[^>]*id="gateway-key"[^>]*>', html).group(0)
    assert 'type="password"' in field and 'autocomplete="off"' in field
    assert 'name="ai" value="gateway"' in html


def test_the_check_page_names_the_gateway_s_own_checks():
    for key, item in ops.CHECK_ITEMS:
        if key in ("gateway", "gateway-key"):
            assert setup_server.health.health_check(ops.Check(ops.FAIL, key, item, ""))["check"] == "gateway"
    assert setup_server.health.health_check(ops.Check(ops.FAIL, "claude", "Claude", ""))["check"] == "ai"


# ── Finish: switching the AI, or a new Gateway Key, once setup is done (ADR 0017)


def backend(harness) -> str:
    return Config.load(config_file(harness)).llm.backend


def at_finish_with(harness, ai_name: str) -> None:
    at_finish(harness)
    data = yaml.safe_load(config_file(harness).read_text())
    data["llm"] = {"backend": ai_name}
    config_file(harness).write_text(yaml.safe_dump(data))


def test_on_finish_a_gateway_key_switches_the_household_to_the_gateway(harness, page, caplog,
                                                                       capsys):
    caplog.set_level(logging.DEBUG)
    at_finish(harness)
    harness.model_reply = "OK"

    r = save_gateway_key(page.url, GATEWAY_KEY)

    assert r.status == 200 and r.json()["result"] == "saved"
    assert backend(harness) == "gateway"
    assert harness.secrets == {"gateway-key": GATEWAY_KEY}
    assert call(page.url, "api/state").json()["progress"]["phase"] == "finish"  # still at Finish
    assert_never_leaked(harness, [r, call(page.url, "api/state")], caplog, capsys, GATEWAY_KEY)


@pytest.mark.parametrize("reply, result", [
    (GATEWAY_UNKNOWN_KEY, "rejected"),
    (requests.ConnectionError("Connection refused"), "no-connection"),
])
def test_on_finish_a_gateway_key_that_does_not_work_keeps_the_old_ai(harness, page, reply, result):
    at_finish(harness)
    harness.model_reply = [reply]

    assert save_gateway_key(page.url, GATEWAY_KEY).json() == {"result": result}

    assert backend(harness) == "claude" and harness.secrets == {}


def test_on_finish_a_gateway_household_can_enter_a_new_gateway_key(harness, page):
    at_finish_with(harness, "gateway")
    harness.secrets["gateway-key"] = "sk-bf-old-key-turned-off"
    harness.model_reply = "OK"

    assert save_gateway_key(page.url, GATEWAY_KEY).json()["result"] == "saved"

    assert backend(harness) == "gateway" and harness.secrets == {"gateway-key": GATEWAY_KEY}


@pytest.mark.macos
def test_on_finish_a_claude_token_switches_the_household_to_claude(harness, page, claude):
    at_finish_with(harness, "gateway")
    claude.install()

    r = call(page.url, "api/claude/token", method="POST", body={"token": CLAUDE_TOKEN})

    assert r.status == 200 and r.json()["result"] == "saved"
    assert backend(harness) == "claude"
    assert harness.secrets == {"claude-oauth-token": CLAUDE_TOKEN}
    # So that changes after setup work in Claude Code's chat, as when Welcome picks Claude.
    assert chat_installed(page.url, "claude") == "installed"


@pytest.mark.macos
def test_on_finish_a_claude_token_that_does_not_work_keeps_the_old_ai(harness, page, claude):
    at_finish_with(harness, "gateway")
    harness.model_error = "Your organization has disabled Claude Code"
    claude.install()

    r = call(page.url, "api/claude/token", method="POST", body={"token": CLAUDE_TOKEN})

    assert r.json() == {"result": "test-call-failed"}
    assert backend(harness) == "gateway" and harness.secrets == {}


@pytest.mark.macos
def test_on_finish_a_signed_in_codex_that_answers_a_test_call_switches_the_household(harness, page,
                                                                                    codex):
    at_finish(harness)
    harness.model_reply = {"ok": True}

    out = codex_check(page.url)

    assert out["result"] == "signed-in"
    assert backend(harness) == "codex"
    [test_call] = harness.model_calls
    assert Path(test_call.argv[0]).name == "codex"


@pytest.mark.macos
@pytest.mark.parametrize("reply", [FailedCall("You've hit your usage limit"), {"ok": False}])
def test_on_finish_a_codex_that_fails_its_test_call_keeps_the_old_ai(harness, page, codex, reply):
    at_finish_with(harness, "gateway")
    harness.model_reply = [reply]

    assert codex_check(page.url) == {"result": "test-call-failed"}

    assert backend(harness) == "gateway"


@pytest.mark.macos
def test_before_finish_codex_s_sign_in_makes_no_test_call(harness, page, codex):
    at_the_ai_step(harness, page.url, "codex")

    assert codex_check(page.url)["result"] == "signed-in"

    assert not harness.model_calls


def test_finish_offers_the_three_ais_and_their_sign_ins():
    page_html = (PAGE_DIR / "index.html").read_text()
    panel = inside(page_html, "finish-ai")

    for name in setup_server.context.AIS:
        assert f'name="finish-ai" value="{name}"' in panel, name
        assert f'data-text="welcome.ai.{name}"' in panel, name
    # The sign-ins are Connect's own, with the gateway's privacy text, moved in when it's opened.
    assert 'id="finish-ai-sign-in"' in panel
    assert "finish-ai-sign-in" in (PAGE_DIR / "page.js").read_text()


def test_the_ai_check_s_failure_texts_on_finish_point_to_changing_the_ai():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        for key in ("finish.ai.title", "finish.ai.open", "finish.ai.continue", "finish.ai.note",
                    "finish.ai.saved", *(f"finish.ai.current.{a}" for a in setup_server.context.AIS)):
            assert table.get(key, "").strip(), (language, key)
        for key in ("finish.fail.ai", "finish.fail.gateway"):
            assert table["finish.ai.open"] in table[key], (language, key)
