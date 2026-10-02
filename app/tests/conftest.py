"""End-to-end harness: runs the real `family-brief run` in-process with fakes at the outside edges.

Fakes sit at exactly three boundaries, so internals can be refactored without touching tests:
  1. Sources: each Source's collect entry point returns fixtures or raises. Like the real
     collectors, it skips Messages already marked seen in state and marks the rest seen.
     (test_source_failures.py puts the real Gmail and Wilma Sources back, and fakes their IMAP
     server and `wilma` CLI instead.)
  2. Model process: `subprocess.run` for `claude` / `codex` records argv + stdin, returns a canned reply
     (the same for every call, or one per call from a list, where FailedCall makes that call fail
     and a function makes the reply from the prompt it is given).
  3. Delivery: email sending, iMessage (`osascript`) and the Google Calendar API record what
     they are given, or fail.
Setup commands also meet macOS's secret dialog (`osascript`) and `open`, faked the same way.

Everything else (config, state, archive) is real and lives in a temporary HOME.
"Now" and the local timezone are pinned. Regenerate golden files with:

    pytest --update-goldens
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pytest
import time_machine
import yaml

from family_brief import __main__ as cli
from family_brief.actions import calendar as calendar_action, email as email_action
from family_brief.collectors import gmail, myclub, whatsapp, wilma
from family_brief.collectors.base import CalendarEvent, Message
from family_brief.utils import keychain

TZ = "Europe/Helsinki"
# A Sunday evening in autumn, after the nightly launchd slot.
NOW = datetime(2026, 9, 27, 21, 0, tzinfo=ZoneInfo(TZ))
GOLDEN_DIR = Path(__file__).parent / "golden"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--update-goldens", action="store_true",
                     help="Rewrite golden files from the current output instead of comparing.")


# ── Golden files

@pytest.fixture
def golden(request: pytest.FixtureRequest) -> Callable[[str, str], None]:
    update = request.config.getoption("--update-goldens")

    def check(name: str, actual: str) -> None:
        path = GOLDEN_DIR / name
        if update:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(actual)
            return
        assert path.exists(), f"missing golden {path.name}; run: pytest --update-goldens"
        assert actual == path.read_text(), f"{path.name} differs; if intended, run: pytest --update-goldens"

    return check


def html_for_golden(html: str) -> str:
    # The Brief's HTML is one long line; a newline per tag makes golden diffs reviewable.
    return html.replace("><", ">\n<") + "\n"


def ics_for_golden(payload: bytes) -> str:
    return payload.decode("utf-8").replace("\r\n", "\n")


# ── Recorded outbound traffic

@dataclass
class SentEmail:
    subject: str
    text: str
    html: str | None
    from_addr: str
    to: list[str]
    attachments: list[tuple[str, bytes, str]]

    def attachment(self, suffix: str) -> tuple[str, bytes, str] | None:
        return next((a for a in self.attachments if a[0].endswith(suffix)), None)


@dataclass
class ModelCall:
    argv: list[str]
    stdin: str | None
    cwd: str | None
    cwd_contents: list[str] | None  # what was in cwd when the process started
    env: dict[str, str] | None
    stdin_source: Any  # the `stdin=` argument, e.g. subprocess.DEVNULL


def system_prompt_of(call: ModelCall) -> str:
    return call.argv[call.argv.index("--system-prompt") + 1]


def assert_isolated_claude(call: ModelCall) -> None:
    """Only our system prompt (not Claude Code's own), no tools, no MCP, no user-level settings,
    no saved session, the prompt on stdin rather than in argv (argv is size-capped and shows in
    `ps`), and an empty working dir that is gone once the run ends."""
    argv = call.argv
    assert "--system-prompt" in argv and "--append-system-prompt" not in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in argv and "--mcp-config" not in argv
    assert argv[argv.index("--disallowedTools") + 1] == "mcp__*"
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert "--no-session-persistence" in argv
    assert call.stdin and call.stdin_source is None
    assert not any(call.stdin in a for a in argv)
    assert call.cwd_contents == []
    assert call.cwd is not None and not Path(call.cwd).exists()


@dataclass
class FailedCall:
    """In a list of model replies: this call's process exits non-zero with `stderr`."""
    stderr: str


@dataclass
class Dialog:
    """The macOS secret dialog: what the family types and which button they press.
    `button` is "save", "other" (the dialog's second choice), "cancel", or "fails" (no desktop
    session to show it on)."""
    typed: str = ""
    button: str = "save"
    shown: list[str] = field(default_factory=list)  # each dialog's AppleScript


@dataclass
class FakeCalendarService:
    """Stands in for googleapiclient's Calendar v3 service: events().list/insert(...).execute()."""
    existing: list[dict] = field(default_factory=list)
    inserted: list[dict] = field(default_factory=list)
    error: Exception | None = None
    fail_after: int = 0  # inserts that succeed before `error` is raised

    def events(self) -> "FakeCalendarService":
        return self

    def list(self, **kw: Any) -> "_Call":
        if "privateExtendedProperty" in kw:
            h = kw["privateExtendedProperty"].split("=", 1)[1]
            return _Call(lambda: {"items": [e for e in self.inserted
                                            if e["extendedProperties"]["private"]["family_brief_hash"] == h]})
        return _Call(lambda: {"items": self.existing})

    def insert(self, calendarId: str, body: dict, sendUpdates: str) -> "_Call":
        def run() -> dict:
            if self.error and len(self.inserted) >= self.fail_after:
                raise self.error
            ev = {**body, "id": f"gev{len(self.inserted) + 1}",
                  "htmlLink": f"https://calendar.google.com/event?eid=gev{len(self.inserted) + 1}"}
            self.inserted.append(ev)
            return ev
        return _Call(run)


@dataclass
class _Call:
    fn: Callable[[], dict]

    def execute(self) -> dict:
        return self.fn()


# ── Harness

class Harness:
    def __init__(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.home = home
        self.config: dict[str, Any] = default_config()
        self.sources: dict[str, Any] = {}          # source -> list | (events, msgs) | Exception | callable
        # One reply for every call, or a list with one entry per call, in the order they are made.
        self.model_reply: str | dict | list[str | dict | FailedCall | Callable[[str], dict]] = \
            {"per_kid": [], "calendar_events": [], "message_digest": ""}
        self.model_error: str | None = None        # stderr of a failing model process
        self.email_error: Exception | None = None
        self.imessages: list[tuple[str, str]] = []  # (recipient, text) actually sent
        self.imessage_error: str | None = None      # stderr of a failing osascript
        self.lookback_hours: dict[str, list[int]] = {}  # source -> lookback each run collected with
        self.calendar = FakeCalendarService()
        self.sent: list[SentEmail] = []
        self.model_calls: list[ModelCall] = []
        self.keychain: dict[str, str] = {}          # account -> secret under service family-brief
        self.dialog = Dialog()
        self.opened: list[str] = []                 # what `open` was asked to open
        self.commands: list[list[str]] = []         # every process started, model calls included
        self._install(monkeypatch)

    # Paths
    @property
    def archive_dir(self) -> Path:
        return self.home / "FamilyBrief"

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
        return self.model_calls[i].stdin

    def model_payload(self, i: int = -1) -> dict[str, Any]:
        prompt = self.model_prompt(i)
        return json.loads(prompt[prompt.index("\n{") + 1:])

    # Run
    def run(self, *args: str) -> int:
        return self.cli("run", *args)

    def cli(self, *args: str) -> int:
        cfg_path = self.home / ".family" / "config.yaml"
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(yaml.safe_dump(self.config, allow_unicode=True, sort_keys=False))
        argv = ["family-brief", "-c", str(cfg_path), *args]
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sys, "argv", argv)
            return cli.main()

    # Fakes
    def _install(self, mp: pytest.MonkeyPatch) -> None:
        mp.setenv("HOME", str(self.home))
        # Over SSH, macOS's dialogs aren't tried: a test that wants that sets these itself.
        for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                    "CLAUDE_CODE_OAUTH_TOKEN", "SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"):
            mp.delenv(var, raising=False)

        def source(name: str) -> Callable[..., Any]:
            def collect(cfg: Any, state: Any, *_a: Any, **_k: Any) -> Any:
                if name != "myclub":
                    self.lookback_hours.setdefault(name, []).append(getattr(cfg, name).lookback_hours)
                v = self.sources.get(name, ([], []) if name == "myclub" else [])
                if isinstance(v, Exception):
                    raise v
                v = v() if callable(v) else v
                if name == "myclub":  # the feed supplies every event again each night
                    return v
                new = [m for m in v if not state.has_seen_message(name, m.external_id)]
                for m in new:
                    state.mark_message_seen(name, m.external_id)
                return new
            return collect

        mp.setattr(gmail, "collect", source("gmail"))
        mp.setattr(wilma, "collect", source("wilma"))
        mp.setattr(whatsapp, "collect", source("whatsapp"))
        mp.setattr(myclub, "collect_events", source("myclub"))

        # The keyring library and the `security` CLI both read the same fake Keychain;
        # CI runners have no keyring backend at all.
        mp.setattr(keychain, "get", self.keychain.get)
        mp.setattr(keychain, "set_", self.keychain.__setitem__)
        mp.setattr(keychain, "delete", lambda key: self.keychain.pop(key, None))
        mp.setattr(subprocess, "run", self._fake_subprocess_run)
        mp.setattr(socket, "create_connection", lambda *_a, **_k: _FakeSocket())

        def send(subject: str, body_text: str, from_addr: str, to_addrs: list[str],
                 body_html: str | None = None,
                 attachments: list[tuple[str, bytes, str]] | None = None) -> None:
            if self.email_error:
                raise self.email_error
            self.sent.append(SentEmail(subject, body_text, body_html, from_addr, list(to_addrs),
                                       list(attachments or [])))

        mp.setattr(email_action, "send", send)

        token = self.home / ".family" / "calendar_token.json"
        mp.setattr(calendar_action, "TOKEN_PATH", str(token))
        mp.setattr(calendar_action, "_build_service", lambda: self.calendar)

    def authorize_google_calendar(self) -> None:
        token = Path(calendar_action.TOKEN_PATH)
        token.parent.mkdir(parents=True, exist_ok=True)
        token.write_text("{}")

    def _fake_subprocess_run(self, cmd: list[str], *_a: Any, input: str | None = None,
                             cwd: str | None = None, env: dict[str, str] | None = None,
                             stdin: Any = None, **_k: Any) -> subprocess.CompletedProcess:
        prog = Path(cmd[0]).name
        self.commands.append(list(cmd))
        if prog == "osascript" and input and "display dialog" in input:  # the secret dialog
            return self._show_dialog(cmd, input)
        if prog == "open":
            self.opened.append(cmd[-1])
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if prog == "osascript":  # iMessage via Messages.app
            if self.imessage_error is not None:
                return subprocess.CompletedProcess(cmd, 1, "", self.imessage_error)
            script = cmd[cmd.index("-e") + 1]
            to = script.split('to buddy "', 1)[1].split('"', 1)[0]
            self.imessages.append((to, script.split('send "', 1)[1].rsplit('" to buddy', 1)[0]))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if prog == "security":  # Keychain lookup
            secret = self.keychain.get(cmd[cmd.index("-a") + 1])
            if secret is None:
                return subprocess.CompletedProcess(cmd, 44, "", "item not found")
            return subprocess.CompletedProcess(cmd, 0, secret + "\n", "")
        if prog not in ("claude", "codex"):
            raise AssertionError(f"unexpected subprocess in test: {cmd[:3]}")
        contents = sorted(p.name for p in Path(cwd).iterdir()) if cwd else None
        self.model_calls.append(ModelCall(list(cmd), input, cwd, contents,
                                          dict(env) if env is not None else None, stdin))
        if self.model_error is not None:
            return subprocess.CompletedProcess(cmd, 1, "", self.model_error)
        reply = self.model_reply
        if isinstance(reply, list):
            assert reply, "more model calls than replies"
            reply = reply.pop(0)
        if isinstance(reply, FailedCall):
            return subprocess.CompletedProcess(cmd, 1, "", reply.stderr)
        if callable(reply):
            reply = reply(input)
        reply = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        if prog == "codex":
            Path(cmd[cmd.index("-o") + 1]).write_text(reply)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        envelope = {"type": "result", "subtype": "success", "is_error": False, "result": reply}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(envelope, ensure_ascii=False), "")

    def _show_dialog(self, cmd: list[str], script: str) -> subprocess.CompletedProcess:
        # Replies the way osascript does for the dialog's AppleScript, which returns "1" and the
        # typed text for Save and "2" for the second choice.
        self.dialog.shown.append(script)
        button = self.dialog.button
        if button == "cancel":
            return subprocess.CompletedProcess(cmd, 1, "", "execution error: User canceled. (-128)")
        if button == "fails":
            return subprocess.CompletedProcess(
                cmd, 1, "", "execution error: No user interaction allowed. (-1713)")
        out = "2\n" if button == "other" else f"1{self.dialog.typed}\n"
        return subprocess.CompletedProcess(cmd, 0, out, "")


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
        "archive": {"dir": "~/FamilyBrief"},
        "state_path": "~/.family/state.json",
    }


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    home.mkdir()
    h = Harness(home, monkeypatch)
    # tick=False: every datetime.now() in the run sees exactly NOW, so output is byte-stable.
    with time_machine.travel(NOW, tick=False):
        yield h


# ── Fixture builders

def msg(source: str, ext_id: str, when: str, body: str, *, sender: str | None = None,
        subject: str | None = None, chat: str | None = None, kid: str | None = None) -> Message:
    return Message(source=source, external_id=ext_id,
                   timestamp=datetime.fromisoformat(when).astimezone(ZoneInfo("UTC")),
                   sender=sender, subject=subject, body=body, chat_name=chat, kid_hint=kid)


def myclub_event(ext_id: str, title: str, start: str, end: str, kid: str = "Mia") -> CalendarEvent:
    return CalendarEvent(source="myclub", external_id=ext_id, title=title,
                         start=datetime.fromisoformat(start), end=datetime.fromisoformat(end),
                         location="Leppävaara field", description="MyClub", kid=kid)
