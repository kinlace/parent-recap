"""Connecting Wilma with `parent-recap setup wilma`: it opens the wilma CLI's sign-in in a
terminal window, waits for the family, then reports the Kids and the city as one JSON line.

The outside edges are faked: the desktop runs a terminal window's command right away, as if the
family had just finished in that window, and `wilma` is a fake CLI in Parent Recap's folder,
run on its own Node (`fake_node`), whose sign-in screen behaves like the pinned one's (2.x): it clears the screen before each question, asks which
student when there are several, writes its config only after that, whole and then renamed, then asks what to view and
fails with a 403 if anything is picked. Its Kid list answers in 2.x's JSON. Waiting takes no time: `time.sleep` returns at once.
A window still open after WINDOW_SECONDS is ended, with the processes it started, and fails its
test, so a hang can't hold up the run.
Assertions are on the JSON result, on what the window showed, on what was opened, and on every
place the Wilma password must never reach.

The setup page's Wilma step is at the end, driven with HTTP calls as the page makes them
(`setup_page.py`), against a fake CLI of its own (`WilmaCLI`)."""
from __future__ import annotations

import base64
import contextlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

import fake_node
from family_brief import install_record, setup_server, setup_wilma
from family_brief.config import Config
from fake_system import WINDOWS, Window
from conftest import program
from setup_page import (PAGE_DIR, Response, assert_never_leaked, call, config_file, statuses, towns,
                        wait_for, welcome)
from setup_page import clock, page  # noqa: F401

# The tests of `parent-recap setup wilma` are each marked macos: the Wilma window's pseudo-terminal,
# and the wilma CLI as shell scripts on a shell-script Node.

REAL_RUN = subprocess.run  # before the harness fakes it
PASSWORD = "Wilma-salasana-42"
WINDOW_SECONDS = 30  # the window closes within about a second
CONFIG_MD = Path(__file__).resolve().parents[3] / "docs" / "config.md"
NO_PTY = Path(__file__).parents[1] / "no_pty"
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
    pathlib.Path(__file__).with_name("screen-env.json").write_text(
        json.dumps(os.environ.get("WILMAI_NO_UPDATE_CHECK")))
    print({clear!r} + "? Search tenant by city/name (blank to list all, or type URL)", flush=True)
    time.sleep(0.3)  # the family types the town, the username and the password
    if ctl["signs_in_to"]:
        if len(ctl["students"]) > 1:
            ask({clear!r} + "? Select student")
        cfg.parent.mkdir(parents=True, exist_ok=True)
        saving = cfg.with_name(cfg.name + ".1.tmp")
        saving.write_text(json.dumps(ctl["config"]))
        os.replace(saving, cfg)
        if ask({clear!r} + "? What do you want to view?") is not None:
            print("Error: Wilma HTTP 403 at /overview", file=sys.stderr, flush=True)
            sys.exit(1)
    sys.exit(ctl["exit"])
if args[:2] == ["kids", "list"]:
    if not cfg.exists():
        print(json.dumps(dict(status="error", code="not_logged_in", message="No saved Wilma login.")))
        sys.exit(3)
    print(json.dumps(dict(students=[dict((k, v) for k, v in s.items() if k != "href")
                                    for s in ctl["students"]])))
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
    """The fake wilma CLI on Parent Recap's own Node, and the terminal window it's signed in
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
        self.npm_works = True         # False: npm can't reach its registry
        self.npm_ended = False        # True: npm is ended at the time limit, halfway through
        self.window = ""              # everything the terminal window showed

    @property
    def config_path(self) -> Path:
        return self.home / ".config" / "wilmai" / "config.json"

    def signed_in_before(self, tenant_url: str) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(wilma_config(tenant_url)))

    def install(self, version: str = setup_wilma.WILMA_CLI_VERSION, prefix: Path | None = None) -> None:
        """The fake CLI, in Parent Recap's folder or in npm's `prefix`."""
        bin_wilma = fake_node.install_wilma(
            self.home, FAKE_WILMA.format(python=sys.executable, clear=CLEAR), version, prefix)
        config = wilma_config(self.signs_in_to) if self.signs_in_to else None
        (bin_wilma.parent / "wilma.json").write_text(json.dumps(
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

    def in_window(window: Window) -> None:
        if w.terminal_runs:
            w.window += run_window(window, harness.home)
    harness.desktop.in_window = in_window
    others = subprocess.run  # the harness's fakes

    def run(cmd: list[str], *a: Any, **k: Any) -> subprocess.CompletedProcess:
        if fake_node.is_wilma(cmd):
            harness.commands.append(list(cmd))
            return REAL_RUN(cmd, *a, **k)
        if fake_node.is_npm(cmd):
            harness.commands.append(list(cmd))
            if not w.npm_works:
                return subprocess.CompletedProcess(cmd, 1, "", "npm error code ENOTFOUND")
            if w.npm_ended:  # the package half unpacked, and no bin/wilma yet
                package = fake_node.npm_prefix(cmd) / fake_node.PACKAGE
                package.mkdir(parents=True)
                (package / "package.json").write_text("{")
                raise subprocess.TimeoutExpired(cmd, k["timeout"])
            w.install(prefix=fake_node.npm_prefix(cmd))
            return subprocess.CompletedProcess(cmd, 0, "added 40 packages", "")
        return others(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", run)
    return w


def run_window(window: Window, home: Path) -> str:
    """Runs the terminal window's command, in the home folder with the window's variables added,
    and returns what the window showed. A window still open after WINDOW_SECONDS is ended, with
    the processes it started, and fails the test."""
    running = subprocess.Popen(window.argv, cwd=home, env={**os.environ, **window.env},
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, start_new_session=True)
    try:
        shown, _ = running.communicate(timeout=WINDOW_SECONDS)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(running.pid, signal.SIGKILL)
        shown, _ = running.communicate()
        pytest.fail(f"The Wilma window didn't close within {WINDOW_SECONDS} seconds: its command, "
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
        assert not any(value in part for window in harness.desktop.windows
                       for part in [*window.argv, *window.env.values()])


def preset_cities() -> list[tuple[str, str]]:
    """(Wilma address, city) for each row of "City presets" in docs/config.md."""
    text = CONFIG_MD.read_text()
    table = text.split("## City presets", 1)[1].split("\n\n", 2)[1]
    rows = [[c.strip() for c in line.strip("|").split("|")] for line in table.splitlines()[2:]]
    return [(row[1], row[0].split(" (")[0]) for row in rows]


# ── signing in


@pytest.mark.macos
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
    [window] = harness.desktop.windows
    assert "setup wilma --screen" in " ".join(window.argv)
    assert_password_never_leaked(harness, wilma, printed, caplog)
    # So uninstall removes the profile the CLI saved, and only that.
    assert install_record.entries("wilma-profile") == ["https://espoo.inschool.fi|parent@example.com"]
    assert install_record.entries("wilma-cli") == []  # installed before this ran
    assert PASSWORD not in install_record.path().read_text()


@pytest.mark.macos
def test_a_profile_from_before_setup_is_not_recorded(harness, wilma, capsys):
    wilma.install()
    wilma.signed_in_before("https://espoo.inschool.fi")

    assert harness.cli("setup", "wilma") == 0

    assert result(capsys)[0]["result"] == "signed-in"
    assert install_record.entries("wilma-profile") == []


@pytest.mark.macos
def test_without_the_wilma_cli_it_installs_the_pinned_one_and_records_it(harness, wilma, capsys):
    assert harness.cli("setup", "wilma") == 0

    assert result(capsys)[0]["result"] == "signed-in"
    folder = str(fake_node.wilma_folder(harness.home))
    npm = [c for c in harness.commands if fake_node.is_npm(c)]
    assert [pinned_install(c) for c in npm] == [True]
    assert npm[0][0] == str(wilma.node)
    assert install_record.entries("wilma-cli") == [folder]
    # The Kid list ran on that Node too.
    assert [c[0] for c in harness.commands if fake_node.is_wilma(c)] == [str(wilma.node)]


@pytest.mark.macos
def test_school_and_class_are_reported_when_wilma_gives_them(harness, wilma, capsys):
    wilma.students = [{"studentNumber": "1001", "name": "Mia Virtanen", "school": "Kilo School",
                       "className": "3B"},
                      {"studentNumber": "1002", "name": "Leo Virtanen", "school": "Kilo School"}]
    wilma.install()

    assert harness.cli("setup", "wilma") == 0

    assert result(capsys)[0]["kids"] == [
        {"name": "Mia Virtanen", "school": "Kilo School", "class": "3B"},
        {"name": "Leo Virtanen", "school": "Kilo School", "class": None}]


@pytest.mark.macos
@pytest.mark.parametrize("address, city", preset_cities())
def test_the_city_comes_from_the_wilma_address_of_every_preset_city(harness, wilma, capsys,
                                                                    address, city):
    wilma.signs_in_to = f"https://{address}/"
    wilma.install()

    assert harness.cli("setup", "wilma") == 0

    res, _ = result(capsys)
    assert (res["city"], res["wilma_address"]) == (city, address)


@pytest.mark.macos
def test_a_wilma_address_without_a_preset_gives_no_city(harness, wilma, capsys):
    wilma.signs_in_to = "https://turku.inschool.fi"
    wilma.install()

    assert harness.cli("setup", "wilma") == 0

    res, _ = result(capsys)
    assert res["city"] is None and res["wilma_address"] == "turku.inschool.fi"


@pytest.mark.macos
def test_the_city_is_the_one_just_signed_in_to(harness, wilma, capsys):
    wilma.signed_in_before("https://turku.inschool.fi")
    wilma.signs_in_to = "https://vantaa.inschool.fi"
    wilma.install()

    assert harness.cli("setup", "wilma") == 0

    assert result(capsys)[0]["city"] == "Vantaa"


@pytest.mark.macos
def test_no_open_reads_at_once_when_already_signed_in(harness, wilma, capsys):
    wilma.signed_in_before("https://espoo.inschool.fi")
    wilma.install()

    assert harness.cli("setup", "wilma", "--no-open") == 0

    assert result(capsys)[0]["result"] == "signed-in"
    assert harness.desktop.windows == []


# ── the window


def screen_at(window: str, prompt: str) -> str:
    """What the window shows while the CLI asks `prompt`: everything since the screen was last
    cleared before it."""
    return window[:window.index(prompt)].rsplit(CLEAR, 1)[-1]


@pytest.mark.macos
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


@pytest.mark.macos
def test_the_guide_is_in_english_without_a_language(harness, wilma, capsys):
    wilma.install()

    assert harness.cli("setup", "wilma") == 0

    assert "saved passwords" in screen_at(wilma.window, "Search tenant by city/name")


@pytest.mark.macos
@pytest.mark.parametrize("students", [STUDENTS, STUDENTS[:1]], ids=["two Kids", "one Kid"])
def test_once_signed_in_the_window_ends_wilma_before_its_student_picker_and_menu(
        harness, wilma, capsys, students):
    wilma.students = students
    wilma.install()

    assert harness.cli("setup", "wilma") == 0

    assert result(capsys)[0]["result"] == "signed-in"
    for hidden in ("Select student", "What do you want to view?", "403", "Error:"):
        assert hidden not in wilma.window
    assert wilma.window.rsplit(CLEAR, 1)[-1].strip().endswith("You can close this window.")
    assert "signed in to Wilma" in wilma.window.rsplit(CLEAR, 1)[-1]


@pytest.mark.macos
def test_the_window_s_wilma_cli_never_checks_npm_for_a_newer_version(harness, wilma, capsys):
    # Parent Recap installs the version it pins: the CLI's "Run wilma update" would install another.
    wilma.install()

    assert harness.cli("setup", "wilma") == 0

    assert result(capsys)[0]["result"] == "signed-in"
    assert json.loads((wilma.bin_dir / "screen-env.json").read_text()) == "1"


@pytest.mark.macos
def test_the_window_closes_when_wilma_writes_after_it_is_ended(harness, wilma, capsys):
    # As when wilma prints its menu just as the window ends it: nothing reads that output, and
    # macOS may not let wilma finish exiting until something does (#216).
    wilma.writes_when_ended = True
    wilma.install()

    assert harness.cli("setup", "wilma") == 0

    assert result(capsys)[0]["result"] == "signed-in"
    assert wilma.window.strip().endswith("You can close this window.")


@pytest.mark.macos
def test_the_window_says_it_can_be_closed_when_wilma_ends_without_a_sign_in(harness, wilma,
                                                                         capsys):
    wilma.signs_in_to, wilma.exit = None, 0
    wilma.install()

    assert harness.cli("setup", "wilma", "--language", "zh") == 1

    assert wilma.window.strip().endswith("可以关闭这个窗口了。")


# ── when it doesn't work


@pytest.mark.macos
def test_a_failed_sign_in_is_reported(harness, wilma, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    wilma.signs_in_to, wilma.exit = None, 1  # wrong password: the CLI exits with an error
    wilma.install()

    assert harness.cli("setup", "wilma") == 1

    res, printed = result(capsys)
    assert res["result"] == "sign-in-failed" and "setup wilma" in res["next"]
    assert "kids" not in res
    assert_password_never_leaked(harness, wilma, printed, caplog)


@pytest.mark.macos
def test_leaving_the_sign_in_without_signing_in_is_reported(harness, wilma, capsys):
    wilma.signs_in_to, wilma.exit = None, 0
    wilma.install()

    assert harness.cli("setup", "wilma") == 1

    res, _ = result(capsys)
    assert res["result"] == "not-signed-in" and "setup wilma" in res["next"]


@pytest.mark.macos
def test_waiting_stops_at_the_time_limit(harness, wilma, monkeypatch, capsys):
    waits: list[float] = []
    monkeypatch.setattr(time, "sleep", waits.append)
    wilma.terminal_runs = False
    wilma.install()

    assert harness.cli("setup", "wilma", "--timeout", "30") == 1

    res, _ = result(capsys)
    assert res["result"] == "timeout" and "--no-open" in res["next"]
    assert 0 < sum(waits) <= 30


@pytest.mark.macos
def test_without_its_own_node_it_says_to_run_the_install_again(harness, wilma, capsys):
    shutil.rmtree(wilma.node.parents[1])

    assert harness.cli("setup", "wilma") == 1

    res, _ = result(capsys)
    assert res["result"] == "not-installed" and "install line" in res["next"]
    assert "brew" not in res["next"]
    assert harness.desktop.windows == []


@pytest.mark.macos
def test_when_terminal_cannot_be_opened_it_says_where_to_sign_in(harness, wilma, capsys):
    harness.desktop.opens = False
    wilma.install()

    assert harness.cli("setup", "wilma") == 1

    res, _ = result(capsys)
    assert res["result"] == "no-terminal" and "--no-open" in res["next"]


# ── moving the CLI to the pinned version, as install.sh asks on every update


def package_version(harness) -> str:
    return json.loads((fake_node.wilma_package(harness.home) / "package.json").read_text())["version"]


def pinned_install(cmd: list[str]) -> bool:
    """Whether npm installed the pinned CLI into a new folder next to Parent Recap's own, as a
    global install, the folder gone now that it has taken the place of Parent Recap's own."""
    staging = fake_node.npm_prefix(cmd)
    return cmd[2:] == ["install", "-g", "--prefix", str(staging), "@wilm-ai/wilma-cli@2.1.2"] \
        and staging.parent == fake_node.wilma_folder(Path.home()).parent \
        and staging.name.startswith(".wilma-") and not staging.exists()


@pytest.mark.macos
def test_an_older_wilma_cli_is_moved_to_the_pinned_version(harness, wilma, capsys, monkeypatch):
    wilma.install("1.6.2")
    umasks: list[int] = []
    monkeypatch.setattr(os, "umask", lambda mask: umasks.append(mask) or 0o022)

    assert setup_wilma.main(["update"]) == 0

    npm = [c for c in harness.commands if fake_node.is_npm(c)]
    assert [pinned_install(c) for c in npm] == [True]
    assert npm[0][0] == str(wilma.node)
    assert package_version(harness) == setup_wilma.WILMA_CLI_VERSION
    assert umasks == [0o077]  # install.sh runs it with the shell's umask
    assert capsys.readouterr().out == "✅ The wilma CLI is updated to 2.1.2.\n"


@pytest.mark.macos
def test_the_pinned_wilma_cli_is_not_installed_again(harness, wilma, capsys):
    wilma.install()

    assert setup_wilma.update() == "up-to-date"
    assert setup_wilma.main(["update"]) == 0

    assert not any(fake_node.is_npm(c) for c in harness.commands)
    assert capsys.readouterr().out == ""


@pytest.mark.macos
def test_a_household_without_the_wilma_cli_gets_none_from_an_update(harness, wilma, capsys):
    assert setup_wilma.update() == "not-installed"
    assert setup_wilma.main(["update"]) == 0

    assert not any(fake_node.is_npm(c) for c in harness.commands)
    assert not fake_node.wilma_folder(harness.home).exists()
    assert capsys.readouterr().out == ""


@pytest.mark.macos
def test_a_wilma_cli_that_cannot_be_updated_keeps_working_and_the_install_goes_on(harness, wilma,
                                                                               capsys):
    wilma.install("1.6.2")
    wilma.npm_works = False

    assert setup_wilma.main(["update"]) == 0

    assert package_version(harness) == "1.6.2" and setup_wilma.installed()
    out = capsys.readouterr().out
    assert "couldn't be updated to 2.1.2" in out and "Run the install again" in out
    # The next install tries again.
    wilma.npm_works = True
    assert setup_wilma.update() == "updated"


@pytest.mark.macos
def test_an_update_without_its_own_node_says_to_run_the_install_again(harness, wilma, capsys):
    wilma.install("1.6.2")
    npm = wilma.node.parents[1] / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"
    npm.unlink()

    assert setup_wilma.main(["update"]) == 0

    assert "Node is missing" in capsys.readouterr().out
    assert package_version(harness) == "1.6.2"


def nothing_left_beside_the_cli(harness) -> bool:
    return sorted(p.name for p in (harness.home / "ParentRecap").iterdir()) == ["runtime", "wilma"]


@pytest.mark.macos
def test_an_npm_ended_at_the_time_limit_leaves_the_wilma_cli_as_it_was(harness, wilma, capsys):
    wilma.install("1.6.2")
    wilma.signed_in_before("https://espoo.inschool.fi")
    wilma.npm_ended = True

    assert setup_wilma.main(["update"]) == 0

    assert "couldn't be updated to 2.1.2" in capsys.readouterr().out
    assert setup_wilma.installed() and package_version(harness) == "1.6.2"
    assert nothing_left_beside_the_cli(harness)
    assert harness.cli("setup", "wilma", "--no-open") == 0  # it still signs in
    assert result(capsys)[0]["result"] == "signed-in"
    # The next install tries again.
    wilma.npm_ended = False
    assert setup_wilma.update() == "updated" and package_version(harness) == "2.1.2"


@pytest.mark.macos
def test_an_npm_ended_during_the_first_install_leaves_nothing_behind(harness, wilma, capsys):
    wilma.npm_ended = True

    assert harness.cli("setup", "wilma") == 1

    assert result(capsys)[0]["result"] == "not-installed"
    assert not fake_node.wilma_folder(harness.home).exists()
    assert sorted(p.name for p in (harness.home / "ParentRecap").iterdir()) == ["runtime"]


@pytest.mark.macos
@pytest.mark.parametrize("signed_in", [True, False], ids=["with a sign-in", "without"])
def test_a_wilma_cli_left_half_installed_is_installed_again_when_wilma_was_connected(
        harness, wilma, capsys, signed_in):
    # As an npm ended partway left it, before installs were staged: no bin/wilma.
    wilma.install("1.6.2")
    (fake_node.wilma_folder(harness.home) / "bin" / "wilma").unlink()
    if signed_in:
        wilma.signed_in_before("https://espoo.inschool.fi")
    assert not setup_wilma.installed()

    assert setup_wilma.update() == ("updated" if signed_in else "not-installed")

    assert setup_wilma.installed() is signed_in
    if signed_in:
        assert package_version(harness) == "2.1.2" and nothing_left_beside_the_cli(harness)


# ── Connect: Wilma, on the setup page (ADR 0008)

WILMA_PASSWORD = "Wilma-salasana-42"
ESPOO = "https://espoo.inschool.fi"
TENANTS = {"wilmat": [  # as the Wilma CLI ships them: Wilma's public tenant list
    {"url": "https://aland.inschool.fi", "name": "Ålands skolor",
     "municipalities": [{"name_fi": "Maarianhamina", "name_sv": "Mariehamn"},
                        {"name_fi": "Sund", "name_sv": "Sund"}]},
    {"url": "https://esbosv.inschool.fi", "name": "Esbo stad / Svenska bildningstjänster",
     "municipalities": [{"name_fi": "Espoo", "name_sv": "Esbo"}]},
    {"url": ESPOO, "name": "Espoon kaupunki / Suomenkielisen opetuksen tulosyksikkö",
     "formerUrl": "https://wilma.espoo.fi",
     "municipalities": [{"name_fi": "Espoo", "name_sv": "Esbo"}]},
    {"url": "https://espoonsteiner.inschool.fi", "name": "Espoon Steinerkoulu",
     "municipalities": [{"name_fi": "Espoo", "name_sv": "Esbo"}]},
    {"url": "https://helsinki.inschool.fi", "name": "Helsingin kaupunki",
     "municipalities": [{"name_fi": "Helsinki", "name_sv": "Helsingfors"}]},
    {"url": "https://jarvenpaa.inschool.fi", "name": "Järvenpään kaupunki",
     "municipalities": [{"name_fi": "Järvenpää", "name_sv": "Träskända"}]},
    {"url": "https://omnia.inschool.fi", "name": "Omnia",
     "municipalities": [{"name_fi": "Kirkkonummi", "name_sv": "Kyrkslätt"},
                        {"name_fi": "Espoo", "name_sv": "Esbo"}]},
]}
WILMA_STUDENTS = [{"studentNumber": "1001", "name": "Mia Virtanen", "href": "/!1001/"},
                  {"studentNumber": "1002", "name": "Leo Virtanen", "href": "/!1002/"}]

# The pinned wilma CLI (2.x) as setup uses it. It reads its saved profiles the way its
# dist/config.js (getConfigPath, loadConfig, revealSecret) and dist/credentials.js (resolveAccounts:
# every profile, the one signed in with last first, one per account) do, signs in to a fake Wilma
# that knows one account, and, as dist/access.js (onStudents) and dist/session-store.js do, saves
# each sign-in's Kids back into the config and its session next to it. It answers in 2.x's JSON,
# its errors too, on stdout, and notes in runs.jsonl which config each run read.
FAKE_WILMA_CLI = """#!{python}
import base64, json, os, pathlib, sys
here = pathlib.Path(os.path.realpath(__file__)).parent
ctl = json.loads((here / "wilma.json").read_text())
if os.environ.get("WILMAI_CONFIG_PATH"):
    path = pathlib.Path(os.environ["WILMAI_CONFIG_PATH"])
else:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.environ["HOME"], ".config")
    path = pathlib.Path(base) / "wilmai" / "config.json"
with open(here / "runs.jsonl", "a") as runs:
    runs.write(json.dumps(dict(config=str(path), update_check=os.environ.get("WILMAI_NO_UPDATE_CHECK") != "1")) + "\\n")

def fail(code, message, status=1):
    print(json.dumps(dict(status="error", code=code, message=message)))
    sys.exit(status)

if sys.argv[1:] != ["kids", "list", "--json"]:
    fail("unknown_command", "Unknown command.", 2)
try:
    config = json.loads(path.read_text())
except FileNotFoundError:
    config = dict(profiles=[])
except ValueError:
    fail("config_invalid", "The WilmAI config isn't valid JSON.")
profiles = config.get("profiles") if isinstance(config.get("profiles"), list) else []
accounts, seen = [], set()
for stored in sorted(profiles, key=lambda p: p.get("id") != config.get("lastProfileId")):
    key = stored["tenantUrl"].rstrip("/").lower() + "|" + stored["username"].lower()
    try:
        decoded = base64.b64decode(stored["passwordObfuscated"]).decode()
    except Exception:
        decoded = ""
    if key not in seen and decoded.startswith("wilmai::"):
        seen.add(key)
        accounts.append((stored, decoded[8:]))
if not accounts:
    fail("not_logged_in", "No saved Wilma login. Run `wilma login`.", 3)
kids, sessions, first_error = [], dict(), None
for stored, password in accounts:
    if ctl["fails"]:
        first_error = first_error or ctl["fails"]
    elif ctl["accounts"].get(stored["tenantUrl"] + "|" + stored["username"]) != password:
        first_error = first_error or ["login_failed",
                                      "Wilma didn't accept the saved login (has the password changed?)."]
    else:
        sessions[stored["id"]] = dict(state="a Wilma session", savedAt=0)
        if ctl["students"]:
            stored["students"] = [dict(studentNumber=s["studentNumber"], name=s["name"])
                                  for s in ctl["students"]]
        for s in ctl["students"] or [dict(studentNumber="", name="")]:
            if s["studentNumber"] not in [k["studentNumber"] for k in kids]:
                kids.append(dict((k, v) for k, v in s.items() if k != "href"))
if not sessions:
    fail(*first_error)
path.write_text(json.dumps(config, indent=2) + "\\n")
(path.parent / "wilmai-sessions.json").write_text(json.dumps(sessions))
print(json.dumps(dict(students=kids)))
"""


class WilmaCLI:
    """Parent Recap's own Node and the wilma CLI its npm installs into ~/ParentRecap/wilma,
    laid out as npm lays it out, with Wilma's tenant list inside it. The CLI isn't installed
    until npm or `install()` installs it."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.prefix = fake_node.wilma_folder(home)
        self.node = fake_node.pinned_node(home)
        self.accounts = {f"{ESPOO}|mia.parent": WILMA_PASSWORD}  # tenant|username → password
        self.students: list[dict[str, Any]] = WILMA_STUDENTS
        self.fails: list[str] | None = None  # any other failure, as the CLI's code and words
        self.npm_works = True
        self.installs: list[list[str]] = []
        self.npm_env: dict[str, str] = {}  # the last npm's
        self.ships_tenants = True
        self.signs_in_terminal = True    # the family signs in in the window

    @property
    def package(self) -> Path:
        return fake_node.wilma_package(self.home)

    @property
    def profile_path(self) -> Path:
        return self.home / ".config" / "wilmai" / "config.json"

    def profile(self) -> dict[str, Any]:
        return json.loads(self.profile_path.read_text())

    def install(self, prefix: Path | None = None) -> None:
        """The fake CLI, in Parent Recap's folder or in npm's `prefix`."""
        package = fake_node.install_wilma(self.home, FAKE_WILMA_CLI.format(python=sys.executable),
                                          prefix=prefix).resolve().parents[1]
        if self.ships_tenants:
            client = package / "node_modules" / "@wilm-ai" / "wilma-client"
            client.mkdir(parents=True, exist_ok=True)
            (client / "tenant_list.json").write_text(json.dumps(TENANTS, ensure_ascii=False))
        (package / "dist" / "wilma.json").write_text(json.dumps(
            {"accounts": self.accounts, "students": self.students, "fails": self.fails}))

    def runs(self) -> list[dict[str, Any]]:
        """Which config each run of the CLI read, and whether it would check npm for a newer
        version."""
        runs = self.package / "dist" / "runs.jsonl"
        return [json.loads(line) for line in runs.read_text().splitlines()] if runs.exists() else []

    def signed_in_before(self, tenant: str, username: str = "old.parent",
                         password: str = "old") -> str:
        profile_id = f"{tenant}|{username}"
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        self.profile_path.write_text(json.dumps({"profiles": [{
            "id": profile_id, "tenantUrl": tenant, "tenantName": "Wilma", "username": username,
            "passwordObfuscated": setup_wilma.obfuscate(password), "students": [],
        }], "lastProfileId": profile_id}, indent=2) + "\n")
        return self.profile_path.read_text()


@pytest.fixture
def wilma_cli(harness, monkeypatch) -> WilmaCLI:
    w = WilmaCLI(harness.home)
    for var in ("WILMAI_CONFIG_PATH", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    if not fake_node.WINDOWS:
        monkeypatch.setenv("PATH", "/usr/bin:/bin")  # no Node of the Mac's own
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    others = subprocess.run  # the harness's fakes

    def in_window(window: Any) -> None:
        if w.signs_in_terminal:  # the family signs in in the window: the CLI saves its profile
            w.signed_in_before(ESPOO, "mia.parent", WILMA_PASSWORD)
    harness.desktop.in_window = in_window

    def run(cmd: list[str], *a: Any, **k: Any) -> Any:
        if fake_node.is_wilma(cmd):
            harness.commands.append(list(cmd))
            # On Windows the stand-in node.exe runs nothing: the fake CLI, a Python script, runs
            # on this Python instead.
            return REAL_RUN([sys.executable, *cmd[1:]] if fake_node.WINDOWS else cmd, *a, **k)
        if fake_node.is_npm(cmd):
            harness.commands.append(list(cmd))
            w.installs.append(cmd[2:])
            w.npm_env = k["env"]
            if not w.npm_works:
                return subprocess.CompletedProcess(cmd, 1, "", "npm error code E404")
            w.install(prefix=fake_node.npm_prefix(cmd))
            return subprocess.CompletedProcess(cmd, 0, "added 40 packages", "")
        return others(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", run)
    return w


def sign_in_wilma(url: str, *, tenant: str = ESPOO, town: str | None = "Espoo",
                  username: str = "mia.parent", password: str = WILMA_PASSWORD) -> Response:
    return call(url, "api/wilma", method="POST",
                body={"url": tenant, "town": town, "username": username, "password": password})


def wilma_ready(url: str) -> Response:
    return call(url, "api/wilma/install", method="POST", body={})


def wilma_window_result(url: str) -> dict[str, Any]:
    out: dict[str, Any] = {}

    def done() -> bool:
        out.update(call(url, "api/wilma/check", method="POST", body={}).json())
        return out["result"] != "waiting"
    wait_for(done)
    return out


@pytest.mark.parametrize("query", ["Espoo", "Esbo", "espoo", "ESBO", " Esbo "])
def test_searching_espoo_or_esbo_finds_the_espoo_entries(harness, page, wilma_cli, query):
    wilma_cli.install()

    r = towns(page.url, query)

    assert r.status == 200 and r.json()["result"] == "found"
    found = r.json()["towns"]
    assert {t["url"] for t in found} == {ESPOO, "https://esbosv.inschool.fi",
                                         "https://espoonsteiner.inschool.fi",
                                         "https://omnia.inschool.fi"}
    assert all(t["town"] == "Espoo" for t in found)
    assert next(t for t in found if t["url"] == ESPOO)["name"] == TENANTS["wilmat"][2]["name"]


@pytest.mark.macos
def test_the_town_search_finds_entries_by_name_and_without_accents(harness, page, wilma_cli):
    wilma_cli.install()

    assert [(t["url"], t["town"]) for t in towns(page.url, "steiner").json()["towns"]] == \
        [("https://espoonsteiner.inschool.fi", "Espoo")]
    assert [t["town"] for t in towns(page.url, "jarvenpaa").json()["towns"]] == ["Järvenpää"]
    assert [t["town"] for t in towns(page.url, "Mariehamn").json()["towns"]] == ["Maarianhamina"]
    assert towns(page.url, "E").json()["towns"] == []  # too short to search
    assert towns(page.url, "Tukholma").json()["towns"] == []
    for body in [{}, {"query": 1}, {"query": "Espoo", "x": 1}]:
        assert call(page.url, "api/towns", method="POST", body=body).status == 400, body


def test_setup_installs_the_pinned_wilma_cli_with_its_own_node_when_it_is_missing(
        harness, page, wilma_cli):
    assert towns(page.url, "Espoo").json()["result"] == "not-installed"

    r = wilma_ready(page.url)

    assert r.status == 200 and r.json()["result"] == "installed"
    prefix = str(wilma_cli.prefix)
    # Into a folder of its own next to Parent Recap's, which then takes its place.
    [[*args, staging, version]] = wilma_cli.installs
    assert (args, version) == (["install", "-g", "--prefix"], "@wilm-ai/wilma-cli@2.1.2")
    assert Path(staging).parent == wilma_cli.prefix.parent and not Path(staging).exists()
    assert sorted(p.name for p in wilma_cli.prefix.parent.iterdir()) == ["runtime", "wilma"]
    [npm] = [c for c in harness.commands if fake_node.is_npm(c)]
    assert npm[0] == str(wilma_cli.node)
    # Its package scripts find that Node too, and its cache leaves nothing behind.
    assert wilma_cli.npm_env["PATH"].split(os.pathsep)[0] == str(wilma_cli.node.parent)
    assert not Path(wilma_cli.npm_env["npm_config_cache"]).exists()
    assert towns(page.url, "Espoo").json()["result"] == "found"

    assert wilma_ready(page.url).json()["result"] == "installed"
    assert len(wilma_cli.installs) == 1  # already there: not installed again
    assert install_record.entries("wilma-cli") == [prefix]  # so uninstall removes it


@pytest.mark.macos
def test_a_wilma_cli_and_node_the_mac_already_has_are_not_used(harness, page, wilma_cli,
                                                                tmp_path, monkeypatch):
    # The family's own, on PATH: Parent Recap still installs and runs its own (ADR 0011).
    theirs = tmp_path / "their-bin"
    program(theirs, "wilma", "#!/bin/sh\nexit 99\n")
    program(theirs, "node", "#!/bin/sh\nexit 99\n")
    monkeypatch.setenv("PATH", f"{theirs}:/usr/bin:/bin")

    assert wilma_ready(page.url).json()["result"] == "installed"
    assert sign_in_wilma(page.url).json()["result"] == "signed-in"

    ran = [c for c in harness.commands if fake_node.is_wilma(c) or fake_node.is_npm(c)]
    assert ran and all(c[0] == str(wilma_cli.node) for c in ran)
    assert not any(str(theirs) in arg for c in harness.commands for arg in c)


@pytest.mark.macos
def test_without_its_own_node_it_says_to_run_the_install_again_on_the_page(harness, page, wilma_cli):
    shutil.rmtree(wilma_cli.node.parents[1])

    assert wilma_ready(page.url).json() == {"result": "no-node"}
    assert wilma_cli.installs == []

    fake_node.pinned_node(harness.home)
    wilma_cli.npm_works = False
    assert wilma_ready(page.url).json()["result"] == "install-failed"


def test_no_message_suggests_homebrew_any_more():
    for lang in json.loads((PAGE_DIR / "text.json").read_text()).values():
        assert not any("brew" in text for text in lang.values())
        assert "brew" not in lang["wilma.ready.no-node"].lower()


@pytest.mark.macos
def test_a_cli_without_the_tenant_list_says_so(harness, page, wilma_cli):
    wilma_cli.ships_tenants = False
    wilma_cli.install()

    assert towns(page.url, "Espoo").json()["result"] == "no-list"


def test_a_good_login_writes_the_cli_profile_and_lists_the_kids(harness, page, wilma_cli,
                                                                 caplog, capsys):
    caplog.set_level(logging.DEBUG)
    wilma_cli.install()
    welcome(page.url)

    r = sign_in_wilma(page.url)

    assert r.status == 200
    out = r.json()
    assert out["result"] == "signed-in"
    assert out["kids"] == ["Mia Virtanen", "Leo Virtanen"] and out["city"] == "Espoo"
    assert out["progress"]["sources"]["wilma"] == "done" and out["progress"]["source"] == "gmail"
    # The profile, as the pinned CLI writes it after its own sign-in, which its Kid list read.
    profile = wilma_cli.profile()
    stored = profile["profiles"][0]
    assert profile["lastProfileId"] == stored["id"] == f"{ESPOO}|mia.parent"
    assert set(stored) == {"id", "tenantUrl", "tenantName", "username", "passwordObfuscated",
                           "totpSecretObfuscated", "students", "lastStudentNumber",
                           "lastStudentName", "lastUsedAt"}
    assert (stored["tenantUrl"], stored["tenantName"], stored["username"]) == \
        (ESPOO, TENANTS["wilmat"][2]["name"], "mia.parent")
    assert stored["passwordObfuscated"] == \
        base64.b64encode(f"wilmai::{WILMA_PASSWORD}".encode()).decode()
    assert stored["totpSecretObfuscated"] is None
    assert stored["students"] == [{"studentNumber": "1001", "name": "Mia Virtanen"},
                                  {"studentNumber": "1002", "name": "Leo Virtanen"}]
    assert (stored["lastStudentNumber"], stored["lastStudentName"]) == ("1001", "Mia Virtanen")
    # The CLI checked it on its own, in a config of its own that is gone with the session the CLI
    # saved next to it, and never looked for a newer version of itself.
    [run] = wilma_cli.runs()
    assert run["config"] != str(wilma_cli.profile_path) and not Path(run["config"]).parent.exists()
    assert run["update_check"] is False
    assert sorted(p.name for p in wilma_cli.profile_path.parent.iterdir()) == ["config.json"]
    if not fake_node.WINDOWS:  # where a file's mode says nothing of who can read it
        assert wilma_cli.profile_path.stat().st_mode & 0o777 == 0o600
        assert wilma_cli.profile_path.parent.stat().st_mode & 0o777 == 0o700
    # The Household's answers: the Kids as Wilma spells them, its city, and Wilma on.
    cfg = Config.load(config_file(harness))
    assert [k.name for k in cfg.kids] == ["Mia Virtanen", "Leo Virtanen"]
    assert cfg.city == "Espoo" and cfg.wilma.enabled is True
    assert_never_leaked(harness, [r], caplog, capsys, WILMA_PASSWORD)
    # So uninstall removes the profile, and nothing of the password goes in the record.
    assert install_record.entries("wilma-profile") == [f"{ESPOO}|mia.parent"]
    assert WILMA_PASSWORD not in install_record.path().read_text()


@pytest.mark.macos
def test_signing_in_keeps_the_cli_s_other_profiles(harness, page, wilma_cli):
    wilma_cli.install()
    wilma_cli.signed_in_before("https://helsinki.inschool.fi")

    assert sign_in_wilma(page.url).json()["result"] == "signed-in"

    profiles = wilma_cli.profile()["profiles"]
    assert [p["id"] for p in profiles] == ["https://helsinki.inschool.fi|old.parent",
                                           f"{ESPOO}|mia.parent"]
    assert install_record.entries("wilma-profile") == [f"{ESPOO}|mia.parent"]


@pytest.mark.macos
def test_a_profile_from_before_setup_is_not_recorded_on_the_page(harness, page, wilma_cli):
    wilma_cli.install()
    wilma_cli.signed_in_before(ESPOO, "mia.parent", "older-password")

    assert sign_in_wilma(page.url).json()["result"] == "signed-in"

    assert install_record.entries("wilma-profile") == []


@pytest.mark.macos
def test_a_failed_sign_in_records_no_profile(harness, page, wilma_cli):
    wilma_cli.install()

    assert sign_in_wilma(page.url, password="wrong").json()["result"] == "wrong-password"

    assert install_record.entries("wilma-profile") == []


@pytest.mark.macos
def test_a_wrong_password_is_found_even_when_an_earlier_sign_in_works(harness, page, wilma_cli):
    # From 2.0 the CLI's commands read every profile, so the old one would answer for the new.
    wilma_cli.accounts["https://helsinki.inschool.fi|old.parent"] = "old"
    wilma_cli.install()
    earlier = wilma_cli.signed_in_before("https://helsinki.inschool.fi")

    assert sign_in_wilma(page.url, password="wrong-salasana").json() == {"result": "wrong-password"}

    assert wilma_cli.profile_path.read_text() == earlier
    assert install_record.entries("wilma-profile") == []


@pytest.mark.macos
def test_signing_in_again_replaces_the_account_s_profile_in_its_place_as_the_cli_does(
        harness, page, wilma_cli):
    # As the CLI's saveLogin: the same Wilma and username in any case is one account, which keeps
    # its id, its place among the profiles and its two-step key.
    wilma_cli.accounts = {f"{ESPOO}|Mia.Parent": WILMA_PASSWORD}
    wilma_cli.install()
    helsinki = json.loads(wilma_cli.signed_in_before("https://helsinki.inschool.fi"))["profiles"][0]
    wilma_cli.profile_path.write_text(json.dumps({"profiles": [
        {"id": f"{ESPOO}/|mia.parent", "tenantUrl": f"{ESPOO}/", "tenantName": "Wilma",
         "username": "mia.parent", "passwordObfuscated": setup_wilma.obfuscate("older-password"),
         "totpSecretObfuscated": setup_wilma.obfuscate("JBSWY3DPEHPK3PXP"), "students": []},
        helsinki], "lastProfileId": helsinki["id"]}))

    assert sign_in_wilma(page.url, username="Mia.Parent").json()["result"] == "signed-in"

    config = wilma_cli.profile()
    assert [p["id"] for p in config["profiles"]] == [f"{ESPOO}/|mia.parent",
                                                     "https://helsinki.inschool.fi|old.parent"]
    ours = config["profiles"][0]
    assert (ours["tenantUrl"], ours["username"]) == (ESPOO, "Mia.Parent")
    assert ours["passwordObfuscated"] == setup_wilma.obfuscate(WILMA_PASSWORD)
    assert ours["totpSecretObfuscated"] == setup_wilma.obfuscate("JBSWY3DPEHPK3PXP")
    assert config["lastProfileId"] == ours["id"]
    assert install_record.entries("wilma-profile") == []  # there before setup


@pytest.mark.macos
def test_school_and_class_are_saved_only_when_wilma_gives_them(harness, page, wilma_cli):
    wilma_cli.students = [{"studentNumber": "1001", "name": "Mia Virtanen", "school": "Kilo School",
                           "className": "3B"},
                          {"studentNumber": "1002", "name": "Leo Virtanen"}]
    wilma_cli.install()

    assert sign_in_wilma(page.url).json()["kids"] == ["Mia Virtanen", "Leo Virtanen"]

    mia, leo = Config.load(config_file(harness)).kids
    assert (mia.school, mia.class_name) == ("Kilo School", "3B")
    assert (leo.school, leo.class_name) == (None, None)


@pytest.mark.parametrize("before", [False, True], ids=["first sign-in", "signed in before"])
def test_a_wrong_password_is_reported_as_such_and_not_kept(harness, page, wilma_cli, caplog,
                                                          capsys, before):
    caplog.set_level(logging.DEBUG)
    wilma_cli.install()
    earlier = wilma_cli.signed_in_before("https://helsinki.inschool.fi") if before else None

    r = sign_in_wilma(page.url, password="wrong-salasana")

    assert r.status == 200 and r.json() == {"result": "wrong-password"}
    # The CLI's profile is as it was: the wrong password isn't kept, an earlier sign-in works.
    if before:
        assert wilma_cli.profile_path.read_text() == earlier
    else:
        assert not wilma_cli.profile_path.exists()
    assert statuses(page.url)["wilma"] == "to-do"
    assert not config_file(harness).exists()
    assert_never_leaked(harness, [r], caplog, capsys, "wrong-salasana")


@pytest.mark.parametrize("fails", [["wilma_error", "Wilma answered HTTP 503 while logging in"],
                                   ["mfa_required", "Two-step verification is on for this Wilma account."],
                                   ["network", "fetch failed (ENOTFOUND)"]],
                         ids=["wilma-down", "two-step", "offline"])
def test_any_other_failure_offers_the_terminal_window(harness, page, wilma_cli, fails):
    wilma_cli.fails = fails
    wilma_cli.install()

    r = sign_in_wilma(page.url)

    assert r.status == 200 and r.json() == {"result": "sign-in-failed"}
    assert not wilma_cli.profile_path.exists()
    assert statuses(page.url)["wilma"] == "to-do"


@pytest.mark.macos
def test_an_account_without_kids_is_reported_and_not_kept(harness, page, wilma_cli):
    wilma_cli.students = []
    wilma_cli.install()

    assert sign_in_wilma(page.url).json() == {"result": "no-kids"}
    assert not wilma_cli.profile_path.exists()


@pytest.mark.macos
def test_a_login_needs_an_entry_from_the_list_and_its_town(harness, page, wilma_cli):
    wilma_cli.install()

    for tenant, town in [("https://evil.example", "Espoo"), (ESPOO, "Helsinki"), (ESPOO, None)]:
        r = sign_in_wilma(page.url, tenant=tenant, town=town)
        assert r.status == 400 and r.json()["result"] == "invalid-answers", (tenant, town)
        assert WILMA_PASSWORD.encode() not in r.body
    for body in [{"url": ESPOO, "town": "Espoo", "username": "mia.parent"},
                 {"url": ESPOO, "town": "Espoo", "username": " ", "password": WILMA_PASSWORD},
                 {"url": ESPOO, "town": "Espoo", "username": "mia.parent", "password": ""},
                 {"url": ESPOO, "town": "Espoo", "username": "mia.parent", "password": 1},
                 {"url": ESPOO, "town": "Espoo", "username": "mia.parent",
                  "password": WILMA_PASSWORD, "x": 1}]:
        r = call(page.url, "api/wilma", method="POST", body=body)
        assert r.status == 400 and r.json()["result"] == "invalid-answers", body
        assert WILMA_PASSWORD.encode() not in r.body
    assert not wilma_cli.profile_path.exists()
    assert not any(Path(c[0]).name == "wilma" for c in harness.commands)


@pytest.mark.macos
def test_signing_in_without_the_cli_says_so(harness, page, wilma_cli):
    assert sign_in_wilma(page.url).json() == {"result": "not-installed"}
    assert not wilma_cli.profile_path.exists()


@pytest.mark.macos
def test_an_entry_in_several_towns_is_saved_with_the_town_picked(harness, page, wilma_cli):
    wilma_cli.accounts = {"https://omnia.inschool.fi|mia.parent": WILMA_PASSWORD}
    wilma_cli.install()

    r = sign_in_wilma(page.url, tenant="https://omnia.inschool.fi", town="Kirkkonummi")

    assert r.json()["city"] == "Kirkkonummi"
    assert Config.load(config_file(harness)).city == "Kirkkonummi"


def test_the_terminal_window_signs_in_and_the_page_lists_the_kids(harness, page, wilma_cli,
                                                                  caplog, capsys):
    caplog.set_level(logging.DEBUG)
    wilma_cli.install()
    call(page.url, "api/language", method="POST", body={"language": "fi"})

    r = call(page.url, "api/wilma/terminal", method="POST", body={"town": "Espoo"})

    assert r.status == 200 and r.json()["result"] == "waiting"
    out = wilma_window_result(page.url)
    [window] = harness.desktop.windows
    assert window.argv[window.argv.index("--language") + 1] == "fi"
    assert out["result"] == "signed-in"
    assert out["kids"] == ["Mia Virtanen", "Leo Virtanen"] and out["city"] == "Espoo"
    assert out["progress"]["sources"]["wilma"] == "done"
    cfg = Config.load(config_file(harness))
    assert [k.name for k in cfg.kids] == ["Mia Virtanen", "Leo Virtanen"]
    assert cfg.city == "Espoo" and cfg.wilma.enabled is True
    assert_never_leaked(harness, [r], caplog, capsys, WILMA_PASSWORD)


def test_without_a_town_picked_the_terminal_sign_in_takes_its_entry_s_town(harness, page,
                                                                          wilma_cli):
    wilma_cli.install()

    call(page.url, "api/wilma/terminal", method="POST", body={"town": "Helsinki"})

    assert wilma_window_result(page.url)["city"] == "Espoo"  # signed in to Espoo's Wilma


def test_a_terminal_window_without_a_sign_in_says_so(harness, page, wilma_cli):
    page.context.timeouts.wilma_window = 4
    wilma_cli.signs_in_terminal = False
    wilma_cli.install()

    call(page.url, "api/wilma/terminal", method="POST", body={"town": None})

    assert wilma_window_result(page.url)["result"] == "timeout"
    assert statuses(page.url)["wilma"] == "to-do"


def test_checking_without_a_terminal_window_says_none_is_open(page):
    assert call(page.url, "api/wilma/check", method="POST", body={}).json() == \
        {"result": "no-window"}


def test_a_household_without_wilma_picks_its_town_and_moves_on(harness, page, wilma_cli):
    wilma_cli.install()

    r = call(page.url, "api/town", method="POST", body={"town": "Espoo"})

    assert r.status == 200 and r.json()["result"] == "saved"
    assert r.json()["progress"]["sources"]["wilma"] == "skipped"
    assert r.json()["progress"]["source"] == "gmail"
    cfg = Config.load(config_file(harness))
    assert cfg.city == "Espoo" and cfg.wilma.enabled is False
    for body in [{"town": "Tukholma"}, {"town": None}, {}, {"town": "Espoo", "x": 1}]:
        assert call(page.url, "api/town", method="POST", body=body).status == 400, body


@pytest.mark.macos
def test_the_button_opens_the_passwords_app(harness, page):
    r = call(page.url, "api/open", method="POST", body={"site": "passwords"})

    assert r.status == 200 and r.json()["result"] == "opened"
    assert ["open", "-a", "Passwords"] in harness.commands


def test_off_macos_the_page_offers_neither_whatsapp_nor_the_passwords_app(harness, page):
    # Reading WhatsApp for Mac and the Passwords app exist only on macOS (ADR 0020).
    harness.system = WINDOWS

    sources = [s["name"] for s in call(page.url, "api/state").json()["connect"]["sources"]]
    assert sources == ["wilma", "gmail", "ai", "myclub"]
    r = call(page.url, "api/open", method="POST", body={"site": "passwords"})
    assert r.status == 400 and harness.opened == []


def test_the_wilma_fields_are_standard_login_fields(page):
    html = call(page.url).body.decode()

    form = re.search(r'<form id="wilma-form".*?</form>', html, re.S).group(0)
    username = re.search(r'<input[^>]*id="wilma-username"[^>]*>', form).group(0)
    assert 'autocomplete="username"' in username
    password = re.search(r'<input[^>]*id="wilma-password"[^>]*>', form).group(0)
    assert 'type="password"' in password and 'autocomplete="current-password"' in password
    assert 'id="wilma-passwords"' in form


def test_the_town_box_says_why_it_waits_right_under_it_while_the_wilma_cli_installs(page):
    html = call(page.url).body.decode()

    # Right under the box, before its hint and well above the username, and read out with it,
    # but not announced: the status area further down already is.
    field = re.search(r'<div class="field">\s*<label for="wilma-search".*?</div>', html,
                      re.S).group(0)
    box = re.search(r'<input[^>]*id="wilma-search"[^>]*>', field).group(0)
    assert "disabled" in box and 'aria-describedby="wilma-search-wait"' in box
    wait = re.search(r'<p id="wilma-search-wait"[^>]*>', field).group(0)
    assert "hidden" in wait and 'role="status"' not in wait
    assert field.index(box) < field.index(wait) < field.index('data-text="wilma.search.hint"')


def test_every_wilma_result_is_explained_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        for result in (*setup_server.wilma.WILMA_RESULTS, *setup_server.wilma.TOWN_RESULTS):
            assert table.get(f"wilma.{result}", "").strip(), (language, result)
        for result in setup_server.wilma.WILMA_READY_RESULTS:
            assert table.get(f"wilma.ready.{result}", "").strip(), (language, result)
        # Without Wilma, the install gets the town list ready, on a Mac and a PC alike.
        town = table.get("wilma.ready.installing.town", "")
        assert town.strip() and "wilma" not in town.lower(), language
        assert "wilma.ready.installing.town.windows" not in table, language
        for result in setup_server.wilma.WILMA_WINDOW_RESULTS:
            assert table.get(f"wilma.window.{result}", "").strip(), (language, result)


def test_the_chat_setup_installs_the_same_pinned_cli():
    skill = (Path(__file__).resolve().parents[3] / "skills" / "setup" / "SKILL.md").read_text()

    # `setup wilma` installs it, as the page does, so it's in setup's record for uninstall.
    assert "wilma-cli" not in skill and "`setup wilma` installs" in skill
