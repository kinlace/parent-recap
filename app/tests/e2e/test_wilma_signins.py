"""Setup's Wilma sign-ins are the wilma CLI's own profiles, and from 2.5.0 the CLI keeps their
passwords in the system's keychain itself (#849): setup has it move them there with `wilma config
secrets auto` after each sign-in, on every update, and before a reading until it's set. Every
reading of Wilma, the evening run's, `doctor`'s and the preview's, reads the CLI's own config. An
install from 0.11 to 0.15 (#710), which kept setup's password in Parent Recap's own secret store
and the rest in ~/.family/wilma-signins.json, moves each sign-in back into the CLI's config on its
first reading or update, without the family signing in again, and leaves a sign-in the family made
themselves as it was. `doctor` says where each password is.

The CLI is the fake one on Parent Recap's own Node (fake_sources.py), which notes the config each
command read. All names, Wilmas and passwords are made up."""
from __future__ import annotations

import json
import logging

import pytest

from test_source_failures import the_model_says_something  # noqa: F401 (autouse)
from test_wilma_source import FIRST, LEO, MIA, OTHER, archived, cli, listed, read  # noqa: F401

from family_brief import install_record, ops, setup_wilma
from family_brief.collectors import wilma
from family_brief.collectors.base import Message
from family_brief.system.secrets_error import SecretsError
from fake_system import LINUX

PASSWORD = "salasana-1"
TWO_STEP = "JBSWY3DPEHPK3PXP"
OURS = f"{FIRST['url']}|huoltaja"
THEIRS = f"{OTHER['url']}|oma.huoltaja"
SECURE = ["config", "secrets", "auto"]


def signed_in_on_the_page() -> None:
    """The sign-in the setup page saves for Mia's Wilma."""
    before = [p["id"] for p in setup_wilma.signins()]
    setup_wilma.record_profile(setup_wilma.save_signin(FIRST, "huoltaja", PASSWORD, [MIA]), before)


def signed_in_from_0_11_to_0_15(harness) -> None:
    """What an install of 0.11 to 0.15 left: a sign-in the family made with the CLI themselves in
    the CLI's own config, and the setup page's, with a two-step key, signed in with last, its
    secrets in the secret store and the rest in setup's own file."""
    setup_wilma.write_profile(setup_wilma.config_path(), OTHER, "oma.huoltaja", "oma-salasana",
                              [LEO])
    ours = setup_wilma._profile([], FIRST, "huoltaja", PASSWORD, [MIA])
    ours["lastUsedAt"] = "2026-09-28T00:00:00.000Z"  # after the family's own
    harness.secrets[setup_wilma.secret_name(OURS)] = \
        f"{ours.pop('passwordObfuscated')} {setup_wilma.obfuscate(TWO_STEP)}"
    del ours["totpSecretObfuscated"]
    setup_wilma._write_config(setup_wilma.signins_path(), {"profiles": [ours],
                                                           "lastProfileId": OURS})
    install_record.add("wilma-profile", OURS)


def one_message(cli) -> None:
    cli.answers = {("messages", "list"): listed({"wilmaId": 812, "subject": "Retki"}),
                   ("messages", "read"): read(812, "Retki torstaina.")}


def secured(cli) -> list[str | None]:
    """The config each `wilma config secrets auto` was given."""
    return [r[0] if r else None for c, r in zip(cli.ran, cli.read) if c[:3] == SECURE]


def wilma_secrets(harness) -> list[str]:
    """What of Wilma's Parent Recap's own secret store holds."""
    return [name for name in harness.secrets if name.startswith("wilma-signin-")]


def wilma_lines(capsys) -> list[str]:
    return [line for line in capsys.readouterr().out.splitlines() if "Wilma:" in line]


def test_the_evening_run_reads_wilma_from_the_cli_s_own_config(harness, cli):
    signed_in_on_the_page()
    one_message(cli)

    assert harness.run() == 0

    # Saved in the CLI's own config, whose passwords the CLI then moved to the keychain.
    [profile] = setup_wilma.signins()
    assert (profile["id"], profile["passwordObfuscated"]) == (OURS, setup_wilma.obfuscate(PASSWORD))
    assert secured(cli)[0] == str(setup_wilma.config_path())
    # Every command of the run read that config: none was given one of its own (#710).
    reads = [r for c, r in zip(cli.ran, cli.read) if c[:3] != SECURE]
    assert len(reads) > 1 and reads == [None] * len(reads)
    assert not setup_wilma.signins_path().exists() and not setup_wilma.runs_folder().exists()
    assert not wilma_secrets(harness)  # nothing of Wilma's in Parent Recap's own store
    [m] = archived(harness)
    assert wilma.link(Message.from_dict(m)) == f"{FIRST['url']}/!7731905/messages/812"


def test_a_reading_has_the_cli_keep_the_passwords_in_the_keychain_until_that_s_set(harness, cli):
    setup_wilma.write_profile(setup_wilma.config_path(), FIRST, "huoltaja", PASSWORD, [MIA])
    one_message(cli)

    assert harness.run() == 0
    assert secured(cli) == [str(setup_wilma.config_path())]

    # Once set, the CLI moves a sign-in saved later itself.
    config = json.loads(setup_wilma.config_path().read_text())
    setup_wilma._write_config(setup_wilma.config_path(), {**config, "secretStore": "auto"})
    cli.ran.clear()
    cli.read.clear()
    harness.cli("doctor", "--skip-llm")
    assert secured(cli) == []


def test_an_install_from_0_11_to_0_15_moves_its_sign_in_back_without_signing_in_again(harness,
                                                                                      cli):
    signed_in_from_0_11_to_0_15(harness)
    theirs = setup_wilma.profile(setup_wilma.config_path(), THEIRS)
    one_message(cli)

    assert harness.run() == 0

    # Setup's sign-in, with its two-step key, is in the CLI's config beside the family's own, as
    # it was, signed in with last, and gone from the secret store and setup's own file.
    config = json.loads(setup_wilma.config_path().read_text())
    assert [p["id"] for p in config["profiles"]] == [THEIRS, OURS]
    assert config["profiles"][0] == theirs and config["lastProfileId"] == OURS
    ours = config["profiles"][1]
    assert (ours["passwordObfuscated"], ours["totpSecretObfuscated"]) == \
        (setup_wilma.obfuscate(PASSWORD), setup_wilma.obfuscate(TWO_STEP))
    assert [s["name"] for s in ours["students"]] == ["Mia"]
    assert not wilma_secrets(harness) and not setup_wilma.signins_path().exists()
    # Then the CLI moved both passwords to the keychain, and the run read both from its config.
    assert secured(cli) == [str(setup_wilma.config_path())]
    assert install_record.entries("wilma-profile") == [OURS]  # so uninstall still removes it
    assert archived(harness)


def test_the_update_moves_it_too_and_a_password_the_keychain_doesn_t_take_isn_t_fatal(
        harness, cli, caplog):
    signed_in_from_0_11_to_0_15(harness)
    cli.answers = {tuple(SECURE): (setup_wilma.MOVE_FAILED, {"setting": "auto", "moved": [
        {"wilma": FIRST["name"], "username": "huoltaja", "to": "keychain", "outcome": "failed",
         "message": "The keychain didn't keep the password as saved."}]})}

    assert setup_wilma.main(["update"]) == 0  # as install.sh runs it

    assert [p["id"] for p in setup_wilma.signins()] == [THEIRS, OURS]
    assert not wilma_secrets(harness) and not setup_wilma.signins_path().exists()
    assert secured(cli) == [str(setup_wilma.config_path())]
    assert "The Wilma password of huoltaja at Kuvitteellisen kaupungin koulut stays in the wilma " \
           "CLI's file for now" in caplog.text
    assert PASSWORD not in caplog.text and setup_wilma.obfuscate(PASSWORD) not in caplog.text


def test_a_sign_in_the_store_doesn_t_hand_over_yet_is_moved_by_a_later_reading(harness, cli):
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    signed_in_from_0_11_to_0_15(harness)
    legacy = setup_wilma.signins_path().read_text()
    harness.secrets.asks.add(setup_wilma.secret_name(OURS))  # nobody answers
    one_message(cli)

    assert harness.run() == 0

    assert setup_wilma.signins_path().read_text() == legacy
    assert [p["id"] for p in setup_wilma.signins()] == [THEIRS]
    # The next reading, here doctor's, moves it once the store hands it over.
    harness.secrets.asks.clear()
    cli.answers = {("kids", "list"): {"students": [MIA, LEO]}}
    harness.cli("doctor", "--skip-llm")
    assert [p["id"] for p in setup_wilma.signins()] == [THEIRS, OURS]
    assert not setup_wilma.signins_path().exists()


def test_an_old_copy_the_store_doesn_t_let_go_of_is_left_for_uninstall(harness, cli):
    signed_in_from_0_11_to_0_15(harness)
    harness.secrets.refuses = SecretsError("the store refused", "needs-prompt")  # deleting
    one_message(cli)

    assert harness.run() == 0

    assert [p["id"] for p in setup_wilma.signins()] == [THEIRS, OURS]  # moved all the same
    assert not setup_wilma.signins_path().exists()
    assert setup_wilma.secret_name(OURS) in install_record.entries("keychain")


def test_a_sign_in_whose_password_is_gone_from_the_store_goes_and_says_to_sign_in_again(
        harness, cli, caplog):
    signed_in_from_0_11_to_0_15(harness)
    harness.secrets[setup_wilma.secret_name(OURS)] = ""
    one_message(cli)

    assert harness.run() == 0

    assert [p["id"] for p in setup_wilma.signins()] == [THEIRS]
    assert not setup_wilma.signins_path().exists()
    assert "no Wilma password for huoltaja: sign in to Wilma again" in caplog.text


def test_signing_in_again_on_the_page_replaces_the_sign_in_from_0_11_to_0_15(harness, cli):
    signed_in_from_0_11_to_0_15(harness)  # and no reading has moved it yet

    signed_in_on_the_page()
    assert harness.run() == 0

    # The new sign-in, without the old one's two-step key, which no reading brings back.
    assert [p["id"] for p in setup_wilma.signins()] == [THEIRS, OURS]
    assert setup_wilma.signin(OURS)["totpSecretObfuscated"] is None
    assert not wilma_secrets(harness) and not setup_wilma.signins_path().exists()


@pytest.mark.parametrize("store, on, line", [
    ("keychain", None, "✅ Wilma: the Wilma password of huoltaja at Kuvitteellisen kaupungin "
                       "koulut is in the macOS Keychain"),
    ("file", None, "⚠️  Wilma: the Wilma password of huoltaja at Kuvitteellisen kaupungin koulut "
                   "is still in the wilma CLI's own file, not encrypted: moving it to the macOS "
                   "Keychain didn't work yet."),
    ("file", LINUX, "✅ Wilma: the Wilma password of huoltaja at Kuvitteellisen kaupungin koulut "
                    "is in the wilma CLI's owner-only file, since this server has no keychain"),
], ids=["in the keychain", "in the file", "on a server without a keychain"])
def test_doctor_says_where_each_wilma_password_is(harness, cli, capsys, store, on, line):
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    if on is not None:
        harness.system = on
    signed_in_on_the_page()
    usable = on is None
    cli.answers = {("kids", "list"): {"students": [MIA]}, ("config", "secrets"): {
        "setting": "auto", "logins": [{"wilma": FIRST["name"], "username": "huoltaja",
                                       "store": store, "hasTotp": False}],
        "keychain": {"kind": "macOS Keychain" if usable else "Secret Service", "usable": usable,
                     **({} if usable else {"problem": "no D-Bus session"})}}}

    harness.cli("doctor", "--skip-llm")

    assert any(seen.startswith(line) for seen in wilma_lines(capsys)), line


@pytest.mark.parametrize("code, says", [
    ("secret_missing", "isn't in the keychain any more"),
    ("secret_refused", "Unlock it, then run parent-recap doctor again"),
    ("secret_unavailable", "a keychain this wilma CLI can't open here"),
])
def test_doctor_says_what_to_do_when_the_keychain_doesn_t_hand_the_password_over(
        harness, cli, capsys, code, says):
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    signed_in_on_the_page()
    cli.answers = {("kids", "list"): (4, {"status": "error", "code": code, "message": "no"})}

    harness.cli("doctor", "--skip-llm")

    [line] = wilma_lines(capsys)
    assert line.startswith(ops.FAIL) and says in line


def test_a_check_s_folder_left_by_one_killed_outright_goes_with_the_next_reading(harness, cli):
    signed_in_on_the_page()
    killed = setup_wilma.runs_folder() / "run-killed"  # its lock went with the check
    killed.mkdir(parents=True)
    (killed / setup_wilma.RUN_LOCK).touch()
    (killed / "config.json").write_text(json.dumps({"profiles": [
        {"passwordObfuscated": setup_wilma.obfuscate(PASSWORD)}]}))
    one_message(cli)

    with setup_wilma.run_folder() as going:  # another check, still going
        assert harness.run() == 0
        assert going.exists()

    assert not killed.exists()


def test_nothing_of_the_password_is_logged(harness, cli, caplog):
    caplog.set_level(logging.DEBUG)
    signed_in_from_0_11_to_0_15(harness)
    one_message(cli)

    assert harness.run() == 0

    for secret in (PASSWORD, setup_wilma.obfuscate(PASSWORD), TWO_STEP):
        assert secret not in caplog.text
        assert not any(secret in arg for c in cli.ran for arg in c)
