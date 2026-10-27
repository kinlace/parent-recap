"""WhatsApp on the setup page's Check and Finish: why its check isn't OK, in each language, and
the way back to Connect's list for a skipped Source. The page has no WhatsApp step until the
Linked Device's (ADR 0027): none asks for Full Disk Access or names the Python file.

The fake Mac here, WhatsApp for Mac's database and macOS's permission file in the temporary HOME
that only a `bg` job with Full Disk Access can read, is the one other WhatsApp tests import, until
the database reader goes (#514). Calls are made as the page makes them (`setup_page.py`)."""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import pytest

from family_brief import ops, setup_save, setup_server
from fake_system import FakeJobs
from family_brief.collectors import whatsapp
from setup_page import (PAGE_DIR, Response, at_the_check_step, call, config_file, confirm,
                        health_result, more_progress, source, statuses)
from setup_page import clock, health, page  # noqa: F401

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
        """(name, days before the last message, archived) for each group chat. Installed again,
        it has the chats WhatsApp has synced by then."""
        self.db_dir.mkdir(parents=True, exist_ok=True)
        db = self.db_dir / "ChatStorage.sqlite"
        db.unlink(missing_ok=True)
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


WHATSAPP_PYTHON = os.path.realpath(sys.executable)


# ── Check and Finish: why WhatsApp isn't OK


# Each way the WhatsApp check fails on the fake Mac: its status and the reason it carries.
WHATSAPP_FAILURES = {
    "no-access": (ops.FAIL, "no-access"),
    "hangs": (ops.FAIL, "not-finished"),
    "not-started": (ops.WARN, "not-finished"),
    "missing-groups": (ops.WARN, "missing-groups"),
    "list-failed": (ops.FAIL, "list-failed"),
    "not-installed": (ops.FAIL, "no-whatsapp"),
    "unreadable": (ops.FAIL, "unreadable"),
    "access-unknown": (ops.FAIL, "access-unknown"),
}


def break_whatsapp(mac, monkeypatch, failure: str) -> None:
    """The fake Mac as it is when the WhatsApp check fails in one of WHATSAPP_FAILURES' ways.
    For missing-groups, Leo's piano group has a new name on it."""
    if failure == "not-installed":  # WhatsApp for Mac was never installed or signed in here
        return
    piano = "Leo piano autumn" if failure == "missing-groups" else "Leo piano"
    mac.install_whatsapp([("3B parents", 1, False), (piano, 2, False)])
    if failure == "unreadable":
        def broken() -> Path:
            raise OSError(5, "Input/output error")
        monkeypatch.setattr(whatsapp, "_snapshot_db", broken)
    elif failure == "access-unknown":  # the one file only Full Disk Access opens is gone
        mac.tcc_db.unlink()
    elif failure == "no-access":
        mac.full_disk_access_after = 10_000
    elif failure == "hangs":
        mac.jobs.hangs = True
    elif failure == "not-started":
        mac.jobs.detached_fails = "Bootstrap failed: 5: Input/output error"
    elif failure == "list-failed":
        def malformed(days: int = 180) -> list[dict]:
            raise sqlite3.DatabaseError("database disk image is malformed")
        monkeypatch.setattr(whatsapp, "list_groups", malformed)


# What the page no longer says in any language, now that it has no Full Disk Access step.
FULL_DISK_ACCESS_WORDS = {"en": ("Full Disk Access", "Finder", "Python"),
                          "fi": ("levyn käyttöoikeu", "Finder", "Python"),
                          "zh": ("完全磁盘访问权限", "访达", "Python")}
# Doctor's reasons that only Full Disk Access fixed: the page has no text of its own for them, so
# they get the check's general text.
FULL_DISK_ACCESS_REASONS = ("no-access", "access-unknown")
WHATSAPP_FOR_MAC = {"en": "WhatsApp for Mac", "fi": "WhatsAppin Mac-sovellus", "zh": "WhatsApp Mac 版"}


def next_step(table: dict[str, str], prefix: str, check: dict[str, Any]) -> str:
    """What the page says to do under a check that isn't OK, on the check page ("health") or
    Finish ("finish"), as page.js's nextStep picks it: its reason's own text, else the check's."""
    own = f"{prefix}.fail.{check['check']}.{check.get('reason')}"
    key = own if check.get("reason") and own in table else f"{prefix}.fail.{check['check']}"
    return table[key].replace("{groups}", ", ".join(check.get("groups", [])))


def assert_says_why(prefix: str, check: dict[str, Any]) -> None:
    """In every language, the page says why WhatsApp isn't OK and what fixes it, and never sends
    the parent to Full Disk Access."""
    for language, table in json.loads((PAGE_DIR / "text.json").read_text()).items():
        said = next_step(table, prefix, check)
        assert not any(word in said for word in FULL_DISK_ACCESS_WORDS[language]), (language, check)
        if check["reason"] in FULL_DISK_ACCESS_REASONS:
            assert said == table[f"{prefix}.fail.whatsapp"], (language, check)
            continue
        assert said != table[f"{prefix}.fail.whatsapp"], (language, check)
        if check["reason"] == "missing-groups":
            assert "Leo piano" in said and "WhatsApp" in said, language
        else:
            assert WHATSAPP_FOR_MAC[language] in said, language
        if check["reason"] == "list-failed":
            assert "parent-recap doctor" in said, language


def whatsapp_detail(harness) -> str:
    """What doctor says about WhatsApp, as `parent-recap doctor` prints it, for the same Mac."""
    results = ops.health_checks(str(config_file(harness)), whatsapp_only=True)
    [check] = [c for c in results if c.key == "whatsapp"]
    return check.detail


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
    [shown] = out["checks"]
    # Doctor's own words come along, for the page to fold under its text with Copy (#500).
    detail = shown.pop("detail")
    assert shown == check
    assert detail == whatsapp_detail(harness)
    assert_says_why("health", check)
    assert all(mac.reads) and mac.prompts == 0  # only ever read in a bg job, and never asked


def test_a_reason_the_page_doesn_t_know_gets_the_general_text():
    for table in json.loads((PAGE_DIR / "text.json").read_text()).values():
        for prefix in ("health", "finish"):
            check = {"check": "whatsapp", "status": "fail", "reason": "something-new"}
            assert next_step(table, prefix, check) == table[f"{prefix}.fail.whatsapp"]
            assert next_step(table, prefix, {"check": "whatsapp", "status": "fail"}) == \
                table[f"{prefix}.fail.whatsapp"]


def test_every_reason_whatsapp_isn_t_ok_but_full_disk_access_has_its_own_text_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        for prefix in ("health", "finish"):
            for reason in ops.WHATSAPP_REASONS:
                said = table.get(f"{prefix}.fail.whatsapp.{reason}")
                if reason in FULL_DISK_ACCESS_REASONS:
                    assert said is None, (language, reason)
                else:
                    assert said and said.strip() and said != table[f"{prefix}.fail.whatsapp"], \
                        (language, reason)
            assert "{groups}" in table[f"{prefix}.fail.whatsapp.missing-groups"], language


def test_no_text_on_the_page_names_full_disk_access_finder_or_the_python_file(page):
    for language, table in json.loads((PAGE_DIR / "text.json").read_text()).items():
        for key, said in table.items():
            assert not any(word.lower() in said.lower() for word in FULL_DISK_ACCESS_WORDS[language]), \
                (language, key)
        assert not [key for key in table if key.startswith("whatsapp.")], language  # its old step
    html = call(page.url).body.decode()
    assert 'id="source-whatsapp"' not in html and "python-file" not in html


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
