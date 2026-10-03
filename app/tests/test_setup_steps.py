"""Setup steps a family runs by hand: none of them may look stuck or leave the family guessing.

The outside edges are faked: Gmail's IMAP server, macOS's administrator dialog, and the
`launchctl`, `pmset` and `open` commands. Assertions are on what the family reads and on which commands ran."""
from __future__ import annotations

import imaplib
import os
import plistlib
import shlex
import subprocess
import sys
from typing import Any

import pytest

from family_brief import ops

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
    harness.keychain["gmail-imap-parent@example.com"] = "app-password"
    senders = ["Teacher <teacher@kilo.example.fi>"] * 300 + ["Coach <coach@club.example.fi>"] * 150
    monkeypatch.setattr(imaplib, "IMAP4_SSL", HeaderImap(senders))

    assert harness.cli("discover", "gmail-senders") == 0

    out = capsys.readouterr().out
    assert "minute" in out.split("\n", 1)[0]  # the estimate comes before anything slow
    assert "200 of 450" in out and "400 of 450" in out and "450 of 450" in out
    assert out.index("450 of 450") < out.index("kilo.example.fi")
    assert "300  kilo.example.fi" in out and "150  club.example.fi" in out


# ── App Management


def test_app_management_shows_the_python_file_and_opens_the_settings_pane(monkeypatch, capsys):
    ran: list[list[str]] = []

    def run(cmd: list[str], *_a: Any, **_k: Any) -> subprocess.CompletedProcess:
        ran.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(sys, "argv", ["family-brief", "app-management"])

    from family_brief import __main__ as cli
    assert cli.main() == 0

    python = os.path.realpath(sys.executable)
    assert ["open", "-R", python] in ran
    assert any(c[0] == "open" and "Privacy_AppBundles" in c[-1] for c in ran)
    out = capsys.readouterr().out
    assert python in out and "drag" in out


def test_app_management_gives_the_commands_when_it_cant_open_them(monkeypatch, capsys):
    # An agent's sandbox can block `open`; the family then runs the same commands in Terminal.
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, *_a, **_k: subprocess.CompletedProcess(cmd, 1, "", "-10822"))
    monkeypatch.setattr(sys, "argv", ["family-brief", "app-management"])

    from family_brief import __main__ as cli
    assert cli.main() == 0

    out = capsys.readouterr().out
    assert shlex.join(["open", "-R", os.path.realpath(sys.executable)]) in out
    assert shlex.join(["open", ops.APP_MANAGEMENT_URL]) in out


# ── schedule install and the wake schedule

ALWAYS_AWAKE = """Battery Power:
 sleep                1
AC Power:
 sleep                0
"""
NEVER_SLEEPS = """AC Power:
 displaysleep         10
 sleep                0 (sleep prevented by powerd)
 disksleep            10
"""
SLEEPS = """AC Power:
 displaysleep         10
 sleep                1
"""
NO_SCHEDULE = ""
OTHER_SCHEDULE = """Repeating power events:
  wakepoweron at 7:00AM weekdays only
Scheduled power events:
 [0]  wake at 10/03/2026 07:00:00 by 'com.apple.alarm'
"""
OURS = """Repeating power events:
  wakepoweron at 8:55PM every day
"""


class FakeMac:
    """`launchctl` succeeds; `pmset -g custom` and `pmset -g sched` print what the test sets; the
    administrator dialog (`osascript ... with administrator privileges`) runs `pmset repeat` when
    the family enters their Mac password."""

    def __init__(self) -> None:
        self.custom = SLEEPS
        self.sched = NO_SCHEDULE
        self.admin_dialog = "allow"  # or "cancel", or "unavailable" (no desktop session)
        self.admin_scripts: list[str] = []

    def run(self, cmd: list[str], *_a: Any, **_k: Any) -> subprocess.CompletedProcess:
        if cmd[0] == "launchctl":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:2] == ["pmset", "-g"]:
            return subprocess.CompletedProcess(cmd, 0, self.custom if cmd[2] == "custom"
                                               else self.sched, "")
        if cmd[0] == "osascript" and "administrator privileges" in cmd[-1]:
            self.admin_scripts.append(cmd[-1])
            if self.admin_dialog == "cancel":
                return subprocess.CompletedProcess(cmd, 1, "", "execution error: User canceled. (-128)")
            if self.admin_dialog == "unavailable":
                return subprocess.CompletedProcess(
                    cmd, 1, "", "execution error: No user interaction allowed. (-1713)")
            hh, mm = cmd[-1].split("MTWRFSU ", 1)[1][:5].split(":")
            h = int(hh)
            self.sched = (f"Repeating power events:\n  wakepoweron at {h % 12 or 12}:{mm}"
                          f"{'AM' if h < 12 else 'PM'} every day\n")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"unexpected subprocess in test: {cmd[:3]}")


@pytest.fixture
def mac(harness, monkeypatch):
    fake = FakeMac()
    monkeypatch.setattr(subprocess, "run", fake.run)
    monkeypatch.setattr(ops, "LAUNCH_AGENTS", harness.home / "Library" / "LaunchAgents")
    return fake


def test_schedule_install_sets_the_wake_schedule_through_the_administrator_dialog(harness, mac,
                                                                                  capsys):
    assert harness.cli("schedule", "install") == 0

    out = capsys.readouterr().out
    assert len(mac.admin_scripts) == 1
    assert "pmset repeat wakeorpoweron MTWRFSU 20:55:00" in mac.admin_scripts[0]
    assert "Parent Recap" in mac.admin_scripts[0]  # the dialog says who is asking, and why
    assert "20:55" in out and "sudo" not in out
    assert "replace" not in out


def test_schedule_install_puts_the_native_installers_folder_on_the_jobs_path(harness, mac,
                                                                            monkeypatch):
    # Claude Code's native installer puts claude in ~/.local/bin. The shell that runs schedule
    # install may not have it on PATH yet, but the nightly job must find claude there.
    bin_dir = harness.home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "claude").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    assert harness.cli("schedule", "install") == 0

    plist = plistlib.loads((ops.LAUNCH_AGENTS / f"{ops.JOB_DAILY}.plist").read_bytes())
    assert str(bin_dir) in plist["EnvironmentVariables"]["PATH"].split(":")


def test_schedule_install_skips_the_wake_schedule_when_the_mac_never_sleeps(harness, mac, capsys):
    mac.custom = NEVER_SLEEPS

    assert harness.cli("schedule", "install") == 0

    out = capsys.readouterr().out
    assert mac.admin_scripts == []
    assert "pmset repeat" not in out
    assert "never sleeps" in out


def test_schedule_install_still_wakes_a_laptop_that_sleeps_on_battery(harness, mac, capsys):
    mac.custom = ALWAYS_AWAKE

    assert harness.cli("schedule", "install") == 0

    assert len(mac.admin_scripts) == 1


def test_schedule_install_warns_before_replacing_another_wake_schedule(harness, mac, capsys):
    mac.sched = OTHER_SCHEDULE

    assert harness.cli("schedule", "install") == 0

    out = capsys.readouterr().out
    assert mac.admin_scripts == []  # the family decides before any dialog opens
    assert "wakepoweron at 7:00AM weekdays only" in out
    assert "replaces" in out
    assert "schedule install --replace-wake" in out
    assert "com.apple.alarm" not in out  # one-off events aren't touched by pmset repeat


def test_schedule_install_replaces_another_wake_schedule_once_the_family_agrees(harness, mac,
                                                                               capsys):
    mac.sched = OTHER_SCHEDULE

    assert harness.cli("schedule", "install", "--replace-wake") == 0

    out = capsys.readouterr().out
    assert len(mac.admin_scripts) == 1
    assert "wakepoweron at 7:00AM weekdays only" in out  # what was replaced
    assert "20:55" in out and "sudo" not in out


def test_schedule_install_says_when_the_wake_schedule_is_already_set(harness, mac, capsys):
    mac.sched = OURS

    assert harness.cli("schedule", "install") == 0

    out = capsys.readouterr().out
    assert mac.admin_scripts == []
    assert "pmset repeat" not in out
    assert "already" in out


def test_schedule_install_gives_the_sudo_command_without_a_desktop_session(harness, mac, capsys):
    mac.admin_dialog = "unavailable"

    assert harness.cli("schedule", "install") == 0

    assert "sudo pmset repeat wakeorpoweron MTWRFSU 20:55:00" in capsys.readouterr().out


def test_schedule_install_gives_the_sudo_command_over_ssh(harness, mac, monkeypatch, capsys):
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.2 52000 10.0.0.1 22")
    mac.sched = OTHER_SCHEDULE

    assert harness.cli("schedule", "install") == 0

    out = capsys.readouterr().out
    assert mac.admin_scripts == []
    assert "wakepoweron at 7:00AM weekdays only" in out
    assert out.index("replaces") < out.index("sudo pmset repeat wakeorpoweron MTWRFSU 20:55:00")


def test_schedule_install_says_the_wake_schedule_wasnt_set_when_the_dialog_is_cancelled(
        harness, mac, capsys):
    mac.admin_dialog = "cancel"

    assert harness.cli("schedule", "install") == 0

    out = capsys.readouterr().out
    assert "wasn't set" in out
    assert "sudo pmset repeat wakeorpoweron MTWRFSU 20:55:00" in out
