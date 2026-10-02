"""Saving a Kid's MyClub calendar link with `family-brief setup myclub`: the link goes from
macOS's dialog to one download and the owner-only config, and nowhere else (ADR 0005).

The link carries a personal token, so it's a secret like the others. The outside edges are faked:
the dialog, `open`, Terminal's hidden prompt and MyClub's server. The assistant reads only the
command's one-line JSON result, so assertions are on that result, on the config file, and on
every place the link must never reach."""
from __future__ import annotations

import getpass
import importlib.util
import json
import logging
import stat
import sys
from pathlib import Path
from typing import Any

import pytest
import requests
import yaml

from family_brief.collectors import myclub

TOKEN = "s3cr3t-t0ken"
LINK = f"webcal://example.myclub.fi/ical/{TOKEN}?key={TOKEN}"
CALENDAR = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
            "BEGIN:VEVENT\r\nUID:mc-1\r\nSUMMARY:Training\r\nDTSTART:20261005T150000Z\r\nEND:VEVENT\r\n"
            "BEGIN:VEVENT\r\nUID:mc-2\r\nSUMMARY:Match\r\nDTSTART:20261010T080000Z\r\nEND:VEVENT\r\n"
            "END:VCALENDAR\r\n")
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup_myclub.py"


@pytest.fixture(autouse=True)
def desktop(monkeypatch):
    """A desktop session, as when the family runs setup on their own Mac."""
    for var in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"):
        monkeypatch.delenv(var, raising=False)


class Response:
    def __init__(self, status: int, text: str) -> None:
        self.status_code, self.text = status, text


class MyClubServer:
    """MyClub's server: answers each download with `status` and `text`, or raises `error`."""

    def __init__(self) -> None:
        self.status, self.text = 200, CALENDAR
        self.error: Exception | None = None
        self.downloads: list[str] = []

    def get(self, url: str, **_k: Any) -> Response:
        self.downloads.append(url)
        if self.error:
            raise self.error
        return Response(self.status, self.text)


@pytest.fixture
def server(harness, monkeypatch) -> MyClubServer:
    s = MyClubServer()
    monkeypatch.setattr(myclub.requests, "get", s.get)
    del harness.config["kids"][0]["myclub_ical_url"]  # Mia has no link yet
    return s


@pytest.fixture
def terminal(monkeypatch) -> dict[str, Any]:
    """Terminal's hidden prompt: `value` is what the family types, or an exception to raise."""
    t: dict[str, Any] = {"value": "", "prompts": []}

    def fake(prompt: str = "") -> str:
        t["prompts"].append(prompt)
        if isinstance(t["value"], BaseException):
            raise t["value"]
        return t["value"]
    monkeypatch.setattr(getpass, "getpass", fake)
    return t


def config_file(harness) -> Path:
    return harness.home / ".family" / "config.yaml"


def saved_links(harness) -> dict[str, str | None]:
    kids = yaml.safe_load(config_file(harness).read_text())["kids"]
    return {k["name"]: k.get("myclub_ical_url") for k in kids}


def result(capsys) -> tuple[dict[str, Any], str]:
    out, err = capsys.readouterr()
    lines = out.strip().splitlines()
    assert len(lines) == 1, f"expected one JSON line, got: {out!r}"
    return json.loads(lines[0]), out + err


def assert_never_leaked(harness, printed: str, caplog, secret: str = TOKEN) -> None:
    assert secret not in printed
    assert secret not in caplog.text
    assert not any(secret in arg for cmd in harness.commands for arg in cmd)


# ── the dialog


def test_a_link_pasted_in_the_dialog_is_downloaded_and_saved(harness, server, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    harness.dialog.typed = f"  {LINK}\n"

    assert harness.cli("setup", "myclub", "--kid", "Mia") == 0

    res, printed = result(capsys)
    assert res == {"result": "saved", "kid": "Mia", "events": 2}
    assert harness.opened == ["https://id.myclub.fi"]
    assert len(harness.dialog.shown) == 1 and "hidden answer" in harness.dialog.shown[0]
    assert "Mia" in harness.dialog.shown[0]
    assert server.downloads == [LINK.replace("webcal://", "https://")]
    assert saved_links(harness) == {"Mia": LINK, "Leo": None}
    assert stat.S_IMODE(config_file(harness).stat().st_mode) == 0o600
    assert_never_leaked(harness, printed, caplog)


def test_an_https_link_is_saved_too(harness, server, capsys):
    https = LINK.replace("webcal://", "https://")
    harness.dialog.typed = https

    assert harness.cli("setup", "myclub", "--kid", "Leo") == 0

    assert saved_links(harness)["Leo"] == https


def test_a_new_link_replaces_the_old_one(harness, server, capsys):
    harness.config["kids"][0]["myclub_ical_url"] = "https://example.myclub.fi/ical/old"
    harness.dialog.typed = LINK

    assert harness.cli("setup", "myclub", "--kid", "Mia") == 0

    assert saved_links(harness)["Mia"] == LINK


@pytest.mark.parametrize("typed", [
    "my-myclub-password",
    "https://example.com/ical/s3cr3t-t0ken",
    "https://myclub.fi.example.com/ical/s3cr3t-t0ken",
    "http://example.myclub.fi/ical/s3cr3t-t0ken",
])
def test_something_that_is_not_a_myclub_link_is_never_downloaded(harness, server, capsys, caplog,
                                                                  typed):
    caplog.set_level(logging.DEBUG)
    harness.dialog.typed = typed

    assert harness.cli("setup", "myclub", "--kid", "Mia") == 1

    res, printed = result(capsys)
    assert res["result"] == "not-a-myclub-link" and "webcal://" in res["next"]
    assert server.downloads == []
    assert saved_links(harness)["Mia"] is None
    assert_never_leaked(harness, printed, caplog, typed)


@pytest.mark.parametrize("status", [403, 404, 500])
def test_a_link_that_does_not_open_names_only_the_server_and_status(harness, server, capsys,
                                                                    caplog, status):
    caplog.set_level(logging.DEBUG)
    server.status, server.text = status, f"no calendar for {TOKEN}"
    harness.dialog.typed = LINK

    assert harness.cli("setup", "myclub", "--kid", "Mia") == 1

    res, printed = result(capsys)
    assert res["result"] == "link-failed"
    assert res["error"] == f"example.myclub.fi answered HTTP {status}"
    assert "setup myclub --kid Mia" in res["next"]
    assert saved_links(harness)["Mia"] is None
    assert_never_leaked(harness, printed, caplog)
    assert "/ical/" not in printed


def test_a_link_that_cannot_be_reached_names_only_the_server(harness, server, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    server.error = requests.ConnectionError(
        f"HTTPSConnectionPool(host='example.myclub.fi', port=443): Max retries exceeded with url: "
        f"/ical/{TOKEN}?key={TOKEN} (Caused by NewConnectionError('refused'))")
    harness.dialog.typed = LINK

    assert harness.cli("setup", "myclub", "--kid", "Mia") == 1

    res, printed = result(capsys)
    assert res["result"] == "link-failed"
    assert res["error"] == "example.myclub.fi: ConnectionError"
    assert saved_links(harness)["Mia"] is None
    assert_never_leaked(harness, printed, caplog)


def test_a_page_that_is_not_a_calendar_is_not_saved(harness, server, capsys, caplog):
    # Such as MyClub's sign-in page, when the link was copied from the address bar.
    caplog.set_level(logging.DEBUG)
    server.text = f"<html><body>Sign in to MyClub {TOKEN}</body></html>"
    harness.dialog.typed = LINK

    assert harness.cli("setup", "myclub", "--kid", "Mia") == 1

    res, printed = result(capsys)
    assert res["result"] == "not-a-calendar" and "Calendar subscription" in res["next"]
    assert saved_links(harness)["Mia"] is None
    assert_never_leaked(harness, printed, caplog)


def test_closing_the_dialog_saves_nothing(harness, server, capsys):
    harness.dialog.button = "cancel"

    assert harness.cli("setup", "myclub", "--kid", "Mia") == 1

    assert result(capsys)[0]["result"] == "cancelled"
    assert server.downloads == [] and saved_links(harness)["Mia"] is None


def test_no_open_leaves_the_page_alone(harness, server, capsys):
    harness.dialog.typed = LINK

    assert harness.cli("setup", "myclub", "--kid", "Mia", "--no-open") == 0

    assert harness.opened == []


# ── which Kid


def test_an_unknown_kid_is_reported_with_the_kids_there_are_before_asking(harness, server, capsys):
    assert harness.cli("setup", "myclub", "--kid", "Emma") == 1

    res, _ = result(capsys)
    assert res["result"] == "no-such-kid" and res["kids"] == ["Mia", "Leo"]
    assert harness.dialog.shown == [] and harness.opened == []


def test_without_a_kid_it_lists_the_kids(harness, server, capsys):
    assert harness.cli("setup", "myclub") == 1

    res, _ = result(capsys)
    assert res["result"] == "no-kid" and res["kids"] == ["Mia", "Leo"]
    assert "--kid" in res["next"]
    assert harness.dialog.shown == []


def test_without_a_config_it_says_to_write_it_first(harness, server, monkeypatch, capsys, tmp_path):
    from family_brief.__main__ import main
    monkeypatch.setattr(sys, "argv", ["family-brief", "-c", str(tmp_path / "config.yaml"),
                                      "setup", "myclub", "--kid", "Mia"])

    assert main() == 1

    assert result(capsys)[0]["result"] == "no-config"
    assert harness.dialog.shown == []


def test_a_config_that_does_not_load_is_reported_without_its_links(harness, server, capsys):
    harness.config["kids"][1]["myclub_ical_url"] = LINK
    harness.config["kids"][1]["grade"] = {"not": "a grade"}

    assert harness.cli("setup", "myclub", "--kid", "Mia") == 1

    res, printed = result(capsys)
    assert res["result"] == "bad-config" and "doctor" in res["next"]
    assert TOKEN not in printed and harness.dialog.shown == []


# ── without a desktop session


def test_over_ssh_it_asks_in_a_hidden_terminal_prompt(harness, server, terminal, monkeypatch,
                                                      capsys, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.2 52000 10.0.0.1 22")
    terminal["value"] = LINK

    assert harness.cli("setup", "myclub", "--kid", "Mia") == 0

    res, printed = result(capsys)
    assert res["result"] == "saved"
    assert harness.dialog.shown == [] and len(terminal["prompts"]) == 1
    assert saved_links(harness)["Mia"] == LINK
    assert_never_leaked(harness, printed, caplog)


def test_with_neither_a_dialog_nor_a_terminal_it_says_where_to_run_it(harness, server, terminal,
                                                                     capsys):
    harness.dialog.button = "fails"
    terminal["value"] = EOFError()

    assert harness.cli("setup", "myclub", "--kid", "Mia") == 1

    res, _ = result(capsys)
    assert res["result"] == "no-prompt" and "setup myclub --kid Mia" in res["next"]
    assert saved_links(harness)["Mia"] is None


# ── the Terminal script from 0.4.1 and older


def test_the_terminal_script_still_saves_the_link(harness, server, terminal, monkeypatch, capsys):
    harness.cli("setup", "myclub")  # writes the harness's config; Mia has no link yet
    capsys.readouterr()
    spec = importlib.util.spec_from_file_location("setup_myclub", SCRIPT)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    terminal["value"] = LINK
    monkeypatch.setattr(sys, "argv", ["setup_myclub.py", "Mia", "--config", str(config_file(harness))])

    assert script.main() == 0

    assert saved_links(harness)["Mia"] == LINK
    out, err = capsys.readouterr()
    assert TOKEN not in out + err
