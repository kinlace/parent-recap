"""Connecting Wilma with `family-brief setup wilma`: it opens the wilma CLI's sign-in in a
Terminal window, waits for the family, then reports the Kids and the city as one JSON line.

The outside edges are faked: `open` runs the Terminal script it's given right away, as if the
family had just finished in that window, and `wilma` is a fake CLI on the PATH whose sign-in
screen writes the config the real one writes. Waiting takes no time: `time.sleep` returns at
once. Assertions are on the JSON result, on what was opened, and on every place the Wilma
password must never reach."""
from __future__ import annotations

import base64
import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

REAL_RUN = subprocess.run  # before the harness fakes it
PASSWORD = "Wilma-salasana-42"
CONFIG_MD = Path(__file__).resolve().parents[2] / "docs" / "config.md"
STUDENTS = [{"studentNumber": "1001", "name": "Mia Virtanen", "href": "/!1001/"},
            {"studentNumber": "1002", "name": "Leo Virtanen", "href": "/!1002/"}]

FAKE_WILMA = """#!{python}
import json, os, pathlib, sys
ctl = json.loads(pathlib.Path(__file__).with_name("wilma.json").read_text())
cfg = pathlib.Path(os.environ["HOME"]) / ".config" / "wilmai" / "config.json"
args = sys.argv[1:]
if not args:  # the interactive sign-in screen
    if ctl["signs_in_to"]:
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps(ctl["config"]))
    sys.exit(ctl["exit"])
if args[:2] == ["kids", "list"]:
    if not cfg.exists():
        print("No saved profile found. Run the interactive CLI first.", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(ctl["students"]))
    sys.exit(0)
sys.exit(2)
"""


def wilma_config(tenant_url: str, username: str = "parent@example.com") -> dict[str, Any]:
    """The config the wilma CLI saves after a sign-in, with the password only Base64-encoded."""
    profile_id = f"{tenant_url}|{username}"
    return {"profiles": [{
        "id": profile_id, "tenantUrl": tenant_url, "tenantName": "Wilma", "username": username,
        "passwordObfuscated": base64.b64encode(f"wilmai::{PASSWORD}".encode()).decode(),
        "students": [{"studentNumber": s["studentNumber"], "name": s["name"]} for s in STUDENTS],
    }], "lastProfileId": profile_id}


class Wilma:
    """The fake wilma CLI and the Terminal window it's signed in from."""

    def __init__(self, home: Path, bin_dir: Path) -> None:
        self.home = home
        self.bin_dir = bin_dir
        self.signs_in_to: str | None = "https://espoo.inschool.fi"  # None: no sign-in
        self.exit = 0                 # the sign-in screen's exit status
        self.students: list[dict[str, Any]] = STUDENTS
        self.terminal_runs = True     # False: the family never finishes in the window
        self.terminal_opens = True    # False: macOS won't open Terminal
        self.terminal: list[str] = []  # each script opened in Terminal, as it read then

    @property
    def config_path(self) -> Path:
        return self.home / ".config" / "wilmai" / "config.json"

    def signed_in_before(self, tenant_url: str) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(wilma_config(tenant_url)))

    def install(self) -> None:
        exe = self.bin_dir / "wilma"
        exe.write_text(FAKE_WILMA.format(python=sys.executable))
        exe.chmod(0o755)
        config = wilma_config(self.signs_in_to) if self.signs_in_to else None
        (self.bin_dir / "wilma.json").write_text(json.dumps(
            {"signs_in_to": self.signs_in_to, "config": config, "exit": self.exit,
             "students": self.students}))


@pytest.fixture
def wilma(harness, tmp_path, monkeypatch) -> Wilma:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for var in ("WILMAI_CONFIG_PATH", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    w = Wilma(harness.home, bin_dir)
    others = subprocess.run  # the harness's fakes

    def run(cmd: list[str], *a: Any, **k: Any) -> subprocess.CompletedProcess:
        if Path(cmd[0]).name == "wilma":
            harness.commands.append(list(cmd))
            return REAL_RUN(cmd, *a, **k)
        if cmd[:3] == ["open", "-a", "Terminal"]:
            harness.commands.append(list(cmd))
            if not w.terminal_opens:
                return subprocess.CompletedProcess(cmd, 1, "", "Unable to find application")
            w.terminal.append(Path(cmd[3]).read_text())
            if w.terminal_runs:
                REAL_RUN(["/bin/sh", cmd[3]], capture_output=True)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return others(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", run)
    return w


def result(capsys) -> tuple[dict[str, Any], str]:
    out, err = capsys.readouterr()
    lines = out.strip().splitlines()
    assert len(lines) == 1, f"expected one JSON line, got: {out!r}"
    return json.loads(lines[0]), out + err


def assert_password_never_leaked(harness, wilma: Wilma, printed: str, caplog) -> None:
    encoded = base64.b64encode(f"wilmai::{PASSWORD}".encode()).decode()
    for value in (PASSWORD, encoded):
        assert value not in printed
        assert value not in caplog.text
        assert not any(value in arg for cmd in harness.commands for arg in cmd)
        assert not any(value in script for script in wilma.terminal)


def preset_cities() -> list[tuple[str, str]]:
    """(Wilma address, city) for each row of "City presets" in docs/config.md."""
    text = CONFIG_MD.read_text()
    table = text.split("## City presets", 1)[1].split("\n\n", 2)[1]
    rows = [[c.strip() for c in line.strip("|").split("|")] for line in table.splitlines()[2:]]
    return [(row[1], row[0].split(" (")[0]) for row in rows]


# ── signing in


def test_signing_in_reports_each_kid_and_the_city(harness, wilma, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    wilma.install()

    assert harness.cli("setup", "wilma") == 0

    res, printed = result(capsys)
    assert res == {
        "result": "signed-in", "city": "Espoo", "wilma_address": "espoo.inschool.fi",
        "kids": [{"name": "Mia Virtanen", "school": None, "class": None},
                 {"name": "Leo Virtanen", "school": None, "class": None}],
    }
    [script] = wilma.terminal
    assert str(wilma.bin_dir / "wilma") in script
    assert_password_never_leaked(harness, wilma, printed, caplog)


def test_school_and_class_are_reported_when_wilma_gives_them(harness, wilma, capsys):
    wilma.students = [{"studentNumber": "1001", "name": "Mia Virtanen", "school": "Kilo School",
                       "className": "3B"},
                      {"studentNumber": "1002", "name": "Leo Virtanen", "school": "Kilo School"}]
    wilma.install()

    assert harness.cli("setup", "wilma") == 0

    assert result(capsys)[0]["kids"] == [
        {"name": "Mia Virtanen", "school": "Kilo School", "class": "3B"},
        {"name": "Leo Virtanen", "school": "Kilo School", "class": None}]


@pytest.mark.parametrize("address, city", preset_cities())
def test_the_city_comes_from_the_wilma_address_of_every_preset_city(harness, wilma, capsys,
                                                                    address, city):
    wilma.signs_in_to = f"https://{address}/"
    wilma.install()

    assert harness.cli("setup", "wilma") == 0

    res, _ = result(capsys)
    assert (res["city"], res["wilma_address"]) == (city, address)


def test_a_wilma_address_without_a_preset_gives_no_city(harness, wilma, capsys):
    wilma.signs_in_to = "https://turku.inschool.fi"
    wilma.install()

    assert harness.cli("setup", "wilma") == 0

    res, _ = result(capsys)
    assert res["city"] is None and res["wilma_address"] == "turku.inschool.fi"


def test_the_city_is_the_one_just_signed_in_to(harness, wilma, capsys):
    wilma.signed_in_before("https://turku.inschool.fi")
    wilma.signs_in_to = "https://vantaa.inschool.fi"
    wilma.install()

    assert harness.cli("setup", "wilma") == 0

    assert result(capsys)[0]["city"] == "Vantaa"


def test_no_open_reads_at_once_when_already_signed_in(harness, wilma, capsys):
    wilma.signed_in_before("https://espoo.inschool.fi")
    wilma.install()

    assert harness.cli("setup", "wilma", "--no-open") == 0

    assert result(capsys)[0]["result"] == "signed-in"
    assert wilma.terminal == []


# ── when it doesn't work


def test_a_failed_sign_in_is_reported(harness, wilma, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    wilma.signs_in_to, wilma.exit = None, 1  # wrong password: the CLI exits with an error
    wilma.install()

    assert harness.cli("setup", "wilma") == 1

    res, printed = result(capsys)
    assert res["result"] == "sign-in-failed" and "setup wilma" in res["next"]
    assert "kids" not in res
    assert_password_never_leaked(harness, wilma, printed, caplog)


def test_leaving_the_sign_in_without_signing_in_is_reported(harness, wilma, capsys):
    wilma.signs_in_to, wilma.exit = None, 0
    wilma.install()

    assert harness.cli("setup", "wilma") == 1

    res, _ = result(capsys)
    assert res["result"] == "not-signed-in" and "setup wilma" in res["next"]


def test_waiting_stops_at_the_time_limit(harness, wilma, monkeypatch, capsys):
    waits: list[float] = []
    monkeypatch.setattr(time, "sleep", waits.append)
    wilma.terminal_runs = False
    wilma.install()

    assert harness.cli("setup", "wilma", "--timeout", "30") == 1

    res, _ = result(capsys)
    assert res["result"] == "timeout" and "--no-open" in res["next"]
    assert 0 < sum(waits) <= 30


def test_without_the_wilma_cli_it_says_how_to_install_it(harness, wilma, capsys):
    assert harness.cli("setup", "wilma") == 1

    res, _ = result(capsys)
    assert res["result"] == "not-installed" and "npm install -g @wilm-ai/wilma-cli" in res["next"]
    assert wilma.terminal == []


def test_when_terminal_cannot_be_opened_it_says_where_to_sign_in(harness, wilma, capsys):
    wilma.terminal_opens = False
    wilma.install()

    assert harness.cli("setup", "wilma") == 1

    res, _ = result(capsys)
    assert res["result"] == "no-terminal" and "--no-open" in res["next"]
