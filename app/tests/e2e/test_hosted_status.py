"""The hosted version's status (`signup status`): every Hosted Household the sign-up made, with its
last run and doctor's statuses, read by running `signup health` as each Household's user.

The Households are made by the real sign-up over HTTP (`test_signup`'s fixture), so each has the
config, state, archive and run record a real one has, its first Brief among them. Their evening
runs are run as their users through the fake household host, the program in-process. Assertions
are on what the maintainers' command prints, and on what it must never print.
"""
from __future__ import annotations

import json
import re
from datetime import timedelta, timezone

import pytest
import time_machine

from family_brief.config import Config
from family_brief.last_run import path as last_run_path
from family_brief.signup import status
from family_brief.signup.invites import Recipient
from conftest import NOW
from test_signup import signup, submit, wilma_cli  # noqa: F401

pytestmark = pytest.mark.posix  # the fake wilma CLI is a script on a shell-script Node

FIRST_RUN = f"last run {NOW.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC, ok"
MEASURED = r", \d+ s, \d+ MB"  # how long the run took and its peak memory


def sign_up(s, address: str = "parent@example.com", **fields: str) -> str:
    code = s.invite(Recipient(address, "fi"))
    assert submit(s, code, r0=address, **fields).status == 200
    return s.store.households()[-1].user


def report(s, capsys) -> tuple[int, str]:
    capsys.readouterr()
    code = status.report(s.store, s.host, homes=s.host.root)
    return code, capsys.readouterr().out


def program(s, name: str) -> str:
    return str(s.host.users[name].home / "ParentRecap" / "app" / ".venv" / "bin" / "parent-recap")


def test_each_household_is_listed_with_its_last_run_and_doctor_s_statuses(signup, capsys):
    first = sign_up(signup, "parent@example.com")
    second = sign_up(signup, "other@example.com")

    code, out = report(signup, capsys)

    assert code == 0, out
    lines = out.splitlines()
    for user, evening in ((first, "21:00"), (second, "21:05")):
        [row] = [i for i, line in enumerate(lines) if f" {user} " in line]
        assert re.fullmatch(re.escape(f"✅ {user}  {evening}  {FIRST_RUN}") + MEASURED,
                            lines[row]), lines[row]
        doctor = lines[row + 1]
        assert doctor.startswith("   doctor: ✅ config  ✅ language")
        for check in ("gateway-sends", "wilma", "calendar", "schedule", "last-run"):
            assert f" {check}" in doctor
        assert " claude" not in doctor and " gateway " not in doctor  # no test call to the model
    assert lines[-1] == "2 Households: 0 failed, 0 overdue, 0 unknown"
    # Only `signup health`, run as each Household's own user.
    assert signup.host.commands[-2:] == [[first, program(signup, first), "signup", "health"],
                                         [second, program(signup, second), "signup", "health"]]


def test_a_household_whose_last_run_failed_is_marked(signup, capsys, monkeypatch):
    name = sign_up(signup)
    from family_brief import run as evening

    def crashes(*_a, **_k):
        raise RuntimeError("the run crashed")
    with monkeypatch.context() as mp, pytest.raises(RuntimeError):
        mp.setattr(evening, "_read", crashes)
        signup.host.run_own(name, [program(signup, name), "run"])

    code, out = report(signup, capsys)

    assert code == 1
    assert f"❌ {name}  21:00  last run " in out and "UTC, failed (exit 1)" in out
    assert out.splitlines()[-1] == "1 Households: 1 failed, 0 overdue, 0 unknown"


def test_a_household_whose_run_is_overdue_is_marked(signup, capsys):
    name = sign_up(signup)

    # The next evening's run, due at 21:00, hasn't ended by 23:00.
    with time_machine.travel(NOW + timedelta(days=1, hours=2), tick=False):
        code, out = report(signup, capsys)

    assert code == 1
    assert re.search(re.escape(f"⚠️ {name}  21:00  {FIRST_RUN}") + MEASURED + ", overdue", out)
    assert out.splitlines()[-1] == "1 Households: 0 failed, 1 overdue, 0 unknown"


def health_of(s, name: str) -> dict:
    _, said = s.host.run_as(name, [program(s, name), "signup", "health"], "", 300)
    return json.loads(said.strip().splitlines()[-1])


def noted(s, name: str, monkeypatch):
    home = s.host.users[name].home
    with monkeypatch.context() as mp:
        mp.setenv("HOME", str(home))  # as its config's ~ is
        return last_run_path(Config.load(home / ".family" / "config.yaml"))


def test_each_run_notes_how_long_it_took_and_its_peak_memory(signup, capsys, monkeypatch):
    name = sign_up(signup)
    ran = json.loads(noted(signup, name, monkeypatch).read_text())
    assert isinstance(ran["seconds"], int) and ran["seconds"] >= 0
    assert isinstance(ran["peak_mb"], int) and ran["peak_mb"] > 0  # this process's, at least
    said = health_of(signup, name)
    assert (said["seconds"], said["peak_mb"]) == (ran["seconds"], ran["peak_mb"])

    code, out = report(signup, capsys)

    assert code == 0, out
    assert f"{name}  21:00  {FIRST_RUN}, {ran['seconds']} s, {ran['peak_mb']} MB\n" in out


def test_a_run_noted_before_its_duration_and_peak_were_still_reads(signup, capsys, monkeypatch):
    name = sign_up(signup)
    file = noted(signup, name, monkeypatch)
    ran = json.loads(file.read_text())
    file.write_text(json.dumps({"at": ran["at"], "exit": 0}))  # as 0.10.0 wrote it
    said = health_of(signup, name)
    assert (said["last_run"], said["exit"]) == (ran["at"], 0)
    assert (said["seconds"], said["peak_mb"]) == (None, None)

    code, out = report(signup, capsys)

    assert code == 0, out
    assert f"✅ {name}  21:00  {FIRST_RUN}\n" in out


def test_a_household_without_the_health_command_is_said_to_be_unknown(
        signup, capsys, monkeypatch):
    name = sign_up(signup)

    def older(user, argv, stdin, timeout):  # a program from before `signup health`
        return 2, "parent-recap signup: error: argument signup: invalid choice: 'health'\n"
    monkeypatch.setattr(signup.host, "run_as", older)

    code, out = report(signup, capsys)

    assert code == 1
    assert out.startswith(f"❓ {name}  21:00  status unknown: signup health ended with 2")
    assert out.splitlines()[-1] == "1 Households: 0 failed, 0 overdue, 1 unknown"


def test_no_households_yet(signup, capsys):
    assert report(signup, capsys) == (0, "No Hosted Households yet.\n")


def test_nothing_of_a_household_s_messages_kids_or_addresses_is_printed(
        signup, capsys):
    subject, body, sender = "Retkipäivä perjantaina", "Ottakaa eväät mukaan", "Opettaja Korhonen"
    name = sign_up(signup, "parent@example.com", r1="partner@example.com", r1_language="zh")
    home = signup.host.users[name].home
    # Its config names the Kids and the Recipients; its archive and logs hold a message too.
    config = (home / ".family" / "config.yaml").read_text()
    assert "Mia Virtanen" in config and "partner@example.com" in config
    archive = home / "ParentRecap"
    (archive / "2026-09-27.raw.json").write_text(json.dumps({"messages": [{
        "source": "wilma", "sender": sender, "subject": subject, "body": body,
        "kid_hint": "Mia Virtanen"}]}, ensure_ascii=False))
    logs = archive / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "run-stderr.log").write_text(
        f"21:00:03 INFO family_brief | Wilma: {subject} from {sender} about Mia Virtanen\n"
        f"21:00:04 INFO family_brief | {body}\n"
        "21:01:10 INFO family_brief | Brief sent to parent@example.com, partner@example.com\n")
    # Its own `signup health` line says nothing of them either.
    _, said = signup.host.run_as(name, [program(signup, name), "signup", "health"], "", 300)
    health = said.strip().splitlines()[-1]

    code, out = report(signup, capsys)

    assert f"{name}  21:00  {FIRST_RUN}" in out
    for private in (subject, body, sender, "Korhonen", "Mia", "Leo", "Virtanen",
                    "parent@example.com", "partner@example.com", "@", "Kilon"):
        assert private not in out, private
        assert private not in health, private
    assert set(json.loads(health)) == {"last_run", "exit", "seconds", "peak_mb", "overdue",
                                       "checks"}
