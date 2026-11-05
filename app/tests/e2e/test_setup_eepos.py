"""Adding an Eepos sign-in with `parent-recap setup eepos` (ADR 0038, #755), `doctor`'s check of it
and uninstall: the password goes from the secret store's own prompt to one sign-in and the store,
never into the config, the command's output or a dialog; the students are matched to the Kids by
name. The outside edges are faked: the store's prompt (`secrets.typed`) and each school's portal.
The command's one line of JSON on stdout is what the maintainer reads. The setup page's Eepos step
is at the end, with the password typed into the page's own field (#791)."""
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
from setup_page import (Response, assert_never_leaked, call, config_file, refuse_secrets,
                        save_progress, source, statuses)
from setup_page import clock, page  # noqa: F401

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


# ── Connect: Eepos, on the setup page (#791), driven with HTTP calls as the page makes them
# (`setup_page.py`), against the same fake portal

def at_the_eepos_step(harness) -> None:
    config_file(harness).parent.mkdir(parents=True, exist_ok=True)
    config_file(harness).write_text(yaml.safe_dump(harness.config, allow_unicode=True))
    save_progress(harness, {"phase": "connect", "source": "eepos", "sources": {
        "wilma": "done", "gmail": "done", "ai": "done", "whatsapp": "done", "myclub": "skipped"}})
    harness.source_fakes["whatsapp"].set_up()  # WhatsApp done: paired


def sign_in(url: str, password: str = EEPOS_PASSWORD, school: str = "emo.eepos.fi",
            username: str = USERNAME) -> Response:
    return call(url, "api/eepos", method="POST",
                body={"school": school, "username": username, "password": password})


def test_a_sign_in_typed_on_the_page_is_checked_kept_in_the_store_and_listed(harness, page, portal,
                                                                            caplog, capsys):
    at_the_eepos_step(harness)

    r = sign_in(page.url)

    out = r.json()
    assert out["result"] == "saved" and out["events"] == 1
    assert out["found"] == [{"name": "Mia Virtanen", "kid": "Mia", "id": "11", "first": "Mia"},
                            {"name": "Leo Virtanen", "kid": "Leo", "id": "12", "first": "Leo"}]
    assert out["signins"] == [{"school": "emo", "address": "emo.eepos.fi", "username": USERNAME,
                               "kids": ["Mia", "Leo"]}]
    # Done, and the step stays for another school until Continue.
    assert out["progress"]["sources"]["eepos"] == "done" and out["progress"]["source"] == "eepos"
    assert saved_config(harness)["eepos"] == {"signins": [{"school": "emo", "username": USERNAME,
                                                           "kids": {"11": "Mia", "12": "Leo"}}]}
    assert harness.secrets[SECRET] == EEPOS_PASSWORD and eepos.TYPED not in harness.secrets
    assert SECRET in install_record.entries("keychain")
    state = call(page.url, "api/state")
    assert state.json()["eepos"]["signins"] == out["signins"]
    assert_never_leaked(harness, [r, state], caplog, capsys, EEPOS_PASSWORD)  # but in the store

    assert call(page.url, "api/eepos/done", method="POST", body={}).json()["progress"]["source"] == "evaka"


def test_a_wrong_password_on_the_page_is_refused_and_keeps_the_one_that_works(harness, page, portal):
    harness.config["eepos"] = {"signins": [{"school": "emo", "username": USERNAME}]}
    harness.secrets[SECRET] = EEPOS_PASSWORD
    at_the_eepos_step(harness)

    assert sign_in(page.url, "typo").json() == {"result": "wrong-password"}
    assert harness.secrets[SECRET] == EEPOS_PASSWORD
    assert saved_config(harness)["eepos"] == {"signins": [{"school": "emo", "username": USERNAME}]}
    assert statuses(page.url)["eepos"] == "to-do"


@pytest.mark.parametrize("change, result", [
    ({"school": "emo.example.fi"}, "not-a-school"),
    ({"username": " "}, "no-username"),
], ids=["not-eepos", "no-username"])
def test_what_isn_t_a_sign_in_is_refused_without_signing_in(harness, page, portal, change, result):
    at_the_eepos_step(harness)

    assert sign_in(page.url, **change).json() == {"result": result}
    assert portal.requests == [] and SECRET not in harness.secrets


def test_a_store_the_page_can_t_reach_is_said_before_signing_in(harness, page, portal):
    harness.secrets.reachable_here = False  # macOS, with the page started in tmux or over SSH
    at_the_eepos_step(harness)

    assert sign_in(page.url).json() == {"result": "keychain-not-reachable"}
    assert portal.requests == []


def test_a_store_that_refuses_the_password_keeps_nothing(harness, page, portal):
    at_the_eepos_step(harness)
    refuse_secrets(harness, -25293)

    assert sign_in(page.url).json() == {"result": "not-stored", "code": -25293}
    assert "eepos" not in saved_config(harness) and statuses(page.url)["eepos"] == "to-do"


def test_a_portal_that_changed_says_so_without_its_words(harness, page, portal):
    portal.answers[("emo", "guardian/students")] = (200, "<html>Eepos</html>")
    at_the_eepos_step(harness)

    assert sign_in(page.url).json() == {"result": "changed"}


def test_a_student_who_is_none_of_the_kids_is_shown_not_guessed(harness, page, portal):
    portal.school("emo").students.append(eepos_student(13, "Olli", "Virtanen"))
    at_the_eepos_step(harness)

    out = sign_in(page.url).json()

    assert out["found"][-1] == {"name": "Olli Virtanen", "kid": None, "id": "13", "first": "Olli"}
    assert saved_config(harness)["eepos"]["signins"][0]["kids"] == {"11": "Mia", "12": "Leo"}


def test_a_second_school_is_added_and_one_is_removed_with_its_password(harness, page, portal):
    portal.school("espoo").accounts[USERNAME] = EEPOS_PASSWORD
    portal.school("espoo").students = [eepos_student(31, "Mia", "Virtanen")]
    at_the_eepos_step(harness)
    sign_in(page.url)
    assert [s["school"] for s in sign_in(page.url, school="espoo").json()["signins"]] == ["emo", "espoo"]

    r = call(page.url, "api/eepos/remove", method="POST", body={"school": "emo", "username": USERNAME})

    assert r.json()["result"] == "removed" and r.json()["signins"][0]["school"] == "espoo"
    assert r.json()["progress"]["sources"]["eepos"] == "done"
    assert SECRET not in harness.secrets and SECRET not in install_record.entries("keychain")
    assert eepos.secret_name("espoo", USERNAME) in harness.secrets
    assert [s["school"] for s in saved_config(harness)["eepos"]["signins"]] == ["espoo"]

    # With the last one removed, Eepos is to do again: to add one or skip it.
    r = call(page.url, "api/eepos/remove", method="POST", body={"school": "espoo", "username": USERNAME})
    assert r.json()["signins"] == [] and r.json()["progress"]["sources"]["eepos"] == "to-do"
    assert call(page.url, "api/eepos/remove", method="POST",
                body={"school": "espoo", "username": USERNAME}).status == 400


def test_a_sign_in_added_from_a_terminal_is_listed_and_continue_keeps_it(harness, page, portal):
    harness.config["eepos"] = {"signins": [{"school": "emo", "username": USERNAME, "kids": {"12": "Leo"}}]}
    harness.secrets[SECRET] = EEPOS_PASSWORD
    at_the_eepos_step(harness)

    assert call(page.url, "api/state").json()["eepos"]["signins"] == [
        {"school": "emo", "address": "emo.eepos.fi", "username": USERNAME, "kids": ["Leo"]}]
    r = call(page.url, "api/eepos/done", method="POST", body={})

    assert r.json()["progress"]["sources"]["eepos"] == "done"
    assert harness.secrets[SECRET] == EEPOS_PASSWORD


def test_continue_needs_a_sign_in_first(harness, page, portal):
    at_the_eepos_step(harness)

    assert call(page.url, "api/eepos/done", method="POST", body={}).status == 400
    assert statuses(page.url)["eepos"] == "to-do"


def test_skipping_eepos_removes_its_sign_ins_and_passwords(harness, page, portal):
    harness.config["eepos"] = {"signins": [{"school": "emo", "username": USERNAME}]}
    harness.secrets[SECRET] = EEPOS_PASSWORD
    at_the_eepos_step(harness)

    out = source(page.url, "eepos", "skip").json()

    assert out["progress"]["sources"]["eepos"] == "skipped" and out["progress"]["source"] == "evaka"
    assert saved_config(harness)["eepos"]["signins"] == [] and SECRET not in harness.secrets


def test_a_password_is_never_an_empty_answer(harness, page, portal):
    at_the_eepos_step(harness)

    assert sign_in(page.url, " ").status == 400
    assert portal.requests == []


# ── A Household without Wilma adds its Kids on Eepos's step (#796)

def an_eepos_only_household(harness) -> None:
    """A Household whose music school's Eepos is its only Source of the Kids: no Wilma, no
    MyClub, no Kid yet, at Eepos's step."""
    harness.config["kids"] = []
    harness.config["wilma"] = {"enabled": False}
    harness.config["whatsapp"] = {"enabled": False}
    at_the_eepos_step(harness)


def add_kid(url: str, name: str, student: str | None = None) -> Response:
    body = {"name": name} if student is None else \
        {"name": name, "school": "emo", "username": USERNAME, "id": student}
    return call(url, "api/eepos/kid", method="POST", body=body)


def test_a_student_found_is_added_as_a_kid_and_the_evening_puts_their_lessons_under_them(harness, page,
                                                                                         portal):
    an_eepos_only_household(harness)
    found = sign_in(page.url).json()["found"]
    assert [(p["id"], p["first"], p["kid"]) for p in found] == [("11", "Mia", None), ("12", "Leo", None)]

    out = add_kid(page.url, "Leevi", "12").json()

    assert out["result"] == "added" and out["signins"][0]["kids"] == ["Leevi"]
    config = saved_config(harness)
    assert config["kids"] == [{"name": "Leevi"}]
    assert config["eepos"]["signins"][0]["kids"] == {"12": "Leevi"}
    assert add_kid(page.url, "leevi", "11").json() == {"result": "kid-exists"}
    assert add_kid(page.url, "Mia", "99").status == 400  # not a student this sign-in found

    harness.config = saved_config(harness)
    harness.model_reply = {"per_kid": [], "calendar_events": [], "message_digest": "- Piano"}
    assert harness.run() == 0
    [email] = harness.sent
    _, ics, _ = email.attachment(".ics")
    assert "Parent Recap · Leevi]".encode() in ics.replace(b"\r\n ", b"")


def test_with_wilma_no_kid_is_added_on_eepos_s_step(harness, page, portal):
    portal.school("emo").students.append(eepos_student(13, "Olli", "Virtanen"))
    at_the_eepos_step(harness)
    sign_in(page.url)

    for r in (add_kid(page.url, "Olli", "13"), add_kid(page.url, "Olli")):
        assert r.status == 400 and r.json()["result"] == "invalid-answers"
    assert [k["name"] for k in saved_config(harness)["kids"]] == ["Mia", "Leo"]
