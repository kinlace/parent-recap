"""The systems' profiles (ADR 0020): each one says everything a caller asks of it, and only the
system package checks which system the program runs on, so a new system adds its profile and
adapters there and changes no caller."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import family_brief
from family_brief.setup_server.context import AIS, CHAT_SKILLS, LANGUAGES
from family_brief.system import profile_macos, profile_windows

PROFILES = [profile_macos.PROFILE, profile_windows.PROFILE]
PACKAGE = Path(family_brief.__file__).parent


@pytest.mark.parametrize("system", PROFILES, ids=lambda p: p.system)
def test_a_profile_has_what_the_setup_page_and_its_callers_ask_of_it(system):
    assert set(system.closed_lines) == set(LANGUAGES)
    assert system.terminal in system.closed_lines["en"]
    assert set(system.ais) <= set(AIS) and len(set(system.ais)) == len(system.ais)
    assert system.chats <= set(CHAT_SKILLS)
    assert system.page_installs <= set(system.install_lines) and "claude" in system.install_lines
    assert "{folder}" in system.remove_folder
    assert system.program_endings


def test_the_profiles_are_told_apart_by_their_system():
    assert [p.system for p in PROFILES] == ["macos", "windows"]  # the page's words for them


def test_only_the_system_package_checks_which_system_it_runs_on():
    checks = re.compile(r"\bsys\.platform\b|\bplatform\.system\(|\bos\.name\b")
    found = [f"{path.relative_to(PACKAGE)}:{n}" for path in sorted(PACKAGE.rglob("*.py"))
             if path.relative_to(PACKAGE).parts[0] != "system"
             for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
             if checks.search(line)]
    assert found == []
