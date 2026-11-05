"""Adding an eVaka sign-in with `parent-recap setup evaka` (ADR 0039, #768), `doctor`'s check of it
and uninstall: the password goes from the secret store's own prompt to one sign-in and the store,
never into the config, the command's output or a dialog; the children are matched to the Kids by
name. The outside edges are faked: the store's prompt (`secrets.typed`) and each city's eVaka.
The command's one line of JSON on stdout is what the maintainer reads."""
from __future__ import annotations

import json

import pytest
import yaml
from test_doctor import nothing_loaded  # noqa: F401  (a fixture)
from test_uninstall import set_up, uninstall

from conftest import evaka_child, evaka_event
from fake_sources import EVAKA_PASSWORD
from family_brief import install_record, ops
from family_brief.collectors import evaka
from family_brief.config import Config

USERNAME = "parent@example.com"
SECRET = evaka.secret_name("espoo", USERNAME)
MIA, LEO = "1a2b3c4d-0000-4000-8000-000000000011", "1a2b3c4d-0000-4000-8000-000000000012"


@pytest.fixture
def portal(harness):
    """Espoo's eVaka, where the guardian's password is EVAKA_PASSWORD and Mia and Leo are in daycare."""
    fake = harness.source_fakes["evaka"]
    city = fake.city("espoo")
    city.accounts[USERNAME] = EVAKA_PASSWORD
    city.children = [evaka_child(MIA, "Mia Emilia", "Virtanen"), evaka_child(LEO, "Leo", "Virtanen")]
    harness.sources = {"evaka": [evaka_event("Retki Nuuksioon", "2026-09-28", children=(LEO,))]}
    return fake


def setup_evaka(harness, capsys, *args: str, typed: str | None = EVAKA_PASSWORD) -> tuple[int, dict]:
    harness.secrets.typed = typed
    code = harness.cli("setup", "evaka", *args)
    captured = capsys.readouterr()
    [line] = captured.out.splitlines()
    assert typed is None or typed not in captured.out + captured.err
    return code, json.loads(line)


def saved_config(harness) -> dict:
    return yaml.safe_load((harness.home / ".family" / "config.yaml").read_text())


def test_a_sign_in_typed_at_the_store_s_prompt_is_checked_matched_and_saved(harness, portal, capsys):
    code, out = setup_evaka(harness, capsys, "--city", "https://espoonvarhaiskasvatus.fi/calendar",
                            "--username", USERNAME)

    assert code == 0
    assert out == {"result": "saved", "city": "espoonvarhaiskasvatus.fi", "events": 2,
                   "children": [{"name": "Mia Emilia Virtanen", "kid": "Mia"}, {"name": "Leo Virtanen", "kid": "Leo"}]}
    config = saved_config(harness)
    assert config["evaka"] == {"signins": [{"city": "espoo", "username": USERNAME,
                                            "kids": {MIA: "Mia", LEO: "Leo"}}]}
    assert EVAKA_PASSWORD not in json.dumps(config)
    assert harness.secrets[SECRET] == EVAKA_PASSWORD and evaka.TYPED not in harness.secrets
    assert SECRET in install_record.entries("keychain")
    # The device cookie it got is the one the first evening sends.
    devices = evaka.devices_path(Config.model_validate(config))
    kept = json.loads(devices.read_text())["espoonvarhaiskasvatus.fi"]

    # The next evening reads it.
    harness.config = config
    portal.requests.clear()
    harness.model_reply = {"per_kid": [], "calendar_events": [], "message_digest": "- Leo has a trip tomorrow"}
    assert harness.run() == 0
    assert portal.requests[0]["devices"] == kept
    [email] = harness.sent
    assert "eVaka 2 个日程" in email.text  # the default config's Brief is in Chinese


def test_a_wrong_password_saves_nothing_and_keeps_the_one_that_works(harness, portal, capsys):
    harness.secrets[SECRET] = EVAKA_PASSWORD  # added before

    code, out = setup_evaka(harness, capsys, "--city", "espoo", "--username", USERNAME, typed="typo")

    assert code == 1 and out["result"] == "wrong-password"
    assert "Omat tiedot" in out["next"] and "setup evaka --city espoo --username" in out["next"]
    assert "evaka" not in saved_config(harness)
    assert harness.secrets[SECRET] == EVAKA_PASSWORD and evaka.TYPED not in harness.secrets


def test_nothing_typed_at_the_prompt_saves_nothing(harness, portal, capsys):
    code, out = setup_evaka(harness, capsys, "--city", "espoo", "--username", USERNAME, typed=None)

    assert code == 1 and out["result"] == "not-stored"
    assert portal.requests == []


@pytest.mark.parametrize("args, result", [
    (("--city", "helsinki", "--username", USERNAME), "not-a-city"),
    (("--city", "espoo", "--username", " "), "no-username"),
], ids=["not-evaka", "no-username"])
def test_what_isn_t_a_sign_in_is_refused_before_the_password_is_asked(harness, portal, capsys, args, result):
    code, out = setup_evaka(harness, capsys, *args)

    assert code == 1 and out["result"] == result
    assert harness.secrets.typed == EVAKA_PASSWORD and evaka.TYPED not in harness.secrets
    assert portal.requests == []


def test_a_keychain_tmux_or_ssh_can_t_reach_is_said_before_the_password_is_asked(harness, portal, capsys):
    harness.secrets.reachable_here = False  # macOS over SSH or in tmux

    code, out = setup_evaka(harness, capsys, "--city", "espoo", "--username", USERNAME)

    assert code == 1 and out["result"] == "keychain-not-reachable"
    assert "tmux or SSH" in out["next"] and "setup evaka --city espoo --username" in out["next"]
    assert harness.secrets.typed == EVAKA_PASSWORD and evaka.TYPED not in harness.secrets
    assert portal.requests == [] and "evaka" not in saved_config(harness)


def test_an_api_that_changed_saves_nothing_and_says_so(harness, portal, capsys):
    portal.answers[("espoo", "children")] = (200, "<html>eVaka</html>")

    code, out = setup_evaka(harness, capsys, "--city", "espoo", "--username", USERNAME)

    assert code == 1 and out["result"] == "changed" and "tell the maintainers" in out["next"]
    assert SECRET not in harness.secrets


def test_a_child_who_is_none_of_the_kids_is_saved_and_named(harness, portal, capsys):
    portal.city("espoo").children.append(evaka_child("1a2b3c4d-0000-4000-8000-000000000013", "Olli", "Virtanen"))

    code, out = setup_evaka(harness, capsys, "--city", "espoo", "--username", USERNAME)

    assert code == 0 and out["result"] == "saved"
    assert out["next"].startswith("Olli Virtanen matched none of the Kids, so their events go to the whole Household")
    assert saved_config(harness)["evaka"]["signins"][0]["kids"] == {MIA: "Mia", LEO: "Leo"}


def test_the_same_sign_in_again_replaces_it_and_another_city_is_added(harness, portal, capsys):
    portal.city("tampere").accounts[USERNAME] = EVAKA_PASSWORD
    portal.city("tampere").children = [evaka_child("t-31", "Mia", "Virtanen")]

    for city in ("espoo", "tampere", "espoo"):
        assert setup_evaka(harness, capsys, "--city", city, "--username", USERNAME)[0] == 0
        harness.config = saved_config(harness)  # which the next command's config is written from

    signins = saved_config(harness)["evaka"]["signins"]
    assert [(s["city"], s["kids"]) for s in signins] == [("tampere", {"t-31": "Mia"}),
                                                         ("espoo", {MIA: "Mia", LEO: "Leo"})]
    assert evaka.secret_name("tampere", USERNAME) in harness.secrets


def test_doctor_signs_in_to_each_city_and_names_its_children(harness, nothing_loaded, portal,  # noqa: F811
                                                             capsys):
    harness.config["evaka"] = {"signins": [{"city": "espoo", "username": USERNAME}]}
    harness.secrets[SECRET] = EVAKA_PASSWORD

    harness.cli("doctor", "--skip-llm")

    lines = capsys.readouterr().out.splitlines()
    assert (f"{ops.OK} eVaka (espoonvarhaiskasvatus.fi): signed in as {USERNAME}, 2 events in the next "
            "28 days, messages readable; children: Mia Emilia Virtanen (Mia), Leo Virtanen (Leo)") in lines


def test_doctor_warns_when_the_messages_can_t_be_read_while_the_rest_can(harness, nothing_loaded, portal,  # noqa: F811
                                                                         capsys):
    harness.config["evaka"] = {"signins": [{"city": "espoo", "username": USERNAME}]}
    harness.secrets[SECRET] = EVAKA_PASSWORD
    portal.answers[("espoo", "messages/received")] = (404, "Not Found")

    harness.cli("doctor", "--skip-llm")

    [line] = [l for l in capsys.readouterr().out.splitlines() if "eVaka" in l]
    assert line == (f"{ops.WARN} eVaka (espoonvarhaiskasvatus.fi): signed in as {USERNAME}, 2 events in the "
                    "next 28 days, no messages; children: Mia Emilia Virtanen (Mia), Leo Virtanen (Leo). The "
                    "messages couldn't be read: espoonvarhaiskasvatus.fi has no messages where it was (HTTP 404)")


def test_doctor_says_how_to_type_a_password_evaka_no_longer_takes(harness, nothing_loaded, portal,  # noqa: F811
                                                                  capsys):
    harness.config["evaka"] = {"signins": [{"city": "espoo", "username": USERNAME}]}
    harness.secrets[SECRET] = "old"

    assert harness.cli("doctor", "--skip-llm") == 1

    [line] = [l for l in capsys.readouterr().out.splitlines() if "eVaka" in l]
    assert line.startswith(f"{ops.FAIL} eVaka (espoonvarhaiskasvatus.fi): the sign-in as {USERNAME} doesn't "
                           "work: login failed at espoonvarhaiskasvatus.fi (type the password again: ")
    assert line.endswith(f"setup evaka --city espoo --username {USERNAME})")


def test_uninstall_deletes_each_evaka_password_and_the_device_cookies(harness, portal, monkeypatch, capsys):
    set_up(harness, monkeypatch)
    harness.config["evaka"] = {"signins": [{"city": "espoo", "username": USERNAME}]}
    harness.secrets[SECRET] = EVAKA_PASSWORD
    harness.cli("setup", "status")  # writes the config with its eVaka sign-in
    assert harness.cli("doctor", "--skip-llm") in (0, 1)  # signs in, which keeps a device cookie
    devices = evaka.devices_path(Config.model_validate(saved_config(harness)))
    assert devices.exists()
    capsys.readouterr()

    assert uninstall(harness) == 0
    out = capsys.readouterr().out
    assert SECRET in out and "eVaka's device cookies" in out

    assert uninstall(harness, "--confirm", "--remove-archive") == 0
    assert SECRET not in harness.secrets and not devices.exists()
