"""Adding an Eepos sign-in with `parent-recap setup eepos` (ADR 0038, #755), `doctor`'s check of it
and uninstall: the password goes from the secret store's own prompt to one sign-in and the store,
never into the config, the command's output or a dialog; the students are matched to the Kids by
name. The outside edges are faked: the store's prompt (`secrets.typed`) and each school's portal.
The command's one line of JSON on stdout is what the maintainer reads."""
from __future__ import annotations

import json

import pytest
import yaml
from test_doctor import nothing_loaded  # noqa: F401  (a fixture)
from test_uninstall import set_up, uninstall

from conftest import eepos_entry, eepos_student
from fake_sources import EEPOS_PASSWORD
from family_brief import install_record, ops
from family_brief.collectors import eepos

USERNAME = "parent@example.com"
SECRET = eepos.secret_name("emo", USERNAME)


@pytest.fixture
def portal(harness):
    """emo.eepos.fi, where the guardian's password is EEPOS_PASSWORD and Mia and Leo take lessons."""
    fake = harness.source_fakes["eepos"]
    school = fake.school("emo")
    school.accounts[USERNAME] = EEPOS_PASSWORD
    school.students = [eepos_student(11, "Mia", "Virtanen"), eepos_student(12, "Leo", "Virtanen")]
    harness.sources = {"eepos": [eepos_entry("Virtanen Leo, Piano", "2026-09-28", students=(12,))]}
    return fake


def setup_eepos(harness, capsys, *args: str, typed: str | None = EEPOS_PASSWORD) -> tuple[int, dict]:
    harness.secrets.typed = typed
    code = harness.cli("setup", "eepos", *args)
    captured = capsys.readouterr()
    [line] = captured.out.splitlines()
    assert typed is None or typed not in captured.out + captured.err
    return code, json.loads(line)


def saved_config(harness) -> dict:
    return yaml.safe_load((harness.home / ".family" / "config.yaml").read_text())


def test_a_sign_in_typed_at_the_store_s_prompt_is_checked_matched_and_saved(harness, portal, capsys):
    code, out = setup_eepos(harness, capsys, "--school", "https://emo.eepos.fi/#/portal", "--username", USERNAME)

    assert code == 0
    assert out == {"result": "saved", "school": "emo.eepos.fi", "events": 1,
                   "students": [{"name": "Mia Virtanen", "kid": "Mia"}, {"name": "Leo Virtanen", "kid": "Leo"}]}
    config = saved_config(harness)
    assert config["eepos"] == {"signins": [{"school": "emo", "username": USERNAME,
                                            "kids": {"11": "Mia", "12": "Leo"}}]}
    assert EEPOS_PASSWORD not in json.dumps(config)
    assert harness.secrets[SECRET] == EEPOS_PASSWORD and eepos.TYPED not in harness.secrets
    assert SECRET in install_record.entries("keychain")

    # The next evening reads it.
    harness.config = config
    harness.model_reply = {"per_kid": [], "calendar_events": [], "message_digest": "- Leo has piano tomorrow"}
    assert harness.run() == 0
    [email] = harness.sent
    assert "Eepos 1 个日程" in email.text  # the default config's Brief is in Chinese


def test_a_wrong_password_saves_nothing_and_keeps_the_one_that_works(harness, portal, capsys):
    harness.secrets[SECRET] = EEPOS_PASSWORD  # added before

    code, out = setup_eepos(harness, capsys, "--school", "emo", "--username", USERNAME, typed="typo")

    assert code == 1 and out["result"] == "wrong-password"
    assert "Forgot your password?" in out["next"] and "setup eepos --school emo --username" in out["next"]
    assert "eepos" not in saved_config(harness)
    assert harness.secrets[SECRET] == EEPOS_PASSWORD and eepos.TYPED not in harness.secrets


def test_nothing_typed_at_the_prompt_saves_nothing(harness, portal, capsys):
    code, out = setup_eepos(harness, capsys, "--school", "emo", "--username", USERNAME, typed=None)

    assert code == 1 and out["result"] == "not-stored"
    assert portal.requests == []


@pytest.mark.parametrize("args, result", [
    (("--school", "emo.example.fi", "--username", USERNAME), "not-a-school"),
    (("--school", "emo", "--username", " "), "no-username"),
], ids=["not-eepos", "no-username"])
def test_what_isn_t_a_sign_in_is_refused_before_the_password_is_asked(harness, portal, capsys, args, result):
    code, out = setup_eepos(harness, capsys, *args)

    assert code == 1 and out["result"] == result
    assert harness.secrets.typed == EEPOS_PASSWORD and eepos.TYPED not in harness.secrets
    assert portal.requests == []


def test_a_keychain_tmux_or_ssh_can_t_reach_is_said_before_the_password_is_asked(harness, portal, capsys):
    harness.secrets.reachable_here = False  # macOS over SSH or in tmux

    code, out = setup_eepos(harness, capsys, "--school", "emo", "--username", USERNAME)

    assert code == 1 and out["result"] == "keychain-not-reachable"
    assert "tmux or SSH" in out["next"] and "setup eepos --school emo --username" in out["next"]
    assert harness.secrets.typed == EEPOS_PASSWORD and eepos.TYPED not in harness.secrets
    assert portal.requests == [] and "eepos" not in saved_config(harness)


def test_a_portal_that_changed_saves_nothing_and_says_so(harness, portal, capsys):
    portal.answers[("emo", "guardian/students")] = (200, "<html>Eepos</html>")

    code, out = setup_eepos(harness, capsys, "--school", "emo", "--username", USERNAME)

    assert code == 1 and out["result"] == "changed" and "tell the maintainers" in out["next"]
    assert SECRET not in harness.secrets


def test_a_student_who_is_none_of_the_kids_is_saved_and_named(harness, portal, capsys):
    portal.school("emo").students.append(eepos_student(13, "Olli", "Virtanen"))

    code, out = setup_eepos(harness, capsys, "--school", "emo", "--username", USERNAME)

    assert code == 0 and out["result"] == "saved"
    assert out["next"].startswith("Olli Virtanen matched none of the Kids, so their lessons go to the whole Household")
    assert saved_config(harness)["eepos"]["signins"][0]["kids"] == {"11": "Mia", "12": "Leo"}


def test_the_same_sign_in_again_replaces_it_and_another_school_is_added(harness, portal, capsys):
    portal.school("espoo").accounts[USERNAME] = EEPOS_PASSWORD
    portal.school("espoo").students = [eepos_student(31, "Mia", "Virtanen")]

    for school in ("emo", "espoo", "emo"):
        assert setup_eepos(harness, capsys, "--school", school, "--username", USERNAME)[0] == 0
        harness.config = saved_config(harness)  # which the next command's config is written from

    signins = saved_config(harness)["eepos"]["signins"]
    assert [(s["school"], s["kids"]) for s in signins] == [("espoo", {"31": "Mia"}), ("emo", {"11": "Mia", "12": "Leo"})]
    assert eepos.secret_name("espoo", USERNAME) in harness.secrets


def test_doctor_signs_in_to_each_school_and_names_its_students(harness, nothing_loaded, portal,  # noqa: F811
                                                               capsys):
    harness.config["eepos"] = {"signins": [{"school": "emo", "username": USERNAME}]}
    harness.secrets[SECRET] = EEPOS_PASSWORD

    harness.cli("doctor", "--skip-llm")

    lines = capsys.readouterr().out.splitlines()
    assert (f"{ops.OK} Eepos (emo.eepos.fi): signed in as {USERNAME}, 1 events in the next 14 days; "
            "students: Mia Virtanen (Mia), Leo Virtanen (Leo)") in lines


def test_doctor_says_how_to_type_a_password_eepos_no_longer_takes(harness, nothing_loaded, portal,  # noqa: F811
                                                                  capsys):
    harness.config["eepos"] = {"signins": [{"school": "emo", "username": USERNAME}]}
    harness.secrets[SECRET] = "old"

    assert harness.cli("doctor", "--skip-llm") == 1

    [line] = [l for l in capsys.readouterr().out.splitlines() if "Eepos" in l]
    assert line.startswith(f"{ops.FAIL} Eepos (emo.eepos.fi): the sign-in as {USERNAME} doesn't work: "
                           "login failed at emo.eepos.fi (type the password again: ")
    assert line.endswith(f"setup eepos --school emo --username {USERNAME})")


def test_uninstall_deletes_each_eepos_password(harness, portal, monkeypatch, capsys):
    set_up(harness, monkeypatch)
    harness.config["eepos"] = {"signins": [{"school": "emo", "username": USERNAME}]}
    harness.secrets[SECRET] = EEPOS_PASSWORD
    harness.cli("setup", "status")  # writes the config with its Eepos sign-in
    capsys.readouterr()

    assert uninstall(harness) == 0
    assert SECRET in capsys.readouterr().out

    assert uninstall(harness, "--confirm", "--remove-archive") == 0
    assert SECRET not in harness.secrets
