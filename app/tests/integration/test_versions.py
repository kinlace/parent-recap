"""The installed program's versions (ADR 0047): `switch`, which install.sh runs once a release's
install finished, keeps the version switched from, one version deep, with the runtimes it runs
on, and takes over an install from before versions; `parent-recap switch-back` points `app` at
the previous version again and has it put its own pieces back. On real folders and real links
(symlinks on macOS and Linux, junctions on Windows) in a temporary HOME, with the subprocesses and
the always-running job as the harness's fakes. install.sh itself is run in test_install.py, the
Windows installer in test_windows_install.py and on CI's clean Windows runner."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from family_brief import run_lock, versions
from family_brief.config import Config
from family_brief.system import links

from fake_system import LINUX, MACOS, WINDOWS


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

    assert links.is_link(folder / "app") and (folder / "app").resolve() == v.resolve()
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
    # The copy it replaced may still be running a Brief: it goes with the next switch.
    assert (folder / "versions" / "1_1.bbb").is_dir()
    install(folder, "1_2.ddd")
    assert not (folder / "versions" / "1_1.bbb").exists()
    assert versions.previous(folder) == again


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
    assert versions.current(folder) == new and links.is_link(app)
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


# ── the Linked Device after an install

def test_the_install_puts_a_running_linked_device_on_this_versions_command_and_node(harness,
                                                                                    folder):
    # Its job named the Node pinned before, which goes once no kept version runs on it.
    from family_brief import linked_device
    from family_brief.config import Config
    import fake_node
    node = fake_node.pinned_node(harness.home, "26.0.0")
    fake_node.install_baileys(fake_node.baileys_folder(harness.home))
    config = harness.home / ".family" / "config.yaml"  # the Household's, where the install reads it
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(yaml.safe_dump(harness.config))
    harness.jobs.install_always([str(folder / "runtime" / "node-v24.0.0" / "bin" / "node"),
                                 "follow"], folder / "logs")

    assert linked_device.main(["restart"]) == 0

    assert harness.jobs.always == linked_device.command("follow", Config.load(None))
    assert harness.jobs.always[0] == str(node) and harness.jobs.always_running


def test_the_install_leaves_a_linked_device_that_isnt_running_as_it_is(harness, folder):
    from family_brief import linked_device
    harness.jobs.install_always(["node", "follow"], folder / "logs")
    harness.jobs.stop_always()

    assert linked_device.main(["restart"]) == 0

    assert harness.jobs.always == ["node", "follow"] and not harness.jobs.always_running


# ── parent-recap switch-back

def steps(harness) -> list[list[str]]:
    harness.processes.answers.append(
        lambda cmd, prog, **_: subprocess.CompletedProcess(cmd, 0, "", "") if prog == "python"
        else None)
    return harness.processes.commands


@pytest.mark.parametrize("on, put_back", [
    (MACOS, ["command_path", "setup_wilma", "baileys_install", "program_entries",
             "schedule_update"]),
    (WINDOWS, ["command_path", "setup_wilma", "baileys_install", "program_entries",
               "schedule_update"]),
    (LINUX, ["command_path", "setup_wilma", "baileys_install"])], ids=["macos", "windows", "linux"])
def test_switch_back_has_the_version_put_its_own_pieces_back_and_restarts_the_linked_device(
        harness, folder, capsys, on, put_back):
    harness.system = on
    install(folder, "1_0.aaa")
    install(folder, "1_1.bbb")
    harness.jobs.install_always(["node", "follow"], folder / "logs")
    ran = steps(harness)

    assert harness.cli("switch-back") == 0

    assert (folder / "app" / "VERSION").read_text() == "1.0\n"
    python = str(folder / "app" / ".venv" / on.python)
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


# ── where a link isn't replaced in one step: Windows' junctions (#991)

@pytest.fixture
def in_two_steps(harness, monkeypatch) -> None:
    """A system whose `app` is missing for a moment while it's pointed at another version, as a
    junction is on Windows, with a Household whose run lock is there to take."""
    monkeypatch.setattr(links, "one_step", lambda: False)
    config = harness.home / ".family" / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(yaml.safe_dump(harness.config))


class Watched:
    """The Linked Device's job and the run lock, as each switch of `app` finds them."""

    def __init__(self, harness, monkeypatch):
        self.seen: list[tuple[bool, bool]] = []  # (the job running, the lock held) at each switch
        point = links.point

        def watched(link: Path, to: Path) -> None:
            if link.name == "app":
                self.seen.append((harness.jobs.always_running,
                                  run_lock.is_busy(Config.load(None))))
            point(link, to)
        monkeypatch.setattr(links, "point", watched)


@pytest.fixture
def watched(harness, monkeypatch, in_two_steps) -> Watched:
    return Watched(harness, monkeypatch)


def test_the_switch_stops_the_linked_device_and_holds_the_run_lock_then_starts_it_again(
        harness, folder, watched):
    install(folder, "1_0.aaa")
    harness.jobs.install_always(["node", "follow"], folder / "logs")

    new = install(folder, "1_1.bbb")

    assert watched.seen[-1] == (False, True)
    assert harness.jobs.always_running and harness.jobs.always_starts == 1
    assert versions.current(folder) == new


def test_a_linked_device_that_wasnt_running_is_left_stopped(harness, folder, watched):
    harness.jobs.install_always(["node", "follow"], folder / "logs")
    harness.jobs.stop_always()

    install(folder, "1_0.aaa")

    assert not harness.jobs.always_running and harness.jobs.always_starts == 0


def test_an_install_from_before_versions_is_taken_over_with_the_linked_device_stopped(
        harness, folder, watched):
    app = folder / "app"
    (app / ".venv").mkdir(parents=True)
    (app / "VERSION").write_text("0.9.0\n")
    moved_while: list[bool] = []
    rename = versions.os.rename

    def watched_rename(a, b):
        if Path(a) == app and not links.is_link(app):  # not a junction's own rename on Windows
            moved_while.append(harness.jobs.always_running)
        rename(a, b)
    harness.jobs.install_always(["node", "follow"], folder / "logs")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(versions.os, "rename", watched_rename)
        install(folder, "1_0.aaa")

    assert moved_while == [False]
    assert harness.jobs.always_running
    assert versions.version(versions.previous(folder)) == "0.9.0"


def test_a_folder_something_else_holds_stops_the_switch_and_changes_nothing(
        harness, folder, in_two_steps, monkeypatch, capsys):
    app = folder / "app"
    (app / ".venv").mkdir(parents=True)
    (app / "VERSION").write_text("0.9.0\n")
    new = built(folder, "1_0.aaa")
    harness.jobs.install_always(["node", "follow"], folder / "logs")

    def held(a, b):
        raise PermissionError(13, "The process cannot access the file because it is being used "
                                  "by another process", str(a))
    monkeypatch.setattr(versions.os, "rename", held)
    monkeypatch.setattr(versions, "MOVE_WAIT", 0)

    assert versions.main(["switch", str(folder), str(new)]) == 1

    out = capsys.readouterr().out
    assert f"couldn't move its program in {app}" in out
    assert "being used by another process" in out and "Nothing was changed" in out
    assert not links.is_link(app) and (app / "VERSION").read_text() == "0.9.0\n"
    assert not (app / "RUNTIMES").exists()
    assert harness.jobs.always_running  # started again


def test_a_link_that_couldnt_be_made_after_the_take_over_puts_the_program_back(
        harness, folder, in_two_steps, monkeypatch):
    app = folder / "app"
    (app / ".venv").mkdir(parents=True)
    (app / "VERSION").write_text("0.9.0\n")
    new = built(folder, "1_0.aaa")

    def refused(link, to):
        raise PermissionError(5, "Access is denied", str(link))
    monkeypatch.setattr(links, "point", refused)

    with pytest.raises(PermissionError):
        versions.switch(folder, new)

    assert not links.is_link(app) and (app / "VERSION").read_text() == "0.9.0\n"
    assert not (folder / "versions" / "0.9.0.before").exists()


@pytest.mark.windows
def test_on_windows_a_file_open_in_the_program_s_folder_stops_the_take_over(harness, folder,
                                                                            capsys):
    # As a program started from it holds it: Windows doesn't let the folder be moved.
    app = folder / "app"
    app.mkdir()
    (app / "VERSION").write_text("0.9.0\n")
    new = built(folder, "1_0.aaa")

    with (app / "VERSION").open():
        assert versions.main(["switch", str(folder), str(new)]) == 1

    assert "couldn't move its program" in capsys.readouterr().out
    assert not links.is_link(app) and (app / "VERSION").read_text() == "0.9.0\n"


def test_a_run_going_stops_the_switch_and_changes_nothing(harness, folder, in_two_steps, capsys):
    old = install(folder, "1_0.aaa")
    new = built(folder, "1_1.bbb")

    with run_lock.exclusive(Config.load(None)):
        assert versions.main(["switch", str(folder), str(new)]) == 1

    assert "A Parent Recap run is going right now" in capsys.readouterr().out
    assert versions.current(folder) == old


def test_the_evening_run_s_own_update_switches_inside_the_run_s_lock(harness, folder,
                                                                     in_two_steps, monkeypatch):
    install(folder, "1_0.aaa")
    new = built(folder, "1_1.bbb")
    monkeypatch.setenv("PARENT_RECAP_UPDATING", "1")

    with run_lock.exclusive(Config.load(None)):
        versions.switch(folder, new)

    assert versions.current(folder) == new


def test_the_same_version_s_folder_again_only_clears_what_else_is_there(harness, folder, watched):
    # As the Windows installer installs the same version again in its own folder.
    harness.jobs.install_always(["node", "follow"], folder / "logs")
    v = install(folder, "1_0.aaa")
    (folder / "versions" / "1_1.cut").mkdir()

    versions.switch(folder, v)

    assert versions.current(folder) == v and not (folder / "versions" / "1_1.cut").exists()
    assert len(watched.seen) == 1  # the first install's: the Linked Device ran on
    assert harness.jobs.always_starts == 1


def test_switch_back_while_a_run_is_going_says_so_and_changes_nothing(harness, folder,
                                                                    in_two_steps, capsys):
    install(folder, "1_0.aaa")
    new = install(folder, "1_1.bbb")
    ran = steps(harness)

    with run_lock.exclusive(Config.load(None)):
        assert harness.cli("switch-back") == run_lock.BUSY

    assert "A Parent Recap run is going right now" in capsys.readouterr().out
    assert versions.current(folder) == new and ran == []
