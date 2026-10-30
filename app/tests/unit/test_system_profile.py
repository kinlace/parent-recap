"""The systems' profiles (ADR 0020): each one says everything a caller asks of it, and only the
system package checks which system the program runs on, so a new system adds its profile and
adapters there and changes no caller."""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest

import family_brief
from family_brief.setup_server.context import AIS, CHAT_SKILLS, LANGUAGES
from family_brief import system
from family_brief.system import profile_linux, profile_macos, profile_windows

PROFILES = [profile_macos.PROFILE, profile_windows.PROFILE, profile_linux.PROFILE]
CONCERNS = ["profile", "secrets", "dialog", "desktop", "user_path", "jobs", "wake"]
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
    # The page's words for them.
    assert [p.system for p in PROFILES] == ["macos", "windows", "linux"]


def test_only_the_system_package_checks_which_system_it_runs_on():
    checks = re.compile(r"\bsys\.platform\b|\bplatform\.system\(|\bos\.name\b")
    found = [f"{path.relative_to(PACKAGE)}:{n}" for path in sorted(PACKAGE.rglob("*.py"))
             if path.relative_to(PACKAGE).parts[0] != "system"
             for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
             if checks.search(line)]
    assert found == []


def test_on_linux_the_gateway_is_the_only_ai_and_nothing_is_installed_or_chatted_in():
    linux = profile_linux.PROFILE

    assert linux.ais == ("gateway",)
    assert linux.page_installs == frozenset() and linux.chats == frozenset()
    assert linux.install_folders(Path("/home/h1")) == [Path("/home/h1/ParentRecap")]
    mac = profile_macos.PROFILE
    assert (linux.command, linux.node) == (mac.command, mac.node)


@pytest.mark.parametrize("platform, expected", [("darwin", "macos"), ("win32", "windows"),
                                              ("linux", "linux")])
@pytest.mark.parametrize("concern", CONCERNS)
def test_each_interface_s_adapter_is_the_one_of_the_platform_it_runs_on(monkeypatch, platform,
                                                                        expected, concern):
    monkeypatch.setattr(sys, "platform", platform)
    # Imported for this test's platform, not the one it runs on: the interface's module itself
    # stays as the program loaded it.
    monkeypatch.setattr(importlib, "import_module", lambda name: name)

    assert system.adapter(concern) == f"family_brief.system.{concern}_{expected}"


def test_a_platform_the_program_doesnt_run_on_is_said_so(monkeypatch):
    monkeypatch.setattr(sys, "platform", "freebsd14")

    with pytest.raises(RuntimeError, match="macOS, Windows and Linux"):
        system.adapter("secrets")


@pytest.mark.parametrize("concern", CONCERNS)
@pytest.mark.parametrize("system_name", ["macos", "windows", "linux"])
def test_every_system_has_an_adapter_for_every_interface(concern, system_name):
    assert (PACKAGE / "system" / f"{concern}_{system_name}.py").is_file()
