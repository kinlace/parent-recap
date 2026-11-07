"""The Windows adapter of the entries (ADR 0042): two shortcuts in a `Parent Recap` folder of the
user's Start menu. What each runs is checked on any system; the shortcuts are made for real on
Windows, in a temporary %APPDATA%, and read back through the Shell that made them."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from family_brief.system import entries_windows as entries
from family_brief.system.entries import CHECK, NAMES, SETTINGS

TARGET = Path(r"C:\Users\O’Brien\AppData\Local\ParentRecap")


def test_settings_runs_the_page_on_the_venv_s_pythonw_which_leaves_no_console_window():
    made = entries.shortcut(SETTINGS, TARGET)

    assert made["target"] == str(TARGET / "app" / ".venv" / "Scripts" / "pythonw.exe")
    assert made["arguments"] == "-m family_brief setup page"


def test_check_runs_doctor_in_a_powershell_window_that_stays_open(monkeypatch):
    monkeypatch.setenv("SystemRoot", r"C:\Windows")

    made = entries.shortcut(CHECK, TARGET)

    assert made["target"] == str(Path(r"C:\Windows") / "System32" / "WindowsPowerShell" / "v1.0" /
                                 "powershell.exe")
    assert made["arguments"].startswith("-NoExit ")
    # The venv's Python, by a path whose curly quote PowerShell would take for the string's end.
    python = str(TARGET / "app" / ".venv" / "Scripts" / "python.exe").replace("’", "’’")
    assert made["arguments"].endswith(f"-Command \"& '{python}' -m family_brief doctor\"")


def test_they_go_in_a_parent_recap_folder_of_the_user_s_start_menu(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))

    assert entries.path(SETTINGS) == (tmp_path / "Microsoft" / "Windows" / "Start Menu" /
                                      "Programs" / "Parent Recap" / "Parent Recap Settings.lnk")
    assert entries.where() == "the Start menu"


@pytest.fixture
def appdata(home, monkeypatch) -> Path:
    appdata = home / "AppData" / "Roaming"
    monkeypatch.setenv("APPDATA", str(appdata))
    return appdata


def read_back(link: Path) -> dict[str, str]:
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LINK); "
              "@{target = $s.TargetPath; arguments = $s.Arguments} | ConvertTo-Json")
    out = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                         env={**os.environ, "LINK": str(link)}, capture_output=True, text=True,
                         check=True).stdout
    return json.loads(out)


@pytest.mark.windows
def test_on_windows_it_makes_both_shortcuts_and_removes_them_with_their_folder(appdata, home):
    target = home / "AppData" / "Local" / "ParentRecap"

    for name in NAMES:
        entries.place(name, target)
        entries.place(name, target)  # an update makes it again

    folder = entries.folder()
    assert sorted(p.name for p in folder.iterdir()) == \
        ["Parent Recap Check.lnk", "Parent Recap Settings.lnk"]
    for name in NAMES:
        assert entries.placed(name)
        made = entries.shortcut(name, target)
        assert read_back(entries.path(name)) == {"target": made["target"],
                                                 "arguments": made["arguments"]}

    for name in NAMES:
        entries.remove(name)
    assert not folder.exists()


@pytest.mark.windows
def test_on_windows_a_file_of_the_family_s_own_beside_them_stays(appdata, home):
    entries.place(SETTINGS, home / "ParentRecap")
    own = entries.folder() / "notes.txt"
    own.write_text("")

    entries.remove(SETTINGS)

    assert not entries.placed(SETTINGS)
    assert [p.name for p in entries.folder().iterdir()] == ["notes.txt"]
