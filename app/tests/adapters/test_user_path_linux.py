"""The Linux adapter of the user's PATH: `parent-recap` linked into `~/.local/bin`, which Ubuntu's
own `~/.profile` puts on the PATH of every login shell, and taken out again leaving the rest of
that folder as it was. Real files in a temporary HOME."""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import program
from family_brief.system import user_path_linux as user_path

pytestmark = pytest.mark.posix


@pytest.fixture
def local_bin(home: Path) -> Path:
    return home / ".local" / "bin"


@pytest.fixture
def folder(home: Path) -> Path:
    """The install's own command folder, with `parent-recap` in it."""
    folder = home / "ParentRecap" / "bin"
    program(folder, "parent-recap")
    return folder


def test_links_the_folder_s_command_into_the_local_bin_however_often_it_runs(local_bin, folder):
    for _ in range(3):
        user_path.add(folder)

    assert [p.name for p in local_bin.iterdir()] == ["parent-recap"]
    assert (local_bin / "parent-recap").resolve() == (folder / "parent-recap").resolve()
    assert user_path.has(folder)
    assert user_path.where() == "~/.local/bin"


def test_a_program_of_the_user_s_own_by_that_name_is_left_alone(local_bin, folder):
    own = program(local_bin, "parent-recap", "#!/bin/sh\necho mine\n")

    user_path.add(folder)

    assert not own.is_symlink() and "mine" in own.read_text()
    assert not user_path.has(folder)


def test_removing_takes_out_only_its_own_link(local_bin, folder):
    user_path.add(folder)
    program(local_bin, "other-tool")

    user_path.remove(folder)

    assert [p.name for p in local_bin.iterdir()] == ["other-tool"]
    assert not user_path.has(folder)


def test_removing_another_folder_s_link_changes_nothing(local_bin, folder, home):
    user_path.add(folder)

    user_path.remove(home / "elsewhere" / "bin")

    assert user_path.has(folder)


def test_removing_when_nothing_was_added_is_fine(local_bin, folder):
    user_path.remove(folder)

    assert not local_bin.exists()
