"""Setup's Wilma sign-ins keep their passwords in the secret store, not in the wilma CLI's own
config (#710). Every reading of Wilma, the evening run's, `doctor`'s and the preview's, gives the
CLI a config of its own, written with those passwords into an owner-only folder that goes when the
reading ends, however it ends. An install from before moves its sign-in there on its first run,
without the family signing in again, and leaves a sign-in the family made themselves as it was.

The CLI is the fake one on Parent Recap's own Node (fake_sources.py), which notes the config each
command read. All names, Wilmas and passwords are made up."""
from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path

import pytest

from test_source_failures import the_model_says_something  # noqa: F401 (autouse)
from test_wilma_source import FIRST, LEO, MIA, OTHER, archived, cli, listed, read  # noqa: F401

from family_brief import install_record, ops, setup_wilma
from family_brief.collectors import wilma
from family_brief.collectors.base import Message
from family_brief.system.secrets_error import SecretsError

PASSWORD = "salasana-1"
TWO_STEP = "JBSWY3DPEHPK3PXP"
OURS = f"{FIRST['url']}|huoltaja"


def signed_in_on_the_page() -> None:
    """The sign-in the setup page saves for Mia's Wilma."""
    before = [p["id"] for p in setup_wilma.signins()]
    setup_wilma.record_profile(setup_wilma.save_signin(FIRST, "huoltaja", PASSWORD, [MIA]), before)


def signed_in_before_710() -> Path:
    """The CLI's own config as an install from before #710 left it: a sign-in the family made with
    the CLI themselves, then the setup page's, with a two-step key, signed in with last, and the
    Wilma sessions the CLI saved beside them. Returns the config."""
    config = setup_wilma.config_path()
    setup_wilma.write_profile(config, OTHER, "oma.huoltaja", "oma-salasana", [LEO])
    setup_wilma.write_profile(config, FIRST, "huoltaja", PASSWORD, [MIA])
    saved = json.loads(config.read_text())
    saved["profiles"][1]["totpSecretObfuscated"] = setup_wilma.obfuscate(TWO_STEP)
    config.write_text(json.dumps(saved))
    install_record.add("wilma-profile", OURS)
    config.with_name(setup_wilma.SESSIONS).write_text('{"x": {"state": "a session", "savedAt": 0}}')
    return config


def one_message(cli) -> None:
    cli.answers = {("messages", "list"): listed({"wilmaId": 812, "subject": "Retki"}),
                   ("messages", "read"): read(812, "Retki torstaina.")}


def nothing_left() -> bool:
    """No run's config is left, nor anything else readable of the password on disk."""
    runs = setup_wilma.runs_folder()
    readable = [p for p in (setup_wilma.signins_path(), setup_wilma.config_path()) if p.exists()]
    return not (runs.exists() and list(runs.iterdir())) \
        and not any(PASSWORD in p.read_text() or setup_wilma.obfuscate(PASSWORD) in p.read_text()
                    for p in readable)


def test_the_evening_run_reads_wilma_through_a_config_of_its_own_that_goes_after(harness, cli):
    signed_in_on_the_page()
    one_message(cli)

    assert harness.run() == 0

    # Every command read the one config, so they carried on with one Wilma session.
    assert len(cli.read) > 1 and len({path for path, _ in cli.read}) == 1
    path, config = cli.read[0]
    assert Path(path).parent.parent == setup_wilma.runs_folder()
    [profile] = config["profiles"]
    assert (profile["id"], profile["passwordObfuscated"]) == (OURS, setup_wilma.obfuscate(PASSWORD))
    assert config["lastProfileId"] == OURS
    assert not Path(path).parent.exists() and nothing_left()
    assert not setup_wilma.config_path().exists()  # never the CLI's own
    [m] = archived(harness)
    assert wilma.link(Message.from_dict(m)) == f"{FIRST['url']}/!7731905/messages/812"


def test_doctor_and_the_preview_read_wilma_the_same_way(harness, cli, capsys):
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    signed_in_on_the_page()
    cli.answers = {("kids", "list"): {"students": [MIA]}}

    harness.cli("doctor", "--skip-llm")
    assert harness.run("--preview") == 0

    assert any(line.startswith(ops.OK) for line in capsys.readouterr().out.splitlines()
               if "Wilma:" in line)
    assert {tuple(c[:2]) for c in cli.ran} >= {("kids", "list"), ("messages", "list")}
    assert all(r is not None and r[1]["profiles"][0]["passwordObfuscated"]
               == setup_wilma.obfuscate(PASSWORD) for r in cli.read)
    assert nothing_left()


def test_an_install_from_before_moves_its_wilma_password_to_the_secret_store(harness, cli):
    config = signed_in_before_710()
    theirs = setup_wilma.profile(config, f"{OTHER['url']}|oma.huoltaja")
    one_message(cli)

    assert harness.run() == 0

    # Setup's sign-in, with its two-step key, is in the store, and gone from the CLI's config
    # with the sessions the CLI saved; the family's own is as it was.
    assert harness.secrets[setup_wilma.secret_name(OURS)] == \
        f"{setup_wilma.obfuscate(PASSWORD)} {setup_wilma.obfuscate(TWO_STEP)}"
    assert json.loads(config.read_text())["profiles"] == [theirs]
    assert not config.with_name(setup_wilma.SESSIONS).exists()
    assert [p["id"] for p in setup_wilma.signins()] == [theirs["id"], OURS]
    assert nothing_left()
    # The run read both, setup's first, without the family signing in again.
    path, read_config = cli.read[0]
    assert read_config["lastProfileId"] == OURS
    assert {p["username"]: (p["passwordObfuscated"], p["totpSecretObfuscated"])
            for p in read_config["profiles"]} == {
        "oma.huoltaja": (setup_wilma.obfuscate("oma-salasana"), None),
        "huoltaja": (setup_wilma.obfuscate(PASSWORD), setup_wilma.obfuscate(TWO_STEP))}


def test_a_sign_in_the_store_can_t_take_yet_stays_where_it_is_and_still_works(harness, cli,
                                                                             capsys):
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    config = signed_in_before_710()
    before = config.read_text()
    harness.secrets.refuses = SecretsError("the store refused", "needs-prompt")
    one_message(cli)

    assert harness.run() == 0

    assert config.read_text() == before
    assert {p["username"] for p in cli.read[0][1]["profiles"]} == {"oma.huoltaja", "huoltaja"}
    assert archived(harness)
    # The next reading, here doctor's, moves it once the store takes it.
    harness.secrets.refuses = None
    cli.answers = {("kids", "list"): {"students": [MIA, LEO]}}
    harness.cli("doctor", "--skip-llm")
    assert setup_wilma.secret_name(OURS) in harness.secrets
    assert PASSWORD not in config.read_text() and setup_wilma.obfuscate(PASSWORD) not in \
        config.read_text()


@pytest.mark.parametrize("how", ["fails", pytest.param("is stopped", marks=pytest.mark.skipif(
    sys.platform == "win32", reason="os.kill on Windows ends the process, as a kill does"))])
def test_a_reading_that_fails_or_is_stopped_leaves_no_config_behind(harness, cli, how):
    signed_in_on_the_page()

    def messages(_args: list[str]):
        if how == "is stopped":  # as launchd or Task Scheduler stops the evening job
            os.kill(os.getpid(), signal.SIGTERM)
        raise RuntimeError("the CLI broke")
    cli.answers = {("messages", "list"): messages}

    if how == "is stopped":
        with pytest.raises(SystemExit):
            harness.run()
        assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL  # as it was
    else:
        harness.run()

    assert cli.read and nothing_left()


def test_a_config_left_by_a_run_killed_outright_goes_with_the_next_run(harness, cli):
    signed_in_on_the_page()
    runs = setup_wilma.runs_folder()
    killed = runs / "run-killed"  # its lock went with the run
    killed.mkdir(parents=True)
    (killed / setup_wilma.RUN_LOCK).touch()
    (killed / "config.json").write_text(json.dumps({"profiles": [
        {"passwordObfuscated": setup_wilma.obfuscate(PASSWORD)}]}))
    one_message(cli)

    with setup_wilma.run_folder() as going:  # another reading, still going
        assert harness.run() == 0
        assert going.exists()

    assert not killed.exists() and nothing_left()
