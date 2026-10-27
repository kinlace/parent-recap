"""The Linked Device's Node program (ADR 0025) in its `pair` and `groups` modes, run by a real Node
on the fake Baileys package (`fake_baileys/`) in the test's module folder: what it prints, the
auth state it writes and doesn't, its log, and that it only reads. No JavaScript test toolchain
(ADR 0024): the Node is Parent Recap's pinned one where it is installed, else one on PATH."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

import fake_node
from family_brief import linked_device, own_node

NODE = own_node.node() or shutil.which("node")
GROUPS = {"120363@g.us": {"id": "120363@g.us", "subject": "Luokka 3B", "size": 24},
          "120999@g.us": {"id": "120999@g.us", "subject": "Futis P2017", "size": 31}}
LISTED = {"groups": [{"id": "120999@g.us", "subject": "Futis P2017"},
                     {"id": "120363@g.us", "subject": "Luokka 3B"}]}
ME = {"id": "358401234567:3@s.whatsapp.net", "name": "Mia's parent"}
PAIRED = [{"creds": {"me": ME}}, {"keys": {"pre-key": {"1": {"public": "p1"}}}}]
TEXT = "Retki torstaina, eväät mukaan"
# Calls that only read: opening the socket, listening, listing the groups, closing it.
READS = {"makeWASocket", "ev.on", "groupFetchAllParticipating", "end", "keys.get"}


class Run:
    def __init__(self, proc: subprocess.CompletedProcess, calls: Path, log: Path) -> None:
        self.code = proc.returncode
        self.printed = [json.loads(line) for line in proc.stdout.splitlines()]
        self.stderr = proc.stderr
        self.calls = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
        self.log = log.read_text(encoding="utf-8") if log.exists() else ""

    def options(self) -> list[dict[str, Any]]:
        return [c["options"] for c in self.calls if c["call"] == "makeWASocket"]


@pytest.fixture
def device(tmp_path: Path):
    """Runs the program in a mode, with the fake playing `sockets`, one list of steps per socket
    it opens; its auth state is in tmp_path/linked-device/auth."""
    if NODE is None:
        if os.environ.get("CI"):
            pytest.fail("CI has no Node to run the Linked Device's program")
        pytest.skip("no Node: neither Parent Recap's own nor one on PATH")
    tree = tmp_path / "baileys"
    fake_node.install_fake_baileys(tree)
    runs = iter(range(100))

    def run(mode: str, sockets: list[list[dict[str, Any]]], timeout: int = 20) -> Run:
        n = next(runs)
        scenario, calls = tmp_path / f"scenario-{n}.json", tmp_path / f"calls-{n}.jsonl"
        scenario.write_text(json.dumps({"sockets": sockets, "groups": GROUPS}), encoding="utf-8")
        calls.write_text("", encoding="utf-8")
        log = tmp_path / "logs" / f"linked-device-{n}.log"
        log.parent.mkdir(exist_ok=True)
        proc = subprocess.run(
            [str(NODE), str(linked_device.PROGRAM), mode, "--modules", str(tree),
             "--auth", str(auth(tmp_path)), "--log", str(log), "--timeout", str(timeout)],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
            env={**os.environ, "FAKE_BAILEYS_SCENARIO": str(scenario),
                 "FAKE_BAILEYS_CALLS": str(calls)})
        return Run(proc, calls, log)
    return run


def auth(tmp_path: Path) -> Path:
    return tmp_path / "linked-device" / "auth"


def assert_only_reads(run: Run) -> None:
    made = {c["call"] for c in run.calls}
    assert made <= READS, f"calls beyond reading: {made - READS}"
    for options in run.options():
        assert options["browser"][0] == "Parent Recap"
        assert options["markOnlineOnConnect"] is False
        assert options["syncFullHistory"] is False


def files(folder: Path) -> dict[str, bytes]:
    return {p.relative_to(folder).as_posix(): p.read_bytes()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def test_pair_prints_each_qr_then_keeps_the_auth_state_and_lists_the_groups(device, tmp_path):
    run = device("pair", [[{"qr": "2@first"}, {"qr": "2@second"}, *PAIRED, {"open": True}]])

    assert run.code == 0, run.stderr
    assert run.printed == [{"qr": "2@first"}, {"qr": "2@second"}, {"paired": True}, LISTED]
    creds = json.loads((auth(tmp_path) / "creds.json").read_text(encoding="utf-8"))
    assert creds["me"] == ME
    assert creds["noiseKey"]["private"] == {"type": "Buffer", "data": "bm9pc2U="}  # as Baileys
    assert json.loads((auth(tmp_path) / "pre-key-1.json").read_text(encoding="utf-8")) == {
        "public": "p1"}
    assert [c for c in os.listdir(tmp_path / "linked-device")] == ["auth"]  # no staging left
    assert_only_reads(run)
    assert run.options()[0]["me"] is None  # it paired with no auth state
    assert "QR code shown" in run.log and "connected" in run.log


def test_pair_opens_a_new_socket_when_whatsapp_asks_for_a_restart(device, tmp_path):
    run = device("pair", [[{"qr": "2@first"}, *PAIRED, {"close": 515}], [{"open": True}]])

    assert run.code == 0, run.stderr
    assert run.printed == [{"qr": "2@first"}, {"paired": True}, LISTED]
    assert [o["me"] for o in run.options()] == [None, ME]  # the second with what pairing made
    assert json.loads((auth(tmp_path) / "creds.json").read_text(encoding="utf-8"))["me"] == ME


@pytest.mark.macos
def test_the_auth_state_is_only_the_users(device, tmp_path):
    device("pair", [[{"qr": "2@first"}, *PAIRED, {"open": True}]])

    assert stat.S_IMODE(auth(tmp_path).stat().st_mode) == 0o700
    for f in auth(tmp_path).iterdir():
        assert stat.S_IMODE(f.stat().st_mode) == 0o600, f.name
    log = next((tmp_path / "logs").iterdir())
    assert stat.S_IMODE(log.stat().st_mode) == 0o600


def test_pair_not_paired_in_time_exits_and_leaves_no_auth_state(device, tmp_path):
    run = device("pair", [[{"qr": "2@first"}, {"creds": {"advSecretKey": "k"}}]], timeout=1)

    assert run.code == linked_device.TIMED_OUT
    assert run.printed == [{"qr": "2@first"}]
    assert not (tmp_path / "linked-device").exists()
    assert "not paired after 1 seconds" in run.log


def test_groups_lists_the_groups_on_the_saved_auth_state_and_keeps_no_message(device, tmp_path):
    device("pair", [[{"qr": "2@first"}, *PAIRED, {"open": True}]])
    before = files(tmp_path)

    run = device("groups", [[{"get": ["pre-key", ["1", "2"]]},
                             {"message": {"chat": "120363@g.us", "text": TEXT}}, {"log": TEXT},
                             {"open": True}]])

    assert run.code == 0, run.stderr
    assert run.printed == [LISTED]
    [options] = run.options()
    assert options["me"] == ME and options["noiseKeyIsBuffer"] is True
    [got] = [c["result"] for c in run.calls if c["call"] == "keys.get"]
    assert got == {"1": {"public": "p1"}, "2": None}
    # Nothing but its log was written (the fake's files aside), and no message anywhere on disk.
    after = {name: data for name, data in files(tmp_path).items()
             if not name.startswith(("calls-", "scenario-"))}
    assert {name for name in after if before.get(name) != after[name]} == {
        "logs/linked-device-1.log"}
    assert not any(TEXT.encode() in data for data in after.values())
    assert_only_reads(run)
    assert "connected" in run.log and "failed to decrypt message" in run.log
    assert TEXT not in run.log and "received" not in run.log


def test_groups_without_auth_state_needs_a_new_pairing(device, tmp_path):
    run = device("groups", [[{"open": True}]])

    assert run.code == linked_device.NEEDS_PAIRING
    assert run.printed == [] and run.calls == []
    assert not (tmp_path / "linked-device").exists()


def test_groups_when_whatsapp_ended_the_link_needs_a_new_pairing(device):
    device("pair", [[{"qr": "2@first"}, *PAIRED, {"open": True}]])

    run = device("groups", [[{"close": 401}]])

    assert run.code == linked_device.NEEDS_PAIRING
    assert run.printed == []
    assert "logged out" in run.log
