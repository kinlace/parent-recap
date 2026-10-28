"""`python -m family_brief.command_path install <folder>`, which the install runs once the program
is in place, puts `parent-recap` in a folder of its own beside the program and that folder on the
user's PATH, so a new Terminal or PowerShell window finds it (#560). Called through its `main()`,
with the user's PATH as the harness's fake (fake_system.py) and real files in a temporary HOME."""
from __future__ import annotations

from pathlib import Path

import pytest

from family_brief import command_path, install_record

from fake_system import WINDOWS


def installed_program(target: Path, profile) -> Path:
    """The venv's `parent-recap` the install made."""
    command = target / "app" / ".venv" / profile.command.with_name(name(profile))
    command.parent.mkdir(parents=True)
    command.write_text("the venv's parent-recap\n")
    (command.parent / ("python.exe" if profile is WINDOWS else "python")).write_text("")
    return command


def name(profile) -> str:
    """The command's file name: `parent-recap`, or `parent-recap.exe` on Windows."""
    return "parent-recap" + profile.command.suffix


@pytest.fixture
def target(harness) -> Path:
    return harness.home / "ParentRecap"


def test_puts_only_parent_recap_in_its_own_folder_on_the_path(harness, target):
    program = installed_program(target, harness.system)

    assert command_path.main(["install", str(target)]) == 0

    folder = target / "bin"
    assert [p.name for p in folder.iterdir()] == [name(harness.system)]  # no python beside it
    assert (folder / name(harness.system)).read_bytes() == program.read_bytes()
    assert harness.user_path.folders == [folder]


def test_installing_again_adds_it_once(harness, target):
    installed_program(target, harness.system)

    for _ in range(3):
        assert command_path.main(["install", str(target)]) == 0

    assert harness.user_path.folders == [target / "bin"]
    assert [p.name for p in (target / "bin").iterdir()] == [name(harness.system)]


def test_records_the_folder_and_the_path_entry_for_uninstall(harness, target):
    installed_program(target, harness.system)

    assert command_path.main(["install", str(target)]) == 0

    assert install_record.entries("command") == [str(target / "bin")]
    assert install_record.entries("user-path") == [str(target / "bin")]


def test_on_windows_it_is_a_copy_of_the_venv_s_launcher(harness, target):
    harness.system = WINDOWS
    program = installed_program(target, WINDOWS)

    assert command_path.main(["install", str(target)]) == 0

    copy = target / "bin" / "parent-recap.exe"
    assert [p.name for p in (target / "bin").iterdir()] == ["parent-recap.exe"]
    assert not copy.is_symlink() and copy.read_bytes() == program.read_bytes()
    # An update brings the new launcher.
    program.write_text("new launcher\n")
    assert command_path.main(["install", str(target)]) == 0
    assert copy.read_text() == "new launcher\n"


def test_on_windows_a_copy_in_use_is_kept_and_the_install_goes_on(harness, target, monkeypatch):
    # Windows won't replace a program while it runs, as when the family's update runs from it.
    harness.system = WINDOWS
    installed_program(target, WINDOWS)
    assert command_path.main(["install", str(target)]) == 0

    def in_use(*_a, **_k):
        raise PermissionError(13, "The process cannot access the file")
    monkeypatch.setattr(command_path.shutil, "copyfile", in_use)

    assert command_path.main(["install", str(target)]) == 0
    assert (target / "bin" / "parent-recap.exe").exists()
    assert harness.user_path.folders == [target / "bin"]


def test_without_the_program_it_changes_nothing(harness, target, capsys):
    assert command_path.main(["install", str(target)]) == 1

    assert not (target / "bin").exists() and harness.user_path.folders == []
    assert not install_record.exists()
