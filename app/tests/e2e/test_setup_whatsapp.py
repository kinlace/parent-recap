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
from family_brief import baileys_install, linked_device, own_node
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


def test_without_the_whatsapp_library_the_step_says_so_and_runs_nothing(harness, page):
    at_the_whatsapp_step(harness)

    assert pair(page.url) == {"result": "no-library"}
    assert said(page.url) == {"result": "no-pairing"}


def test_the_pairing_calls_take_no_answers(harness, page):
    for path in ("api/whatsapp/pair", "api/whatsapp/check", "api/whatsapp/stop"):
        assert call(page.url, path, method="POST", body={"x": 1}).status == 400, path


def test_the_step_names_linked_devices_as_whatsapp_does_in_each_language():
    texts = json.loads((Path(linked_device.__file__).parent / "page" / "text.json").read_text(
        encoding="utf-8"))

    for language, words in {"en": ("Linked devices", "Link a device"),
                            "fi": ("Linkitetyt laitteet", "Linkitä laite"),
                            "zh": ("已关联设备", "关联新设备")}.items():
        for said in words:
            assert said in texts[language]["whatsapp.step.link"], (language, said)
