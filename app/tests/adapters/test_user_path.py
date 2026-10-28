"""The macOS adapter of the user's PATH: one marked line in `~/.zprofile`, added once, and taken out
again with the rest of the file as the family had it (#560). Real files in a temporary HOME."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from family_brief.system import user_path_macos as user_path


@pytest.fixture
def zprofile(home: Path) -> Path:
    return home / ".zprofile"


@pytest.fixture
def folder(home: Path) -> Path:
    return home / "Parent Recap" / "bin"  # a space, as a custom install folder may have


def test_adds_one_marked_line_however_often_it_runs(zprofile, folder):
    zprofile.write_text('eval "$(/opt/homebrew/bin/brew shellenv)"')  # no newline at its end

    for _ in range(3):
        user_path.add(folder)

    lines = zprofile.read_text().splitlines()
    assert lines[0] == 'eval "$(/opt/homebrew/bin/brew shellenv)"'
    assert len(lines) == 2 and "parent-recap uninstall removes this line" in lines[1]
    assert user_path.has(folder)


def test_makes_the_file_when_there_is_none(zprofile, folder):
    user_path.add(folder)

    assert len(zprofile.read_text().splitlines()) == 1 and user_path.has(folder)


@pytest.mark.macos
def test_a_new_login_shell_finds_the_folder_first(zprofile, folder, home):
    user_path.add(folder)

    shell = subprocess.run(["/bin/zsh", "-l", "-c", 'print -r -- "$PATH"'], capture_output=True,
                           text=True, env={"HOME": str(home), "PATH": "/usr/bin:/bin"})

    assert shell.stdout.split(":")[0] == str(folder)


def test_removing_leaves_the_rest_of_the_file_as_it_was(zprofile, folder):
    mine = "# mine\nexport EDITOR=vim\n"
    zprofile.write_text(mine)
    user_path.add(folder)
    with zprofile.open("a") as f:
        f.write("alias ll='ls -l'\n")

    user_path.remove(folder)

    assert zprofile.read_text() == mine + "alias ll='ls -l'\n"
    assert not user_path.has(folder)


def test_removing_the_only_line_removes_the_file_add_made(zprofile, folder):
    user_path.add(folder)

    user_path.remove(folder)

    assert not zprofile.exists()


def test_removing_another_folder_s_line_changes_nothing(zprofile, folder, home):
    user_path.add(folder)
    before = zprofile.read_text()

    user_path.remove(home / "elsewhere" / "bin")

    assert zprofile.read_text() == before
