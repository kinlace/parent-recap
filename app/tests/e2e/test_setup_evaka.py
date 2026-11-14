"""Adding an eVaka sign-in with `parent-recap setup evaka` (ADR 0039, #768), `doctor`'s check of it
and uninstall: the password goes from the secret store's own prompt to one sign-in and the store,
never into the config, the command's output or a dialog; the children are matched to the Kids by
name. The outside edges are faked: the store's prompt (`secrets.typed`) and each city's eVaka.
The command's one line of JSON on stdout is what the maintainer reads. The setup page's eVaka step
is at the end, with the password typed into the page's own field (#791)."""
from __future__ import annotations

import json

import pytest
import yaml
from test_doctor import nothing_loaded  # noqa: F401  (a fixture)
from test_uninstall import set_up, uninstall

from conftest import evaka_child, evaka_event
from fake_sources import EVAKA_PASSWORD
from family_brief import install_record, ops
from family_brief.config import Config
from family_brief.sources.fi.evaka import collector as evaka
from setup_page import Response, assert_never_leaked, call, config_file, save_progress, source
from setup_page import clock, page  # noqa: F401

USERNAME = "parent@example.com"
SECRET = evaka.secret_name("espoo", USERNAME)
MIA, LEO = "1a2b3c4d-0000-4000-8000-000000000011", "1a2b3c4d-0000-4000-8000-000000000012"
OLLI = "1a2b3c4d-0000-4000-8000-000000000013"


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


# ── Connect: eVaka, on the setup page (#791), driven with HTTP calls as the page makes them
# (`setup_page.py`), against the same fake eVaka

def at_the_evaka_step(harness) -> None:
    config_file(harness).parent.mkdir(parents=True, exist_ok=True)
    config_file(harness).write_text(yaml.safe_dump(harness.config, allow_unicode=True))
    save_progress(harness, {"phase": "connect", "source": "evaka", "sources": {
        "wilma": "done", "gmail": "done", "ai": "done", "whatsapp": "done", "myclub": "skipped",
        "eepos": "skipped"}})
    harness.source_fakes["whatsapp"].set_up()  # WhatsApp done: paired


def sign_in(url: str, password: str = EVAKA_PASSWORD, city: str = "espoo",
            username: str = USERNAME) -> Response:
    return call(url, "api/evaka", method="POST",
                body={"city": city, "username": username, "password": password})


def test_the_step_lists_the_cities_on_evaka_by_their_own_names(harness, page, portal):
    at_the_evaka_step(harness)

    cities = call(page.url, "api/state").json()["evaka"]["cities"]

    assert {"name": "hameenkyro", "label": "Hämeenkyrö"} in cities
    assert {c["name"] for c in cities} == set(evaka.CITIES)


def test_a_sign_in_typed_on_the_page_is_checked_kept_in_the_store_and_listed(harness, page, portal,
                                                                            caplog, capsys):
    at_the_evaka_step(harness)

    r = sign_in(page.url)

    out = r.json()
    assert out["result"] == "saved" and out["events"] == 2
    assert out["found"] == [{"name": "Mia Emilia Virtanen", "kid": "Mia", "id": MIA, "first": "Mia"},
                            {"name": "Leo Virtanen", "kid": "Leo", "id": LEO, "first": "Leo"}]
    assert out["signins"] == [{"city": "espoo", "label": "Espoo", "address": "espoonvarhaiskasvatus.fi",
                               "username": USERNAME, "kids": ["Mia", "Leo"]}]
    assert out["progress"]["sources"]["evaka"] == "done"
    assert saved_config(harness)["evaka"] == {"signins": [{"city": "espoo", "username": USERNAME,
                                                           "kids": {MIA: "Mia", LEO: "Leo"}}]}
    assert harness.secrets[SECRET] == EVAKA_PASSWORD and evaka.TYPED not in harness.secrets
    assert_never_leaked(harness, [r, call(page.url, "api/state")], caplog, capsys, EVAKA_PASSWORD)

    assert call(page.url, "api/evaka/done", method="POST", body={}).json()["progress"]["source"] is None


def test_a_city_not_on_the_list_is_given_by_its_host(harness, page, portal):
    portal.city("evaka.example.fi").accounts[USERNAME] = EVAKA_PASSWORD
    portal.city("evaka.example.fi").children = [evaka_child(MIA, "Mia", "Virtanen")]
    at_the_evaka_step(harness)

    out = sign_in(page.url, city="https://evaka.example.fi/calendar").json()

    assert out["result"] == "saved"
    assert out["signins"][0]["city"] == out["signins"][0]["address"] == "evaka.example.fi"


def test_a_wrong_password_on_the_page_is_refused_and_keeps_the_one_that_works(harness, page, portal):
    harness.config["evaka"] = {"signins": [{"city": "espoo", "username": USERNAME}]}
    harness.secrets[SECRET] = EVAKA_PASSWORD
    at_the_evaka_step(harness)

    assert sign_in(page.url, "typo").json() == {"result": "wrong-password"}
    assert harness.secrets[SECRET] == EVAKA_PASSWORD
    assert saved_config(harness)["evaka"] == {"signins": [{"city": "espoo", "username": USERNAME}]}


def test_what_isn_t_a_city_is_refused_without_signing_in(harness, page, portal):
    at_the_evaka_step(harness)

    assert sign_in(page.url, city="not a city").json() == {"result": "not-a-city"}
    assert portal.requests == []


def test_a_child_who_is_none_of_the_kids_is_shown_not_guessed(harness, page, portal):
    portal.city("espoo").children.append(evaka_child(OLLI, "Olli", "Virtanen"))
    at_the_evaka_step(harness)

    assert sign_in(page.url).json()["found"][-1] == {"name": "Olli Virtanen", "kid": None, "id": OLLI,
                                                     "first": "Olli"}


def test_a_city_is_removed_with_its_password(harness, page, portal):
    at_the_evaka_step(harness)
    sign_in(page.url)

    r = call(page.url, "api/evaka/remove", method="POST", body={"city": "espoo", "username": USERNAME})

    assert r.json()["result"] == "removed" and r.json()["signins"] == []
    assert r.json()["progress"]["sources"]["evaka"] == "to-do"
    assert SECRET not in harness.secrets and saved_config(harness)["evaka"]["signins"] == []


def test_skipping_evaka_removes_its_sign_ins_and_passwords(harness, page, portal):
    harness.config["evaka"] = {"signins": [{"city": "espoo", "username": USERNAME}]}
    harness.secrets[SECRET] = EVAKA_PASSWORD
    at_the_evaka_step(harness)

    assert source(page.url, "evaka", "skip").json()["progress"]["sources"]["evaka"] == "skipped"
    assert saved_config(harness)["evaka"]["signins"] == [] and SECRET not in harness.secrets


# ── A Household without Wilma adds its Kids on eVaka's step (#796)

def an_evaka_only_household(harness) -> None:
    """A Household whose daycare's eVaka is its only Source of the Kids: no Wilma, no MyClub,
    no Kid yet, at eVaka's step."""
    harness.config["kids"] = []
    harness.config["wilma"] = {"enabled": False}
    harness.config["whatsapp"] = {"enabled": False}
    at_the_evaka_step(harness)


def add_kid(url: str, name: str, child: str | None = None, city: str = "espoo",
            username: str = USERNAME) -> Response:
    body = {"name": name} if child is None else {"name": name, "city": city, "username": username, "id": child}
    return call(url, "api/evaka/kid", method="POST", body=body)


def ics_of_the_evening(harness) -> bytes:
    """The evening run's .ics, its folded lines unfolded."""
    harness.config = saved_config(harness)
    harness.model_reply = {"per_kid": [], "calendar_events": [], "message_digest": "- A trip and a photo day"}
    assert harness.run() == 0
    [email] = harness.sent
    _, ics, _ = email.attachment(".ics")
    return ics.replace(b"\r\n ", b"")


def test_a_child_found_is_added_as_a_kid_and_the_evening_puts_their_events_under_them(harness, page, portal):
    harness.sources["evaka"].append(evaka_event("Kuvauspäivä", "2026-10-01", children=(MIA,)))
    an_evaka_only_household(harness)
    assert call(page.url, "api/state").json()["evaka"]["add"] is True
    found = sign_in(page.url).json()["found"]
    assert [(p["first"], p["kid"]) for p in found] == [("Mia", None), ("Leo", None)]

    # Leo by the name eVaka gives, Mia by one it doesn't.
    leo = add_kid(page.url, " Leo ", LEO).json()
    mia = add_kid(page.url, "Mimi", MIA).json()

    assert leo["result"] == mia["result"] == "added" and mia["kid"] == "Mimi"
    assert mia["signins"][0]["kids"] == ["Leo", "Mimi"]  # the step lists them at once
    assert call(page.url, "api/state").json()["evaka"]["signins"][0]["kids"] == ["Leo", "Mimi"]
    config = saved_config(harness)
    assert config["kids"] == [{"name": "Leo"}, {"name": "Mimi"}]
    assert config["evaka"]["signins"] == [{"city": "espoo", "username": USERNAME,
                                           "kids": {LEO: "Leo", MIA: "Mimi"}}]
    assert mia["progress"]["sources"]["evaka"] == "done"

    ics = ics_of_the_evening(harness)
    assert "Retki Nuuksioon".encode() in ics and "Parent Recap · Leo]".encode() in ics
    assert "Parent Recap · Mimi]".encode() in ics


def test_a_kid_added_by_name_alone_gets_the_events_of_the_child_with_that_name(harness, page, portal):
    an_evaka_only_household(harness)
    sign_in(page.url)

    out = add_kid(page.url, "Leo").json()

    assert out["result"] == "added" and saved_config(harness)["kids"] == [{"name": "Leo"}]
    # Matched by name each evening, so the sign-in names no Kid for the child.
    assert saved_config(harness)["evaka"]["signins"][0]["kids"] == {}
    assert "Parent Recap · Leo]".encode() in ics_of_the_evening(harness)


def test_a_kid_already_there_is_not_added_again(harness, page, portal):
    an_evaka_only_household(harness)
    sign_in(page.url)
    add_kid(page.url, "Leo", LEO)

    assert add_kid(page.url, "LEO").json() == {"result": "kid-exists"}
    assert add_kid(page.url, "leo", MIA).json() == {"result": "kid-exists"}
    config = saved_config(harness)
    assert config["kids"] == [{"name": "Leo"}]
    assert config["evaka"]["signins"][0]["kids"] == {LEO: "Leo"}


def test_with_wilma_the_kids_are_wilma_s_and_none_is_added_here(harness, page, portal):
    portal.city("espoo").children.append(evaka_child(OLLI, "Olli", "Virtanen"))
    at_the_evaka_step(harness)  # Wilma lists Mia and Leo
    sign_in(page.url)
    assert call(page.url, "api/state").json()["evaka"]["add"] is False

    for r in (add_kid(page.url, "Olli", OLLI), add_kid(page.url, "Olli")):
        assert r.status == 400 and r.json()["result"] == "invalid-answers"
    config = saved_config(harness)
    assert [k["name"] for k in config["kids"]] == ["Mia", "Leo"]
    assert config["evaka"]["signins"][0]["kids"] == {MIA: "Mia", LEO: "Leo"}


@pytest.mark.parametrize("change", [
    {"id": "1a2b3c4d-0000-4000-8000-000000000099"},  # not a child this sign-in found
    {"username": "someone@example.com"},
    {"city": "tampere"},
    {"city": "not a city"},
    {"id": None},
    {"name": " "},
], ids=["unknown-child", "another-username", "another-city", "not-a-city", "no-id", "no-name"])
def test_a_kid_is_added_only_for_a_child_a_sign_in_just_found(harness, page, portal, change):
    an_evaka_only_household(harness)
    sign_in(page.url)
    body = {"name": "Leo", "city": "espoo", "username": USERNAME, "id": LEO, **change}

    r = call(page.url, "api/evaka/kid", method="POST", body={k: v for k, v in body.items() if v is not None})

    assert r.status == 400 and r.json()["result"] == "invalid-answers"
    assert saved_config(harness)["kids"] == []
