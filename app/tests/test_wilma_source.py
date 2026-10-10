"""The Wilma Source reads what the pinned wilma CLI (2.x) prints, run for real against a fake CLI on
Parent Recap's own Node: a message's text and its sender, news with their text, and the CLI's
errors. A CLI kept from before 2.0, when an update couldn't install the
new one, is still read. All names and messages are made up."""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest

import fake_node
from test_source_failures import (REAL_WILMA_COLLECT, caught_up_last_night, one_whatsapp_message,
                                  payload_ids, seen)

from family_brief.collectors import wilma

STUDENT = {"studentNumber": "7731905", "name": "Virtanen Mia"}


def listed(*messages: dict[str, Any]) -> dict[str, Any]:
    """`messages list` as the wilma CLI 2.x prints it."""
    return {"students": [{"student": STUDENT, "messages": [
        {"wilmaId": 0, "subject": "", "sentAt": "2026-09-27T09:00:00+03:00",
         "senderName": "Opettaja Saarinen", "folder": "inbox", "unread": True, "replyCount": 0, **m}
        for m in messages]}]}


def read(wilma_id: int, content: str, recipients: Any = None, **more: Any) -> dict[str, Any]:
    """`messages read` as the wilma CLI 2.x prints it: the message with its recipients and replies."""
    return {"student": STUDENT, "message": {
        "wilmaId": wilma_id, "subject": "", "sentAt": "2026-09-27T09:00:00+03:00", "folder": "inbox",
        "senderName": "Opettaja Saarinen", "content": content, "recipients": recipients,
        "replies": [], **more}}


def failed(code: str, message: str, status: int = 1) -> tuple[int, dict[str, Any]]:
    """An error as the wilma CLI 2.x prints it with --json: on stdout, with its exit status."""
    return status, {"status": "error", "code": code, "message": message}


@pytest.fixture
def cli(harness, monkeypatch) -> SimpleNamespace:
    """The real Wilma Source against a fake wilma CLI on Parent Recap's own Node. `answers` maps the
    start of a command (without node, the CLI and --json) to what the CLI prints, or to its exit
    status and what it prints; any other command lists nothing. `ran` is each command run."""
    harness.config["gmail"]["allowlist_domains"] = []  # Gmail fails fast; this is about Wilma
    one_whatsapp_message(harness)
    caught_up_last_night(harness)
    monkeypatch.setattr(wilma, "collect", REAL_WILMA_COLLECT)
    fake_node.pinned_node(harness.home)
    fake_node.install_wilma(harness.home, "#!/bin/sh\n")
    others = subprocess.run
    fake = SimpleNamespace(answers={}, ran=[])

    def run(cmd: list[str], *a: Any, **k: Any) -> subprocess.CompletedProcess:
        if not fake_node.is_wilma(cmd):
            return others(cmd, *a, **k)
        args = cmd[2:-1]
        fake.ran.append(args)
        answer = next((v for start, v in fake.answers.items() if tuple(args[:len(start)]) == start),
                      {"students": []})
        status, out = answer if isinstance(answer, tuple) else (0, answer)
        return subprocess.CompletedProcess(cmd, status, json.dumps(out), "")
    monkeypatch.setattr(subprocess, "run", run)
    return fake


def archived(harness) -> list[dict[str, Any]]:
    """Tonight's Wilma messages as the archive keeps them, with the names the AI saw as placeholders."""
    raw = json.loads((harness.archive_dir / "2026-09-27.raw.json").read_text())
    return [m for m in raw["messages"] if m["source"] == "wilma"]


def test_a_message_s_text_and_sender_come_from_the_new_json(harness, cli):
    cli.answers = {
        ("messages", "list"): listed({"wilmaId": 813, "subject": "Huoltajakysely"},
                                     {"wilmaId": 814, "subject": "Retki"}),
        ("messages", "read", "813"): read(813, "Kysely on auki 9.10. asti."),
        ("messages", "read", "814"): read(814, "Retki torstaina."),
    }

    assert harness.run() == 0

    survey, trip = archived(harness)
    assert (survey["sender"], survey["body"]) == ("Opettaja Saarinen", "Kysely on auki 9.10. asti.")
    assert (trip["sender"], trip["body"]) == ("Opettaja Saarinen", "Retki torstaina.")
    assert [c[:3] for c in cli.ran if c[1] == "read"] == [["messages", "read", "813"],
                                                          ["messages", "read", "814"]]
    assert all(c[3:] == ["--student", "7731905"] for c in cli.ran if c[1] == "read")


def test_news_text_comes_from_the_new_json_and_pinned_news_without_a_date_are_left(harness, cli):
    # From 2.0 the CLI also lists the news pinned to Wilma's page, which have no date and stay
    # there all year: they aren't new tonight, so they are marked seen without being read.
    cli.answers = {
        ("news", "list"): {"students": [{"student": STUDENT, "news": [
            {"wilmaId": 41, "title": "Poliisin tiedote", "published": "2026-09-27T00:00:00+03:00",
             "author": "Rehtori Saarinen", "pinned": False},
            {"wilmaId": 7, "title": "Lukuvuositiedote", "published": None, "pinned": True},
        ]}]},
        ("news", "read", "41"): {"student": STUDENT, "news": {
            "wilmaId": 41, "title": "Poliisin tiedote", "author": "Rehtori Saarinen",
            "published": "2026-09-27T00:00:00+03:00", "content": "Poliisi valvoo liikennettä.",
            "resources": []}},
    }

    assert harness.run() == 0

    [news] = archived(harness)
    assert (news["external_id"], news["sender"], news["body"]) == \
        ("news:41", "Rehtori Saarinen", "Poliisi valvoo liikennettä.")
    assert not any(c[:3] == ["news", "read", "7"] for c in cli.ran)
    assert seen(harness, "wilma") == ["news:41", "news:7"]


def test_a_message_the_cli_gives_no_time_for_is_left_as_one_before_2_0_dated_the_epoch(harness, cli):
    cli.answers = {("messages", "list"): listed({"wilmaId": 816, "subject": "?", "sentAt": None},
                                                {"wilmaId": 817, "subject": "Retki"})}

    assert harness.run() == 0

    assert payload_ids(harness, "wilma") == ["message:817"]
    assert seen(harness, "wilma") == ["message:816", "message:817"]


def test_a_password_wilma_turns_down_is_said_as_a_failed_login(harness, cli):
    cli.answers = {("messages", "list"): failed(
        "login_failed", "Wilma didn't accept the saved login (has the password changed?).")}

    assert harness.run() == 0

    [email] = harness.sent
    assert "Wilma 没读到（登录失败）" in email.text
    with pytest.raises(wilma.WilmaError) as e:
        wilma._run(["messages", "list"])
    assert e.value.code == "login_failed" and e.value.wrong_password


@pytest.mark.parametrize("code", ["network", "wilma_error", "mfa_required", "not_logged_in"])
def test_other_failures_are_not_a_wrong_password(harness, cli, code):
    cli.answers = {("kids", "list"): failed(code, "Something else went wrong.", 3)}

    with pytest.raises(wilma.WilmaError) as e:
        wilma.list_kids()

    assert e.value.code == code and not e.value.wrong_password
    assert f"wilma kids list failed: {code}: Something else went wrong." == str(e.value)


def test_a_sign_in_that_does_not_answer_beside_one_that_does_says_wilma_was_partly_read(harness, cli):
    # From 2.0 the CLI reads every sign-in its config has, and lists those that failed.
    answer = listed({"wilmaId": 818, "subject": "Retki"})
    answer["problems"] = [{"wilma": "Kuvitteellinen koulu", "message": "Wilma login failed"}]
    cli.answers = {("messages", "list"): answer, ("messages", "read"): read(818, "Retki torstaina.")}

    assert harness.run() == 0

    assert payload_ids(harness, "wilma") == ["message:818"]
    [email] = harness.sent
    assert "Wilma 部分没读到（登录失败）" in email.text


def test_a_wilma_cli_kept_from_before_2_0_is_still_read(harness, cli):
    # As the CLI 1.6.2 printed it, for a Household whose update couldn't install the new one.
    cli.answers = {
        ("messages", "list"): {"students": [{"student": {**STUDENT, "href": "/!7731905/"}, "items": [
            {"wilmaId": 819, "subject": "Retki", "sentAt": "2026-09-27T06:00:00.000Z",
             "folder": "inbox", "senderName": "Opettaja Saarinen", "fetchedAt": "2026-09-27T18:00:00.000Z"},
            {"wilmaId": 820, "subject": "?", "sentAt": "1970-01-01T00:00:00.000Z", "folder": "inbox"}]}]},
        ("messages", "read"): {"wilmaId": 819, "subject": "Retki", "senderName": "Opettaja Saarinen",
                               "content": "Retki torstaina.", "fetchedAt": "2026-09-27T18:00:00.000Z"},
    }

    assert harness.run() == 0

    [m] = archived(harness)
    assert (m["external_id"], m["sender"], m["body"]) == ("message:819", "Opettaja Saarinen", "Retki torstaina.")
