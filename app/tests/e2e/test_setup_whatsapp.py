"""WhatsApp's connect step on the setup page, over HTTP: pairing the Linked Device (ADR 0025,
ADR 0027) with a QR code, the same on macOS and Windows. The server runs the Node program's
`pair` mode for real, on a real Node and the fake Baileys package (`fake_baileys/`) in the
family's Baileys folder, which plays the QR strings, the scan and the groups the test writes."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

import fake_node
from conftest import SAYS_SOMETHING, msg
from family_brief import baileys_install, linked_device, own_node, setup_server
from family_brief.config import Config
from family_brief.system import jobs
from setup_page import (call, check_page, config_file, confirm, health_result, more_progress,
                        save_progress, statuses, wait_for)
from setup_page import clock, health, page  # noqa: F401

NODE = own_node.node() or shutil.which("node")
ME = {"id": "358401234567:3@s.whatsapp.net", "name": "Mia's parent"}
SCANNED = [{"creds": {"me": ME}}, {"keys": {"pre-key": {"1": {"public": "p1"}}}}, {"open": True}]
CLASS, FOOTBALL = "120363@g.us", "120999@g.us"
GROUPS = {CLASS: {"id": CLASS, "subject": "Luokka 3B"},
          FOOTBALL: {"id": FOOTBALL, "subject": "Futis P2017"}}


class Phone:
    """The family's phone and WhatsApp, as the fake Baileys plays them to the pairing: what each
    socket the program opens does, and the steps it waits on until the test lets them go."""

    def __init__(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.folder = home / "fake-whatsapp"
        self.folder.mkdir()
        self.scenario, self.calls = self.folder / "scenario.json", self.folder / "calls.jsonl"
        self.calls.write_text("", encoding="utf-8")
        monkeypatch.setenv("FAKE_BAILEYS_SCENARIO", str(self.scenario))
        monkeypatch.setenv("FAKE_BAILEYS_CALLS", str(self.calls))
        self.plays([])

    def plays(self, *sockets: list[dict[str, Any]]) -> None:
        self.scenario.write_text(json.dumps({"sockets": list(sockets), "groups": GROUPS}),
                                 encoding="utf-8")

    def go(self, name: str) -> None:
        """Lets the fake's {"wait": name} step go on: the code changes, or the phone scans it."""
        (self.folder / name).touch()


@pytest.fixture
def phone(harness, monkeypatch) -> Phone:
    """Parent Recap's own Node, a real one, and the Baileys tree as the install leaves it, with
    the fake Baileys in it."""
    if NODE is None:
        if os.environ.get("CI"):
            pytest.fail("CI has no Node to run the Linked Device's program")
        pytest.skip("no Node: neither Parent Recap's own nor one on PATH")
    pinned = fake_node.pinned_node(harness.home)
    pinned.unlink()
    pinned.symlink_to(NODE)
    fake_node.install_fake_baileys(baileys_install.folder())
    return Phone(harness.home, monkeypatch)


def at_the_whatsapp_step(harness) -> None:
    """Wilma, Gmail and the AI done, with Wilma's Kids, and WhatsApp next."""
    config_file(harness).parent.mkdir(parents=True, exist_ok=True)
    harness.config["whatsapp"] = {"enabled": False, "chats": []}
    config_file(harness).write_text(yaml.safe_dump(harness.config, allow_unicode=True))
    save_progress(harness, {"phase": "connect", "source": "whatsapp", "sources": {
        "wilma": "done", "gmail": "done", "ai": "done"}})


def pair(url: str) -> dict[str, Any]:
    return call(url, "api/whatsapp/pair", method="POST", body={}).json()


def said(url: str) -> dict[str, Any]:
    return call(url, "api/whatsapp/check", method="POST", body={}).json()


def until_said(url: str, result: str, **more: Any) -> dict[str, Any]:
    """What the pairing says once it says `result` (with `more`), asked as the page asks."""
    out: dict[str, Any] = {}

    def there() -> bool:
        out.clear()
        out.update(said(url))
        return out["result"] == result and all(out.get(k) == v for k, v in more.items())
    wait_for(there, 20)
    return out


def test_pairing_shows_each_qr_code_then_keeps_the_groups_with_ids_and_kid_hints(
        harness, page, phone):
    at_the_whatsapp_step(harness)
    phone.plays([{"qr": "2@first"}, {"wait": "next"}, {"qr": "2@second"}, {"wait": "scan"},
                 *SCANNED])

    assert pair(page.url)["result"] in ("starting", "qr")
    assert until_said(page.url, "qr", qr="2@first") == {"result": "qr", "qr": "2@first"}
    assert said(page.url)["qr"] == "2@first"  # until WhatsApp changes it
    phone.go("next")
    until_said(page.url, "qr", qr="2@second")
    assert pair(page.url) == {"result": "qr", "qr": "2@second"}  # one pairing, not a second
    phone.go("scan")
    out = until_said(page.url, "paired")

    progress = out["progress"]
    assert progress["sources"]["whatsapp"] == "done" and progress["source"] == "myclub"
    assert progress["whatsapp_chats"] == [
        {"name": "Futis P2017", "id": FOOTBALL},
        {"name": "Luokka 3B", "id": CLASS, "hint": {"kids": ["Mia"], "matched": ["3B"]}}]
    opened = [json.loads(line) for line in phone.calls.read_text(encoding="utf-8").splitlines()
              if json.loads(line)["call"] == "makeWASocket"]
    assert [o["options"]["browser"][0] for o in opened] == ["Parent Recap"]
    assert (linked_device.auth_folder() / "creds.json").is_file()


def test_pairing_installs_the_always_running_job_that_follows_the_picked_groups(
        harness, page, phone):
    at_the_whatsapp_step(harness)
    phone.plays([{"qr": "2@first"}, *SCANNED])

    pair(page.url)
    until_said(page.url, "paired")

    logs = Config.load(config_file(harness)).archive.resolved_dir() / "logs"
    program = harness.jobs.program(jobs.ALWAYS)
    assert program[:3] == [str(own_node.node()), str(linked_device.PROGRAM), "follow"]
    options = dict(zip(program[3::2], program[4::2]))
    assert options == {"--modules": str(baileys_install.folder()),
                       "--auth": str(linked_device.auth_folder()),
                       "--log": str(logs / linked_device.LOG),
                       "--picked": str(linked_device.picked_file()),
                       "--store": str(linked_device.store()),
                       "--status": str(linked_device.status_file())}
    assert harness.jobs.always_log_dir == logs
    assert harness.jobs.always_running
    assert json.loads(linked_device.picked_file().read_text(encoding="utf-8")) == {"groups": []}


def test_the_check_page_lists_the_paired_groups_ticked_by_hint_and_saves_names_and_ids(
        harness, page, phone, health):
    at_the_whatsapp_step(harness)
    phone.plays([{"qr": "2@first"}, *SCANNED])
    pair(page.url)
    until_said(page.url, "paired")
    more_progress(harness, phase="check", sources={"myclub": "skipped"}, gmail_senders=[])

    offered = check_page(page.url)["whatsapp"]

    assert [(g["name"], g["id"], g["ticked"], g["kids"]) for g in offered] == [
        ("Futis P2017", FOOTBALL, False, []), ("Luokka 3B", CLASS, True, ["Mia"])]
    assert confirm(page.url, kids=[{"name": "Mia", "everyday_name": "Mia"},
                                   {"name": "Leo", "everyday_name": "Leo"}],
                   whatsapp=["Luokka 3B"], senders=[]).status == 200
    health_result(page.url)
    cfg = Config.load(config_file(harness))
    assert [(c.name, c.id, c.kid) for c in cfg.whatsapp.chats] == [("Luokka 3B", CLASS, "Mia")]
    assert cfg.whatsapp.enabled
    # follow, already running, keeps the group's messages from now on.
    assert json.loads(linked_device.picked_file().read_text(encoding="utf-8")) == {
        "groups": [CLASS]}


def test_a_code_not_scanned_in_time_offers_a_new_one_and_the_old_job_runs_again(
        harness, page, phone):
    at_the_whatsapp_step(harness)
    harness.jobs.install_always(["node", "follow"], harness.home / "logs")  # a pairing before
    page.context.timeouts.whatsapp_pair = 1
    phone.plays([{"qr": "2@first"}])

    pair(page.url)
    assert not harness.jobs.always_running  # stopped, so it can't write over a new pairing
    assert until_said(page.url, "timed-out") == {"result": "timed-out"}

    assert statuses(page.url)["whatsapp"] == "to-do"
    assert not linked_device.auth_folder().exists()
    assert harness.jobs.always_running  # the old pairing goes on
    phone.plays([{"qr": "2@fresh"}, *SCANNED])
    page.context.timeouts.whatsapp_pair = 20
    pair(page.url)
    until_said(page.url, "qr", qr="2@fresh")
    until_said(page.url, "paired")


def test_moving_on_from_the_step_ends_a_pairing_not_scanned(harness, page, phone):
    at_the_whatsapp_step(harness)
    phone.plays([{"qr": "2@first"}, {"wait": "never"}])
    pair(page.url)
    until_said(page.url, "qr")

    call(page.url, "api/source", method="POST", body={"source": "whatsapp", "action": "skip"})
    assert call(page.url, "api/whatsapp/stop", method="POST", body={}).json() == {
        "result": "stopped"}

    assert until_said(page.url, "no-pairing") == {"result": "no-pairing"}
    assert statuses(page.url)["whatsapp"] == "skipped"
    assert harness.jobs.program(jobs.ALWAYS) is None


def test_a_scan_whose_groups_aren_t_listed_offers_a_new_code_with_why(harness, page, phone):
    at_the_whatsapp_step(harness)
    page.context.timeouts.whatsapp_pair = 1
    # Scanned, but the socket WhatsApp asks for after the scan never opens to list the groups.
    phone.plays([{"qr": "2@first"}, {"creds": {"me": ME}}, {"close": 515}], [])

    pair(page.url)
    out = until_said(page.url, "failed")

    assert "exit code 3" in out["reason"] and "2@first" not in out["reason"]
    assert statuses(page.url)["whatsapp"] == "to-do"
    assert harness.jobs.program(jobs.ALWAYS) is None


def test_without_the_whatsapp_library_the_step_says_so_and_runs_nothing(harness, page):
    at_the_whatsapp_step(harness)

    assert pair(page.url) == {"result": "no-library"}
    assert said(page.url) == {"result": "no-pairing"}


def test_the_pairing_calls_take_no_answers(harness, page):
    for path in ("api/whatsapp/pair", "api/whatsapp/check", "api/whatsapp/stop",
                 "api/whatsapp/off"):
        assert call(page.url, path, method="POST", body={"x": 1}).status == 400, path


def test_the_step_names_linked_devices_as_whatsapp_does_in_each_language():
    texts = json.loads((Path(linked_device.__file__).parent / "page" / "text.json").read_text(
        encoding="utf-8"))

    for language, words in {"en": ("Linked devices", "Link a device"),
                            "fi": ("Linkitetyt laitteet", "Linkitä laite"),
                            "zh": ("已关联设备", "关联新设备")}.items():
        for said in words:
            assert said in texts[language]["whatsapp.step.link"], (language, said)


def test_every_pairing_result_is_explained_in_all_three_languages():
    texts = json.loads((Path(linked_device.__file__).parent / "page" / "text.json").read_text(
        encoding="utf-8"))

    for language, table in texts.items():
        for result in setup_server.whatsapp.PAIR_RESULTS:
            assert table.get(f"whatsapp.{result}", "").strip(), (language, result)


# ── A link that isn't fresh: a new pairing keeps the picked groups


ALL_DONE = {"wilma": "done", "gmail": "done", "ai": "done", "whatsapp": "done", "myclub": "done"}


def finished_with_groups(harness, chats: list[dict[str, Any]]) -> None:
    """A Household set up before, the page opened again at Finish, with WhatsApp's `chats`
    picked."""
    config_file(harness).parent.mkdir(parents=True, exist_ok=True)
    harness.config["whatsapp"] = {"enabled": True, "chats": chats}
    config_file(harness).write_text(yaml.safe_dump(harness.config, allow_unicode=True))
    save_progress(harness, {"phase": "finish", "source": None, "sources": ALL_DONE})


def opened(url: str) -> dict[str, Any]:
    """The page's state as it loads."""
    return call(url, "api/state").json()


def saved_config(harness) -> Config:
    """The config the page saved, which the harness's runs write from then on."""
    harness.config = yaml.safe_load(config_file(harness).read_text(encoding="utf-8"))
    return Config.load(config_file(harness))


def test_a_link_whatsapp_ended_is_paired_again_keeping_the_picked_groups(harness, page, phone):
    picked = [{"name": "3B parents", "id": CLASS, "kid": "Mia", "label": "class"}]
    finished_with_groups(harness, picked)
    harness.source_fakes["whatsapp"].status(paired=False, connected_since=None,
                                            last_connected="2026-09-26T05:30:00.000Z",
                                            link_ended_at="2026-09-26T05:30:00.000Z")

    state = opened(page.url)

    assert (state["progress"]["phase"], state["progress"]["source"]) == ("connect", "whatsapp")
    assert state["progress"]["sources"]["whatsapp"] == "to-do"
    assert state["whatsapp"] == {"picked": ["3B parents"]}  # named as the page says it
    phone.plays([{"qr": "2@again"}, *SCANNED])
    pair(page.url)
    out = until_said(page.url, "paired")

    assert out["progress"]["phase"] == "finish"  # back where the family was
    assert out["progress"]["sources"]["whatsapp"] == "done"
    cfg = saved_config(harness)
    assert cfg.whatsapp.enabled
    assert [c.model_dump(exclude_none=True) for c in cfg.whatsapp.chats] == picked
    assert linked_device.link().paired and linked_device.link().ended_at is None
    assert harness.jobs.always_running
    assert opened(page.url)["progress"]["phase"] == "finish"  # nothing left to pair

    harness.config["summary_language"] = "en"
    harness.model_reply = SAYS_SOMETHING
    # Not read tonight; its CLI in place, so its fake doesn't put a stand-in over the real Node
    # the pairing ran on.
    harness.config["wilma"]["enabled"] = False
    fake_node.install_wilma(harness.home, "#!/bin/sh\n")
    harness.sources = {"gmail": [msg("gmail", "1001", "2026-09-27T18:00:00+03:00", "Retki",
                                     sender="teacher@kilo.example.fi", subject="Retki")]}
    assert harness.run() == 0
    [raw] = harness.archive_dir.glob("*.raw.json")
    coverage = json.loads(raw.read_text(encoding="utf-8"))["summary"]["_coverage"]
    assert coverage["whatsapp"] == {"count": 0, "error": None}  # read, with no note


def test_groups_picked_by_name_only_get_their_ids_from_the_pairing(harness, page, phone):
    # As a Household that read WhatsApp for Mac's database has it after the update: no pairing.
    finished_with_groups(harness, [{"name": "Luokka 3B", "kid": "Mia", "label": "class"},
                                   {"name": "Kuoro", "kid": "Leo"}])
    assert not linked_device.folder().exists()

    assert opened(page.url)["progress"]["source"] == "whatsapp"
    phone.plays([{"qr": "2@first"}, *SCANNED])
    pair(page.url)
    until_said(page.url, "paired")

    cfg = saved_config(harness)
    assert [(c.name, c.id, c.kid) for c in cfg.whatsapp.chats] == [
        ("Luokka 3B", CLASS, "Mia"), ("Kuoro", None, "Leo")]  # one not found stays picked
    assert json.loads(linked_device.picked_file().read_text(encoding="utf-8")) == {
        "groups": [CLASS]}


def test_a_paired_link_leaves_the_page_where_the_family_was(harness, page):
    finished_with_groups(harness, [{"name": "3B parents", "id": CLASS, "kid": "Mia"}])
    harness.source_fakes["whatsapp"].status(paired=True, connected_since=None,
                                            last_connected=None, link_ended_at=None)

    state = opened(page.url)

    assert state["progress"]["phase"] == "finish" and state["progress"]["sources"] == ALL_DONE


# ── Changing the picked groups (#548): at any later visit, with nothing skipped, and from a
# WhatsApp step opened again in Connect


def paired_at_finish(harness) -> None:
    """Set up before, with a pairing that holds and the groups it found, opened at Finish."""
    finished_with_groups(harness, [{"name": "3B parents", "id": CLASS, "kid": "Mia"}])
    harness.source_fakes["whatsapp"].status(paired=True, connected_since=None,
                                            last_connected=None, link_ended_at=None)
    more_progress(harness, gmail_senders=[], whatsapp_chats=[
        {"name": "3B parents", "id": CLASS}, {"name": "Futis P2017", "id": FOOTBALL}])


def change_groups(url: str, body: Any = None) -> Any:
    return call(url, "api/whatsapp/groups", method="POST", body={} if body is None else body)


def test_finish_takes_the_family_to_the_check_page_to_change_the_groups(harness, page, health):
    paired_at_finish(harness)
    assert opened(page.url)["whatsapp"] == {"picked": ["3B parents"]}

    r = change_groups(page.url)

    assert r.status == 200
    progress = r.json()["progress"]
    assert (progress["phase"], progress["source"]) == ("check", None)
    assert progress["sources"] == ALL_DONE  # nothing had to be skipped
    offered = check_page(page.url)["whatsapp"]
    assert [(g["name"], g["ticked"]) for g in offered] == [
        ("3B parents", True), ("Futis P2017", False)]
    assert confirm(page.url, whatsapp=["3B parents", "Futis P2017"], senders=[]).status == 200
    assert health_result(page.url)["result"] == "ok"
    assert [c.id for c in saved_config(harness).whatsapp.chats] == [CLASS, FOOTBALL]


def test_the_groups_change_only_once_whatsapp_is_done_past_connect_never_while_checking(
        harness, page, health):
    paired_at_finish(harness)
    assert change_groups(page.url, {"x": 1}).status == 400
    more_progress(harness, sources={"whatsapp": "skipped"})
    assert change_groups(page.url).status == 400  # off: nothing to change
    more_progress(harness, phase="connect", source=None, sources={"whatsapp": "done"})
    assert change_groups(page.url).status == 400  # Connect goes on to Check by itself
    more_progress(harness, phase="first-brief")
    assert change_groups(page.url).status == 200

    health.go.clear()
    assert confirm(page.url, senders=[]).status == 200
    assert change_groups(page.url).status == 409  # the health check is running
    health.go.set()
    health_result(page.url)


def test_a_done_whatsapp_opened_from_connect_s_list_goes_on_from_there(harness, page):
    at_the_whatsapp_step(harness)
    more_progress(harness, sources={"whatsapp": "done", "myclub": "to-do"})

    out = call(page.url, "api/whatsapp/done", method="POST", body={}).json()

    assert out["progress"]["source"] == "myclub"  # the next to do
    more_progress(harness, source="whatsapp", sources={"myclub": "skipped"})
    out = call(page.url, "api/whatsapp/done", method="POST", body={}).json()
    assert out["progress"]["source"] is None  # none left: on to Check
    more_progress(harness, source="whatsapp", sources={"whatsapp": "to-do"})
    assert call(page.url, "api/whatsapp/done", method="POST", body={}).status == 400


# ── Turning WhatsApp off


def test_turning_whatsapp_off_logs_out_removes_the_job_and_the_folder(harness, page, phone,
                                                                     capsys):
    finished_with_groups(harness, [{"name": "3B parents", "id": CLASS, "kid": "Mia"}])
    phone.plays([{"qr": "2@first"}, *SCANNED])
    opened(page.url)  # no pairing yet: the page goes to the step
    pair(page.url)
    until_said(page.url, "paired")
    assert harness.jobs.always_running and linked_device.folder().is_dir()
    phone.plays([{"open": True}])  # the logout's socket

    out = call(page.url, "api/whatsapp/off", method="POST", body={}).json()

    assert out["result"] == "off"
    assert out["progress"]["sources"]["whatsapp"] == "skipped"
    assert out["progress"]["phase"] == "finish"
    assert harness.jobs.always_status() == jobs.AlwaysStatus(installed=False, running=False)
    assert not linked_device.folder().exists()
    assert "logout" in {json.loads(line)["call"]
                        for line in phone.calls.read_text(encoding="utf-8").splitlines()}
    cfg = saved_config(harness)
    assert not cfg.whatsapp.enabled
    assert [c.name for c in cfg.whatsapp.chats] == ["3B parents"]  # kept for turning it on again

    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["wilma"]["enabled"] = False
    capsys.readouterr()
    harness.cli("doctor", "--skip-llm")
    [line] = [l for l in capsys.readouterr().out.splitlines() if "WhatsApp:" in l]
    assert line.startswith("✅ WhatsApp: off")
