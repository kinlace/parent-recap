"""The update setting on a Windows PC (ADR 0052, #995): the setup page's Updates, with the sentence,
the switch and Update now; `doctor`'s Updates line; `parent-recap update on` and `off`; and the
update before a Source is added, on the page and in PowerShell. What test_setup_updates.py,
test_doctor.py and the two newest-first files show on a Mac holds on a PC; these show it does, on
test_update_windows.py's installed PC (`PC`), whose release is signed by a test key and checked by
the real `ssh-keygen`. And the texts a Windows family can meet on the way name PowerShell and the
PC, never Terminal or the Mac.

Over HTTP for the page, as the page's own script calls it, on the server `setup_page.py` starts
inside the harness; through main() for the commands. Each test asks for `github` before
`harness`, so the Sources' fakes, which pass on every request that isn't theirs, sit in front of
the fake GitHub."""
from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any

import pytest
import yaml

from family_brief import brief_text, ops, run_lock, update
from family_brief.config import Config
from family_brief.setup_server import server as page_server
from fake_sources import EEPOS_PASSWORD, KONNECT_PASSWORD
from setup_page import CAN_LISTEN, PAGE_DIR, call, config_file, connect_done, wait_for
from setup_page import clock, health, page, schedule  # noqa: F401
from test_setup_eepos import SECRET as EEPOS_SECRET, at_the_eepos_step, sign_in
from test_setup_eepos import portal  # noqa: F401
from test_setup_konnect import SECRET as KONNECT_SECRET, USERNAME
from test_setup_konnect import portal as konnect_portal  # noqa: F401
from test_setup_page_newest_first import check, started_again, stopped_to_start_again  # noqa: F401
from test_setup_updates import saved, switch, update_now, updates
from test_update import INSTALLED, release_zip
from test_update_skips import parts
from test_update_windows import github, keys, pc, release, update_log  # noqa: F401

TEXT = json.loads((PAGE_DIR / "text.json").read_text(encoding="utf-8"))
KONNECT = ("setup", "konnect", "--portal", "mijnbso", "--username", USERNAME)
# What names a Mac's own things, in each of the page's languages.
MAC_WORDS = re.compile(r"Terminal|Pääte|终端|\bMac\b|macOS|Macin|Macilla|Macissa")


@pytest.fixture
def household(harness, pc):  # noqa: F811
    """A Household set up on a Windows PC, running an installed release."""
    connect_done(harness)
    return harness


def doctor_updates_line(harness, capsys) -> str:
    capsys.readouterr()
    harness.cli("doctor", "--skip-llm")
    [line] = [said for said in capsys.readouterr().out.splitlines() if " Updates: " in said]
    return line


# ---------------------------------------------------------------- the setup page

def test_the_page_on_a_pc_shows_that_updates_are_on_and_the_switch_saves_the_setting(
        github, household, page):  # noqa: F811
    shown = updates(page.url)

    assert shown["available"] is True and shown["on"] is True and shown["version"] == INSTALLED
    assert github.requests == []

    r = switch(page.url, False)

    assert r.json()["result"] == "saved" and r.json()["updates"]["on"] is False
    assert saved(household)["updates"] == {"enabled": False}
    assert switch(page.url, True).json()["updates"]["on"] is True
    assert saved(household)["updates"] == {"enabled": True}


def test_update_now_on_a_pc_installs_a_newer_release_and_the_page_goes_on_in_it(
        github, household, keys, pc, page):  # noqa: F811
    release(github, keys, "0.20.0")

    out = update_now(page.url)

    assert out["result"] == "updated" and out["version"] == "0.20.0"
    assert out["updates"]["version"] == "0.20.0" and out["updates"]["newer"] is None
    assert pc.version() == "0.20.0" and len(pc.installs) == 1
    # The installer is the release's Python program, never PowerShell.
    assert pc.installs[0][2].endswith("windows_install.py")
    assert updates(page.url)["version"] == "0.20.0"  # the page goes on, showing the new version


def test_update_now_on_a_pc_whose_new_version_fails_its_check_switches_back(
        github, household, keys, pc, page):  # noqa: F811
    release(github, keys, "0.20.0")
    pc.fails["0.20.0"] = ["Calendar"]

    out = update_now(page.url)

    assert out["result"] == "switched-back" and out["tried"] == "0.20.0"
    assert pc.version() == INSTALLED
    assert "Calendar" in out["reason"]


def test_update_now_on_a_pc_while_a_brief_is_being_made_says_busy_and_changes_nothing(
        github, household, keys, pc, page):  # noqa: F811
    release(github, keys, "0.20.0")
    asked = len(github.requests)

    with run_lock.exclusive(Config.load(config_file(household))):
        r = call(page.url, "api/updates/now", method="POST", body={})

    assert r.json() == {"result": "busy"}
    assert len(github.requests) == asked and pc.installs == [] and pc.version() == INSTALLED


def test_update_now_on_a_pc_while_another_update_runs_says_busy_and_changes_nothing(
        github, household, keys, pc, page):  # noqa: F811
    release(github, keys, "0.20.0")

    with update.alone(str(config_file(household))):  # `parent-recap update` in PowerShell
        r = call(page.url, "api/updates/now", method="POST", body={})

    assert r.json() == {"result": "busy"}
    assert pc.installs == [] and pc.version() == INSTALLED


# ---------------------------------------------------------------- doctor and the command

def test_doctor_on_a_pc_says_updates_are_on_and_the_installed_version(github, harness, pc, capsys):  # noqa: F811
    assert doctor_updates_line(harness, capsys) == (
        f"{ops.OK} Updates: version {INSTALLED}; updates are on (parent-recap update off turns "
        "them off); hasn't looked for a newer release yet")
    assert github.requests == []


def test_doctor_on_a_pc_shows_an_update_that_failed(github, harness, keys, pc, capsys):  # noqa: F811
    release(github, keys, "0.20.0")
    pc.fails["0.20.0"] = ["Calendar"]
    assert harness.cli("update") == 1

    line = doctor_updates_line(harness, capsys)

    assert line.startswith(f"{ops.OK} Updates: version {INSTALLED}; updates are on")
    assert line.endswith(f"0.20.0 failed its check; back on {INSTALLED}")


def test_doctor_on_a_pc_shows_an_update_that_waits_for_the_family(github, harness, keys, pc, capsys):  # noqa: F811
    data = release_zip("0.20.0", extra={
        "parent-recap/release.json": json.dumps({"background_from": "0.20.0", "asks": None}),
        "parent-recap/app/windows_install.py": "raise SystemExit(1)\n"})
    release(github, keys, "0.20.0", data)
    harness.cli("doctor", "--skip-llm")  # writes the harness's config, as any command does here
    assert update.update(None, by_family=False).result == "needs-family"

    line = doctor_updates_line(harness, capsys)

    assert line.endswith("an update is waiting: version 0.20.0 needs you at the computer, so it "
                         "isn't installed by itself (parent-recap update installs it)")
    assert pc.installs == []


def test_update_off_and_on_on_a_pc_save_the_setting_and_say_it(github, harness, pc, capsys):  # noqa: F811
    assert harness.cli("update", "off") == 0
    assert capsys.readouterr().out.strip() == update.OFF
    assert yaml.safe_load(config_file(harness).read_text())["updates"] == {"enabled": False}
    harness.config["updates"] = {"enabled": False}  # as saved: each command writes the harness's
    assert "updates are off" in doctor_updates_line(harness, capsys)

    assert harness.cli("update", "on") == 0
    assert capsys.readouterr().out.strip() == update.ON
    assert yaml.safe_load(config_file(harness).read_text())["updates"] == {"enabled": True}
    assert github.requests == []


# ---------------------------------------------------------------- adding a Source updates first

EEPOS_ONLY = parts(eepos="0.20.0")
KONNECT_ONLY = parts(konnect="0.20.0")


@pytest.mark.skipif(not CAN_LISTEN, reason="a second server on the same port needs a real one")
def test_adding_eepos_on_a_pc_updates_first_and_the_step_goes_on_in_the_new_version(
        github, harness, keys, pc, page, portal, started_again):  # noqa: F811
    at_the_eepos_step(harness)
    release(github, keys, "0.20.0")
    github.parts = EEPOS_ONLY

    first = sign_in(page.url)

    assert first.json() == {"result": "updating-first", "version": "0.20.0"}
    wait_for(lambda: stopped_to_start_again(page))
    assert portal.requests == [] and EEPOS_SECRET not in harness.secrets
    assert pc.version() == "0.20.0" and len(pc.installs) == 1
    assert "adding fi/eepos, which changed since: updating first" in update_log(pc)

    new = started_again(page)
    assert check(new.url) == {"result": "no-update"}
    again = sign_in(new.url)

    assert again.json()["result"] == "saved"
    assert harness.secrets[EEPOS_SECRET] == EEPOS_PASSWORD
    assert len(pc.installs) == 1


def test_setup_konnect_in_powershell_updates_first_and_the_new_version_runs_the_step(
        github, harness, keys, pc, konnect_portal, capsys):  # noqa: F811
    release(github, keys, "0.20.0")
    github.parts = KONNECT_ONLY
    ran: list[tuple[list[str], str]] = []

    def new_version(cmd: list[str], _prog: str, **_k: Any) -> subprocess.CompletedProcess | None:
        # The installed version's Python, as `parent-recap.cmd` runs it, with the step.
        if cmd[:3] != [pc.python(), "-m", "family_brief"] or "setup" not in cmd:
            return None
        ran.append((cmd, pc.version()))
        return subprocess.CompletedProcess(cmd, harness.cli(*cmd[cmd.index("setup"):]))
    harness.processes.answers.insert(0, new_version)
    harness.secrets.typed = KONNECT_PASSWORD

    code = harness.cli(*KONNECT)

    captured = capsys.readouterr()
    assert code == 0, captured
    [(cmd, version)] = ran
    assert version == "0.20.0" and cmd[-len(KONNECT):] == list(KONNECT)
    assert harness.secrets[KONNECT_SECRET] == KONNECT_PASSWORD
    assert "Updating Parent Recap to version 0.20.0 first" in captured.err + captured.out


# ---------------------------------------------------------------- the page started again

class NewProgram:
    """The installed version's program as `start_again` starts it on Windows, through
    `subprocess.Popen`: `started` is each command and environment it was started with; `waits`
    are what each wait for it does, in turn: an exit status, or an exception it raises."""

    def __init__(self, waits: list[Any]) -> None:
        self.started: list[tuple[list[str], dict[str, str]]] = []
        self.waits = waits

    def __call__(self, command: list[str], env: dict[str, str]) -> "NewProgram":
        if isinstance(self.waits[0], OSError):  # it couldn't be started
            raise self.waits[0]
        self.started.append((command, env))
        return self

    def wait(self) -> int:
        done = self.waits.pop(0)
        if isinstance(done, BaseException):
            raise done
        return done


@pytest.fixture
def page_to_start_again(harness, pc, clock, monkeypatch):  # noqa: F811
    """A page's server on the PC that stopped after an update to start again in the installed
    version, and how that version's program is started: os.execve fails the test."""
    from family_brief import setup_server
    from setup_page import context
    if not CAN_LISTEN:
        pytest.skip("needs a port to listen on")
    server = setup_server.SetupServer(context(config_file(harness), clock))
    monkeypatch.setattr(os, "execve", lambda *a: pytest.fail("os.execve on Windows"))
    server.context.restart = True
    server.context.stopped.set()

    def started_as(program: NewProgram) -> setup_server.SetupServer:
        monkeypatch.setattr(page_server.subprocess, "Popen", program)
        return server
    return started_as


def test_on_a_pc_the_page_s_program_waits_for_the_installed_version_s_and_ends_with_it(
        github, harness, pc, page_to_start_again, capsys):  # noqa: F811
    """On Windows os.execve starts the new program and ends this one at once, so the window the
    install line was pasted into would take the page for closed: the program started again is
    waited for instead, also through Ctrl+C, which reaches it too, and this one ends as it ends,
    saying nothing of its own."""
    program = NewProgram([KeyboardInterrupt(), 3])
    server = page_to_start_again(program)

    with pytest.raises(SystemExit) as ended:
        page_server.serve(server, opened=True)

    assert ended.value.code == 3 and program.waits == []
    [(cmd, env)] = program.started
    assert cmd == [pc.python(), "-m", "family_brief", "-c", str(config_file(harness)), "setup", "page"]
    assert env[page_server.STARTED_AGAIN] == f"{server.address[1]} {server.code}"
    assert capsys.readouterr().out == ""  # the new program says when the page closed


def test_on_a_pc_a_program_that_cannot_be_started_again_says_the_page_closed(
        github, harness, pc, page_to_start_again, capsys):  # noqa: F811
    blocked = OSError(22, "Operation did not complete successfully because the file contains a "
                          "virus or potentially unwanted software")
    server = page_to_start_again(NewProgram([blocked]))

    assert page_server.serve(server, opened=True) == 0
    assert capsys.readouterr().out.strip() == page_server.closed_line("en")


# ---------------------------------------------------------------- the words a PC family reads

UPDATE_KEYS = re.compile(r"^(finish\.updates\.|source\.update|source\.updating|"
                         r"health\.check\.sign-in-after-update)")


def test_the_page_s_update_texts_name_no_terminal_and_no_mac_on_a_pc():
    for language, table in TEXT.items():
        for key, said in table.items():
            # None has a twin in the PC's words (`.windows`): each is said alike on both.
            if UPDATE_KEYS.match(key):
                assert not MAC_WORDS.search(said), (language, key, said)


def test_what_the_update_command_and_the_brief_say_name_no_terminal_and_no_mac():
    said = [update.ON, update.OFF, *update.BUSY, *update.SKIPPED,
            *update.NOT_ADDED.values(), *update.NOT_ADDED_BUSY.values()]
    for language, text in brief_text.TEXT.items():
        said += [text.updated, text.updates_itself, text.update_failed, text.update_available,
                 text.update_waiting]
    for text in said:
        assert not MAC_WORDS.search(text), text


def test_on_a_pc_what_the_update_says_where_it_can_t_update_names_powershell_and_the_pc(
        harness, pc):  # noqa: F811
    without_keygen = update.no_ssh_keygen("there is no ssh-keygen")

    assert without_keygen.startswith("This PC can't update Parent Recap by itself")
    assert "PowerShell" in without_keygen and not MAC_WORDS.search(without_keygen)
