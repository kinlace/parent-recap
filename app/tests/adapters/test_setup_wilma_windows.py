"""The Wilma sign-in window on Windows, which has no pseudo-terminal for the wilma CLI's own screens:
`setup wilma --screen` runs the CLI's sign-in page (`wilma login`), which the CLI opens in the
browser, under a short guide, and ends the CLI once it has saved its profile.

The CLI is faked where it's started: it saves its profile, as after the family signed in on its
page, on its third check, unless the family never does, and then waits for more Wilmas until it's
ended. Waiting takes no time: `time.sleep` returns at once. The window's step that waits for
the result, and the setup page around it, are tested in test_setup_wilma.py."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

import fake_node
from family_brief import setup_steps

pytestmark = pytest.mark.windows

ESPOO = "https://espoo.inschool.fi"


class FakeLogin:
    """`wilma login`, started on Parent Recap's own Node."""

    signs_in = True     # False: the family closes the page without signing in
    exits_with = 1      # how it ends on its own, when the family doesn't sign in
    started: list[FakeLogin] = []

    def __init__(self, argv: list[str], env: dict[str, str], **_k: Any) -> None:
        self.argv, self.env = list(argv), env
        self.config = Path(env["USERPROFILE"]) / ".config" / "wilmai" / "config.json"
        self.checks, self.returncode, self.ended = 0, None, False
        type(self).started.append(self)

    def poll(self) -> int | None:
        self.checks += 1
        if self.returncode is None and self.checks == 3:
            if self.signs_in:  # whole, as the CLI writes it and renames it
                self.config.parent.mkdir(parents=True, exist_ok=True)
                profile_id = f"{ESPOO}|mia.parent"
                self.config.write_text(json.dumps({"profiles": [{
                    "id": profile_id, "tenantUrl": ESPOO, "tenantName": "Espoo",
                    "username": "mia.parent", "passwordObfuscated": "d2lsbWFpOjpzYWxhc2FuYQ==",
                    "students": [{"studentNumber": "1001", "name": "Mia Virtanen"}]}],
                    "lastProfileId": profile_id}))
            else:
                self.returncode = self.exits_with
        return self.returncode

    def terminate(self) -> None:
        self.ended = True
        if self.returncode is None:
            self.returncode = 1  # TerminateProcess's exit code

    def wait(self, *_a: Any, **_k: Any) -> int:
        assert self.returncode is not None, "waited for a CLI that never ends"
        return self.returncode


@pytest.fixture
def login(harness, monkeypatch) -> type[FakeLogin]:
    for var in ("WILMAI_CONFIG_PATH", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    fake_node.pinned_node(harness.home)
    fake_node.install_wilma(harness.home, "")
    fake = type("Login", (FakeLogin,), {"started": []})
    monkeypatch.setattr(subprocess, "Popen", fake)
    return fake


def screen(harness, status: Path, language: str = "en") -> int:
    return harness.cli("setup", "wilma", "--screen", "--language", language, "--status",
                       str(status))


def test_the_window_runs_wilma_s_sign_in_page_and_ends_it_once_signed_in(harness, login, capsys,
                                                                         tmp_path):
    status = tmp_path / "exit-status"

    assert screen(harness, status, "fi") == 0

    [cli] = login.started
    node = harness.home / "ParentRecap" / "runtime" / f"node-v{fake_node.NODE_VERSION}" / "node.exe"
    script = fake_node.wilma_package(harness.home) / fake_node.SCRIPT
    assert cli.argv == [str(node), str(script), "login"]
    assert cli.env["WILMAI_NO_UPDATE_CHECK"] == "1"  # Parent Recap installs the version it pins
    assert cli.ended  # before it waits for more Wilmas
    assert status.read_text().strip() == "0"
    shown = capsys.readouterr().out
    text = setup_steps.WILMA_WINDOW["fi"]
    assert text["page"] in shown and text["guide"][2] in shown
    assert text["signed_in"] in shown and text["close"] in shown


def test_the_page_closed_without_signing_in_is_the_cli_s_own_ending(harness, login, capsys,
                                                                    tmp_path):
    login.signs_in = False
    status = tmp_path / "exit-status"

    assert screen(harness, status) == 1

    [cli] = login.started
    assert not cli.ended
    assert status.read_text().strip() == "1"
    shown = capsys.readouterr().out
    assert setup_steps.WILMA_WINDOW["en"]["close"] in shown
    assert setup_steps.WILMA_WINDOW["en"]["signed_in"] not in shown


def test_without_the_cli_the_window_ends_at_once(harness, login, tmp_path):
    fake_node.wilma_package(harness.home).joinpath("package.json").unlink()
    status = tmp_path / "exit-status"

    assert screen(harness, status) == 127

    assert login.started == [] and status.read_text().strip() == "127"
