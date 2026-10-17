"""The Windows adapter of `family_brief.system.desktop`: the default browser and Notepad, PowerShell
windows and the Windows display languages (ADR 0020). Callers' tests use fake_system.FakeDesktop
instead.

Opening is faked: `os.startfile` and starting a program record what they're given. A PowerShell
window is its command, run by Windows PowerShell without a window of its own and without -NoExit,
which only keeps the window open for the family to read."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import Any

import pytest

from conftest import REAL_RUN

from family_brief import setup_steps
from family_brief.system import desktop, desktop_windows

pytestmark = pytest.mark.windows

REAL_POPEN = subprocess.Popen  # before the fixture fakes it; subprocess.run starts programs with it
TOKEN = "sk-ant-oat01-Abc_def-123456789"


class FakeWindows:
    def __init__(self) -> None:
        self.started: list[str] = []  # each os.startfile's target
        self.programs: list[tuple[list[str], dict[str, Any]]] = []  # each program started
        self.opens = True

    def startfile(self, target: str, *_a: Any) -> None:
        if not self.opens:
            raise OSError(2, "The system cannot find the file specified")
        self.started.append(target)

    def popen(self, argv: list[str], **kwargs: Any) -> Any:
        if not self.opens:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        self.programs.append((list(argv), kwargs))
        return None


@pytest.fixture
def windows(monkeypatch) -> FakeWindows:
    fake = FakeWindows()
    monkeypatch.setattr(desktop, "_adapter", desktop_windows)  # in place of the harness's fake
    monkeypatch.setattr(os, "startfile", fake.startfile, raising=False)
    monkeypatch.setattr(subprocess, "Popen", fake.popen)
    return fake


def test_a_page_opens_in_the_browser_and_a_file_in_notepad(harness, windows):
    assert desktop.open_url("https://example.com/")
    assert desktop.open_file(harness.home / "list.txt")

    assert windows.started == ["https://example.com/"]
    [(argv, _)] = windows.programs
    assert argv == ["notepad.exe", str(harness.home / "list.txt")]


def test_nothing_opened_is_said_so(harness, windows):
    windows.opens = False

    assert not desktop.open_url("https://example.com/")
    assert not desktop.open_file(harness.home / "list.txt")
    assert not desktop.open_terminal(["cmd"], {}, "Parent Recap test")


def test_a_powershell_window_runs_its_command_in_the_home_folder_with_its_title(harness, windows,
                                                                                  monkeypatch):
    ran = harness.home / "ran"
    title = "Parent Recap's test"

    assert desktop.open_terminal(
        [sys.executable, "-c", "import os, sys; open(sys.argv[1], 'w').write("
                               "os.getcwd() + '|' + os.environ['GREETING'] + '|' + sys.argv[2])",
         str(ran), "it's O’Brien’s $HOME `n"],
        {"GREETING": "hello 'there'"}, title)

    [(argv, kwargs)] = windows.programs
    assert argv[0] == "powershell.exe" and "-NoExit" in argv  # the family reads what it says
    assert kwargs["creationflags"] & subprocess.CREATE_NEW_CONSOLE
    assert kwargs["cwd"] == str(harness.home)
    # Its command, as Windows PowerShell runs it once the window is open.
    monkeypatch.setattr(subprocess, "Popen", REAL_POPEN)
    shown = REAL_RUN([a for a in argv if a != "-NoExit"], env=kwargs["env"], cwd=kwargs["cwd"],
                     capture_output=True, text=True, timeout=60)
    assert shown.returncode == 0, shown.stderr
    assert ran.read_text() == f"{harness.home}|hello 'there'|it's O’Brien’s $HOME `n"
    # The title, which PowerShell without a console of its own can't show, is set first.
    assert "$Host.UI.RawUI.WindowTitle = 'Parent Recap''s test'" in desktop_windows.window_script(
        ["cmd"], title)


def test_the_claude_sign_in_window_runs_setup_token_in_windows_powershell(harness, windows,
                                                                           monkeypatch):
    claude = harness.home / "claude.cmd"
    claude.write_text(f'@echo off\r\nif "%~1"=="setup-token" echo {TOKEN}\r\n')

    assert desktop.open_terminal(
        setup_steps.setup_token_window(str(claude), "the field on the setup page"), {},
        setup_steps.CLAUDE_WINDOW)

    [(argv, kwargs)] = windows.programs
    monkeypatch.setattr(subprocess, "Popen", REAL_POPEN)
    # No family to press Enter: the window clears itself as it would once they had.
    shown = REAL_RUN([a for a in argv if a != "-NoExit"], env=kwargs["env"], cwd=kwargs["cwd"],
                     stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    assert shown.returncode == 0, shown.stderr
    before, after = shown.stdout.split("\x1b[H\x1b[2J\x1b[3J")
    assert TOKEN in before and "paste it in the field on the setup page" in before
    assert after.strip() == "You can close this window."


def test_a_word_windows_powershell_would_change_is_refused():
    # Windows PowerShell 5.1 drops a double quote inside a word it passes to a program (#376).
    with pytest.raises(ValueError):
        desktop_windows.window_script(["python.exe", "-c", 'print("hello")'], "Parent Recap test")


def test_forgetting_a_window_is_harmless(windows):
    desktop.forget_terminal("Parent Recap test")  # nothing is written for a window


def test_the_windows_display_languages_are_read():
    languages = desktop_windows.preferred_languages()

    assert languages and all(re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]+)*", t) for t in languages)


def test_the_null_device_is_no_terminal_though_isatty_says_it_is():
    # An agent's stdin on Windows is NUL, a character device: isatty() alone took it for a
    # Terminal and uninstall waited for an answer (#372).
    probe = ("import sys; from family_brief.system import desktop_windows; "
             "print(sys.stdin.isatty(), desktop_windows.typing_at_terminal())")
    out = REAL_RUN([sys.executable, "-c", probe], stdin=subprocess.DEVNULL, capture_output=True,
                   text=True, encoding="utf-8", check=True).stdout.split()

    assert out == ["True", "False"]
