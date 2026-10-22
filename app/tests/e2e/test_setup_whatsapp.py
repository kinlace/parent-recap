"""Connecting WhatsApp with `parent-recap setup whatsapp`: it checks through a `bg` job whether
the scheduled job's Python has Full Disk Access, and until it does, shows that Python in Finder,
opens Full Disk Access and waits for the family. Only then does it read WhatsApp. Then it reports the permission and the chats as one JSON
line, with a hint on each chat that looks like it's about a Kid.

The outside edges are faked: the harness's FakeJobs runs the `bg` job in this process, against a
WhatsApp database and macOS's permission file in the temporary HOME that only the job can read,
and only once the fake Mac has given the job's Python Full Disk Access. Waiting takes no time: a
fake clock moves on when the command sleeps.

The setup page's WhatsApp step, and why WhatsApp isn't OK on its Check and Finish, are at the end,
driven with HTTP calls as the page makes them (`setup_page.py`), on the same fake Mac."""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import pytest
import yaml

from family_brief import ops, setup_save, setup_server, setup_steps, whatsapp_python
from fake_system import FakeJobs
from family_brief.collectors import whatsapp
from setup_page import (PAGE_DIR, Response, at_the_check_step, call, check_page, config_file, confirm,
                        health_result, more_progress, progress_file, save_progress, source, statuses)
from setup_page import clock, health, page  # noqa: F401

# The tests of `parent-recap setup whatsapp` are each marked macos: WhatsApp for Mac and its Full
# Disk Access.

MESSAGE_DATE = datetime(2026, 9, 26, 18, 0).timestamp() - whatsapp.CORE_DATA_EPOCH


class Mac:
    """The family's Mac: WhatsApp's database, and macOS's permissions for the job's Python.

    Full Disk Access lets the job's Python read WhatsApp without a question, and opens the one file
    only it unlocks. Without it, reading WhatsApp makes macOS ask whether python3 may access data
    from other apps, and Allow lets that one read through (ADR 0012)."""

    def __init__(self, home: Path) -> None:
        self.db_dir = home / "Library" / "Group Containers" / "group.net.whatsapp.WhatsApp.shared"
        self.tcc_db = home / "Library" / "Application Support" / "com.apple.TCC" / "TCC.db"
        self.tcc_db.parent.mkdir(parents=True, exist_ok=True)
        self.tcc_db.write_bytes(b"")
        self.tcc_db.chmod(0)  # only Full Disk Access opens it
        self.full_disk_access_after = 0  # jobs run before the family turns Full Disk Access on
        self.allow = "allow"           # the answer to macOS's question: "allow" or "deny"
        self.refused = 0               # jobs run without Full Disk Access
        self.prompts = 0               # questions macOS asked about data from other apps
        self.reads: list[bool] = []          # each read of the database: was it inside a job?
        self._job_has_access = False
        self.jobs: FakeJobs | None = None  # the scheduler its `bg` jobs run in

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

    def read(self, src: Path) -> None:
        """A read of WhatsApp's database: macOS lets it through, asks, or refuses."""
        inside = bool(os.environ.get(ops.BG_ENV))
        self.reads.append(inside)
        if inside and self._job_has_access:
            return
        self.prompts += 1
        if not inside or self.allow != "allow":
            raise PermissionError(1, "Operation not permitted", str(src))

    @contextlib.contextmanager
    def job(self) -> Iterator[None]:
        """A `bg` job as it runs: with Full Disk Access once the family has given it, which opens
        the one file only it unlocks."""
        self._job_has_access = self.refused >= self.full_disk_access_after
        if self._job_has_access and self.tcc_db.exists():
            self.tcc_db.chmod(0o600)
        else:
            self.refused += 1
        try:
            yield
        finally:
            if self.tcc_db.exists():
                self.tcc_db.chmod(0)
            self._job_has_access = False
        self.jobs: FakeJobs | None = None  # the scheduler its `bg` jobs run in


@pytest.fixture
def mac(harness, monkeypatch) -> Mac:
    m = fake_mac(harness, monkeypatch)
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    monkeypatch.setattr(time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    m.clock = clock
    return m


def fake_mac(harness, monkeypatch) -> Mac:
    """The family's Mac, with WhatsApp's database faked, and the real clock. Its `bg` jobs run
    in the harness's FakeJobs, with the permissions the Mac gives them."""
    m = Mac(harness.home)
    harness.source_fakes["whatsapp"].installed = False  # this Mac installs WhatsApp itself
    m.jobs = harness.jobs
    harness.jobs.inside = m.job
    monkeypatch.setattr(whatsapp, "DB_DIR", m.db_dir)
    monkeypatch.setattr(whatsapp, "DB_FILE", m.db_dir / "ChatStorage.sqlite")

    real_copy = shutil.copy2

    def copy2(src: Any, dst: Any, **k: Any) -> Any:
        if Path(src).parent == m.db_dir:
            m.read(Path(src))
        return real_copy(src, dst, **k)
    monkeypatch.setattr(shutil, "copy2", copy2)

    return m


def result(capsys) -> dict[str, Any]:
    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert len(lines) == 1, f"expected one JSON line, got: {out!r}"
    return json.loads(lines[0])


def assert_read_only_through_bg(harness, mac: Mac) -> None:
    assert mac.reads and all(mac.reads), "WhatsApp was read outside a bg job"
    assert all(job[:2] == ["-c", str(harness.home / ".family" / "config.yaml")]
               for job in harness.jobs.detached)
    assert harness.desktop.windows == []


PYTHON = os.path.realpath(sys.executable)
CHATS = [("3B parents", 1, False), ("Neighbours ", 3, False), ("Kilo School families 🏫", 2, True)]


# ── already readable


@pytest.mark.macos
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
    assert mac.prompts == 0
    assert_read_only_through_bg(harness, mac)


@pytest.mark.macos
def test_chats_without_activity_in_the_last_days_are_left_out(harness, mac, capsys):
    mac.install_whatsapp([("3B parents", 1, False), ("Last year's 2B", 400, False)])

    assert harness.cli("setup", "whatsapp") == 0

    assert [c["name"] for c in result(capsys)["chats"]] == ["3B parents"]


@pytest.mark.macos
def test_days_widens_how_far_back_a_chat_counts(harness, mac, capsys):
    mac.install_whatsapp([("3B parents", 1, False), ("Last year's 2B", 400, False)])

    assert harness.cli("setup", "whatsapp", "--days", "500") == 0

    assert [c["name"] for c in result(capsys)["chats"]] == ["3B parents", "Last year's 2B"]


# ── hints


@pytest.mark.macos
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


@pytest.mark.macos
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


@pytest.mark.macos
def test_without_kids_in_the_config_yet_chats_have_no_hints(harness, mac, capsys):
    harness.config["kids"] = []
    mac.install_whatsapp(CHATS)

    assert harness.cli("setup", "whatsapp") == 0

    assert all("hint" not in c for c in result(capsys)["chats"])


# ── waiting for Full Disk Access


@pytest.mark.macos
def test_without_full_disk_access_it_opens_the_pane_and_waits_for_it(harness, mac, capsys):
    mac.install_whatsapp(CHATS)
    mac.full_disk_access_after = 3

    assert harness.cli("setup", "whatsapp") == 0

    res = result(capsys)
    assert res["result"] == "readable" and len(res["chats"]) == 3
    assert harness.opened == [PYTHON, ops.FULL_DISK_ACCESS_URL]
    assert ["open", "-R", PYTHON] in harness.commands
    assert mac.refused == 3 and mac.prompts == 0  # WhatsApp wasn't touched before Full Disk Access
    assert_read_only_through_bg(harness, mac)


@pytest.mark.macos
def test_full_disk_access_not_given_in_time_is_reported(harness, mac, capsys):
    mac.install_whatsapp(CHATS)
    mac.full_disk_access_after = 10_000
    start = mac.clock[0]

    assert harness.cli("setup", "whatsapp", "--timeout", "60") == 1

    res = result(capsys)
    assert res["result"] == "no-permission" and res["python"] == PYTHON
    assert "Full Disk Access" in res["next"] and "setup whatsapp --no-open" in res["next"]
    assert f"the Python file {os.path.basename(PYTHON)} is selected" in res["next"]
    assert "App Management" not in res["next"] and "Allow" not in res["next"]
    assert "chats" not in res
    assert harness.opened == [PYTHON, ops.FULL_DISK_ACCESS_URL]
    assert 0 < mac.clock[0] - start <= 60 + 5
    assert mac.reads == [] and mac.prompts == 0


@pytest.mark.macos
def test_an_allow_click_cannot_make_it_pass_without_full_disk_access(harness, mac, capsys):
    # On macOS 26 Allow lets one read through and macOS asks again on the next (ADR 0012).
    mac.install_whatsapp(CHATS)
    mac.full_disk_access_after = 10_000
    mac.allow = "allow"

    assert harness.cli("setup", "whatsapp", "--timeout", "30") == 1

    assert result(capsys)["result"] == "no-permission"
    assert mac.prompts == 0 and mac.reads == []  # macOS never asked
    assert whatsapp_python.granted() is None


# ── the Python with Full Disk Access, for doctor to compare with the evening job's (ADR 0012)


@pytest.mark.macos
def test_once_whatsapp_is_read_setup_records_the_real_path_of_the_python_allowed(harness, mac, capsys):
    mac.install_whatsapp(CHATS)
    mac.full_disk_access_after = 1

    assert harness.cli("setup", "whatsapp") == 0

    assert result(capsys)["result"] == "readable"
    assert whatsapp_python.granted() == PYTHON


@pytest.mark.macos
def test_without_full_disk_access_nothing_is_recorded(harness, mac, capsys):
    mac.install_whatsapp(CHATS)
    mac.full_disk_access_after = 10_000

    assert harness.cli("setup", "whatsapp", "--timeout", "0") == 1

    assert result(capsys)["result"] == "no-permission"
    assert whatsapp_python.granted() is None
    assert not whatsapp_python.path().exists()


@pytest.mark.macos
def test_a_read_that_does_not_finish_is_reported_as_waiting(harness, mac, capsys):
    mac.install_whatsapp(CHATS)
    harness.jobs.hangs = True

    assert harness.cli("setup", "whatsapp", "--timeout", "60") == 1

    res = result(capsys)
    assert res["result"] == "waiting" and "--no-open" in res["next"]
    assert "Allow" not in res["next"]


@pytest.mark.macos
def test_no_open_waits_without_opening_finder_or_system_settings_again(harness, mac, capsys):
    mac.install_whatsapp(CHATS)
    mac.full_disk_access_after = 2

    assert harness.cli("setup", "whatsapp", "--no-open") == 0

    assert result(capsys)["result"] == "readable"
    assert harness.opened == []


@pytest.mark.macos
def test_timeout_zero_only_reports_where_it_stands(harness, mac, capsys):
    mac.install_whatsapp(CHATS)
    mac.full_disk_access_after = 10_000

    assert harness.cli("setup", "whatsapp", "--no-open", "--timeout", "0") == 1

    assert result(capsys)["result"] == "no-permission"
    assert len(harness.jobs.detached) == 1


# ── when it doesn't work


@pytest.mark.macos
def test_without_whatsapp_for_mac_it_says_to_install_it(harness, mac, capsys):
    assert harness.cli("setup", "whatsapp") == 1  # checked once Full Disk Access is on

    res = result(capsys)
    assert res["result"] == "not-installed" and "App Store" in res["next"]
    assert harness.opened == []


@pytest.mark.macos
def test_full_disk_access_that_cannot_be_checked_leaves_whatsapp_untouched(harness, mac, capsys):
    mac.install_whatsapp(CHATS)
    mac.tcc_db.unlink()

    assert harness.cli("setup", "whatsapp", "--timeout", "0") == 1

    res = result(capsys)
    assert res["result"] == "unreadable" and "Full Disk Access" in res["error"]
    assert mac.reads == [] and mac.prompts == 0


@pytest.mark.macos
def test_a_background_job_that_does_not_start_is_reported(harness, mac, capsys):
    mac.install_whatsapp(CHATS)
    harness.jobs.detached_fails = "Bootstrap failed: 5: Input/output error"

    assert harness.cli("setup", "whatsapp") == 1

    res = result(capsys)
    assert res["result"] == "bg-failed" and "Input/output error" in res["error"]


@pytest.mark.macos
def test_the_read_step_refuses_to_run_outside_a_bg_job(harness, mac, monkeypatch, capsys):
    monkeypatch.delenv(ops.BG_ENV, raising=False)
    mac.install_whatsapp(CHATS)

    assert harness.cli("setup", "whatsapp", "--read") == 2

    assert mac.reads == []
    assert "bg" in capsys.readouterr().out


# ── Connect: WhatsApp, on the setup page

WHATSAPP_CHATS, WHATSAPP_PYTHON = CHATS, PYTHON


def at_the_whatsapp_step(harness) -> None:
    config_file(harness).parent.mkdir(parents=True, exist_ok=True)
    config_file(harness).write_text(yaml.safe_dump(harness.config, allow_unicode=True))
    save_progress(harness, {"phase": "connect", "source": "whatsapp",
                            "sources": {"wilma": "done", "gmail": "done", "ai": "done"}})


def check_whatsapp(url: str) -> Response:
    return call(url, "api/whatsapp/check", method="POST", body={})


@pytest.mark.macos
def test_a_whatsapp_the_job_can_read_ticks_the_entry_and_keeps_the_groups(harness, page, mac):
    harness.config["kids"][0]["name"] = "Mia Virtanen"
    at_the_whatsapp_step(harness)
    mac.install_whatsapp(WHATSAPP_CHATS)

    r = check_whatsapp(page.url)

    assert r.status == 200
    out = r.json()
    assert out["result"] == "readable"
    assert out["progress"]["sources"]["whatsapp"] == "done"
    assert out["progress"]["source"] == "myclub"
    found = [{"name": "3B parents", "last": "2026-09-25", "archived": False,
              "hint": {"kids": ["Mia Virtanen"], "matched": ["3B"]}},
             {"name": "Kilo School families 🏫", "last": "2026-09-24", "archived": True,
              "hint": {"kids": ["Mia Virtanen", "Leo"], "matched": ["Kilo School"]}},
             {"name": "Neighbours ", "last": "2026-09-23", "archived": False}]
    assert out["chats"] == found
    # Kept for the check page, which reads them from setup's progress, as the chat setup can.
    assert call(page.url, "api/state").json()["progress"]["whatsapp_chats"] == found
    assert setup_save.read(config_file(harness))["progress"]["whatsapp_chats"] == found
    assert harness.opened == []  # nothing for the family to do
    assert_read_only_through_bg(harness, mac)


@pytest.mark.macos
def test_the_button_shows_the_python_and_opens_full_disk_access(harness, page, mac):
    at_the_whatsapp_step(harness)

    r = call(page.url, "api/whatsapp/open", method="POST", body={})

    assert r.status == 200 and r.json() == {"result": "opened"}
    assert harness.opened == [WHATSAPP_PYTHON, ops.FULL_DISK_ACCESS_URL]
    assert ["open", "-R", WHATSAPP_PYTHON] in harness.commands


# The texts that name the Python file Finder selects, by its name on this Mac (#297).
PYTHON_FILE_TEXTS = ("whatsapp.explain", "whatsapp.picture.drag", "whatsapp.open.opened",
                     "health.fail.whatsapp.no-access", "finish.fail.whatsapp.no-access")


@pytest.mark.macos
@pytest.mark.parametrize("version", ["3.13", "3.14"])
def test_the_page_names_the_python_file_finder_selects(harness, page, monkeypatch, version):
    # A parent stopped at "the python3 file" while Finder had python3.13 selected. The name comes
    # from the file, so a newly pinned Python changes it too.
    python = (harness.home / "ParentRecap" / "runtime" / f"python-{version}.0-20261003" / "bin" /
              f"python{version}")
    python.parent.mkdir(parents=True)
    python.touch()
    venv = harness.home / "ParentRecap" / "app" / ".venv" / "bin" / "python"
    venv.parent.mkdir(parents=True)
    venv.symlink_to(python)
    monkeypatch.setattr(sys, "executable", str(venv))
    at_the_whatsapp_step(harness)

    name = call(page.url, "api/state").json()["whatsapp"]["python"]
    assert call(page.url, "api/whatsapp/open", method="POST", body={}).json() == \
        {"result": "opened"}

    revealed = harness.opened[0]
    assert ["open", "-R", revealed] in harness.commands  # the file Finder selects
    assert name == Path(revealed).name == f"python{version}"
    for language, table in json.loads((PAGE_DIR / "text.json").read_text()).items():
        for key in PYTHON_FILE_TEXTS:
            assert f"python{version}" in table[key].replace("{python}", name), (language, key)


def test_every_text_that_names_the_python_file_takes_the_name_from_the_server(page):
    text = json.loads((PAGE_DIR / "text.json").read_text())
    for language, table in text.items():
        for key in PYTHON_FILE_TEXTS:
            assert "{python}" in table[key], (language, key)
        # No text names a file of its own, such as python3, that Finder may not show.
        assert not [k for k, v in table.items() if "python3" in v.lower()], language

    # The pictures show the same name, which the page puts in (the browser tests).
    html = call(page.url).body.decode()
    whatsapp_step = re.search(r'<div id="source-whatsapp".*?\n    </div>\n', html, re.S).group(0)
    assert 'id="whatsapp-explain" data-text="whatsapp.explain"' in whatsapp_step
    assert 'id="whatsapp-drag" data-text="whatsapp.picture.drag"' in whatsapp_step
    assert len(re.findall(r'<text class="python-file"', whatsapp_step)) == 2
    assert not re.search(r'<text(?! class="python-file")[^>]*>python', whatsapp_step)


@pytest.mark.macos
def test_without_full_disk_access_the_entry_ticks_itself_once_it_is_given(harness, page, mac):
    at_the_whatsapp_step(harness)
    mac.install_whatsapp(WHATSAPP_CHATS)
    mac.full_disk_access_after = 2  # the family turns the switch on while the page checks

    assert check_whatsapp(page.url).json() == {"result": "no-permission"}
    assert check_whatsapp(page.url).json() == {"result": "no-permission"}
    assert statuses(page.url)["whatsapp"] == "to-do"
    out = check_whatsapp(page.url).json()

    assert out["result"] == "readable" and len(out["chats"]) == 3
    assert statuses(page.url)["whatsapp"] == "done"
    assert mac.prompts == 0
    assert_read_only_through_bg(harness, mac)


@pytest.mark.macos
def test_a_read_that_ends_after_the_parent_moved_on_keeps_their_choice(harness, page, mac,
                                                                      monkeypatch):
    at_the_whatsapp_step(harness)
    mac.install_whatsapp(WHATSAPP_CHATS)
    read = setup_steps.read_whatsapp_through_bg

    def slow(*a: Any) -> dict[str, Any]:  # the parent skips WhatsApp while it reads
        out = read(*a)
        save_progress(harness, {**json.loads(progress_file(harness).read_text()),
                                "source": "myclub", "sources": {"wilma": "done", "gmail": "done",
                                                                "ai": "done", "whatsapp": "skipped"}})
        return out
    monkeypatch.setattr(setup_steps, "read_whatsapp_through_bg", slow)

    assert check_whatsapp(page.url).json()["result"] == "readable"

    progress = call(page.url, "api/state").json()["progress"]
    assert progress["sources"]["whatsapp"] == "skipped" and progress["source"] == "myclub"

    monkeypatch.setattr(setup_steps, "read_whatsapp_through_bg", read)
    save_progress(harness, {**progress, "source": "gmail",  # the parent went back to Gmail
                            "sources": {**progress["sources"], "whatsapp": "to-do"}})
    assert check_whatsapp(page.url).json()["progress"]["source"] == "gmail"  # not moved on
    assert statuses(page.url)["whatsapp"] == "done"


@pytest.mark.macos
def test_an_allow_click_cannot_tick_the_entry_without_full_disk_access(harness, page, mac):
    # On macOS 26 Allow lets one read through and macOS asks again on the next (ADR 0012).
    at_the_whatsapp_step(harness)
    mac.install_whatsapp(WHATSAPP_CHATS)
    mac.full_disk_access_after = 10_000
    mac.allow = "allow"

    assert check_whatsapp(page.url).json() == {"result": "no-permission"}
    assert check_whatsapp(page.url).json() == {"result": "no-permission"}
    assert mac.prompts == 0 and mac.reads == []  # macOS never asked
    assert statuses(page.url)["whatsapp"] == "to-do"


@pytest.mark.macos
def test_a_read_that_does_not_finish_is_waiting(harness, page, mac):
    at_the_whatsapp_step(harness)
    mac.install_whatsapp(WHATSAPP_CHATS)
    harness.jobs.hangs = True

    assert check_whatsapp(page.url).json() == {"result": "waiting"}
    assert not any(mac.reads)
    assert statuses(page.url)["whatsapp"] == "to-do"


@pytest.mark.macos
def test_without_whatsapp_for_mac_it_says_so(harness, page, mac):
    at_the_whatsapp_step(harness)

    assert check_whatsapp(page.url).json() == {"result": "not-installed"}


@pytest.mark.macos
def test_a_read_that_fails_says_so_without_the_error(harness, page, mac, monkeypatch):
    at_the_whatsapp_step(harness)
    mac.install_whatsapp(WHATSAPP_CHATS)
    monkeypatch.setattr(whatsapp, "check_access", lambda: "disk I/O error at /Users/x")

    assert check_whatsapp(page.url).json() == {"result": "unreadable"}

    harness.jobs.detached_fails = "Bootstrap failed: 5: Input/output error"
    assert check_whatsapp(page.url).json() == {"result": "bg-failed"}
    assert statuses(page.url)["whatsapp"] == "to-do"


@pytest.mark.macos
def test_system_settings_that_does_not_open_says_so(harness, page, mac, monkeypatch):
    at_the_whatsapp_step(harness)
    others = subprocess.run

    def run(cmd: list[str], *a: Any, **k: Any) -> subprocess.CompletedProcess:
        if Path(cmd[0]).name == "open":
            return subprocess.CompletedProcess(cmd, 1, "", "")
        return others(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", run)

    assert call(page.url, "api/whatsapp/open", method="POST", body={}).json() == \
        {"result": "not-opened"}


def test_the_whatsapp_calls_take_nothing(page):
    for path in ("api/whatsapp/check", "api/whatsapp/open"):
        assert call(page.url, path, method="POST", body={"x": 1}).status == 400, path


def test_the_whatsapp_step_shows_two_or_three_pictures_of_what_to_switch_on(page):
    html = call(page.url).body.decode()

    step = re.search(r'<div id="source-whatsapp".*?\n    </div>\n', html, re.S).group(0)
    figures = re.findall(r"<figure>.*?</figure>", step, re.S)
    assert 2 <= len(figures) <= 3
    for figure in figures:
        assert "<svg" in figure and re.search(r'<figcaption[^>]* data-text="[\w.-]+">', figure)
    assert 'id="whatsapp-open"' in step
    assert "done" not in step.lower()  # it ticks itself: there's no Done to press


def test_every_whatsapp_result_is_explained_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        for result in setup_server.whatsapp.WHATSAPP_RESULTS:
            assert table.get(f"whatsapp.{result}", "").strip(), (language, result)
        for result in setup_server.whatsapp.WHATSAPP_OPEN_RESULTS:
            assert table.get(f"whatsapp.open.{result}", "").strip(), (language, result)


def test_the_whatsapp_step_names_full_disk_access_and_never_allow(page):
    # ADR 0012: only Full Disk Access lasts; an Allow click lets one read through.
    text = json.loads((PAGE_DIR / "text.json").read_text())
    names = {"en": "Full Disk Access", "fi": "Täysi levyn käyttöoikeus", "zh": "完全磁盘访问权限"}
    allow = {"en": ("App Management", "Allow"), "fi": ("Apin hallinta", "Salli"),
             "zh": ("App 管理", "允许")}

    for language, table in text.items():
        whatsapp = {k: v for k, v in table.items() if k.startswith("whatsapp.")}
        assert names[language] in whatsapp["whatsapp.explain"], language
        for key, value in whatsapp.items():
            assert not any(word in value for word in allow[language]), (language, key)
    html = call(page.url).body.decode()
    step = re.search(r'<div id="source-whatsapp".*?\n    </div>\n', html, re.S).group(0)
    assert "allow" not in step.lower()


# ── Check and Finish: why WhatsApp isn't OK, and the step that fixes it


# Each way the WhatsApp check fails on the fake Mac: its status and the reason it carries.
WHATSAPP_FAILURES = {
    "no-access": (ops.FAIL, "no-access"),
    "hangs": (ops.FAIL, "not-finished"),
    "not-started": (ops.WARN, "not-finished"),
    "missing-groups": (ops.WARN, "missing-groups"),
    "list-failed": (ops.FAIL, "list-failed"),
}


def break_whatsapp(mac, monkeypatch, failure: str) -> None:
    """The fake Mac as it is when the WhatsApp check fails in one of WHATSAPP_FAILURES' ways.
    For missing-groups, Leo's piano group has a new name on it."""
    piano = "Leo piano autumn" if failure == "missing-groups" else "Leo piano"
    mac.install_whatsapp([("3B parents", 1, False), (piano, 2, False)])
    if failure == "no-access":
        mac.full_disk_access_after = 10_000
    elif failure == "hangs":
        mac.jobs.hangs = True
    elif failure == "not-started":
        mac.jobs.detached_fails = "Bootstrap failed: 5: Input/output error"
    elif failure == "list-failed":
        def malformed(days: int = 180) -> list[dict]:
            raise sqlite3.DatabaseError("database disk image is malformed")
        monkeypatch.setattr(whatsapp, "list_groups", malformed)


FULL_DISK_ACCESS = {"en": "Full Disk Access", "fi": "levyn käyttöoikeu", "zh": "完全磁盘访问权限"}
WHATSAPP_FOR_MAC = {"en": "WhatsApp for Mac", "fi": "WhatsAppin Mac-sovellus", "zh": "WhatsApp Mac 版"}
PYTHON_FILE = os.path.basename(WHATSAPP_PYTHON)  # the name the server gives the page


def next_step(table: dict[str, str], prefix: str, check: dict[str, Any]) -> str:
    """What the page says to do under a check that isn't OK, on the check page ("health") or
    Finish ("finish"), as page.js's nextStep picks it: its reason's own text, else the check's."""
    own = f"{prefix}.fail.{check['check']}.{check.get('reason')}"
    key = own if check.get("reason") and own in table else f"{prefix}.fail.{check['check']}"
    return table[key].replace("{groups}", ", ".join(check.get("groups", []))) \
        .replace("{python}", PYTHON_FILE)


def assert_says_why(prefix: str, check: dict[str, Any]) -> None:
    """In every language, the page says why WhatsApp isn't OK and what fixes it."""
    for language, table in json.loads((PAGE_DIR / "text.json").read_text()).items():
        said = next_step(table, prefix, check)
        assert said != table[f"{prefix}.fail.whatsapp"], (language, check)
        if check["reason"] == "no-access":  # only the parent can turn it on, with the button
            assert FULL_DISK_ACCESS[language] in said and table["whatsapp.open"] in said, language
            assert PYTHON_FILE in said and "{python}" not in said, language  # what Finder selects
            assert "Claude" in said and "ChatGPT" in said, language
        elif check["reason"] == "missing-groups":
            assert "Leo piano" in said and "WhatsApp" in said, language
        else:
            assert WHATSAPP_FOR_MAC[language] in said, language
        if check["reason"] == "list-failed":
            assert "parent-recap doctor" in said, language


def back_to_whatsapp(url: str, body: Any = None) -> Response:
    return call(url, "api/whatsapp/back", method="POST", body={} if body is None else body)


def back_to_connect(url: str, body: Any = None) -> Response:
    return call(url, "api/connect/back", method="POST", body={} if body is None else body)


@pytest.mark.macos
@pytest.mark.parametrize("failure", WHATSAPP_FAILURES)
def test_the_check_page_says_why_whatsapp_isn_t_ok(harness, page, mac, monkeypatch, failure):
    at_the_check_step(harness)
    break_whatsapp(mac, monkeypatch, failure)
    real = ops.health_checks  # only the WhatsApp check, for real, through the bg job
    monkeypatch.setattr(ops, "health_checks", lambda config, **k: real(config, whatsapp_only=True))

    confirm(page.url, whatsapp=["3B parents", "Leo piano"])
    out = health_result(page.url)

    status, reason = WHATSAPP_FAILURES[failure]
    check = {"check": "whatsapp", "status": setup_server.health.HEALTH_STATUSES[status], "reason": reason,
             **({"groups": ["Leo piano"]} if reason == "missing-groups" else {})}
    assert out["checks"] == [check]
    # Only the reason, and the groups the check page lists anyway: no path and no error.
    assert WHATSAPP_PYTHON not in json.dumps(out) and "malformed" not in json.dumps(out)
    assert_says_why("health", check)
    assert all(mac.reads) and mac.prompts == 0  # only ever read in a bg job, and never asked


def test_a_reason_the_page_doesn_t_know_gets_the_general_text():
    for table in json.loads((PAGE_DIR / "text.json").read_text()).values():
        for prefix in ("health", "finish"):
            check = {"check": "whatsapp", "status": "fail", "reason": "something-new"}
            assert next_step(table, prefix, check) == table[f"{prefix}.fail.whatsapp"]
            assert next_step(table, prefix, {"check": "whatsapp", "status": "fail"}) == \
                table[f"{prefix}.fail.whatsapp"]


def test_every_reason_whatsapp_isn_t_ok_has_its_own_text_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        assert table.get("whatsapp.back", "").strip(), language
        for prefix in ("health", "finish"):
            for reason in ops.WHATSAPP_REASONS:
                said = table.get(f"{prefix}.fail.whatsapp.{reason}", "")
                assert said.strip() and said != table[f"{prefix}.fail.whatsapp"], (language, reason)
            assert "{groups}" in table[f"{prefix}.fail.whatsapp.missing-groups"], language


@pytest.mark.macos
def test_groups_not_on_this_mac_lead_back_to_whatsapp_s_step_to_read_them_again(harness, page,
                                                                                 mac, health):
    at_the_check_step(harness)
    health.results = [(ops.FAIL, "gmail", "Gmail", "IMAP login failed"),
                      (ops.WARN, "whatsapp", "WhatsApp", "not on this Mac", "missing-groups",
                       ("3B parents",))]
    confirm(page.url)
    assert health_result(page.url)["result"] == "not-ok"

    r = back_to_whatsapp(page.url)

    assert r.status == 200
    progress = r.json()["progress"]
    assert (progress["phase"], progress["source"]) == ("connect", "whatsapp")
    assert progress["sources"]["whatsapp"] == "done"
    # What the health check said was about the groups as they were.
    assert call(page.url, "api/check/health", method="POST", body={}).json() == \
        {"result": "no-check"}
    # WhatsApp's step reads the groups again, and Check offers the new name.
    mac.install_whatsapp([("3B parents 2026", 1, False)])
    out = check_whatsapp(page.url).json()
    assert out["result"] == "readable" and out["progress"]["source"] is None
    assert "3B parents 2026" in [g["name"] for g in check_page(page.url)["whatsapp"]]


def test_only_check_and_finish_go_back_to_whatsapp_and_never_while_checking(harness, page,
                                                                            health):
    at_the_check_step(harness)
    assert back_to_whatsapp(page.url, {"x": 1}).status == 400
    health.go.clear()
    confirm(page.url)
    assert back_to_whatsapp(page.url).status == 409  # the health check is running
    health.go.set()
    health_result(page.url)

    more_progress(harness, phase="first-brief")
    assert back_to_whatsapp(page.url).status == 400
    more_progress(harness, phase="check", sources={"whatsapp": "skipped"})
    assert back_to_whatsapp(page.url).status == 400
    assert setup_save.read(config_file(harness))["progress"]["phase"] == "check"


def test_check_leads_back_to_connect_s_list_where_a_skipped_source_can_be_opened(harness, page,
                                                                                health):
    at_the_check_step(harness)  # MyClub skipped
    confirm(page.url)
    assert health_result(page.url)["result"] == "ok"
    more_progress(harness, phase="check")

    r = back_to_connect(page.url)

    assert r.status == 200
    progress = r.json()["progress"]
    assert (progress["phase"], progress["source"]) == ("connect", None)
    assert progress["sources"]["myclub"] == "skipped"  # until it's opened from the list
    assert call(page.url, "api/check/health", method="POST", body={}).json() == \
        {"result": "no-check"}
    assert source(page.url, "myclub", "open").json()["progress"]["source"] == "myclub"
    assert statuses(page.url)["myclub"] == "to-do"


def test_only_check_and_finish_go_back_to_connect_with_a_skipped_source_never_while_checking(
        harness, page, health):
    at_the_check_step(harness)
    assert back_to_connect(page.url, {"x": 1}).status == 400
    health.go.clear()
    confirm(page.url)
    assert back_to_connect(page.url).status == 409  # the health check is running
    health.go.set()
    health_result(page.url)

    more_progress(harness, phase="first-brief")
    assert back_to_connect(page.url).status == 400
    more_progress(harness, phase="check", sources={"myclub": "done"})
    assert back_to_connect(page.url).status == 400  # nothing skipped to add
    assert setup_save.read(config_file(harness))["progress"]["phase"] == "check"


def test_the_page_offers_the_way_back_to_connect_on_check_and_finish_only_with_a_skipped_source():
    html_text = (PAGE_DIR / "index.html").read_text()

    for section, link in (("check", "check-back"), ("finish", "finish-back")):
        part = html_text.split(f'<section id="{section}"', 1)[1].split("</section>", 1)[0]
        assert f'id="{link}"' in part and 'data-text="connect.back"' in part, section
    for language, table in json.loads((PAGE_DIR / "text.json").read_text()).items():
        assert table.get("connect.back", "").strip(), language
