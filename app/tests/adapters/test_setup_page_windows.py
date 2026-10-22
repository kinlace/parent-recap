"""The setup page from a fresh install to Finish with the gateway, Gmail and MyClub, driven with
HTTP calls as the page makes them (ADR 0006, ADR 0019).

Gmail's IMAP server, the gateway and the Brief's Sources are the harness's fakes; MyClub's
calendar comes from this file's fake web. On any system the secrets, the evening jobs and the wake
are its in-memory fakes too; on Windows a second run stores the secrets in the real Credential Manager and registers the evening jobs in the real
Task Scheduler, under names of the test's own, removed after, and reads the real "Allow wake
timers". After Finish, uninstall removes all of it again. The steps themselves are tested one at a
time in the e2e test_setup_*.py files, and uninstall in test_uninstall.py."""
from __future__ import annotations

import subprocess
import sys
import uuid
from typing import Any

import pytest
import yaml

from conftest import REAL_RUN, msg
from family_brief import gateway
from family_brief.collectors import myclub
from family_brief.config import Config
from family_brief.system import jobs, jobs_windows, secrets, wake, wake_windows, windows_only
from setup_page import (APP_PASSWORD, call, check_page, clock, config_file,  # noqa: F401
                        finish, finished, health_result, page, source)
from test_setup_ai import GATEWAY_KEY, save_gateway_key
from test_setup_check import read_to_the_end, start_reading
from test_setup_first_brief import BRIEF_TEXT, brief_made, make_brief, send_it_to_me
from test_setup_gmail import TYPED, connect_gmail
from test_setup_myclub import CALENDAR, LINK as MYCLUB_LINK, add_kid, save_link
from test_setup_status import install_program
from test_uninstall import leftovers, remove_at_once, uninstall

PARENT = "parent@example.com"
# Windows' own Task Scheduler takes minutes on CI's runner to register, list and check the tasks
# (test_jobs_windows.py takes as long), so Finish and the checks around it are given that.
SLOW = 600
SENDERS = ["Opettaja <opettaja@edu.espoo.fi>"] * 3 + ["Coach <coach@tapiolan-seura.fi>"] * 2


def school_mail(harness) -> None:
    """Gmail, taking only the App Password, with the school's and the club's mail, whose senders
    the page lists: the school's first message is the trip the first Brief has to say."""
    harness.source_fakes["gmail"].password = APP_PASSWORD
    harness.sources["gmail"] = [msg("gmail", f"100{n}", "2026-09-27T09:00:00+03:00",
                                    BRIEF_TEXT if n == 1 else "Hei!", sender=sender,
                                    subject="Trip" if n == 1 else "Viesti")
                                for n, sender in enumerate(SENDERS, 1)]
    harness.model_reply = {"per_kid": [], "calendar_events": [], "message_digest": BRIEF_TEXT}


class Web:
    """The web the steps reach besides Gmail and the gateway: MyClub's calendar, and Helsinki's
    events for Weekend Picks."""

    class Reply:
        def __init__(self, text: str) -> None:
            self.status_code, self.text = 200, text

        def raise_for_status(self) -> None:
            pass

    def get(self, url: str, **_k: Any) -> "Web.Reply":
        return self.Reply(CALENDAR if "myclub" in url else '{"data": []}')


class TaskScheduler:
    """Windows' own Task Scheduler for the evening jobs, under names of the test's own. A one-off
    run, as the first Brief is, stays the harness's, which runs it in this process on its fakes."""

    def __init__(self, harness) -> None:
        self.one_off = harness.jobs

    def __getattr__(self, name: str) -> Any:
        return getattr(jobs_windows, name)

    def run_detached(self, *args: Any, **kwargs: Any) -> Any:
        return self.one_off.run_detached(*args, **kwargs)


@pytest.fixture
def fresh_install(harness, monkeypatch):
    """A PC the install line has just put Parent Recap on: the program and its record, running as
    the program's own Python, and no config yet, with the fakes the steps reach."""
    install_program(harness)
    venv = harness.home / "ParentRecap" / "app" / ".venv"
    python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("")
    monkeypatch.setattr(sys, "executable", str(python))
    monkeypatch.setattr(myclub.requests, "get", Web().get)
    school_mail(harness)
    return harness


@pytest.fixture
def windows_pc(fresh_install, credential_manager, monkeypatch):
    """The same, with Windows' own Credential Manager, Task Scheduler and power settings, past
    the harness's fake `subprocess.run`, which stands in for the AIs' programs only."""
    harness = fresh_install
    credential_manager.extend([f"gmail-imap-{PARENT}", gateway.ACCOUNT])
    monkeypatch.setattr(jobs, "_adapter", TaskScheduler(harness))
    monkeypatch.setattr(jobs_windows, "PREFIX", f"Parent Recap test {uuid.uuid4().hex[:8]} ")
    monkeypatch.setattr(wake, "_adapter", wake_windows)
    fake = subprocess.run

    def run(cmd: list[str], *a: Any, **k: Any) -> subprocess.CompletedProcess:
        windows = cmd[0].lower().removesuffix(".exe") in ("powershell", "powercfg")
        return (REAL_RUN if windows else fake)(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", run)
    yield harness
    jobs.remove_all()


def walk_to_finish(harness, url: str) -> dict[str, Any]:
    """Every step a family without Wilma takes, with a Gateway Key: the language, Welcome, each
    Source the page lists, Working, Check and the first Brief, then Finish, with Weekend Picks
    turned on as manage turns them on. Returns how Finish ended."""
    state = call(url, "api/state").json()
    assert state["system"] == ("windows" if sys.platform == "win32" else "macos")
    assert call(url, "api/language", method="POST", body={"language": "en"}).status == 200
    assert call(url, "api/welcome", method="POST",
                body={"ai": "gateway", "partner": None, "feedback": False}).status == 200

    listed = [s["name"] for s in call(url, "api/state").json()["connect"]["sources"]]
    # WhatsApp for Mac, and its Full Disk Access, only on macOS.
    assert listed == ["wilma", "gmail", "ai", *(["whatsapp"] if sys.platform == "darwin" else []),
                      "myclub"]
    for name in listed:
        if name in ("wilma", "whatsapp"):  # the school has no Wilma; WhatsApp can wait
            assert source(url, name, "skip").status == 200, name
        elif name == "gmail":
            assert connect_gmail(url, PARENT, TYPED).json()["result"] == "saved"
        elif name == "ai":
            assert save_gateway_key(url, GATEWAY_KEY).json()["result"] == "saved"
        elif name == "myclub":
            assert add_kid(url, "Mia").json()["result"] == "added"
            out = save_link(url, "Mia", MYCLUB_LINK).json()
            assert out["result"] == "saved" and out["progress"]["source"] is None
    statuses = call(url, "api/state").json()["progress"]["sources"]
    assert {statuses[s] for s in ("gmail", "ai", "myclub")} == {"done"}

    assert start_reading(url).status == 200
    assert read_to_the_end(url)["result"] == "read"
    offered = check_page(url)
    assert offered["whatsapp"] is None
    answers = {"kids": [{"name": k["name"], "everyday_name": k["everyday_name"]}
                        for k in offered["kids"]],
               "whatsapp": None, "senders": [s["domain"] for s in offered["senders"] if s["ticked"]],
               "recipients": offered["recipients"], "evening": offered["evening"]}
    assert call(url, "api/check", method="POST", body=answers).status == 200
    health = health_result(url, SLOW)
    assert health["result"] == "ok", health

    assert make_brief(url).status == 200
    assert brief_made(url)["result"] == "made"
    assert send_it_to_me(url).json() == {"result": "sent", "to": PARENT}
    assert call(url, "api/brief/done", method="POST", body={}).status == 200

    data = yaml.safe_load(config_file(harness).read_text())
    data["weekend_events"] = {**data.get("weekend_events", {}), "enabled": True}
    config_file(harness).write_text(yaml.safe_dump(data, allow_unicode=True))
    assert finish(url).status == 200
    return finished(url, SLOW)


def assert_finished(harness, out: dict[str, Any]) -> None:
    """The secrets where the evening run reads them, and both jobs in the system's scheduler."""
    outcomes = {o["outcome"]: o["ok"] for o in out["outcomes"]}
    assert {k: v for k, v in outcomes.items() if k != "wake"} == \
        {"installed": True, "doctor": True, "brief": True, "nightly": True}, out
    assert jobs.installed() == {jobs.DAILY, jobs.WEEKEND}
    assert jobs.program(jobs.DAILY) == [sys.executable, "-m", "family_brief", "run"]
    assert gateway.key() == GATEWAY_KEY
    cfg = Config.load(config_file(harness))
    assert (cfg.llm.backend, cfg.gmail.username, cfg.kids[0].name) == ("gateway", PARENT, "Mia")
    assert [e.to for e in harness.sent] == [[PARENT]]


def uninstall_leaves_nothing(harness, monkeypatch) -> None:
    """Uninstall, confirmed with the archive removed, leaves no job, secret or file of Parent
    Recap's."""
    monkeypatch.setattr(windows_only, "remove_after_exit", remove_at_once)

    assert uninstall(harness, "--confirm", "--remove-archive") == 0

    assert jobs.found() == []
    assert [n for n in (f"gmail-imap-{PARENT}", gateway.ACCOUNT) if secrets.has(n)] == []
    assert leftovers(harness.home) == []


def test_a_family_sets_up_with_the_gateway_gmail_and_myclub_on_the_page_and_uninstalls(
        fresh_install, page, monkeypatch):
    out = walk_to_finish(fresh_install, page.url)

    assert_finished(fresh_install, out)
    assert fresh_install.secrets[f"gmail-imap-{PARENT}"] == APP_PASSWORD
    assert out["result"] == "done" and out["wake"] == "set"
    uninstall_leaves_nothing(fresh_install, monkeypatch)
    assert fresh_install.wake.wakes == []


@pytest.mark.windows
def test_on_windows_setup_stores_its_secrets_in_credential_manager_and_registers_both_tasks(
        windows_pc, page, monkeypatch):
    out = walk_to_finish(windows_pc, page.url)

    assert_finished(windows_pc, out)
    assert secrets.get(f"gmail-imap-{PARENT}") == APP_PASSWORD
    # Windows asks nobody about waking the PC: whether it does is the family's own "Allow wake
    # timers", and none of macOS's dialogs or apps was opened on the way.
    assert out["wake"] in ("already-set", "never-sleeps", "not-set")
    assert not any(c[0] in ("osascript", "open", "security") for c in windows_pc.commands)
    # And uninstall takes all of it out of Credential Manager and Task Scheduler again.
    uninstall_leaves_nothing(windows_pc, monkeypatch)
