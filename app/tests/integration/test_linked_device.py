"""The Linked Device's Node program (ADR 0025) in its `pair`, `groups` and `follow` modes, run by
a real Node on the fake Baileys package (`fake_baileys/`) in the test's module folder: what it
prints, the auth state, message store and status file it writes and doesn't, how it reconnects,
its log, and that it only reads. No JavaScript test toolchain (ADR 0024): the Node is Parent
Recap's pinned one where it is installed, else one on PATH."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

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
CLASS, FOOTBALL = "120363@g.us", "120999@g.us"
PRIVATE = "358401111111@s.whatsapp.net"
OTHER_TEXT, PRIVATE_TEXT = "Harjoitukset peruttu", "Soitatko illalla?"
LOGOUT = {"close": 401}
# Calls that only read: opening the socket, listening, listing the groups and one group's
# details, closing it.
READS = {"makeWASocket", "ev.on", "groupFetchAllParticipating", "groupMetadata", "end",
         "keys.get"}


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

    def run(mode: str, sockets: list[list[dict[str, Any]]], timeout: int = 20,
            during: Callable[[subprocess.Popen], None] | None = None) -> Run:
        """`during`, if given, is called with the program's process while it runs, as the fake's
        {"wait": NAME} steps wait for it to make their files with `go`."""
        n = next(runs)
        scenario, calls = tmp_path / f"scenario-{n}.json", tmp_path / f"calls-{n}.jsonl"
        scenario.write_text(json.dumps({"sockets": sockets, "groups": GROUPS}), encoding="utf-8")
        calls.write_text("", encoding="utf-8")
        log = tmp_path / "logs" / f"linked-device-{n}.log"
        log.parent.mkdir(exist_ok=True)
        if mode == "follow":
            options = ["--picked", str(picked(tmp_path)), "--store", str(store(tmp_path)),
                       "--status", str(status(tmp_path))]
        else:
            options = ["--timeout", str(timeout)]
        with subprocess.Popen(
                [str(NODE), str(linked_device.PROGRAM), mode, "--modules", str(tree),
                 "--auth", str(auth(tmp_path)), "--log", str(log), *options],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                env={**os.environ, "FAKE_BAILEYS_SCENARIO": str(scenario),
                     "FAKE_BAILEYS_CALLS": str(calls)}) as proc:
            try:
                if during:
                    during(proc)
                stdout, stderr = proc.communicate(timeout=60)
            finally:
                proc.kill()
        return Run(subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr),
                   calls, log)
    return run


def auth(tmp_path: Path) -> Path:
    return tmp_path / "linked-device" / "auth"


def store(tmp_path: Path) -> Path:
    return tmp_path / "linked-device" / "messages.jsonl"


def status(tmp_path: Path) -> Path:
    return tmp_path / "linked-device" / "status.json"


def picked(tmp_path: Path) -> Path:
    return tmp_path / "linked-device" / "picked-groups.json"


def pick(tmp_path: Path, *ids: str) -> None:
    """Writes the picked-groups file as Python does: in one move, in place of the last one."""
    staging = picked(tmp_path).with_suffix(".partial")
    staging.write_text(json.dumps({"groups": list(ids)}), encoding="utf-8")
    os.replace(staging, picked(tmp_path))


def go(tmp_path: Path, name: str = "go") -> None:
    """Lets the fake's {"wait": name} step go on."""
    (tmp_path / name).touch()


def until(check: Callable[[], Any], seconds: float = 20) -> Any:
    deadline = time.monotonic() + seconds
    while not (found := check()):
        assert time.monotonic() < deadline, "waited in vain"
        time.sleep(0.05)
    return found


def stored(tmp_path: Path) -> list[dict[str, Any]]:
    path = store(tmp_path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def read_status(tmp_path: Path) -> dict[str, Any]:
    try:
        return json.loads(status(tmp_path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def paired(device, tmp_path: Path, *ids: str) -> None:
    device("pair", [[{"qr": "2@first"}, *PAIRED, {"open": True}]])
    pick(tmp_path, *ids)


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


def test_pair_keeps_a_pairing_whose_restarted_socket_never_opens(device, tmp_path):
    run = device("pair", [[{"qr": "2@first"}, *PAIRED, {"close": 515}], []], timeout=1)

    assert run.code == linked_device.TIMED_OUT
    assert run.printed == [{"qr": "2@first"}, {"paired": True}]  # the phone lists it already
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


def test_follow_keeps_only_the_picked_groups_messages(device, tmp_path):
    paired(device, tmp_path, CLASS)

    run = device("follow", [[
        {"open": True},
        {"message": {"chat": CLASS, "id": "M1", "text": TEXT, "sender": "Liisa", "time": 1000}},
        {"message": {"chat": FOOTBALL, "id": "M2", "text": OTHER_TEXT}},
        {"message": {"chat": PRIVATE, "id": "M3", "text": PRIVATE_TEXT}},
        {"message": {"chat": CLASS, "id": "M4", "text": "Kiitos!", "fromMe": True, "time": 1001}},
        {"message": {"chat": CLASS, "id": "M5", "media": "imageMessage", "time": 1002}},
        {"message": {"chat": CLASS, "id": "M6", "time": 1003, "content": {
            "ephemeralMessage": {"message": {"extendedTextMessage": {"text": "Linkki: x.fi"}}}}}},
        LOGOUT]])

    assert run.code == linked_device.LINK_ENDED, run.stderr
    class_ = {"group_id": CLASS, "group_subject": "Luokka 3B"}
    assert stored(tmp_path) == [
        {"id": "M1", **class_, "sender": "Liisa", "timestamp": 1000, "text": TEXT,
         "from_me": False},
        {"id": "M4", **class_, "sender": "Mia's parent", "timestamp": 1001, "text": "Kiitos!",
         "from_me": True},
        {"id": "M5", **class_, "sender": "Liisa", "timestamp": 1002, "text": "[image]",
         "from_me": False},
        {"id": "M6", **class_, "sender": "Liisa", "timestamp": 1003, "text": "Linkki: x.fi",
         "from_me": False}]
    on_disk = {name: data for name, data in files(tmp_path).items()
               if not name.startswith(("calls-", "scenario-"))}
    for text in (OTHER_TEXT, PRIVATE_TEXT):
        assert not any(text.encode() in data for data in on_disk.values()), text
    assert_only_reads(run)
    assert TEXT not in run.log and "stored 1 message" in run.log


def test_follow_keeps_the_picked_groups_history_whatsapp_hands_over(device, tmp_path):
    paired(device, tmp_path, CLASS)

    run = device("follow", [[{"open": True}, {"history": [
        {"chat": FOOTBALL, "id": "H1", "text": OTHER_TEXT},
        {"chat": CLASS, "id": "H2", "text": TEXT, "time": 900},
        {"chat": PRIVATE, "id": "H3", "text": PRIVATE_TEXT}]}, LOGOUT]])

    assert run.code == linked_device.LINK_ENDED, run.stderr
    assert [(r["id"], r["text"], r["timestamp"]) for r in stored(tmp_path)] == [("H2", TEXT, 900)]


def test_follow_keeps_its_status_and_marks_a_logout_as_the_link_ended(device, tmp_path):
    paired(device, tmp_path, CLASS)
    seen: dict[str, Any] = {}

    def connected(_proc: subprocess.Popen) -> None:
        seen.update(until(lambda: (now := read_status(tmp_path)).get("connected_since") and now))
        go(tmp_path)

    run = device("follow", [[{"open": True}, {"wait": "go"}, LOGOUT]], during=connected)

    assert run.code == linked_device.LINK_ENDED, run.stderr
    assert seen["paired"] is True and seen["link_ended_at"] is None
    assert seen["last_connected"] == seen["connected_since"]
    ended = read_status(tmp_path)
    assert ended["paired"] is False and ended["connected_since"] is None
    assert ended["link_ended_at"] >= ended["last_connected"] >= seen["connected_since"]
    assert len(run.options()) == 1  # no reconnect after a logout
    assert "logged out: the link ended" in run.log


def test_follow_started_again_keeps_an_ended_link_ended_until_it_connects(device, tmp_path):
    paired(device, tmp_path, CLASS)
    device("follow", [[{"open": True}, LOGOUT]])
    ended = read_status(tmp_path)
    seen: dict[str, Any] = {}

    def starting(_proc: subprocess.Popen) -> None:
        until(lambda: tmp_path.joinpath("calls-2.jsonl").read_text(encoding="utf-8"))
        seen.update(read_status(tmp_path))
        go(tmp_path)

    run = device("follow", [[{"wait": "go"}, {"open": True}, LOGOUT]], during=starting)

    assert run.code == linked_device.LINK_ENDED, run.stderr
    assert seen == ended
    assert read_status(tmp_path)["link_ended_at"] > ended["link_ended_at"]


@pytest.mark.macos
def test_follow_stopped_by_the_os_is_no_longer_connected(device, tmp_path):
    paired(device, tmp_path, CLASS)

    def stop(proc: subprocess.Popen) -> None:
        until(lambda: read_status(tmp_path).get("connected_since"))
        proc.terminate()

    run = device("follow", [[{"open": True}, {"wait": "never"}]], during=stop)

    assert run.code == linked_device.STOPPED, run.stderr
    stopped = read_status(tmp_path)
    assert stopped["paired"] is True and stopped["connected_since"] is None
    assert stopped["last_connected"] is not None and stopped["link_ended_at"] is None
    assert "stopped by SIGTERM" in run.log


@pytest.mark.macos
def test_the_store_and_status_are_only_the_users(device, tmp_path):
    paired(device, tmp_path, CLASS)

    device("follow", [[{"open": True}, {"message": {"chat": CLASS, "text": TEXT}}, LOGOUT]])

    assert stat.S_IMODE((tmp_path / "linked-device").stat().st_mode) == 0o700
    for f in (store(tmp_path), status(tmp_path)):
        assert stat.S_IMODE(f.stat().st_mode) == 0o600, f.name


def test_follow_reconnects_after_a_drop_with_a_growing_pause(device, tmp_path):
    paired(device, tmp_path, CLASS)

    run = device("follow", [[{"open": True}, {"close": 428}], [{"close": 408}],
                            [{"open": True}, {"message": {"chat": CLASS, "text": TEXT}}, LOGOUT]])

    assert run.code == linked_device.LINK_ENDED, run.stderr
    opened = [c["at"] for c in run.calls if c["call"] == "makeWASocket"]
    assert len(opened) == 3
    first, second = opened[1] - opened[0], opened[2] - opened[1]
    assert first >= 1900 and second >= 2 * first - 200, (first, second)
    assert [r["text"] for r in stored(tmp_path)] == [TEXT]
    assert_only_reads(run)


def test_follow_takes_a_changed_pick_without_a_new_pairing(device, tmp_path):
    paired(device, tmp_path, CLASS)

    def pick_football(_proc: subprocess.Popen) -> None:
        until(lambda: stored(tmp_path))
        pick(tmp_path, CLASS, FOOTBALL)
        go(tmp_path)

    run = device("follow", [[
        {"open": True}, {"message": {"chat": FOOTBALL, "id": "F1", "text": OTHER_TEXT}},
        {"message": {"chat": CLASS, "id": "C1", "text": TEXT}}, {"wait": "go"},
        {"rename": {"id": FOOTBALL, "subject": "Futis P2017 syksy"}},
        {"message": {"chat": FOOTBALL, "id": "F2", "text": "Peli lauantaina"}}, LOGOUT]],
        during=pick_football)

    assert run.code == linked_device.LINK_ENDED, run.stderr
    assert [(r["id"], r["group_subject"]) for r in stored(tmp_path)] == [
        ("C1", "Luokka 3B"), ("F2", "Futis P2017 syksy")]


def test_follow_without_auth_state_needs_a_new_pairing(device, tmp_path):
    run = device("follow", [[{"open": True}]])

    assert run.code == linked_device.LINK_ENDED
    assert run.calls == []
    assert not store(tmp_path).exists()


def test_follow_asked_for_a_qr_code_marks_the_link_ended(device, tmp_path):
    paired(device, tmp_path, CLASS)

    run = device("follow", [[{"qr": "2@again"}]])

    assert run.code == linked_device.LINK_ENDED
    assert run.printed == []
    assert read_status(tmp_path)["link_ended_at"] is not None


def test_logout_logs_out_on_the_saved_auth_state_and_reads_nothing_else(device, tmp_path):
    device("pair", [[{"qr": "2@first"}, *PAIRED, {"open": True}]])

    run = device("logout", [[{"message": {"chat": CLASS, "text": TEXT}}, {"open": True}]])

    assert run.code == 0, run.stderr
    assert run.printed == []
    assert [c["call"] for c in run.calls if c["call"] not in READS] == ["logout"]
    assert run.options()[0]["me"] == ME
    assert "logged out" in run.log
    assert not store(tmp_path).exists()


def test_logout_of_a_link_whatsapp_ended_needs_a_new_pairing(device):
    device("pair", [[{"qr": "2@first"}, *PAIRED, {"open": True}]])

    run = device("logout", [[LOGOUT]])

    assert run.code == linked_device.NEEDS_PAIRING
    assert "logout" not in {c["call"] for c in run.calls}


def test_logout_without_auth_state_needs_a_new_pairing(device):
    run = device("logout", [[{"open": True}]])

    assert run.code == linked_device.NEEDS_PAIRING
    assert run.calls == []
