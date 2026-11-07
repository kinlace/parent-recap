"""`python -m family_brief.program_entries install <folder>`, which the install runs once the program
is in place, puts Parent Recap Settings and Parent Recap Check where the system lists programs
(ADR 0042). Called through its `main()`, with the entries as the harness's fake (fake_system.py).
What each system's entries are is in test_entries.py and test_entries_windows.py."""
from __future__ import annotations

from pathlib import Path

import pytest

from family_brief import program_entries
from family_brief.system import entries

from fake_system import LINUX, MACOS, WINDOWS


@pytest.fixture
def target(harness) -> Path:
    return harness.home / "ParentRecap"


@pytest.mark.parametrize("on", [MACOS, WINDOWS], ids=["macos", "windows"])
def test_puts_settings_and_check_for_the_install(harness, target, on):
    harness.system = on

    assert program_entries.main(["install", str(target)]) == 0

    assert harness.entries.made == {entries.SETTINGS: target, entries.CHECK: target}


def test_an_update_makes_them_again_for_the_install_it_updates(harness, target):
    # A family that installed before they existed gets them with its next update.
    elsewhere = harness.home / "Old"
    harness.entries.made = {entries.SETTINGS: elsewhere}

    for _ in range(2):
        assert program_entries.main(["install", str(target)]) == 0

    assert harness.entries.made == {entries.SETTINGS: target, entries.CHECK: target}


def test_a_hosted_household_s_server_gets_none(harness, target):
    harness.system = LINUX

    assert program_entries.main(["install", str(target)]) == 0

    assert harness.entries.made == {}


def test_one_with_their_name_the_install_didn_t_make_stays_and_the_install_goes_on(harness, target,
                                                                                   capsys):
    harness.entries.foreign = {entries.SETTINGS}

    assert program_entries.main(["install", str(target)]) == 0

    assert harness.entries.made == {entries.CHECK: target}
    assert f"{entries.SETTINGS} wasn't put in the programs list" in capsys.readouterr().err


def test_one_the_system_refuses_never_fails_the_install(harness, target, capsys):
    harness.entries.refuses = PermissionError(13, "Permission denied")

    assert program_entries.main(["install", str(target)]) == 0

    assert harness.entries.made == {}
    assert "Permission denied" in capsys.readouterr().err


def test_says_how_to_call_it():
    assert program_entries.main([]) == 2
