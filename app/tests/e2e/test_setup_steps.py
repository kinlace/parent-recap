"""Setup steps a family runs by hand: none of them may look stuck or leave the family guessing.

The outside edges are faked: Gmail's IMAP server, the harness's in-memory OS services (secrets, the
wake schedule) and the `open` command. Assertions are on what the family reads and on what was
asked of the system."""
from __future__ import annotations

import imaplib
import json
from typing import Any

import pytest

from family_brief.system import wake

# ── discover gmail-senders


class HeaderImap:
    """Gmail's IMAP server for the sender scan: one search, then From: headers in batches."""

    def __init__(self, senders: list[str]) -> None:
        self.senders = senders

    def __call__(self, *_a: Any) -> "HeaderImap":
        return self

    def login(self, *_a: Any) -> None:
        pass

    def select(self, *_a: Any, **_k: Any) -> None:
        pass

    def logout(self) -> None:
        pass

    def search(self, *_a: Any) -> tuple[str, list[bytes]]:
        return "OK", [b" ".join(str(n).encode() for n in range(1, len(self.senders) + 1))]

    def fetch(self, ids: bytes, _what: str) -> tuple[str, list[Any]]:
        rows: list[Any] = []
        for n in ids.split(b","):
            rows += [(f"{int(n)} (BODY[HEADER.FIELDS (FROM)] {{40}}".encode(),
                      f"From: {self.senders[int(n) - 1]}\r\n\r\n".encode()), b")"]
        return "OK", rows


def test_discover_gmail_senders_says_how_long_it_takes_and_shows_progress(harness, monkeypatch,
                                                                          capsys):
    harness.secrets["gmail-imap-parent@example.com"] = "app-password"
    senders = ["Teacher <teacher@kilo.example.fi>"] * 300 + ["Coach <coach@club.example.fi>"] * 150
    monkeypatch.setattr(imaplib, "IMAP4_SSL", HeaderImap(senders))

    assert harness.cli("discover", "gmail-senders") == 0

    out = capsys.readouterr().out
    assert "minute" in out.split("\n", 1)[0]  # the estimate comes before anything slow
    assert "200 of 450" in out and "400 of 450" in out and "450 of 450" in out
    assert out.index("450 of 450") < out.index("kilo.example.fi")
    assert "300  kilo.example.fi" in out and "150  club.example.fi" in out


def test_discover_gmail_senders_as_json_ticks_school_city_and_club_senders(harness, monkeypatch,
                                                                          capsys):
    harness.secrets["gmail-imap-parent@example.com"] = "app-password"
    harness.config["city"] = "Espoo"
    # Without MyClub as a Source, MyClub's own mail is the family's club news.
    del harness.config["kids"][0]["myclub_ical_url"]
    senders = (["Opettaja <opettaja@edu.espoo.fi>"] * 5 + ["MyClub <noreply@myclub.fi>"] * 4
               + ["Coach <coach@tapiolan-seura.fi>"] * 3 + ["Kilo <office@kilokoulu.fi>"] * 2
               + ["Friend <friend@gmail.com>"] * 6 + ["Shop <news@shop.example.com>"] * 7
               + ["Helsinki <info@hel.fi>"])
    monkeypatch.setattr(imaplib, "IMAP4_SSL", HeaderImap(senders))

    assert harness.cli("discover", "gmail-senders", "--json") == 0

    captured = capsys.readouterr()
    out = json.loads(captured.out)  # one line of JSON, and nothing else
    assert out["result"] == "read" and out["days"] == 60
    assert out["senders"] == [
        {"domain": "shop.example.com", "count": 7, "example": "Shop", "likely": False},
        {"domain": "gmail.com", "count": 6, "example": "Friend", "likely": False, "public": True},
        {"domain": "edu.espoo.fi", "count": 5, "example": "Opettaja", "likely": True},
        {"domain": "myclub.fi", "count": 4, "example": "MyClub", "likely": True},
        {"domain": "tapiolan-seura.fi", "count": 3, "example": "Coach", "likely": True},
        {"domain": "kilokoulu.fi", "count": 2, "example": "Kilo", "likely": True},
        # Another city's: Espoo's family gets it now and then, but it isn't their school's.
        {"domain": "hel.fi", "count": 1, "example": "Helsinki", "likely": False},
    ]
    assert "28 of 28" in captured.err  # the count still goes up as it reads


@pytest.mark.parametrize("connected", [True, False])
def test_discover_gmail_senders_as_json_leaves_out_the_mail_of_a_connected_source(
        harness, monkeypatch, capsys, connected):
    # Reading it too would put the news MyClub or Wilma already brings in the Brief twice.
    harness.secrets["gmail-imap-parent@example.com"] = "app-password"
    harness.config["wilma"]["enabled"] = connected
    if not connected:
        del harness.config["kids"][0]["myclub_ical_url"]
    monkeypatch.setattr(imaplib, "IMAP4_SSL", HeaderImap(
        ["MyClub <noreply@myclub.fi>"] * 2 + ["Wilma <noreply@kilo.inschool.fi>"]))

    assert harness.cli("discover", "gmail-senders", "--json") == 0

    out = json.loads(capsys.readouterr().out)
    assert [(s["domain"], s["likely"]) for s in out["senders"]] == [
        ("myclub.fi", not connected), ("kilo.inschool.fi", not connected)]


def test_the_city_presets_match_docs_config():
    from pathlib import Path

    from family_brief import setup_steps
    docs = (Path(__file__).parents[3] / "docs" / "config.md").read_text()
    table = docs.split("## City presets", 1)[1].split("\n\n", 2)[1]
    rows = [[c.strip() for c in line.strip("|").split("|")] for line in table.splitlines()[2:]]
    addresses = {address: city.split(" (")[0] for city, address, *_ in rows}
    domains = {city.split(" (")[0]: allowlist.strip("`") for city, _, allowlist, _ in rows
               if allowlist.startswith("`")}

    assert addresses == setup_steps.WILMA_CITIES
    assert domains == setup_steps.CITY_DOMAINS


def test_discover_gmail_senders_as_json_says_when_gmail_cannot_be_read(harness, capsys):
    assert harness.cli("discover", "gmail-senders", "--json") == 1  # no App Password stored

    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "read-failed" and "parent-recap doctor" in out["next"]


# ── The steps that read WhatsApp for Mac, gone with it (ADR 0027)


@pytest.mark.parametrize("command", [["full-disk-access"], ["app-management"],
                                     ["discover", "whatsapp-chats"]])
def test_the_whatsapp_for_mac_commands_are_unknown(harness, capsys, command):
    with pytest.raises(SystemExit) as exited:
        harness.cli(*command)

    assert exited.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


# ── schedule install and the wake schedule

OTHER_WAKE = "wake every weekday at 07:00"


@pytest.mark.macos  # the Mac's wake dialog; Windows' wake: test_windows_only.py
def test_schedule_install_sets_the_wake_schedule_through_the_administrator_dialog(harness, capsys):
    assert harness.cli("schedule", "install") == 0

    out = capsys.readouterr().out
    assert harness.wake.asked == ["set 20:55"]
    assert harness.wake.wakes == [harness.wake.line(21, 0)]
    assert "20:55" in out and wake.manual_step(21, 0) not in out
    assert "replace" not in out


def test_schedule_install_skips_the_wake_schedule_when_the_mac_never_sleeps(harness, capsys):
    harness.wake.never_sleeps = True

    assert harness.cli("schedule", "install") == 0

    out = capsys.readouterr().out
    assert harness.wake.asked == []
    assert wake.manual_step(21, 0) not in out
    assert "never sleeps" in out


@pytest.mark.macos  # the Mac's wake dialog; Windows' wake: test_windows_only.py
def test_schedule_install_warns_before_replacing_another_wake_schedule(harness, capsys):
    harness.wake.wakes = [OTHER_WAKE]

    assert harness.cli("schedule", "install") == 0

    out = capsys.readouterr().out
    assert harness.wake.asked == []  # the family decides before any dialog opens
    assert OTHER_WAKE in out
    assert "replaces" in out
    assert "schedule install --replace-wake" in out


@pytest.mark.macos  # the Mac's wake dialog; Windows' wake: test_windows_only.py
def test_schedule_install_replaces_another_wake_schedule_once_the_family_agrees(harness, capsys):
    harness.wake.wakes = [OTHER_WAKE]

    assert harness.cli("schedule", "install", "--replace-wake") == 0

    out = capsys.readouterr().out
    assert harness.wake.asked == ["set 20:55"]
    assert harness.wake.wakes == [harness.wake.line(21, 0)]
    assert OTHER_WAKE in out  # what was replaced
    assert "20:55" in out and wake.manual_step(21, 0) not in out


@pytest.mark.macos  # the Mac's wake dialog; Windows' wake: test_windows_only.py
def test_schedule_install_says_when_the_wake_schedule_is_already_set(harness, capsys):
    harness.wake.wakes = [harness.wake.line(21, 0)]

    assert harness.cli("schedule", "install") == 0

    out = capsys.readouterr().out
    assert harness.wake.asked == []
    assert wake.manual_step(21, 0) not in out
    assert "already" in out


def test_schedule_install_gives_the_sudo_command_without_a_desktop_session(harness, capsys):
    harness.wake.dialog = "none"

    assert harness.cli("schedule", "install") == 0

    assert wake.manual_step(21, 0) in capsys.readouterr().out


@pytest.mark.macos  # the Mac's wake dialog; Windows' wake: test_windows_only.py
def test_schedule_install_gives_the_sudo_command_over_ssh(harness, monkeypatch, capsys):
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.2 52000 10.0.0.1 22")
    harness.wake.wakes = [OTHER_WAKE]

    assert harness.cli("schedule", "install") == 0

    out = capsys.readouterr().out
    assert harness.wake.asked == []
    assert OTHER_WAKE in out
    assert out.index("replaces") < out.index(wake.manual_step(21, 0))


@pytest.mark.macos  # the Mac's wake dialog; Windows' wake: test_windows_only.py
def test_schedule_install_says_the_wake_schedule_wasnt_set_when_the_dialog_is_cancelled(harness,
                                                                                       capsys):
    harness.wake.dialog = "cancel"

    assert harness.cli("schedule", "install") == 0

    out = capsys.readouterr().out
    assert "wasn't set" in out
    assert wake.manual_step(21, 0) in out
