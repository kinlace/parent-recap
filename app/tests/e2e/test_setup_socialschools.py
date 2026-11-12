"""Adding a Social Schools sign-in with `parent-recap setup socialschools` (ADR 0049, #904),
`doctor`'s check of it and uninstall: the password goes from the secret store's own prompt to one
sign-in and the store, never into the config, the command's output or a dialog; the children are
matched to the Kids by name; an account that signs in with Google, Microsoft or Apple, or asks for
a further step, is told so and nothing is saved. The outside edges are faked: the store's prompt
(`secrets.typed`) and Social Schools. The command's one line of JSON on stdout is what the
assistant reads."""
from __future__ import annotations

import json

import pytest
import yaml
from test_doctor import nothing_loaded  # noqa: F401  (a fixture)
from test_uninstall import set_up, uninstall

from conftest import socialschools_child, socialschools_event
from fake_sources import SOCIALSCHOOLS_PASSWORD
from family_brief import install_record, ops
from family_brief.collectors import socialschools

USERNAME = "parent@example.nl"
SECRET = socialschools.secret_name(USERNAME)


@pytest.fixture
def portal(harness):
    """Social Schools, where the parent's password is SOCIALSCHOOLS_PASSWORD and Mia and Leo are
    at school 7."""
    fake = harness.source_fakes["socialschools"]
    fake.accounts[USERNAME] = SOCIALSCHOOLS_PASSWORD
    fake.school(7).children = [socialschools_child("Mia", "Mia Virtanen", ("Groep 5",)),
                               socialschools_child("Leo", "Leo Virtanen", ("Groep 3",))]
    harness.sources = {"socialschools": [socialschools_event(1, "Studiedag", "2026-09-30T00:00:00Z",
                                                             "2026-10-01T00:00:00Z", all_day=True)]}
    return fake


def setup_socialschools(harness, capsys, *args: str, typed: str | None = SOCIALSCHOOLS_PASSWORD) -> tuple[int, dict]:
    harness.secrets.typed = typed
    code = harness.cli("setup", "socialschools", *args)
    captured = capsys.readouterr()
    [line] = captured.out.splitlines()
    assert typed is None or typed not in captured.out + captured.err
    return code, json.loads(line)


def saved_config(harness) -> dict:
    return yaml.safe_load((harness.home / ".family" / "config.yaml").read_text())


def test_a_sign_in_typed_at_the_store_s_prompt_is_checked_matched_and_saved(harness, portal, capsys):
    code, out = setup_socialschools(harness, capsys, "--username", USERNAME)

    assert code == 0
    assert out == {"result": "saved", "schools": ["Basisschool De Kilo"], "events": 1,
                   "children": [{"name": "Mia Virtanen", "kid": "Mia"}, {"name": "Leo Virtanen", "kid": "Leo"}]}
    config = saved_config(harness)
    assert config["socialschools"] == {"signins": [{"username": USERNAME,
                                                    "kids": {"Mia Virtanen": "Mia", "Leo Virtanen": "Leo"}}]}
    assert SOCIALSCHOOLS_PASSWORD not in json.dumps(config)
    assert harness.secrets[SECRET] == SOCIALSCHOOLS_PASSWORD and socialschools.TYPED not in harness.secrets
    assert SECRET in install_record.entries("keychain")
    assert {r["method"] for r in portal.requests if r["host"] == socialschools.API_HOST} == {"GET"}

    # The next evening reads it.
    harness.config = config
    harness.model_reply = {"per_kid": [], "calendar_events": [], "message_digest": "- No school on Wednesday"}
    assert harness.run() == 0
    [email] = harness.sent
    assert "Social Schools 1 个日程" in email.text  # the default config's Brief is in Chinese


def test_a_wrong_password_saves_nothing_keeps_the_one_that_works_and_names_other_sign_ins(harness, portal,
                                                                                          capsys):
    harness.secrets[SECRET] = SOCIALSCHOOLS_PASSWORD  # added before

    code, out = setup_socialschools(harness, capsys, "--username", USERNAME, typed="typo")

    assert code == 1 and out["result"] == "wrong-password"
    assert "Google, Microsoft or Apple" in out["next"] and "setup socialschools --username" in out["next"]
    assert "socialschools" not in saved_config(harness)
    assert harness.secrets[SECRET] == SOCIALSCHOOLS_PASSWORD and socialschools.TYPED not in harness.secrets


@pytest.mark.parametrize("way", ["two-step", "external"])
def test_an_account_that_signs_in_another_way_is_told_why_and_nothing_is_saved(harness, portal, capsys, way):
    portal.ways[USERNAME] = way

    code, out = setup_socialschools(harness, capsys, "--username", USERNAME)

    assert code == 1 and out["result"] == "other-sign-in"
    assert "can only sign in to Social Schools with an email and password" in out["next"]
    assert "socialschools" not in saved_config(harness) and SECRET not in harness.secrets


def test_nothing_typed_at_the_prompt_saves_nothing(harness, portal, capsys):
    code, out = setup_socialschools(harness, capsys, "--username", USERNAME, typed=None)

    assert code == 1 and out["result"] == "not-stored"
    assert portal.requests == []


def test_no_username_is_refused_before_the_password_is_asked(harness, portal, capsys):
    code, out = setup_socialschools(harness, capsys, "--username", " ")

    assert code == 1 and out["result"] == "no-username"
    assert harness.secrets.typed == SOCIALSCHOOLS_PASSWORD and portal.requests == []


def test_a_keychain_tmux_or_ssh_can_t_reach_is_said_before_the_password_is_asked(harness, portal, capsys):
    harness.secrets.reachable_here = False  # macOS over SSH or in tmux

    code, out = setup_socialschools(harness, capsys, "--username", USERNAME)

    assert code == 1 and out["result"] == "keychain-not-reachable"
    assert "tmux or SSH" in out["next"] and portal.requests == []


def test_an_api_that_changed_saves_nothing_and_says_so(harness, portal, capsys):
    portal.answers["useraccounts/current"] = (200, "<html>Social Schools</html>")

    code, out = setup_socialschools(harness, capsys, "--username", USERNAME)

    assert code == 1 and out["result"] == "changed" and "tell the maintainers" in out["next"]
    assert SECRET not in harness.secrets


def test_a_child_who_is_none_of_the_kids_is_named(harness, portal, capsys):
    portal.school(7).children.append(socialschools_child("Olli", "Olli Virtanen", ("Groep 1",)))

    code, out = setup_socialschools(harness, capsys, "--username", USERNAME)

    assert code == 0 and out["next"].startswith("Olli Virtanen matched none of the Kids")
    assert saved_config(harness)["socialschools"]["signins"][0]["kids"] == {"Mia Virtanen": "Mia",
                                                                            "Leo Virtanen": "Leo"}


def test_the_same_sign_in_again_replaces_it(harness, portal, capsys):
    for _ in range(2):
        assert setup_socialschools(harness, capsys, "--username", USERNAME)[0] == 0
        harness.config = saved_config(harness)

    assert len(saved_config(harness)["socialschools"]["signins"]) == 1


def test_doctor_signs_in_and_names_the_children(harness, nothing_loaded, portal, capsys):  # noqa: F811
    harness.config["socialschools"] = {"signins": [{"username": USERNAME}]}
    harness.secrets[SECRET] = SOCIALSCHOOLS_PASSWORD

    harness.cli("doctor", "--skip-llm")

    lines = capsys.readouterr().out.splitlines()
    assert (f"{ops.OK} Social Schools: signed in as {USERNAME}, 1 school, 1 events in the next 28 days, "
            "announcements readable; children: Mia Virtanen (Mia), Leo Virtanen (Leo)") in lines


def test_doctor_says_how_to_type_a_password_social_schools_no_longer_takes(harness, nothing_loaded, portal,  # noqa: F811
                                                                           capsys):
    harness.config["socialschools"] = {"signins": [{"username": USERNAME}]}
    harness.secrets[SECRET] = "old"

    assert harness.cli("doctor", "--skip-llm") == 1

    [line] = [l for l in capsys.readouterr().out.splitlines() if "Social Schools" in l]
    assert line.startswith(f"{ops.FAIL} Social Schools: the sign-in as {USERNAME} doesn't work: login failed")
    assert "Google, Microsoft or Apple" in line
    assert line.endswith(f"setup socialschools --username {USERNAME})")


def test_doctor_says_an_account_with_a_further_step_can_t_be_read(harness, nothing_loaded, portal,  # noqa: F811
                                                                  capsys):
    harness.config["socialschools"] = {"signins": [{"username": USERNAME}]}
    harness.secrets[SECRET] = SOCIALSCHOOLS_PASSWORD
    portal.ways[USERNAME] = "two-step"

    assert harness.cli("doctor", "--skip-llm") == 1

    [line] = [l for l in capsys.readouterr().out.splitlines() if "Social Schools" in l]
    assert line.startswith(f"{ops.FAIL} Social Schools: the sign-in as {USERNAME} can't be done")
    assert "further step after the password" in line and SOCIALSCHOOLS_PASSWORD not in line


def test_uninstall_deletes_the_social_schools_password(harness, portal, monkeypatch, capsys):
    set_up(harness, monkeypatch)
    harness.config["socialschools"] = {"signins": [{"username": USERNAME}]}
    harness.secrets[SECRET] = SOCIALSCHOOLS_PASSWORD
    harness.cli("setup", "status")  # writes the config with its sign-in
    capsys.readouterr()

    assert uninstall(harness) == 0
    assert SECRET in capsys.readouterr().out

    assert uninstall(harness, "--confirm", "--remove-archive") == 0
    assert SECRET not in harness.secrets
