"""Connecting Wilma with `parent-recap setup wilma`: it opens the wilma CLI's sign-in in a
Terminal window, waits for the family, then reports the Kids and the city as one JSON line.

The outside edges are faked: `open` runs the Terminal script it's given right away, as if the
family had just finished in that window, and `wilma` is a fake CLI in Parent Recap's folder,
run on its own Node (`fake_node`), whose sign-in screen behaves like the real one's: it clears the screen before each question, asks which
student when there are several, writes its config only after that, then asks what to view and
fails with a 403 if anything is picked. Waiting takes no time: `time.sleep` returns at once.
A window still open after WINDOW_SECONDS is ended, with the processes it started, and fails its
test, so a hang can't hold up the run.
Assertions are on the JSON result, on what the window showed, on what was opened, and on every
place the Wilma password must never reach."""
from __future__ import annotations

import base64
import contextlib
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

import fake_node
from family_brief import install_record, setup_wilma

REAL_RUN = subprocess.run  # before the harness fakes it
PASSWORD = "Wilma-salasana-42"
WINDOW_SECONDS = 30  # the window closes within about a second
CONFIG_MD = Path(__file__).resolve().parents[2] / "docs" / "config.md"
NO_PTY = Path(__file__).with_name("no_pty")
STUDENTS = [{"studentNumber": "1001", "name": "Mia Virtanen", "href": "/!1001/"},
            {"studentNumber": "1002", "name": "Leo Virtanen", "href": "/!1002/"}]

CLEAR = "\x1b[1;1H\x1b[0J"  # Node's console.clear(), which the CLI calls before each question
FAKE_WILMA = """#!{python}
import json, os, pathlib, select, signal, sys, time
ctl = json.loads(pathlib.Path(__file__).with_name("wilma.json").read_text())
cfg = pathlib.Path(os.environ["HOME"]) / ".config" / "wilmai" / "config.json"
args = sys.argv[1:]

def ask(question):  # the answer's first key, or None when none comes
    print(question, flush=True)
    if select.select([0], [], [], 5)[0]:
        return os.read(0, 1) or None

def ended(*_):  # shows the cursor again, after the window has stopped reading
    print("\\x1b[?25h", end="", flush=True)
    sys.exit(143)

if ctl["writes_when_ended"]:
    # Once its session has opened /dev/tty, macOS lets this exit only after the window has read
    # what it wrote, however long that takes. Bash, the /bin/sh that runs the stand-in node,
    # opens it when it starts. Opened here too, so that holds whichever shell /bin/sh is.
    try:
        os.close(os.open("/dev/tty", os.O_RDWR))
    except OSError:  # no terminal (no_pty)
        pass
    signal.signal(signal.SIGTERM, ended)

if not args:  # the interactive sign-in screen
    print({clear!r} + "? Search tenant by city/name (blank to list all, or type URL)", flush=True)
    time.sleep(0.3)  # the family types the town, the username and the password
    if ctl["signs_in_to"]:
        if len(ctl["students"]) > 1:
            ask({clear!r} + "? Select student")
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps(ctl["config"]))
        if ask({clear!r} + "? What do you want to view?") is not None:
            print("CLI error: Wilma HTTP 403 at /!1001/overview", file=sys.stderr, flush=True)
            sys.exit(1)
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
    """The fake wilma CLI on Parent Recap's own Node, and the Terminal window it's signed in
    from."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.node = fake_node.pinned_node(home)
        self.bin_dir = fake_node.wilma_folder(home) / "bin"
        self.signs_in_to: str | None = "https://espoo.inschool.fi"  # None: no sign-in
        self.exit = 0                 # the sign-in screen's exit status
        self.students: list[dict[str, Any]] = STUDENTS
        self.writes_when_ended = False  # True: the sign-in screen writes once it's ended
        self.terminal_runs = True     # False: the family never finishes in the window
        self.terminal_opens = True    # False: macOS won't open Terminal
        self.terminal: list[str] = []  # each script opened in Terminal, as it read then
        self.window = ""              # everything the Terminal window showed

    @property
    def config_path(self) -> Path:
        return self.home / ".config" / "wilmai" / "config.json"

    def signed_in_before(self, tenant_url: str) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(wilma_config(tenant_url)))

    def install(self) -> None:
        fake_node.install_wilma(self.home, FAKE_WILMA.format(python=sys.executable, clear=CLEAR))
        config = wilma_config(self.signs_in_to) if self.signs_in_to else None
        (self.bin_dir / "wilma.json").write_text(json.dumps(
            {"signs_in_to": self.signs_in_to, "config": config, "exit": self.exit,
             "students": self.students, "writes_when_ended": self.writes_when_ended}))


def can_open_a_pty() -> bool:
    try:
        for fd in os.openpty():
            os.close(fd)
    except OSError:
        return False
    return True


@pytest.fixture
def wilma(harness, tmp_path, monkeypatch) -> Wilma:
    for var in ("WILMAI_CONFIG_PATH", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")  # no Node of the Mac's own
    if not can_open_a_pty():
        monkeypatch.setenv("PYTHONPATH", str(NO_PTY))
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    w = Wilma(harness.home)
    others = subprocess.run  # the harness's fakes

    def run(cmd: list[str], *a: Any, **k: Any) -> subprocess.CompletedProcess:
        if fake_node.is_wilma(cmd):
            harness.commands.append(list(cmd))
            return REAL_RUN(cmd, *a, **k)
        if fake_node.is_npm(cmd):
            harness.commands.append(list(cmd))
            w.install()
            return subprocess.CompletedProcess(cmd, 0, "added 40 packages", "")
        if cmd[:3] == ["open", "-a", "Terminal"]:
            harness.commands.append(list(cmd))
            if not w.terminal_opens:
                return subprocess.CompletedProcess(cmd, 1, "", "Unable to find application")
            w.terminal.append(Path(cmd[3]).read_text())
            if w.terminal_runs:
                w.window += run_window(cmd[3])
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return others(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", run)
    return w


def run_window(script: str) -> str:
    """Runs the Terminal window's script and returns what the window showed. A window still open
    after WINDOW_SECONDS is ended, with the processes it started, and fails the test."""
    window = subprocess.Popen(["/bin/sh", script], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              start_new_session=True)
    try:
        shown, _ = window.communicate(timeout=WINDOW_SECONDS)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(window.pid, signal.SIGKILL)
        shown, _ = window.communicate()
        pytest.fail(f"The Wilma window didn't close within {WINDOW_SECONDS} seconds: its script, "
                    "`setup wilma --screen` or the wilma CLI was still running. The window "
                    f"showed: {shown.decode(errors='replace')!r}")
    return shown.decode()


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
    assert "setup wilma --screen" in script
    assert_password_never_leaked(harness, wilma, printed, caplog)
    # So uninstall removes the profile the CLI saved, and only that.
    assert install_record.entries("wilma-profile") == ["https://espoo.inschool.fi|parent@example.com"]
    assert install_record.entries("wilma-cli") == []  # installed before this ran
    assert PASSWORD not in install_record.path().read_text()


def test_a_profile_from_before_setup_is_not_recorded(harness, wilma, capsys):
    wilma.install()
    wilma.signed_in_before("https://espoo.inschool.fi")

    assert harness.cli("setup", "wilma") == 0

    assert result(capsys)[0]["result"] == "signed-in"
    assert install_record.entries("wilma-profile") == []


def test_without_the_wilma_cli_it_installs_the_pinned_one_and_records_it(harness, wilma, capsys):
    assert harness.cli("setup", "wilma") == 0

    assert result(capsys)[0]["result"] == "signed-in"
    folder = str(fake_node.wilma_folder(harness.home))
    npm = [c for c in harness.commands if fake_node.is_npm(c)]
    assert [c[2:] for c in npm] == [["install", "-g", "--prefix", folder, "@wilm-ai/wilma-cli@1.6.2"]]
    assert npm[0][0] == str(wilma.node)
    assert install_record.entries("wilma-cli") == [folder]
    # The Kid list ran on that Node too.
    assert [c[0] for c in harness.commands if fake_node.is_wilma(c)] == [str(wilma.node)]


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


# ── the window


def screen_at(window: str, prompt: str) -> str:
    """What the window shows while the CLI asks `prompt`: everything since the screen was last
    cleared before it."""
    return window[:window.index(prompt)].rsplit(CLEAR, 1)[-1]


@pytest.mark.parametrize("language, words", [
    ("en", ["Espoo, Helsinki or Vantaa", "Wilma website or app", "saved passwords"]),
    ("zh", ["Espoo、Helsinki 或 Vantaa", "Wilma 网站或 App", "浏览器保存的密码"]),
    ("fi", ["Espoo, Helsinki tai Vantaa", "Wilman verkkosivulla tai sovelluksessa",
            "tallennetuista salasanoista"]),
    ("sv", ["Espoo, Helsinki or Vantaa", "Wilma website or app", "saved passwords"]),
])
def test_the_window_guides_the_family_in_their_language_while_wilma_asks_for_the_town(
        harness, wilma, capsys, language, words):
    wilma.install()

    assert harness.cli("setup", "wilma", "--language", language) == 0

    shown = screen_at(wilma.window, "Search tenant by city/name")
    assert all(w in shown for w in words), shown


def test_the_guide_is_in_english_without_a_language(harness, wilma, capsys):
    wilma.install()

    assert harness.cli("setup", "wilma") == 0

    assert "saved passwords" in screen_at(wilma.window, "Search tenant by city/name")


@pytest.mark.parametrize("students", [STUDENTS, STUDENTS[:1]], ids=["two Kids", "one Kid"])
def test_once_signed_in_the_window_ends_wilma_before_its_student_picker_and_menu(
        harness, wilma, capsys, students):
    wilma.students = students
    wilma.install()

    assert harness.cli("setup", "wilma") == 0

    assert result(capsys)[0]["result"] == "signed-in"
    for hidden in ("Select student", "What do you want to view?", "403", "CLI error"):
        assert hidden not in wilma.window
    assert wilma.window.rsplit(CLEAR, 1)[-1].strip().endswith("You can close this window.")
    assert "signed in to Wilma" in wilma.window.rsplit(CLEAR, 1)[-1]


def test_the_window_closes_when_wilma_writes_after_it_is_ended(harness, wilma, capsys):
    # As when wilma prints its menu just as the window ends it: nothing reads that output, and
    # macOS may not let wilma finish exiting until something does (#216).
    wilma.writes_when_ended = True
    wilma.install()

    assert harness.cli("setup", "wilma") == 0

    assert result(capsys)[0]["result"] == "signed-in"
    assert wilma.window.strip().endswith("You can close this window.")


def test_the_window_says_it_can_be_closed_when_wilma_ends_without_a_sign_in(harness, wilma,
                                                                         capsys):
    wilma.signs_in_to, wilma.exit = None, 0
    wilma.install()

    assert harness.cli("setup", "wilma", "--language", "zh") == 1

    assert wilma.window.strip().endswith("可以关闭这个窗口了。")


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


def test_without_its_own_node_it_says_to_run_the_install_again(harness, wilma, capsys):
    shutil.rmtree(wilma.node.parents[1])

    assert harness.cli("setup", "wilma") == 1

    res, _ = result(capsys)
    assert res["result"] == "not-installed" and "install line" in res["next"]
    assert "brew" not in res["next"]
    assert wilma.terminal == []


def test_when_terminal_cannot_be_opened_it_says_where_to_sign_in(harness, wilma, capsys):
    wilma.terminal_opens = False
    wilma.install()

    assert harness.cli("setup", "wilma") == 1

    res, _ = result(capsys)
    assert res["result"] == "no-terminal" and "--no-open" in res["next"]
