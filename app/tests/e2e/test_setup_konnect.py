"""Adding a Konnect sign-in with `parent-recap setup konnect` (ADR 0049, #905), `doctor`'s check
of it and uninstall: the password goes from the secret store's own prompt to one sign-in and the
store, never into the config, the command's output or a dialog; the children are matched to the
Kids by name; a portal that wants a new password says what to do. The outside edges are faked:
the store's prompt (`secrets.typed`) and each provider's parent portal. Konnect has no step on
the setup page. All names are made up."""
from __future__ import annotations

import json

import pytest
import yaml
from test_doctor import nothing_loaded  # noqa: F401  (a fixture)
from test_uninstall import set_up, uninstall

from conftest import konnect_child, konnect_conversation, konnect_planned
from fake_sources import KONNECT_PASSWORD
from family_brief import ops
from family_brief.collectors import konnect
from setup_page import PAGE_DIR, call, source
from setup_page import clock, page  # noqa: F401

USERNAME = "parent@example.nl"
SECRET = konnect.secret_name("mijnbso", USERNAME)
MIA, LEO = "c0ffee0000000000000000000000a001", "c0ffee0000000000000000000000a002"


@pytest.fixture
def portal(harness):
    """A BSO's parent portal, mijnbso.ouderportaal.nl, where the parent's password is
    KONNECT_PASSWORD and Mia and Leo go after school."""
    fake = harness.source_fakes["konnect"]
    there = fake.portal("mijnbso")
    there.accounts[USERNAME] = KONNECT_PASSWORD
    there.children = [konnect_child(MIA, "Mia", "Testdochter"), konnect_child(LEO, "Leo", "Testzoon")]
    there.conversations = [konnect_conversation("Uitje", "<p>Morgen naar het bos.</p>",
                                                "2026-09-27T16:00:00+02:00", children=(there.children[0],))]
    harness.sources = {"konnect": [
        konnect_planned(MIA, "2026-09-28T14:00:00+02:00", "2026-09-28T18:30:00+02:00"),
        konnect_planned(LEO, "2026-09-29T14:00:00+02:00", "2026-09-29T18:30:00+02:00")]}
    return fake


def setup_konnect(harness, capsys, *args: str, typed: str | None = KONNECT_PASSWORD) -> tuple[int, dict]:
    harness.secrets.typed = typed
    code = harness.cli("setup", "konnect", *args)
    captured = capsys.readouterr()
    [line] = captured.out.splitlines()
    assert typed is None or typed not in captured.out + captured.err
    return code, json.loads(line)


def saved_config(harness) -> dict:
    return yaml.safe_load((harness.home / ".family" / "config.yaml").read_text())


def test_a_sign_in_typed_at_the_store_s_prompt_is_checked_matched_and_saved(harness, portal, capsys):
    code, out = setup_konnect(harness, capsys, "--portal", "https://mijnbso.ouderportaal.nl/parent/",
                              "--username", USERNAME)

    assert code == 0
    assert out == {"result": "saved", "portal": "mijnbso.ouderportaal.nl", "events": 2,
                   "children": [{"name": "Mia Testdochter", "kid": "Mia"}, {"name": "Leo Testzoon", "kid": "Leo"}]}
    assert saved_config(harness)["konnect"] == {"signins": [{"portal": "mijnbso", "username": USERNAME,
                                                             "kids": {MIA: "Mia", LEO: "Leo"}}]}
    assert harness.secrets[SECRET] == KONNECT_PASSWORD and konnect.TYPED not in harness.secrets
    # Nothing of the children but their ids and names is kept.
    assert "Zonnebloem" not in (harness.home / ".family" / "config.yaml").read_text()
    assert not [p for p in portal.paths() if "timeline" in p]


def test_a_wrong_password_saves_nothing_and_says_how_to_try_again(harness, portal, capsys):
    code, out = setup_konnect(harness, capsys, "--portal", "mijnbso", "--username", USERNAME, typed="typo")

    assert code == 1 and out["result"] == "wrong-password"
    assert "setup konnect --portal mijnbso --username" in out["next"]
    assert "konnect" not in saved_config(harness) and SECRET not in harness.secrets


def test_a_portal_that_wants_a_new_password_says_to_set_it_in_a_browser_first(harness, portal, capsys):
    portal.portal("mijnbso").must_reset = {USERNAME}

    code, out = setup_konnect(harness, capsys, "--portal", "mijnbso", "--username", USERNAME)

    assert code == 1 and out["result"] == "must-reset-password"
    assert "https://mijnbso.ouderportaal.nl" in out["next"] and "new password" in out["next"]
    assert "konnect" not in saved_config(harness) and SECRET not in harness.secrets


@pytest.mark.parametrize("given, result", [("www.example-kinderopvang.nl", "portal-website"),
                                           ("mijn bso", "not-a-portal")])
def test_what_isn_t_a_portal_is_refused_before_a_password_is_asked(harness, portal, capsys, given, result):
    code, out = setup_konnect(harness, capsys, "--portal", given, "--username", USERNAME, typed=None)

    assert code == 1 and out["result"] == result and portal.requests == []


def test_doctor_signs_in_to_each_portal_and_names_its_children(harness, nothing_loaded, portal,  # noqa: F811
                                                               capsys):
    harness.config["konnect"] = {"signins": [{"portal": "mijnbso", "username": USERNAME}]}
    harness.secrets[SECRET] = KONNECT_PASSWORD

    harness.cli("doctor", "--skip-llm")

    lines = capsys.readouterr().out.splitlines()
    assert (f"{ops.OK} Konnect (mijnbso.ouderportaal.nl): signed in as {USERNAME}, 2 planned times in "
            "the next 14 days, 1 message in the last day; children: Mia Testdochter (Mia), "
            "Leo Testzoon (Leo)") in lines


def test_doctor_says_what_to_do_when_the_portal_wants_a_new_password(harness, nothing_loaded, portal,  # noqa: F811
                                                                     capsys):
    harness.config["konnect"] = {"signins": [{"portal": "mijnbso", "username": USERNAME}]}
    harness.secrets[SECRET] = KONNECT_PASSWORD
    portal.portal("mijnbso").must_reset = {USERNAME}

    assert harness.cli("doctor", "--skip-llm") == 1

    [line] = [l for l in capsys.readouterr().out.splitlines() if "Konnect" in l]
    assert line.startswith(f"{ops.FAIL} Konnect (mijnbso.ouderportaal.nl): the sign-in as {USERNAME} needs "
                           "a new password: Sign in at https://mijnbso.ouderportaal.nl in a browser once "
                           "and set the new password it asks for, then type it again: ")
    assert line.endswith(f"setup konnect --portal mijnbso --username {USERNAME}")


def test_doctor_says_how_to_type_a_password_konnect_no_longer_takes(harness, nothing_loaded, portal,  # noqa: F811
                                                                    capsys):
    harness.config["konnect"] = {"signins": [{"portal": "mijnbso", "username": USERNAME}]}
    harness.secrets[SECRET] = "old"

    assert harness.cli("doctor", "--skip-llm") == 1

    [line] = [l for l in capsys.readouterr().out.splitlines() if "Konnect" in l]
    assert line.startswith(f"{ops.FAIL} Konnect (mijnbso.ouderportaal.nl): the sign-in as {USERNAME} "
                           "doesn't work: login failed at mijnbso.ouderportaal.nl (type the password again: ")


def test_uninstall_deletes_each_konnect_password(harness, portal, monkeypatch, capsys):
    set_up(harness, monkeypatch)
    harness.config["konnect"] = {"signins": [{"portal": "mijnbso", "username": USERNAME}]}
    harness.secrets[SECRET] = KONNECT_PASSWORD
    harness.cli("setup", "status")  # writes the config with its Konnect sign-in
    capsys.readouterr()

    assert uninstall(harness) == 0
    assert SECRET in capsys.readouterr().out

    assert uninstall(harness, "--confirm", "--remove-archive") == 0
    assert SECRET not in harness.secrets


# ── The setup page has no Konnect step (#905): a family elsewhere doesn't see one

def test_the_setup_page_offers_no_konnect_step(harness, page):
    listed = [s["name"] for s in call(page.url, "api/state").json()["connect"]["sources"]]

    assert "konnect" not in listed and listed[-1] == "gmail"
    assert source(page.url, "konnect", "skip").status == 400
    assert "konnect" not in (PAGE_DIR / "index.html").read_text()


def test_doctor_s_konnect_check_is_named_on_the_check_page_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        for key in ("health.check.konnect", "health.fail.konnect", "finish.fail.konnect"):
            assert table.get(key, "").strip(), (language, key)
        assert "{portal}" in table["health.check.konnect"], language
