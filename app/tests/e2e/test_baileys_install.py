"""The install puts the pinned Baileys tree in Parent Recap's folder with its own Node's `npm ci`,
staged like the wilma CLI, so an npm that fails or is ended leaves the tree that was there."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

import fake_node
from family_brief import baileys_install, install_record
from fake_system import LINUX


class Npm:
    """The pinned Node's npm, whose `npm ci` lays out the tree its folder's lockfile pins."""

    def __init__(self, home: Path) -> None:
        self.node = fake_node.pinned_node(home)
        self.commands: list[list[str]] = []
        self.works = True        # False: npm can't reach its registry
        self.ended = False       # True: npm is ended at the time limit, halfway through
        self.version = baileys_install.BAILEYS_VERSION  # what npm installs

    def run(self, cmd: list[str], **k: Any) -> subprocess.CompletedProcess:
        self.commands.append(list(cmd))
        folder = fake_node.npm_prefix(cmd)
        if not self.works:
            return subprocess.CompletedProcess(cmd, 1, "", "npm error code ENOTFOUND")
        if self.ended:  # a package half unpacked
            (folder / "node_modules" / "ws").mkdir(parents=True)
            raise subprocess.TimeoutExpired(cmd, k["timeout"])
        assert (folder / "package-lock.json").read_bytes() == \
            (baileys_install.PINNED / "package-lock.json").read_bytes()
        fake_node.install_baileys(folder, self.version)
        return subprocess.CompletedProcess(cmd, 0, "added 72 packages", "")


@pytest.fixture
def npm(harness, monkeypatch) -> Npm:
    n = Npm(harness.home)
    others = subprocess.run  # the harness's fakes

    def run(cmd: list[str], *a: Any, **k: Any) -> subprocess.CompletedProcess:
        return n.run(cmd, **k) if fake_node.is_npm(cmd) else others(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", run)
    return n


def folder(harness) -> Path:
    return fake_node.baileys_folder(harness.home)


def installed_version(harness) -> str:
    package = folder(harness) / "node_modules" / baileys_install.BAILEYS_PACKAGE / "package.json"
    return json.loads(package.read_text())["version"]


def pinned_ci(cmd: list[str], home: Path) -> bool:
    """Whether npm ran `npm ci` without package scripts in a new folder next to the tree's, the
    folder gone now that it has taken the tree's place."""
    staging = fake_node.npm_prefix(cmd)
    return cmd[2] == "ci" and "--ignore-scripts" in cmd \
        and staging.parent == fake_node.baileys_folder(home).parent \
        and staging.name.startswith(".baileys-") and not staging.exists()


def nothing_left_beside_the_tree(harness) -> bool:
    return sorted(p.name for p in (harness.home / "ParentRecap").iterdir()) == ["baileys", "runtime"]


def test_the_install_puts_the_pinned_tree_in_parent_recaps_folder_and_records_it(
        harness, npm, monkeypatch, capsys):
    umasks: list[int] = []
    monkeypatch.setattr(os, "umask", lambda mask: umasks.append(mask) or 0o022)

    assert baileys_install.main(["install"]) == 0

    assert [pinned_ci(c, harness.home) for c in npm.commands] == [True]
    assert npm.commands[0][0] == str(npm.node)
    assert installed_version(harness) == "7.0.0-rc14"
    assert install_record.entries("baileys") == [str(folder(harness))]
    assert nothing_left_beside_the_tree(harness)
    assert umasks == [0o077]  # install.sh runs it with the shell's umask
    assert "✅" in capsys.readouterr().out


def test_the_hosted_version_s_install_puts_no_tree_there(harness, npm, capsys):
    harness.system = LINUX  # which has no WhatsApp (ADR 0029)

    assert baileys_install.main(["install"]) == 0

    assert npm.commands == [] and not folder(harness).exists()
    assert install_record.entries("baileys") == []
    assert capsys.readouterr().out == ""


def test_an_up_to_date_tree_is_not_installed_again(harness, npm, capsys):
    fake_node.install_baileys(folder(harness))

    assert baileys_install.install() == "up-to-date"
    assert baileys_install.main(["install"]) == 0

    assert npm.commands == []
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("version, lockfile", [("7.0.0-rc9", None), ("7.0.0-rc14", b"{}\n")],
                         ids=["older Baileys", "another lockfile"])
def test_a_tree_other_than_the_pinned_one_is_replaced_by_it(harness, npm, capsys, version,
                                                            lockfile):
    fake_node.install_baileys(folder(harness), version, lockfile)

    assert baileys_install.main(["install"]) == 0

    assert [pinned_ci(c, harness.home) for c in npm.commands] == [True]
    assert baileys_install.up_to_date() and nothing_left_beside_the_tree(harness)
    assert capsys.readouterr().out == "✅ The Linked Device's WhatsApp library is updated to " \
                                      "7.0.0-rc14.\n"


def test_a_tree_that_cannot_be_updated_stays_and_the_install_goes_on(harness, npm, capsys,
                                                                     caplog):
    fake_node.install_baileys(folder(harness), "7.0.0-rc9")
    npm.works = False

    assert baileys_install.main(["install"]) == 0

    assert installed_version(harness) == "7.0.0-rc9" and nothing_left_beside_the_tree(harness)
    out = capsys.readouterr().out
    assert "couldn't be installed" in out and "Run the install again" in out
    assert "npm ended with exit code 1:\nnpm error code ENOTFOUND" in caplog.text  # why, logged
    # The next install tries again.
    npm.works = True
    assert baileys_install.install() == "updated"


def test_an_npm_ended_at_the_time_limit_leaves_the_tree_as_it_was(harness, npm, capsys):
    fake_node.install_baileys(folder(harness), "7.0.0-rc9")
    npm.ended = True

    assert baileys_install.main(["install"]) == 0

    assert "couldn't be installed" in capsys.readouterr().out
    assert installed_version(harness) == "7.0.0-rc9" and nothing_left_beside_the_tree(harness)


def test_an_npm_ended_during_the_first_install_leaves_no_half_tree(harness, npm):
    npm.ended = True

    assert baileys_install.install() == "failed"

    assert sorted(p.name for p in (harness.home / "ParentRecap").iterdir()) == ["runtime"]
    # Recorded before npm ran, so uninstall would find a tree it left anyway.
    assert install_record.entries("baileys") == [str(folder(harness))]


def test_an_npm_that_installs_another_baileys_changes_nothing(harness, npm, caplog):
    npm.version = "7.0.0"

    assert baileys_install.install() == "failed"

    assert not folder(harness).exists()
    assert "npm installed version 7.0.0 of the WhatsApp library, not 7.0.0-rc14." in caplog.text


def test_without_its_own_node_it_says_to_run_the_install_again(harness, npm, capsys):
    fake_node.install_baileys(folder(harness), "7.0.0-rc9")
    node_folder = npm.node.parent if fake_node.WINDOWS else npm.node.parents[1]
    for script in node_folder.rglob("npm-cli.js"):
        script.unlink()

    assert baileys_install.main(["install"]) == 0

    assert "Node is missing" in capsys.readouterr().out
    assert installed_version(harness) == "7.0.0-rc9" and npm.commands == []
