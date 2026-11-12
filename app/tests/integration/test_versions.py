"""The installed program's versions (ADR 0047): `switch`, which install.sh runs once a release's
install finished, keeps the version switched from, one version deep, with the runtimes it runs
on, and takes over an install from before versions; `parent-recap switch-back` points `app` at
the previous version again and has it put its own pieces back. On real folders in a temporary
HOME, with the subprocesses and the always-running job as the harness's fakes. install.sh itself
is run in test_install.py."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from family_brief import versions

from fake_system import LINUX, MACOS

# install.sh's, on a Mac or a Linux server: install.ps1 keeps Windows' program in place.
pytestmark = pytest.mark.posix


@pytest.fixture
def folder(harness) -> Path:
    """Parent Recap's folder, where install.sh puts everything."""
    f = harness.home / "ParentRecap"
    (f / "runtime").mkdir(parents=True)
    return f


def built(folder: Path, name: str, runs_on: tuple[str, ...] = ("python-1", "node-v1")) -> Path:
    """A version's folder as install.sh leaves it before the switch."""
    v = folder / "versions" / name
    v.mkdir(parents=True)
    (v / "VERSION").write_text(name.split(".")[0].replace("_", ".") + "\n")
    (v / "pyproject.toml").write_text('[project]\nname = "family-brief"\n')
    (v / "RUNTIMES").write_text("".join(f"{r}\n" for r in runs_on))
    for r in runs_on:
        (folder / "runtime" / r).mkdir(exist_ok=True)
    return v


def install(folder: Path, name: str, runs_on: tuple[str, ...] = ("python-1", "node-v1")) -> Path:
    v = built(folder, name, runs_on)
    versions.switch(folder, v)
    return v


def test_the_first_install_points_app_at_its_version_with_none_before(folder):
    v = install(folder, "1_0.aaa")

    assert (folder / "app").is_symlink() and (folder / "app").resolve() == v.resolve()
    assert versions.current(folder) == v and versions.previous(folder) is None
    assert (folder / "app" / "VERSION").read_text() == "1.0\n"


def test_an_install_over_an_older_version_keeps_it_and_the_runtimes_it_runs_on(folder):
    old = install(folder, "1_0.aaa", ("python-1", "node-v1"))

    new = install(folder, "1_1.bbb", ("python-2", "node-v1"))

    assert versions.current(folder) == new and versions.previous(folder) == old
    assert sorted(p.name for p in (folder / "runtime").iterdir()) == ["node-v1", "python-1",
                                                                      "python-2"]


def test_a_third_install_removes_the_oldest_and_what_only_it_ran_on(folder):
    install(folder, "1_0.aaa", ("python-1", "node-v1"))
    middle = install(folder, "1_1.bbb", ("python-2", "node-v1"))

    new = install(folder, "1_2.ccc", ("python-2", "node-v2"))

    assert sorted(p.name for p in (folder / "versions").iterdir()) == ["1_1.bbb", "1_2.ccc",
                                                                       "previous"]
    assert versions.current(folder) == new and versions.previous(folder) == middle
    assert sorted(p.name for p in (folder / "runtime").iterdir()) == ["node-v1", "node-v2",
                                                                      "python-2"]


def test_the_same_version_installed_again_keeps_the_version_before_it(folder):
    # As manage does to add Google's packages: the version to switch back to stays a different one.
    old = install(folder, "1_0.aaa")
    install(folder, "1_1.bbb")

    again = install(folder, "1_1.ccc")

    assert versions.current(folder) == again and versions.previous(folder) == old
    assert not (folder / "versions" / "1_1.bbb").exists()


def test_what_an_install_that_failed_left_goes_with_the_next_switch(folder):
    old = install(folder, "1_0.aaa")
    (folder / "versions" / "1_1.cut").mkdir()  # cut off before its switch

    new = install(folder, "1_1.ddd")

    assert sorted(p.name for p in (folder / "versions").iterdir()) == ["1_0.aaa", "1_1.ddd",
                                                                       "previous"]
    assert versions.previous(folder) == old and versions.current(folder) == new


def test_an_install_from_before_versions_is_taken_over_as_the_previous_one(folder):
    app = folder / "app"
    (app / ".venv" / "bin").mkdir(parents=True)
    (app / "VERSION").write_text("0.9.0\n")
    (app / "pyproject.toml").write_text('[project]\nname = "family-brief"\n')
    (folder / "runtime" / "python-0").mkdir()

    new = install(folder, "1_0.aaa")

    before = versions.previous(folder)
    assert before is not None and before.name == "0.9.0.before"
    assert (before / ".venv" / "bin").is_dir() and versions.version(before) == "0.9.0"
    assert versions.current(folder) == new and app.is_symlink()
    # It ran on one of what runtime/ held then, which all stay while it's kept.
    assert (before / "RUNTIMES").read_text().split() == ["node-v1", "python-0", "python-1"]
    assert (folder / "runtime" / "python-0").is_dir()

    assert versions.switch_back(folder) == ("0.9.0", "1.0")
    assert (folder / "app" / ".venv" / "bin").is_dir() and versions.current(folder) == before


def test_switching_back_and_again_swaps_the_two(folder):
    old = install(folder, "1_0.aaa")
    new = install(folder, "1_1.bbb")

    assert versions.switch_back(folder) == ("1.0", "1.1")
    assert versions.current(folder) == old and versions.previous(folder) == new

    assert versions.switch_back(folder) == ("1.1", "1.0")
    assert versions.current(folder) == new and versions.previous(folder) == old


def test_with_no_previous_version_there_is_nothing_to_switch_back_to(folder):
    with pytest.raises(versions.NoPrevious):
        versions.switch_back(folder)  # nothing installed
    install(folder, "1_0.aaa")
    with pytest.raises(versions.NoPrevious):
        versions.switch_back(folder)


@pytest.mark.parametrize("inside", ["app/.venv/bin/python", "app/src/family_brief/x.mjs"])
def test_a_path_in_a_version_is_its_path_through_app(folder, inside):
    install(folder, "1_0.aaa")
    rest = inside.removeprefix("app/")

    assert versions.stable(folder / "versions" / "1_0.aaa" / rest) == folder / inside
    assert versions.stable(folder / inside) == folder / inside


def test_a_path_named_versions_elsewhere_is_left_as_it_is(tmp_path):
    path = tmp_path / "versions" / "1.0" / "x"
    assert versions.stable(path) == path


# ── parent-recap switch-back

def steps(harness) -> list[list[str]]:
    harness.processes.answers.append(
        lambda cmd, prog, **_: subprocess.CompletedProcess(cmd, 0, "", "") if prog == "python"
        else None)
    return harness.processes.commands


@pytest.mark.parametrize("on, put_back", [
    (MACOS, ["command_path", "setup_wilma", "baileys_install", "program_entries",
             "schedule_update"]),
    (LINUX, ["command_path", "setup_wilma", "baileys_install"])], ids=["macos", "linux"])
def test_switch_back_has_the_version_put_its_own_pieces_back_and_restarts_the_linked_device(
        harness, folder, capsys, on, put_back):
    harness.system = on
    install(folder, "1_0.aaa")
    install(folder, "1_1.bbb")
    harness.jobs.install_always(["node", "follow"], folder / "logs")
    ran = steps(harness)

    assert harness.cli("switch-back") == 0

    assert (folder / "app" / "VERSION").read_text() == "1.0\n"
    python = str(folder / "app" / ".venv" / "bin" / "python")
    assert [c[0] for c in ran] == [python] * len(put_back)
    assert [c[2].removeprefix("family_brief.") for c in ran] == put_back
    assert harness.jobs.always_restarts == 1
    out = capsys.readouterr().out
    assert "runs version 1.0 again" in out and "Version 1.1 is kept" in out


def test_switch_back_without_a_previous_version_says_so_and_changes_nothing(harness, folder,
                                                                           capsys):
    v = install(folder, "1_0.aaa")
    ran = steps(harness)

    assert harness.cli("switch-back") == 1

    assert "no previous version" in capsys.readouterr().out
    assert versions.current(folder) == v and ran == [] and harness.jobs.always_restarts == 0
