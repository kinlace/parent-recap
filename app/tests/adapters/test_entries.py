"""The macOS adapter of the entries (ADR 0042): two app bundles in `~/Applications`, each run by a
shell script. Real files in a temporary HOME; the scripts run for real with a stand-in for the
installed `parent-recap`. Opening a bundle with Launch Services, as Spotlight does, is left to a
person on a real Mac: `open` can't run where the tests run."""
from __future__ import annotations

import os
import plistlib
import subprocess
import time
from pathlib import Path

import pytest

from family_brief.system import entries_macos as entries
from family_brief.system.entries import CHECK, NAMES, SETTINGS


@pytest.fixture
def target(home: Path) -> Path:
    """An install folder whose `parent-recap` notes what it was asked to do and says it."""
    target = home / "Parent Recap"  # a space, as a custom install folder may have
    command = target / "app" / ".venv" / "bin" / "parent-recap"
    command.parent.mkdir(parents=True)
    command.write_text(f'#!/bin/sh\necho "$@" >> {str(home / "asked")!r}\necho "ran $*"\n')
    command.chmod(0o755)
    return target


def bundle(name: str) -> Path:
    return entries.path(name)


def test_each_is_an_app_bundle_in_the_home_s_applications_folder(home, target):
    for name in NAMES:
        entries.place(name, target)

    assert sorted(p.name for p in (home / "Applications").iterdir()) == \
        ["Parent Recap Check.app", "Parent Recap Settings.app"]
    for name in NAMES:
        with open(bundle(name) / "Contents" / "Info.plist", "rb") as f:
            info = plistlib.load(f)
        assert info["CFBundleName"] == info["CFBundleDisplayName"] == name  # what Spotlight finds
        assert info["CFBundlePackageType"] == "APPL"
        assert info["LSUIElement"] is True  # no Dock icon for a script with no window
        script = bundle(name) / "Contents" / "MacOS" / info["CFBundleExecutable"]
        assert script.read_text().startswith("#!/bin/sh\n") and os.access(script, os.X_OK)
        assert entries.placed(name)
    assert entries.where() == "~/Applications"


@pytest.mark.parametrize("name", NAMES)
def test_neither_tells_another_app_what_to_do(target, name):
    # Telling Terminal what to do with AppleScript makes macOS ask the family to allow it.
    entries.place(name, target)

    text = "".join(p.read_text() for p in bundle(name).rglob("*") if p.is_file())
    assert "osascript" not in text and "tell application" not in text


@pytest.mark.posix
def test_settings_starts_the_setup_page_and_ends_at_once(home, target):
    # An app still running is only brought forward when it's opened again, so the script ends.
    entries.place(SETTINGS, target)
    script = bundle(SETTINGS) / "Contents" / "MacOS" / "settings"

    subprocess.run([str(script)], check=True, timeout=10, stdin=subprocess.DEVNULL)

    for _ in range(50):
        if (home / "asked").exists():
            break
        time.sleep(0.1)
    assert (home / "asked").read_text() == "setup page\n"


@pytest.mark.posix
def test_check_has_terminal_open_a_command_script_that_runs_doctor_and_waits(home, target):
    entries.place(CHECK, target)
    script = bundle(CHECK) / "Contents" / "MacOS" / "check"
    command = bundle(CHECK) / "Contents" / "Resources" / "Parent Recap Check.command"

    # As opening a .command file in Finder does: no permission to control Terminal.
    assert 'exec /usr/bin/open -a Terminal "$(dirname "$0")/../Resources/Parent Recap Check.command"' \
        in script.read_text()
    assert os.access(command, os.X_OK)
    # What Terminal's window shows, kept open until Return so the family can take a screenshot.
    shown = subprocess.run([str(command)], input="\n", capture_output=True, text=True, timeout=10,
                           check=True).stdout
    assert shown.startswith("ran doctor\n")
    assert "Press Return to close it." in shown
    assert (home / "asked").read_text() == "doctor\n"


@pytest.mark.macos
@pytest.mark.parametrize("name", NAMES)
def test_macos_reads_the_bundle_and_it_has_no_quarantine_flag(target, name):
    # Gatekeeper stops only what carries the flag, which a download gets and a file made here doesn't.
    entries.place(name, target)

    subprocess.run(["plutil", "-lint", str(bundle(name) / "Contents" / "Info.plist")], check=True,
                   capture_output=True)
    flag = subprocess.run(["xattr", "-p", "com.apple.quarantine", str(bundle(name))],
                          capture_output=True)
    assert flag.returncode != 0


def test_an_update_makes_it_again_whole_for_the_install_it_updates(home, target):
    older = home / "Older"
    entries.place(CHECK, older)
    leftover = bundle(CHECK) / "Contents" / "Resources" / "from an older version"
    leftover.write_text("")

    entries.place(CHECK, target)

    assert not leftover.exists()
    command = (bundle(CHECK) / "Contents" / "Resources" / "Parent Recap Check.command").read_text()
    assert str(target) in command and str(older) not in command


def test_one_with_its_name_the_install_didn_t_make_is_left_as_it_is(home, target):
    other = bundle(SETTINGS) / "Contents"
    other.mkdir(parents=True)
    with open(other / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleIdentifier": "com.example.settings"}, f)

    with pytest.raises(OSError, match="didn't make it"):
        entries.place(SETTINGS, target)

    assert not entries.placed(SETTINGS)
    assert sorted(p.name for p in other.iterdir()) == ["Info.plist"]


def test_remove_takes_out_only_the_bundle(home, target):
    (home / "Applications").mkdir()
    (home / "Applications" / "Family's own.app").mkdir()
    entries.place(SETTINGS, target)

    entries.remove(SETTINGS)

    assert not bundle(SETTINGS).exists() and not entries.placed(SETTINGS)
    assert [p.name for p in (home / "Applications").iterdir()] == ["Family's own.app"]
