"""Connecting WhatsApp with `family-brief setup whatsapp`: it reads WhatsApp through a `bg` job,
and when the scheduled job's Python can't read it yet, shows that Python in Finder, opens App
Management and waits for the family. Then it reports the permission and the chats as one JSON
line, with a hint on each chat that looks like it's about a Kid.

The outside edges are faked: `launchctl` runs the job in this process with the job's
environment, against a WhatsApp database in the temporary HOME that only the job can read, and
only once the fake Mac has given the job's Python the permission. Waiting takes no time: a fake
clock moves on when the command sleeps."""
from __future__ import annotations

import contextlib
import json
import os
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from family_brief import __main__ as cli, ops
from family_brief.collectors import whatsapp

MESSAGE_DATE = datetime(2026, 9, 26, 18, 0).timestamp() - whatsapp.CORE_DATA_EPOCH


class Mac:
    """The family's Mac: WhatsApp's database, macOS's permission for the job's Python, and launchd."""

    def __init__(self, home: Path) -> None:
        self.db_dir = home / "Library" / "Group Containers" / "group.net.whatsapp.WhatsApp.shared"
        self.grants_after = 0          # reads refused before the family turns on App Management
        self.allow: str | None = "allow"  # the one-time Allow prompt: "allow", or None if unanswered
        self.bootstrap_fails = False
        self.refused = 0               # reads macOS refused
        self.prompts = 0               # Allow prompts shown
        self.allowed = False
        self.jobs: list[list[str]] = []      # each job's ProgramArguments
        self.reads: list[bool] = []          # each read of the database: was it inside a job?
        self._running: dict[str, int | None] = {}

    def install_whatsapp(self, chats: list[tuple[str, int, bool]]) -> None:
        """(name, days before the last message, archived) for each group chat."""
        self.db_dir.mkdir(parents=True)
        db = self.db_dir / "ChatStorage.sqlite"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZPARTNERNAME TEXT, "
                     "ZCONTACTJID TEXT, ZLASTMESSAGEDATE REAL, ZARCHIVED INTEGER, ZREMOVED INTEGER)")
        for i, (name, days_ago, archived) in enumerate(chats):
            conn.execute("INSERT INTO ZWACHATSESSION VALUES (?, ?, ?, ?, ?, 0)",
                         (i + 1, name, f"12036{i}@g.us", MESSAGE_DATE - days_ago * 86400, int(archived)))
        conn.execute("INSERT INTO ZWACHATSESSION VALUES (99, 'Grandma', '358401234567@s.whatsapp.net', "
                     "?, 0, 0)", (MESSAGE_DATE,))
        conn.commit()
        conn.close()
        db.chmod(0)  # nothing on this Mac can read it without macOS's permission

    def launchctl(self, cmd: list[str]) -> subprocess.CompletedProcess:
        action = cmd[1]
        if action == "bootstrap":
            if self.bootstrap_fails:
                return subprocess.CompletedProcess(cmd, 5, "", "Bootstrap failed: 5: Input/output error")
            job = plistlib.loads(Path(cmd[3]).read_bytes())
            self._running[job["Label"]] = self._run(job)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        label = cmd[2].split("/")[-1]
        if action == "print":
            if label not in self._running:
                return subprocess.CompletedProcess(cmd, 113, "", "Could not find service")
            code = self._running[label]
            state = "\tstate = running\n" if code is None else f"\tstate = not running\n\tlast exit code = {code}\n"
            return subprocess.CompletedProcess(cmd, 0, state, "")
        if action == "bootout":
            self._running.pop(label, None)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"unexpected launchctl call: {cmd}")

    def _run(self, job: dict[str, Any]) -> int | None:
        """Runs the job's family-brief command with its environment; None while it's still going."""
        argv = job["ProgramArguments"]
        assert argv[:3] == [sys.executable, "-m", "family_brief"]
        self.jobs.append(argv)
        db = self.db_dir / "ChatStorage.sqlite"
        if self.refused >= self.grants_after and db.exists():
            if not self.allowed:
                self.prompts += 1
                if self.allow != "allow":
                    return None  # the job waits on the prompt nobody answers
                self.allowed = True
            db.chmod(0o600)
        before = dict(os.environ)
        os.environ.update(job["EnvironmentVariables"])
        try:
            with open(job["StandardOutPath"], "w") as out, contextlib.redirect_stdout(out), \
                    pytest.MonkeyPatch.context() as mp:
                mp.setattr(sys, "argv", ["family-brief", *argv[3:]])
                code = cli.main()
        finally:
            os.environ.clear()
            os.environ.update(before)
            if db.exists():
                if not self.allowed:
                    self.refused += 1
                db.chmod(0)
        return code


@pytest.fixture
def mac(harness, monkeypatch) -> Mac:
    m = Mac(harness.home)
    monkeypatch.setattr(whatsapp, "DB_DIR", m.db_dir)
    monkeypatch.setattr(whatsapp, "DB_FILE", m.db_dir / "ChatStorage.sqlite")
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    monkeypatch.setattr(time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    m.clock = clock

    real_copy = shutil.copy2

    def copy2(src: Any, dst: Any, **k: Any) -> Any:
        if Path(src).parent == m.db_dir:
            m.reads.append(bool(os.environ.get(ops.BG_ENV)))
        return real_copy(src, dst, **k)
    monkeypatch.setattr(shutil, "copy2", copy2)

    others = subprocess.run  # the harness's fakes

    def run(cmd: list[str], *a: Any, **k: Any) -> subprocess.CompletedProcess:
        if Path(cmd[0]).name == "launchctl":
            harness.commands.append(list(cmd))
            return m.launchctl(cmd)
        return others(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", run)
    return m


def result(capsys) -> dict[str, Any]:
    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert len(lines) == 1, f"expected one JSON line, got: {out!r}"
    return json.loads(lines[0])


def assert_read_only_through_bg(harness, mac: Mac) -> None:
    assert mac.reads and all(mac.reads), "WhatsApp was read outside a bg job"
    assert all(job[3:5] == ["-c", str(harness.home / ".family" / "config.yaml")] for job in mac.jobs)
    assert not any("Terminal" in arg for cmd in harness.commands for arg in cmd)


PYTHON = os.path.realpath(sys.executable)
CHATS = [("3B parents", 1, False), ("Neighbours ", 3, False), ("Kilo School families 🏫", 2, True)]


# ── already readable


def test_when_the_job_can_read_it_lists_each_chat_with_its_exact_name_and_last_activity(
        harness, mac, capsys):
    mac.install_whatsapp(CHATS)

    assert harness.cli("setup", "whatsapp") == 0

    res = result(capsys)
    assert res["result"] == "readable" and res["python"] == PYTHON
    assert [(c["name"], c["last"], c["archived"]) for c in res["chats"]] == [
        ("3B parents", "2026-09-25", False),
        ("Kilo School families 🏫", "2026-09-24", True),
        ("Neighbours ", "2026-09-23", False),
    ]
    assert harness.opened == []  # nothing for the family to do
    assert mac.prompts == 1
    assert_read_only_through_bg(harness, mac)


def test_chats_without_activity_in_the_last_days_are_left_out(harness, mac, capsys):
    mac.install_whatsapp([("3B parents", 1, False), ("Last year's 2B", 400, False)])

    assert harness.cli("setup", "whatsapp") == 0

    assert [c["name"] for c in result(capsys)["chats"]] == ["3B parents"]


def test_days_widens_how_far_back_a_chat_counts(harness, mac, capsys):
    mac.install_whatsapp([("3B parents", 1, False), ("Last year's 2B", 400, False)])

    assert harness.cli("setup", "whatsapp", "--days", "500") == 0

    assert [c["name"] for c in result(capsys)["chats"]] == ["3B parents", "Last year's 2B"]


# ── hints


def test_chats_that_look_like_they_are_about_a_kid_carry_a_hint(harness, mac, capsys):
    harness.config["kids"][0]["name"] = "Mia Virtanen"  # as Wilma spells it
    mac.install_whatsapp([
        ("3B parents", 1, False),               # Mia's class
        ("Kilo School families", 1, False),     # both Kids' school
        ("FC Kilo football U10", 1, False),     # Mia's club
        ("Leo piano", 1, False),                # Leo's name and club
        ("Mia's birthday 🎈", 1, False),        # Mia's first name
        ("米娅 生日会", 1, False),               # Mia's alias
        ("Leonardo fans", 1, False),            # not Leo
        ("13B reunion", 1, False),              # not 3B
        ("Neighbours", 1, False),
    ])

    assert harness.cli("setup", "whatsapp") == 0

    hints = {c["name"]: c.get("hint") for c in result(capsys)["chats"]}
    assert hints == {
        "3B parents": {"kids": ["Mia Virtanen"], "matched": ["3B"]},
        "Kilo School families": {"kids": ["Mia Virtanen", "Leo"], "matched": ["Kilo School"]},
        "FC Kilo football U10": {"kids": ["Mia Virtanen"], "matched": ["football"]},
        "Leo piano": {"kids": ["Leo"], "matched": ["Leo", "piano"]},
        "Mia's birthday 🎈": {"kids": ["Mia Virtanen"], "matched": ["Mia"]},
        "米娅 生日会": {"kids": ["Mia Virtanen"], "matched": ["米娅"]},
        "Leonardo fans": None,
        "13B reunion": None,
        "Neighbours": None,
    }


def test_kids_without_a_school_or_class_still_get_hints_from_their_names(harness, mac, capsys):
    harness.config["kids"] = [{"name": "Mia Virtanen"}, {"name": "Leo"}]  # setup didn't ask for either
    mac.install_whatsapp([("Mia's birthday 🎈", 1, False), ("Leo piano", 1, False), ("3B parents", 1, False)])

    assert harness.cli("setup", "whatsapp") == 0

    hints = {c["name"]: c.get("hint") for c in result(capsys)["chats"]}
    assert hints == {
        "Mia's birthday 🎈": {"kids": ["Mia Virtanen"], "matched": ["Mia"]},
        "Leo piano": {"kids": ["Leo"], "matched": ["Leo"]},
        "3B parents": None,
    }


def test_without_kids_in_the_config_yet_chats_have_no_hints(harness, mac, capsys):
    harness.config["kids"] = []
    mac.install_whatsapp(CHATS)

    assert harness.cli("setup", "whatsapp") == 0

    assert all("hint" not in c for c in result(capsys)["chats"])


# ── waiting for the permission


def test_without_the_permission_it_opens_app_management_and_waits_for_it(harness, mac, capsys):
    mac.install_whatsapp(CHATS)
    mac.grants_after = 3

    assert harness.cli("setup", "whatsapp") == 0

    res = result(capsys)
    assert res["result"] == "readable" and len(res["chats"]) == 3
    assert harness.opened == [PYTHON, ops.APP_MANAGEMENT_URL]
    assert ["open", "-R", PYTHON] in harness.commands
    assert mac.refused == 3 and mac.prompts == 1  # the Allow prompt comes from a bg run
    assert_read_only_through_bg(harness, mac)


def test_permission_not_given_in_time_is_reported(harness, mac, capsys):
    mac.install_whatsapp(CHATS)
    mac.grants_after = 10_000
    start = mac.clock[0]

    assert harness.cli("setup", "whatsapp", "--timeout", "60") == 1

    res = result(capsys)
    assert res["result"] == "no-permission" and res["python"] == PYTHON
    assert "App Management" in res["next"] and "setup whatsapp --no-open" in res["next"]
    assert "chats" not in res
    assert harness.opened == [PYTHON, ops.APP_MANAGEMENT_URL]
    assert 0 < mac.clock[0] - start <= 60 + 5
    assert_read_only_through_bg(harness, mac)


def test_an_unanswered_allow_prompt_is_reported_as_waiting(harness, mac, capsys):
    mac.install_whatsapp(CHATS)
    mac.allow = None

    assert harness.cli("setup", "whatsapp", "--timeout", "60") == 1

    res = result(capsys)
    assert res["result"] == "waiting" and "Allow" in res["next"]
    assert "--no-open" in res["next"]
    assert mac.prompts == 1
    assert not any(mac.reads)  # nothing was read while the prompt was up
    assert ["launchctl", "bootout"] == harness.commands[-1][:2]  # the job didn't outlive it


def test_no_open_waits_without_opening_finder_or_app_management_again(harness, mac, capsys):
    mac.install_whatsapp(CHATS)
    mac.grants_after = 2

    assert harness.cli("setup", "whatsapp", "--no-open") == 0

    assert result(capsys)["result"] == "readable"
    assert harness.opened == []


def test_timeout_zero_only_reports_where_it_stands(harness, mac, capsys):
    mac.install_whatsapp(CHATS)
    mac.grants_after = 10_000

    assert harness.cli("setup", "whatsapp", "--no-open", "--timeout", "0") == 1

    assert result(capsys)["result"] == "no-permission"
    assert len(mac.jobs) == 1


# ── when it doesn't work


def test_without_whatsapp_for_mac_it_says_to_install_it(harness, mac, capsys):
    assert harness.cli("setup", "whatsapp") == 1

    res = result(capsys)
    assert res["result"] == "not-installed" and "App Store" in res["next"]
    assert harness.opened == []


def test_a_background_job_that_does_not_start_is_reported(harness, mac, capsys):
    mac.install_whatsapp(CHATS)
    mac.bootstrap_fails = True

    assert harness.cli("setup", "whatsapp") == 1

    res = result(capsys)
    assert res["result"] == "bg-failed" and "Input/output error" in res["error"]


def test_the_read_step_refuses_to_run_outside_a_bg_job(harness, mac, monkeypatch, capsys):
    monkeypatch.delenv(ops.BG_ENV, raising=False)
    mac.install_whatsapp(CHATS)

    assert harness.cli("setup", "whatsapp", "--read") == 2

    assert mac.reads == []
    assert "bg" in capsys.readouterr().out
