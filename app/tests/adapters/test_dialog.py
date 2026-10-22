"""The adapters of `family_brief.system.dialog` (ADR 0005, 0020): on macOS an AppleScript dialog
with hidden input through `osascript`, on Windows none, so the family types at the terminal's
hidden prompt. Callers' tests use fake_system.FakeDialog instead.

The outside edges are faked: `osascript`, which answers as the family does in the dialog, and the
terminal's hidden prompt."""
from __future__ import annotations

import getpass
import subprocess
from typing import Any

import pytest

from family_brief import secret_dialog
from family_brief.system import dialog, dialog_macos, dialog_windows


class Osascript:
    """`osascript` running the dialog's script: the family types `typed` and presses `button`,
    "save", "other", "cancel", or "fails" (no desktop session to show it on)."""

    def __init__(self) -> None:
        self.typed = ""
        self.button = "save"
        self.scripts: list[str] = []

    def run(self, cmd: list[str], *_a: Any, input: str | None = None,
            **_k: Any) -> subprocess.CompletedProcess:
        assert cmd == ["osascript", "-"] and input, cmd  # the script on stdin, not argv
        self.scripts.append(input)
        if self.button == "cancel":
            return subprocess.CompletedProcess(cmd, 1, "", "execution error: User canceled. (-128)")
        if self.button == "fails":
            return subprocess.CompletedProcess(
                cmd, 1, "", "execution error: No user interaction allowed. (-1713)")
        # The script returns "1" and the typed text for Save, "2" for the second choice.
        out = "2\n" if self.button == "other" else f"1{self.typed}\n"
        return subprocess.CompletedProcess(cmd, 0, out, "")


@pytest.fixture
def osascript(monkeypatch) -> Osascript:
    fake = Osascript()
    monkeypatch.setattr(dialog, "_adapter", dialog_macos)  # in place of the harness's fake
    monkeypatch.setattr(subprocess, "run", fake.run)
    for var in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"):
        monkeypatch.delenv(var, raising=False)
    return fake


@pytest.fixture
def prompt(monkeypatch) -> list[str]:
    """The terminal's hidden prompt, where the family types "typed at the prompt"."""
    asked: list[str] = []

    def getpass_(text: str = "") -> str:
        asked.append(text)
        return "typed at the prompt"
    monkeypatch.setattr(getpass, "getpass", getpass_)
    return asked


def test_on_macos_the_secret_is_typed_hidden_in_the_dialog(harness, osascript):
    osascript.typed = "  abcd efgh  "

    assert secret_dialog.ask('Paste the "App Password"') == "abcd efgh"

    [script] = osascript.scripts
    assert "with hidden answer" in script and 'with title "Parent Recap"' in script
    assert 'display dialog "Paste the \\"App Password\\""' in script
    assert "abcd" not in script


def test_on_macos_the_dialog_shows_its_second_choice_and_says_when_it_was_pressed(harness,
                                                                                 osascript):
    osascript.button = "other"

    with pytest.raises(secret_dialog.OtherChosen):
        secret_dialog.ask("Paste the token", other="I don't have one")

    assert 'buttons {"I don\'t have one", "Cancel", "Save"}' in osascript.scripts[0]


def test_on_macos_a_cancelled_dialog_is_said_so(harness, osascript):
    osascript.button = "cancel"

    with pytest.raises(secret_dialog.Cancelled):
        secret_dialog.ask("Paste the token")


def test_on_macos_without_a_dialog_the_family_types_at_the_terminal_prompt(harness, osascript,
                                                                          prompt):
    osascript.button = "fails"

    assert secret_dialog.ask("Paste the token") == "typed at the prompt"

    assert len(osascript.scripts) == 1 and prompt[0].startswith("Paste the token")


def test_the_dialog_shows_macos_lock_icon(harness, osascript, monkeypatch, tmp_path):
    icon = tmp_path / "Lock \"icon\".icns"
    icon.write_bytes(b"icns")
    monkeypatch.setattr(dialog_macos, "LOCK_ICON", str(icon))

    dialog.ask("Paste the token")

    script = osascript.scripts[0]
    assert 'with icon ((POSIX file "' + str(icon).replace('"', '\\"') + '") as alias)' in script
    assert "with icon note" not in script


def test_without_the_lock_icon_the_dialog_shows_the_note_icon(harness, osascript, monkeypatch,
                                                              tmp_path):
    monkeypatch.setattr(dialog_macos, "LOCK_ICON", str(tmp_path / "missing.icns"))

    dialog.ask("Paste the token")

    script = osascript.scripts[0]
    assert "with icon note" in script and "POSIX file" not in script


def test_on_windows_the_family_types_at_the_terminal_prompt(harness, monkeypatch, prompt):
    monkeypatch.setattr(dialog, "_adapter", dialog_windows)
    for var in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"):
        monkeypatch.delenv(var, raising=False)

    assert secret_dialog.ask("Paste the token") == "typed at the prompt"

    assert len(prompt) == 1 and harness.commands == []
