"""The macOS adapter of `family_brief.system.desktop`: `open`, Terminal and `defaults` (ADR 0020).
Callers' tests use fake_system.FakeDesktop instead.

The outside edges are faked: `open`, which records what it opens, and `defaults`, which lists the
Mac's languages. A terminal window is its script, run by `/bin/sh` the way Terminal runs it."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from family_brief.system import desktop, desktop_macos

# The macOS adapter for opening pages, files and Terminal windows.
pytestmark = pytest.mark.macos


class FakeMac:
    def __init__(self) -> None:
        self.opened: list[list[str]] = []  # each `open`'s arguments
        self.opens: bool | None = True  # None: there is no `open`
        self.languages = '(\n    "fi-FI",\n    "zh-Hans-FI",\n    "en-FI"\n)\n'

    def run(self, cmd: list[str], *_a: Any, **_k: Any) -> subprocess.CompletedProcess:
        if cmd[:4] == ["defaults", "read", "-g", "AppleLanguages"]:
            return subprocess.CompletedProcess(cmd, 0, self.languages, "")
        assert cmd[0] == "open", cmd
        self.opened.append(cmd[1:])
        if self.opens is None:
            raise FileNotFoundError(2, "No such file or directory", "open")
        return subprocess.CompletedProcess(cmd, 0 if self.opens else 1, "", "")


@pytest.fixture
def mac(monkeypatch) -> FakeMac:
    fake = FakeMac()
    monkeypatch.setattr(desktop, "_adapter", desktop_macos)  # in place of the harness's fake
    monkeypatch.setattr(subprocess, "run", fake.run)
    return fake


def test_a_page_opens_in_the_browser_and_a_file_in_textedit(harness, mac):
    assert desktop.open_url("https://example.com/")
    assert desktop.open_file(harness.home / "list.txt")

    assert mac.opened == [["https://example.com/"], ["-e", str(harness.home / "list.txt")]]


@pytest.mark.parametrize("opens", [False, None], ids=["exits-1", "missing"])
def test_nothing_opened_is_said_so(harness, mac, opens):
    mac.opens = opens

    assert not desktop.open_url("https://example.com/")
    assert not desktop.open_terminal(["true"], {}, "Parent Recap test")
    assert not Path(mac.opened[-1][-1]).parent.exists()  # its script went with it


def test_a_terminal_window_runs_its_command_in_the_home_folder_and_leaves_nothing(harness, mac):
    ran = harness.home / "ran"

    assert desktop.open_terminal(
        [sys.executable, "-c", "import os, sys; open(sys.argv[1], 'w').write("
                               "os.getcwd() + '|' + os.environ['GREETING'])", str(ran)],
        {"GREETING": "hello 'there'"}, "Parent Recap test")

    assert mac.opened[-1][:2] == ["-a", "Terminal"]
    script = Path(mac.opened[-1][-1])
    assert script.name == "Parent Recap test.command" and os.access(script, os.X_OK)
    subprocess.Popen(["/bin/sh", str(script)], env={**os.environ, "HOME": str(harness.home)},
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).wait(10)
    assert ran.read_text() == f"{harness.home}|hello 'there'"
    assert not script.parent.exists()  # the script and its folder go once Terminal has run it


def test_a_window_that_never_ran_its_script_leaves_nothing_once_forgotten(harness, mac):
    assert desktop.open_terminal(["true"], {}, "Parent Recap test")
    script = Path(mac.opened[-1][-1])

    desktop.forget_terminal("Parent Recap test")

    assert not script.parent.exists()


def test_the_macs_languages_are_read_first_first(mac):
    assert desktop.preferred_languages() == ["fi-FI", "zh-Hans-FI", "en-FI"]

    mac.languages = ""

    assert desktop.preferred_languages() == []
