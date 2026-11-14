"""Adding a Source updates Parent Recap first when that Source changed since the installed
version (ADR 0051, #961): a Household that skipped releases while it didn't have a Source may
hold an old copy of it, and a family never signs in to a Source with code older than the newest.

From a terminal, `setup konnect` (and each other Source's step whose Source has a part of its
own) first reads `release.json` on `stable`; when the Source's part is newer than the installed
version it installs the newest release as `parent-recap update` does, and the step is run again
by the new version. Driven through main() as test_update.py drives `parent-recap update`, on
its installed program (`Mac`) and made-up `stable` (`Releases`); the new version's program, which
`run_again` starts, is the harness's process fake, which runs the step as that program would.
Each test asks for `github` before `harness`, so the Sources' fakes, which pass on every request
that isn't theirs, sit in front of the fake GitHub, which answers all it gets."""
from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest
import yaml

from family_brief import sources, update
from test_setup_konnect import SECRET, USERNAME, portal  # noqa: F401  the fixture
from test_update import (INSTALLED, github, mac, needing_family, no_downloads, release_zip,  # noqa: F401
                         signature, update_log)
from test_update_skips import KONNECT_ONLY, out_on_stable, parts, zips_asked

from fake_sources import KONNECT_PASSWORD

# install.sh's layout, on a Mac.
pytestmark = pytest.mark.posix

KONNECT = ("setup", "konnect", "--portal", "mijnbso", "--username", USERNAME)


class NewVersion:
    """The installed program as `run_again` starts it once the update switched: it runs the
    step with what it was given, as the new version's `parent-recap` does. `ran` is each command
    it was started with, and `versions` the version `app` pointed at when it was."""

    def __init__(self, harness, mac) -> None:  # noqa: F811
        self.harness, self.mac = harness, mac
        self.ran: list[list[str]] = []
        self.versions: list[str] = []
        harness.processes.answers.insert(0, self.answer)

    def answer(self, cmd: list[str], _prog: str, **_k: Any) -> subprocess.CompletedProcess | None:
        program = str(self.mac.folder / "app" / ".venv" / "bin" / "parent-recap")
        if cmd[0] != program or "setup" not in cmd:
            return None
        self.ran.append(cmd)
        self.versions.append(self.mac.version())
        return subprocess.CompletedProcess(cmd, self.harness.cli(*cmd[cmd.index("setup"):]))


@pytest.fixture
def new_version(harness, mac) -> NewVersion:  # noqa: F811
    return NewVersion(harness, mac)


def step(harness, capsys, *args: str, typed: str | None = KONNECT_PASSWORD) -> tuple[int, dict, str]:
    """A setup step run from a terminal: its exit status, its one line of JSON, and stderr."""
    harness.secrets.typed = typed
    code = harness.cli(*args)
    captured = capsys.readouterr()
    [line] = captured.out.splitlines()
    assert typed is None or typed not in captured.out + captured.err
    return code, json.loads(line), captured.err


def saved_config(harness) -> dict:
    return yaml.safe_load((harness.home / ".family" / "config.yaml").read_text())


def nothing_was_added(harness, portal) -> bool:  # noqa: F811
    return (portal.requests == [] and "konnect" not in saved_config(harness)
            and SECRET not in harness.secrets)


# ---------------------------------------------------------------- it updates first

def test_setup_konnect_updates_first_and_the_new_version_runs_the_step(
        github, harness, mac, portal, new_version, capsys):  # noqa: F811
    out_on_stable(github, "0.20.0", KONNECT_ONLY)
    asked_before_the_switch: list[int] = []
    mac.after_switch = lambda _env: asked_before_the_switch.append(len(portal.requests)) or ""

    code, out, err = step(harness, capsys, *KONNECT)

    # The update ran, as one the family started: at once, though 0.20.0 is new to this Mac.
    assert mac.version() == "0.20.0" and len(mac.installs) == 1
    # Nothing reached the portal with the old code.
    assert asked_before_the_switch == [0]
    # Then the new version ran the step, with what the old one was given.
    [ran] = new_version.ran
    assert ran[1:] == ["-c", str(harness.home / ".family" / "config.yaml"), *KONNECT]
    assert new_version.versions == ["0.20.0"]
    assert (code, out["result"]) == (0, "saved"), out
    assert saved_config(harness)["konnect"]["signins"][0]["username"] == USERNAME
    assert harness.secrets[SECRET] == KONNECT_PASSWORD
    # What the update says goes to stderr: stdout is the step's one line of JSON.
    assert "Updating Parent Recap to version 0.20.0 first" in err
    assert "adding nl/konnect, which changed since: updating first" in update_log(mac)


def test_a_household_that_skipped_the_release_before_gets_it_when_it_adds_konnect(
        github, harness, mac, portal, new_version, capsys):  # noqa: F811
    out_on_stable(github, "0.20.0", KONNECT_ONLY)
    assert harness.cli("update") == 0  # skipped: this Household has no Konnect
    assert mac.version() == INSTALLED and update.state(mac.folder)["skipped"] == "0.20.0"
    capsys.readouterr()

    code, out, _ = step(harness, capsys, *KONNECT)

    assert code == 0 and out["result"] == "saved" and mac.version() == "0.20.0"
    assert update.state(mac.folder)["skipped"] is None


def test_a_release_that_needs_the_family_installs_with_its_dialogs_as_one_they_started(
        github, harness, mac, portal, new_version, capsys):  # noqa: F811
    out_on_stable(github, "0.20.0", KONNECT_ONLY)
    github.publish("0.20.0", needing_family("0.20.0"))

    code, out, err = step(harness, capsys, *KONNECT)

    assert code == 0 and out["result"] == "saved" and mac.version() == "0.20.0"
    assert ["PARENT_RECAP_UPDATING" in env for env in mac.install_envs] == [False]
    assert "Version 0.20.0 needs you at the computer." in err


def test_every_source_with_a_part_of_its_own_has_the_same_step(github, harness, mac, capsys):  # noqa: F811
    with_parts = [s.name for s in sources.ALL if s.part is not None]
    assert sorted(with_parts) == ["eepos", "evaka", "konnect", "myclub", "socialschools"]
    arguments = {"eepos": ("--school", "emo", "--username", "a"),
                 "evaka": ("--city", "espoo", "--username", "a"),
                 "konnect": ("--portal", "mijnbso", "--username", "a"),
                 "socialschools": ("--username", "a"), "myclub": ("--kid", "Mia")}
    out_on_stable(github, "0.20.0", parts(**{name: "0.20.0" for name in with_parts}))
    github.files.clear()  # the release can't be downloaded, so each step stops at the update

    for name in with_parts:
        code, out, _ = step(harness, capsys, "setup", name, *arguments[name], typed=None)
        assert (code, out["result"]) == (1, "update-failed"), name
    assert mac.version() == INSTALLED


# ---------------------------------------------------------------- it goes on at once

@pytest.mark.parametrize("on_stable, said", [
    (INSTALLED, KONNECT_ONLY),                                # nothing newer is out
    ("0.20.0", parts(core="0.20.0")),                         # a newer release that left Konnect alone
    ("0.21.0", parts(core="0.21.0", myclub="0.20.0")),        # or changed another Source
])
def test_a_part_that_is_not_newer_runs_the_step_at_once_and_downloads_nothing(
        github, harness, mac, portal, new_version, capsys, on_stable, said):  # noqa: F811
    out_on_stable(github, on_stable, said)
    before = update.state(mac.folder)

    code, out, err = step(harness, capsys, *KONNECT)

    assert code == 0 and out["result"] == "saved"
    assert mac.version() == INSTALLED and mac.installs == [] and new_version.ran == []
    assert [r["url"] for r in github.requests] == [update.STABLE_RELEASE]  # the one look
    assert no_downloads(mac) and "Updating" not in err
    assert update.state(mac.folder) == before  # the evening's look is left as it was


def test_a_source_without_a_part_and_a_step_that_adds_none_ask_no_host(github, harness, mac, capsys):  # noqa: F811
    out_on_stable(github, "0.20.0", parts(core="0.20.0"))
    # Gmail's code is still in the shared modules: its changes are the core's.
    assert sources.GMAIL.part is None and update.needed_to_add(sources.GMAIL.part) is None

    step(harness, capsys, "setup", "status", typed=None)

    assert github.requests == [] and mac.version() == INSTALLED


def test_a_program_that_does_not_update_itself_runs_the_step_at_once(github, harness,portal, capsys):  # noqa: F811
    # Not run from an installed release (no `mac`), as on Windows and the hosted server.
    out_on_stable(github, "0.20.0", KONNECT_ONLY)

    code, out, _ = step(harness, capsys, *KONNECT)

    assert code == 0 and out["result"] == "saved" and github.requests == []


@pytest.mark.parametrize("said", [None, {"core": INSTALLED}, {**KONNECT_ONLY, "nl/konnect": "soon"}])
def test_what_the_file_does_not_say_of_the_part_counts_as_changed(
        github, harness, mac, portal, new_version, capsys, said):  # noqa: F811
    out_on_stable(github, "0.20.0", said)

    code, out, _ = step(harness, capsys, *KONNECT)

    assert code == 0 and out["result"] == "saved" and mac.version() == "0.20.0"


# ---------------------------------------------------------------- the update can't be done

SAYS = {"en": ("has to update itself before Konnect is added", "wasn't added and nothing was changed"),
        "fi": ("on päivitettävä itsensä ennen kuin Konnect lisätään", "jäi lisäämättä eikä mitään muutettu"),
        "zh": ("添加 Konnect 之前，Parent Recap 需要先更新自己", "Konnect 没有添加，也没有改动任何东西")}


@pytest.mark.parametrize("language", ["en", "fi", "zh"])
def test_without_a_network_the_step_says_so_in_the_family_s_language_and_stops(
        github, harness, mac, portal, new_version, capsys, language):  # noqa: F811
    harness.config["summary_language"] = language
    github.stable = None  # the look gets no answer it can read

    code, out, _ = step(harness, capsys, *KONNECT, typed=None)

    assert code == 1 and out["result"] == "update-failed"
    for words in SAYS[language]:
        assert words in out["next"], words
    assert "couldn't look for a newer release" in out["error"]
    assert nothing_was_added(harness, portal) and new_version.ran == []
    assert mac.version() == INSTALLED


def test_a_language_without_reviewed_text_gets_it_in_english(github, harness, mac, portal, capsys):  # noqa: F811
    harness.config["summary_language"] = "sv"
    github.stable = None

    _, out, _ = step(harness, capsys, *KONNECT, typed=None)

    assert SAYS["en"][0] in out["next"]


def test_a_signature_that_does_not_verify_stops_the_step_with_the_old_code(
        github, harness, mac, portal, new_version, capsys):  # noqa: F811
    out_on_stable(github, "0.20.0", KONNECT_ONLY)
    data = release_zip("0.20.0")
    github.publish("0.20.0", data, signature(data, key="someone-else"))

    code, out, _ = step(harness, capsys, *KONNECT, typed=None)

    assert code == 1 and out["result"] == "update-failed"
    assert "signature doesn't verify" in out["error"]
    assert nothing_was_added(harness, portal) and new_version.ran == []
    assert mac.version() == INSTALLED and mac.installs == [] and no_downloads(mac)


def test_a_release_that_fails_its_check_switches_back_and_stops_the_step(
        github, harness, mac, portal, new_version, capsys):  # noqa: F811
    out_on_stable(github, "0.20.0", KONNECT_ONLY)
    mac.fails["0.20.0"] = ["Calendar"]

    code, out, _ = step(harness, capsys, *KONNECT, typed=None)

    assert code == 1 and out["result"] == "update-failed"
    assert "switched back to version 0.19.0" in out["error"]
    assert nothing_was_added(harness, portal) and new_version.ran == []
    assert mac.version() == INSTALLED


@pytest.mark.parametrize("language", ["en", "fi", "zh"])
def test_while_a_brief_is_being_made_the_step_says_it_is_busy_and_stops(
        github, harness, mac, portal, new_version, capsys, language):  # noqa: F811
    harness.config["summary_language"] = language
    out_on_stable(github, "0.20.0", KONNECT_ONLY)
    busy = {"en": "it is busy right now", "fi": "juuri nyt muuta tekeillä", "zh": "它现在正忙"}

    with update.alone(str(harness.home / ".family" / "config.yaml")):  # a run, or another update
        code, out, _ = step(harness, capsys, *KONNECT, typed=None)

    assert code == 1 and out["result"] == "update-busy" and "error" not in out
    assert SAYS[language][0] in out["next"] and busy[language] in out["next"]
    assert nothing_was_added(harness, portal) and new_version.ran == []
    assert mac.version() == INSTALLED and zips_asked(github) == []


def test_the_texts_name_the_source_in_every_language():
    for texts in (update.NOT_ADDED, update.NOT_ADDED_BUSY):
        assert set(texts) == {"en", "fi", "zh"}
        for language, text in texts.items():
            assert "{source}" in text and "Eepos" in text.format(source="Eepos"), language
