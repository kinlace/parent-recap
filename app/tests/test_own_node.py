"""The wilma CLI runs on Parent Recap's own Node, from Parent Recap's own folder (ADR 0011), also
when the Mac has no Node at all.

The pinned Node and the CLI are laid out in the test's ~/ParentRecap by `fake_node`, and run for
real, past the harness's fakes: the stand-in `node` runs the CLI, a shell script answering as the
CLI does. PATH leads to no Node."""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

import pytest

import fake_node
from conftest import REAL_RUN, program
from test_source_failures import REAL_WILMA_COLLECT, caught_up_last_night, payload_ids

from family_brief import ops, own_node
from family_brief.collectors import wilma

STUDENT = {"studentNumber": "7", "name": "Virtanen Mia"}
LISTED = {"students": [{"student": STUDENT, "messages": [
    {"wilmaId": 1, "subject": "Retki", "sentAt": "2026-09-27T09:00:00+03:00",
     "senderName": "Opettaja Virtanen"}]}]}
READ = {"student": STUDENT, "message": {"wilmaId": 1, "content": "Retki torstaina."}}
# Answers as the CLI 2.x does: the Kids, one message and its text, and nothing else tonight.
WILMA = f"""#!/bin/sh
case "$1 $2" in
  "kids list") echo '{{"students": [{{"studentNumber": "7", "name": "Mia"}}, {{"studentNumber": "8", "name": "Leo"}}]}}' ;;
  "messages list") echo '{json.dumps(LISTED)}' ;;
  "messages read") echo '{json.dumps(READ)}' ;;
  *) echo '{{"students": []}}' ;;
esac
"""


class Runs(list):
    """Each command the wilma CLI was run with, and in `envs` the environment of each."""

    def __init__(self) -> None:
        super().__init__()
        self.envs: list[dict[str, str] | None] = []


@pytest.fixture
def no_node(harness, monkeypatch, tmp_path) -> Runs:
    """A Mac without Node, whose PATH leads to none, with Parent Recap's own Node and wilma CLI
    in its folder. Returns each command the CLI was run with."""
    harness.config["wilma"]["enabled"] = True
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.delenv("SHELL", raising=False)
    assert shutil.which("node") is None
    fake_node.pinned_node(harness.home)
    fake_node.install_wilma(harness.home, WILMA)
    others = subprocess.run
    ran = Runs()

    def run(cmd: list[str], *a: Any, **k: Any) -> subprocess.CompletedProcess:
        if fake_node.is_wilma(cmd):
            ran.append(list(cmd))
            ran.envs.append(k.get("env"))
            return REAL_RUN(cmd, *a, **k)
        return others(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", run)
    return ran


def doctor_line(capsys, check: str) -> str:
    return next(l for l in capsys.readouterr().out.splitlines() if f"{check}:" in l)


def test_doctor_runs_the_wilma_cli_on_parent_recaps_own_node(harness, no_node, monkeypatch,
                                                              capsys):
    monkeypatch.setattr(ops, "launchctl_loaded", lambda: set())

    harness.cli("doctor", "--skip-llm")

    line = doctor_line(capsys, "Wilma")
    assert line.startswith(ops.OK) and "Mia, Leo" in line
    node = fake_node.pinned_node(harness.home)
    cli = fake_node.wilma_folder(harness.home) / "bin" / "wilma"
    assert no_node == [[str(node), str(cli), "kids", "list", "--json"]]


def test_the_evening_job_runs_the_wilma_cli_on_parent_recaps_own_node(harness, no_node,
                                                                       monkeypatch):
    monkeypatch.setattr(wilma, "collect", REAL_WILMA_COLLECT)
    caught_up_last_night(harness)

    assert harness.run() == 0

    assert payload_ids(harness, "wilma") == ["message:1"]
    node = str(fake_node.pinned_node(harness.home))
    assert no_node and all(c[0] == node for c in no_node)


def test_the_wilma_cli_never_checks_npm_for_a_newer_version(harness, no_node, monkeypatch, capsys):
    # Parent Recap installs the version it pins, and the CLI's "Run wilma update" would install
    # another one.
    monkeypatch.setattr(ops, "launchctl_loaded", lambda: set())
    monkeypatch.setattr(wilma, "collect", REAL_WILMA_COLLECT)
    caught_up_last_night(harness)

    harness.cli("doctor", "--skip-llm")
    assert harness.run() == 0

    assert len(no_node.envs) == len(no_node) > 1
    assert all(env and env["WILMAI_NO_UPDATE_CHECK"] == "1" for env in no_node.envs)
    assert all("WILMAI_CONFIG_PATH" not in env for env in no_node.envs)  # the CLI's own config


def test_a_node_and_wilma_cli_the_mac_has_are_not_used(harness, no_node, monkeypatch, tmp_path,
                                                        capsys):
    monkeypatch.setattr(ops, "launchctl_loaded", lambda: set())
    theirs = tmp_path / "their-bin"
    program(theirs, "node", "#!/bin/sh\nexit 99\n")
    program(theirs, "wilma", "#!/bin/sh\nexit 99\n")
    monkeypatch.setenv("PATH", f"{theirs}:/usr/bin:/bin")

    harness.cli("doctor", "--skip-llm")

    assert doctor_line(capsys, "Wilma").startswith(ops.OK)
    assert not any(str(theirs) in arg for c in no_node for arg in c)


def test_without_the_wilma_cli_doctor_says_to_connect_wilma_again(harness, no_node, monkeypatch,
                                                                  capsys):
    monkeypatch.setattr(ops, "launchctl_loaded", lambda: set())
    shutil.rmtree(fake_node.wilma_folder(harness.home))

    harness.cli("doctor", "--skip-llm")

    line = doctor_line(capsys, "Wilma")
    assert line.startswith(ops.FAIL) and "parent-recap setup wilma" in line
    assert "brew" not in line and "npm" not in line


def test_the_newest_pinned_node_is_used_while_an_older_one_is_still_there(harness):
    # A newly pinned Node is unpacked before the old one is removed; 24.9 sorts after 24.21 as text.
    fake_node.pinned_node(harness.home, "24.9.0")
    newer = fake_node.pinned_node(harness.home, "24.21.0")

    assert own_node.node() == newer


def test_parent_recaps_folder_is_the_one_install_sh_put_the_program_in(harness, tmp_path):
    from family_brief import install_record
    elsewhere = tmp_path / "Elsewhere"
    install_record.add("program", str(elsewhere / "app"))
    node = fake_node.pinned_node(harness.home).relative_to(harness.home / "ParentRecap")
    (elsewhere / node).parent.mkdir(parents=True)
    shutil.copy2(harness.home / "ParentRecap" / node, elsewhere / node)

    assert own_node.node() == elsewhere / node
    assert own_node.wilma_folder() == elsewhere / "wilma"
    assert own_node.wilma() is None  # not installed there
