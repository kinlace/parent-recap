"""The fixtures that fake the program's outside edges, so internals can be refactored without
touching tests. Each fakes one edge, and an integration test asks only for the one it uses:
  1. `sources`: each Source in the registry faked at its transport (fake_sources.py: Gmail's IMAP
     server, the MyClub feed's HTTP, the wilma CLI, WhatsApp's database), serving what
     `sources.tonight` holds for it to its real collector.
  2. `model`: every backend's model calls made to the llm package's in-memory Backend, which
     records each and answers from the replies it is given (fake_model.py, ADR 0023).
  3. `outbox`: email sending, iMessage and the Google Calendar API record what they are given,
     or fail (fake_outbox.py).
  4. `system`: the OS services in `family_brief.system` (secrets, the evening jobs, the daily wake,
     the desktop, the secret dialog) as in-memory fakes in place of their adapters, and the
     system's profile (fake_system.py, ADR 0020).
`harness` composes them and runs the real `parent-recap` in-process, for the end-to-end tests.
Everything else (config, state, archive) is real and lives in a temporary HOME. "Now" and the
local timezone are pinned. Regenerate golden files, all or one night's, with:

    pytest --update-goldens
    pytest --update-goldens=normal_night
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator
from zoneinfo import ZoneInfo

import pytest
import time_machine
import yaml

from family_brief import __main__ as cli, feedback, tools
from family_brief.collectors.base import CalendarEvent, Message
from family_brief.llm import retry
from family_brief.system import profile_macos, secrets
from fake_model import FakeModel
from fake_outbox import FakeOutbox
from fake_sources import FakeSources
from fake_system import WINDOWS, FakeSystem
from goldens import Goldens

# The test layers, one folder each (README.md). Tests import helpers from tests in other layers
# (`from test_source_failures import ...`), so every layer's folder is on the path, whichever runs.
LAYERS = ("unit", "integration", "e2e", "adapters", "scripts")
sys.path += [str(Path(__file__).parent / layer) for layer in LAYERS]
TZ = "Europe/Helsinki"
# A Sunday evening in autumn, after the nightly launchd slot.
NOW = datetime(2026, 9, 27, 21, 0, tzinfo=ZoneInfo(TZ))
GOLDEN_DIR = Path(__file__).parent / "golden"
# A stand-in for the pilot Form the team ships: no real Form is needed.
PILOT_FORM_URL = "https://docs.google.com/forms/d/e/PILOT_FORM/viewform"
PILOT_FORM_FIELDS = {"verdict": "entry.11", "item_text": "entry.12", "source": "entry.13",
                     "backend": "entry.14", "date": "entry.15", "household": "entry.16",
                     "kid": "entry.17"}


REAL_RUN = subprocess.run  # before the harness fakes it


@pytest.fixture(autouse=True)
def _programs_only_where_the_test_puts_them(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not in this Mac's Homebrew or nix folders, and not where its login shell finds them.
    tools.find adds to PATH, which is put back after each test."""
    monkeypatch.setattr(profile_macos, "SYSTEM_DIRS", ())
    monkeypatch.delenv("SHELL", raising=False)
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))


@pytest.fixture
def login_shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[[Path], None]:
    """The family's login shell, whose own setup (fish, nix, a custom npm prefix) puts the folder
    given on its PATH, which this process's PATH doesn't have. It runs for real, past the
    harness's fakes, the way tools.find asks it: `$SHELL -l -c 'command -v NAME'`."""
    shell = tmp_path / "login-shell"

    def put_on_path(folder: Path) -> None:
        shell.write_text('#!/bin/sh\n[ "$1" = -l ] && [ "$2" = -c ] || exit 64\n'
                         f'PATH="{folder}:$PATH" exec /bin/sh -c "$3"\n')
        shell.chmod(0o755)
        monkeypatch.setenv("SHELL", str(shell))
        others = subprocess.run

        def run(cmd: list[str], *a: Any, **k: Any) -> subprocess.CompletedProcess:
            return (REAL_RUN if cmd[0] == str(shell) else others)(cmd, *a, **k)
        monkeypatch.setattr(subprocess, "run", run)
    return put_on_path


@pytest.fixture
def windows_programs(harness: "Harness", monkeypatch: pytest.MonkeyPatch) -> Path:
    """Programs found as Windows finds them: by their Windows names, in Windows' usual folders,
    with %LOCALAPPDATA% in the test's home, which is returned. PATH leads to none of them, and
    off Windows, Windows' own `which` isn't tried, since it can't run there. The program sees
    Windows' profile."""
    harness.system = WINDOWS
    if os.name != "nt":
        monkeypatch.setattr(tools.shutil, "which", lambda *_a, **_k: None)
    local = harness.home / "AppData" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("PATH", str(harness.home / "Windows" / "System32"))
    return local


@pytest.fixture
def credential_manager(harness: "Harness", monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Windows' own Credential Manager in place of the harness's fake secrets store, under a name
    of the test's own, so no secret of the family whose PC runs the tests is touched. The test
    adds the names it stores to the list given, and their credentials are deleted after it."""
    from family_brief.system import secrets_windows
    monkeypatch.setattr(secrets, "_adapter", secrets_windows)
    monkeypatch.setattr(secrets_windows, "TARGET",
                        f"Parent Recap test {uuid.uuid4().hex[:8]}/{{name}}")
    stored: list[str] = []
    yield stored
    for name in stored:
        secrets.delete(name)


def program(folder: Path, name: str, text: str = "#!/bin/sh\n") -> Path:
    """An executable `name` in `folder`."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text)
    (folder / name).chmod(0o755)
    return folder / name


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        # A test's layer is the folder it's in, so `-m unit` and the others select by folder.
        layer = item.path.relative_to(Path(__file__).parent).parts[0]
        if layer not in LAYERS:
            raise pytest.UsageError(f"{item.path.name} is in no layer's folder: {', '.join(LAYERS)}")
        item.add_marker(getattr(pytest.mark, layer))
        # The one way a test says it needs macOS, or Windows, so each CI job runs every other test.
        if sys.platform != "darwin" and item.get_closest_marker("macos"):
            item.add_marker(pytest.mark.skip(reason="needs macOS"))
        if sys.platform != "win32" and item.get_closest_marker("windows"):
            item.add_marker(pytest.mark.skip(reason="needs Windows"))


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--update-goldens", nargs="?", const="*", default=None, metavar="PATTERN",
                     help="Rewrite the golden files whose names match PATTERN (a glob, or a name "
                          "up to a dot such as normal_night; all without one) from the current "
                          "output instead of comparing.")


# ── Golden files

_GOLDENS = pytest.StashKey[Goldens]()


def pytest_configure(config: pytest.Config) -> None:
    pattern = config.getoption("--update-goldens")
    # `--update-goldens tests/e2e` would take the folder for its pattern and rewrite nothing.
    if pattern and ("/" in pattern or os.sep in pattern or pattern.endswith(".py")):
        raise pytest.UsageError(f"--update-goldens takes a golden file name pattern, not a path "
                                f"({pattern}); write it as --update-goldens=PATTERN")
    config.stash[_GOLDENS] = Goldens(GOLDEN_DIR, pattern)


def pytest_terminal_summary(terminalreporter: Any, config: pytest.Config) -> None:
    goldens = config.stash.get(_GOLDENS, None)
    if goldens is None or goldens.pattern is None:
        return
    terminalreporter.section("golden files rewritten")
    for line in goldens.summary():
        terminalreporter.write_line(line)


@pytest.fixture
def golden(request: pytest.FixtureRequest) -> Callable[[str, str], None]:
    return request.config.stash[_GOLDENS].check


def html_for_golden(html: str) -> str:
    # The Brief's HTML is one long line; a newline per tag makes golden diffs reviewable.
    return html.replace("><", ">\n<") + "\n"


def ics_for_golden(payload: bytes) -> str:
    return payload.decode("utf-8").replace("\r\n", "\n")


# ── The fixtures the harness is composed of

class Processes:
    """`subprocess.run` for the program under test: each command it starts is recorded in
    `commands` and answered by the first of `answers` that knows it, and any other fails the test.
    What is left here has no interface of its own, so it meets its command: ADR 0020's gap, the
    macOS-only features (iMessage's osascript, `open` beside Full Disk Access and for the
    Passwords app), the Sources' own programs, and Claude Code's and Codex's sign-in and plugin
    commands. A model call never is one: the model is the in-memory Backend (fake_model.py)."""

    def __init__(self, mp: pytest.MonkeyPatch) -> None:
        self.commands: list[list[str]] = []
        # Each takes the command, its program's name and run's arguments, and gives its result,
        # or None for a command it doesn't know.
        self.answers: list[Callable[..., subprocess.CompletedProcess | None]] = []
        mp.setattr(subprocess, "run", self.run)

    def run(self, cmd: list[str], *_a: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        # Windows' claude.exe, or codex.CMD as `which` names it, in PATHEXT's capitals.
        prog, ext = os.path.splitext(Path(cmd[0]).name)
        prog = prog if ext.lower() in (".exe", ".cmd") else prog + ext
        self.commands.append(list(cmd))
        for answer in self.answers:
            result = answer(cmd, prog, **kwargs)
            if result is not None:
                return result
        raise AssertionError(f"unexpected subprocess in test: {cmd[:3]}")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The family's home folder, empty, as HOME, with none of this computer's sign-ins or
    sessions in the environment."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows' Path.home()
    # Where install.ps1 puts Parent Recap, so uninstall never finds the PC's own install.
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    # Over SSH, macOS's dialogs aren't tried, and in tmux or SSH setup warns that the Keychain
    # can't be written: a test that wants that sets these itself. The wilma CLI's config is
    # the one in the temporary HOME.
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                "CLAUDE_CODE_OAUTH_TOKEN", "SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY", "TMUX",
                "WILMAI_CONFIG_PATH", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    return home


@pytest.fixture
def processes(monkeypatch: pytest.MonkeyPatch) -> Processes:
    return Processes(monkeypatch)


@pytest.fixture
def system(processes: Processes, monkeypatch: pytest.MonkeyPatch) -> FakeSystem:
    """The OS services' in-memory fakes and the system's profile (fake_system.py)."""
    fake = FakeSystem(monkeypatch)
    processes.answers.append(fake.open_command)
    return fake


@pytest.fixture
def sources(home: Path, system: FakeSystem, processes: Processes,
            monkeypatch: pytest.MonkeyPatch) -> FakeSources:
    """Each Source in the registry faked at its transport (fake_sources.py), read with
    default_config()."""
    fake = FakeSources(home, default_config(), system.secrets, monkeypatch)
    processes.answers.append(fake.program)
    return fake


@pytest.fixture
def model(processes: Processes, monkeypatch: pytest.MonkeyPatch) -> FakeModel:
    """The model, as the in-memory Backend every adapter calls (fake_model.py)."""
    fake = FakeModel(monkeypatch)
    processes.answers.append(fake.program_command)
    return fake


@pytest.fixture
def outbox(home: Path, processes: Processes, monkeypatch: pytest.MonkeyPatch) -> FakeOutbox:
    """Email, iMessage and Google Calendar, recording what goes out (fake_outbox.py)."""
    fake = FakeOutbox(home, monkeypatch)
    processes.answers.append(fake.osascript)
    return fake


@pytest.fixture
def pauses(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """The pauses between tries of a busy model, in seconds: each moves the run's monotonic clock
    on instead of being slept."""
    paused: list[float] = []
    monkeypatch.setattr(retry, "_sleep", paused.append)
    monkeypatch.setattr(retry, "_clock", lambda: sum(paused))
    return paused


# ── Harness

def _to(part: str, name: str) -> property:
    """The harness's `name`, which is `name` of its fixture `part`."""
    return property(lambda h: getattr(getattr(h, part), name),
                    lambda h, value: setattr(getattr(h, part), name, value))


class Harness:
    """The four fixtures together, for a test that runs the program end to end: `run` and `cli`
    write the config and call main(). Each fixture's attributes are the harness's too, by the
    names the tests use."""

    def __init__(self, home: Path, sources: FakeSources, model: FakeModel, outbox: FakeOutbox,
                 system: FakeSystem, processes: Processes, monkeypatch: pytest.MonkeyPatch) -> None:
        self.home = home
        self._sources, self.model, self.outbox, self._system = sources, model, outbox, system
        self.processes = processes
        self._install(monkeypatch)

    config = _to("_sources", "config")
    sources = _to("_sources", "tonight")
    source_fakes = _to("_sources", "fakes")
    lookback_hours = _to("_sources", "lookback_hours")
    model_reply = _to("model", "reply")
    model_error = _to("model", "error")
    model_calls = _to("model", "calls")
    signed_in = _to("model", "signed_in")
    claude_plugins = _to("model", "claude_plugins")
    claude_marketplaces = _to("model", "claude_marketplaces")
    plugin_calls = _to("model", "plugin_calls")
    claude_removes = _to("model", "claude_removes")
    sent = _to("outbox", "sent")
    email_error = _to("outbox", "email_error")
    imessages = _to("outbox", "imessages")
    imessage_error = _to("outbox", "imessage_error")
    calendar = _to("outbox", "calendar")
    secrets = _to("_system", "secrets")
    jobs = _to("_system", "jobs")
    wake = _to("_system", "wake")
    desktop = _to("_system", "desktop")
    dialog = _to("_system", "dialog")
    system = _to("_system", "profile")  # the profile the program sees
    signs_in_after_update = _to("_system", "signs_in_after_update")
    opened = _to("_system", "opened")
    commands = _to("processes", "commands")  # every process started

    # Paths
    @property
    def archive_dir(self) -> Path:
        return self.home / "ParentRecap"

    @property
    def state_path(self) -> Path:
        return self.home / ".family" / "state.json"

    def state(self) -> dict[str, Any]:
        return json.loads(self.state_path.read_text())

    def write_archive(self, day: str, data: Any) -> None:
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        (self.archive_dir / f"{day}.raw.json").write_text(text)

    def model_prompt(self, i: int = -1) -> str:
        return self.model.prompt(i)

    def model_payload(self, i: int = -1) -> dict[str, Any]:
        return self.model.payload(i)

    # Run
    def run(self, *args: str) -> int:
        self.set_up_sources()
        return self.cli("run", *args)

    def set_up_sources(self) -> None:
        """What each Source's setup leaves on the family's computer (fake_sources.py), where the
        test hasn't put it itself. `run` does this; a test reading the Sources another way, such
        as the setup page's preview, calls it."""
        self._sources.set_up()

    def cli(self, *args: str) -> int:
        cfg_path = self.home / ".family" / "config.yaml"
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(yaml.safe_dump(self.config, allow_unicode=True, sort_keys=False))
        argv = ["family-brief", "-c", str(cfg_path), *args]
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sys, "argv", argv)
            return cli.main()

    # What no fixture fakes: the network the run waits for, and the pilot Form
    def _install(self, mp: pytest.MonkeyPatch) -> None:
        self.probed: list[str] = []  # each host the run waited for the network on

        def connect(address: tuple[str, int], *_a: Any, **_k: Any) -> _FakeSocket:
            self.probed.append(address[0])
            return _FakeSocket()
        mp.setattr(socket, "create_connection", connect)

        # Whether this version ships the pilot Form is each test's to say, with ship_pilot_form.
        mp.setattr(feedback, "PILOT_FORM", self.home / "pilot_feedback.yaml")

    def ship_pilot_form(self) -> None:
        """This version ships the pilot Form's block, as ops/feedback-form's log prints it."""
        feedback.PILOT_FORM.write_text(yaml.safe_dump({"feedback": {
            "prefill_base_url": PILOT_FORM_URL, "fields": PILOT_FORM_FIELDS}}))

    def authorize_google_calendar(self) -> None:
        self.outbox.authorize_google_calendar()


class _FakeSocket:
    def close(self) -> None:
        pass


def default_config() -> dict[str, Any]:
    return {
        "timezone": TZ,
        # Pilot configs were copied from an example that said zh; a config without the key gets en.
        "summary_language": "zh",
        "kids": [
            {"name": "Mia", "aliases": ["米娅"], "grade": 3, "class_name": "3B",
             "school": "Kilo School", "activities": ["football"],
             "myclub_ical_url": "https://example.myclub.fi/ical/mia.ics"},
            {"name": "Leo", "aliases": ["小狮"], "grade": 1, "class_name": "1A",
             "school": "Kilo School", "activities": ["piano"]},
        ],
        "gmail": {"username": "parent@example.com", "allowlist_domains": ["kilo.example.fi"]},
        "wilma": {"enabled": True},
        "whatsapp": {"enabled": True, "chats": [
            {"name": "3B parents", "kid": "Mia", "label": "class"},
            {"name": "Leo piano", "kid": "Leo", "label": "piano"},
        ]},
        "google_calendar": {"mode": "ics"},
        "email": {"enabled": True, "to": ["parent@example.com", "partner@example.com"]},
        "imessage": {"enabled": False},
        "llm": {"backend": "claude"},
        "archive": {"dir": "~/ParentRecap"},
        "state_path": "~/.family/state.json",
    }


@pytest.fixture
def harness(home: Path, sources: FakeSources, model: FakeModel, outbox: FakeOutbox,
            system: FakeSystem, processes: Processes, monkeypatch: pytest.MonkeyPatch):
    """The four fixtures composed, for the end-to-end tests: an integration test asks only for
    the one it uses."""
    h = Harness(home, sources, model, outbox, system, processes, monkeypatch)
    # tick=False: every datetime.now() in the run sees exactly NOW, so output is byte-stable.
    with time_machine.travel(NOW, tick=False):
        yield h


# ── Fixture builders

# A model reply with something to say, for tests that need the night's Brief to go out: a night
# the model finds nothing to say about, as fake_model.QUIET, sends none (#385).
SAYS_SOMETHING = {"per_kid": [], "calendar_events": [], "message_digest": "- Something new tonight"}


def msg(source: str, ext_id: str, when: str, body: str, *, sender: str | None = None,
        subject: str | None = None, chat: str | None = None, kid: str | None = None,
        url: str | None = None, metadata: dict[str, Any] | None = None) -> Message:
    return Message(source=source, external_id=ext_id,
                   timestamp=datetime.fromisoformat(when).astimezone(ZoneInfo("UTC")),
                   sender=sender, subject=subject, body=body, chat_name=chat, kid_hint=kid,
                   url=url, metadata=metadata or {})


def myclub_event(ext_id: str, title: str, start: str, end: str, kid: str = "Mia") -> CalendarEvent:
    """An event of `kid`'s MyClub feed, titled `title` there, as the MyClub Source gives it."""
    return CalendarEvent(source="myclub", external_id=ext_id, title=f"⚽ {title}",
                         start=datetime.fromisoformat(start), end=datetime.fromisoformat(end),
                         location="Leppävaara field", description="MyClub", kid=kid)
