"""What the setup page's tests (`test_setup_*.py`) share: the page's server, started in-process on a
free port inside the harness (or through socket pairs where no port can be listened on), the HTTP
calls the page's own script makes, its idle clock, and the steps more than one file walks through.

Not a conftest.py: one in e2e/ would replace tests/conftest.py as the `conftest` module and break
every `from conftest import`. So a file with the page's tests imports `page`, `clock` and the
autouse `_listening` from here itself."""
from __future__ import annotations

import http.client
import itertools
import json
import queue
import re
import socket
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
import yaml

from family_brief import ops, setup_save, setup_server
from family_brief.config import Config
from family_brief.llm import codex as codex_backend
from family_brief.state import State
from family_brief.system import jobs
from family_brief.system.secrets_error import SecretsError
from test_setup_status import install_program


PAGE_DIR = Path(setup_server.__file__).parent / "page"


class Clock:
    """The server's idle clock, moved on by the test."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Response:
    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status, self.headers, self.body = status, headers, body

    def json(self) -> Any:
        return json.loads(self.body)


def _can_listen() -> bool:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return True
    except PermissionError:
        return False
    finally:
        s.close()


CAN_LISTEN = _can_listen()


class _PairedServer(setup_server.ThreadingHTTPServer):
    """Where no port can be listened on (a sandbox, for one), the server takes each connection
    through a socket pair instead, which the test's client rings in. It serves them the same way."""
    servers: dict[int, "_PairedServer"] = {}
    _ports = itertools.count(40000)

    def server_bind(self) -> None:
        assert self.server_address == ("127.0.0.1", 0)
        self.socket.close()
        self.socket, self._bell = socket.socketpair()
        self.server_address = ("127.0.0.1", next(self._ports))
        self._waiting: queue.SimpleQueue[socket.socket] = queue.SimpleQueue()
        _PairedServer.servers[self.server_address[1]] = self

    def server_activate(self) -> None:
        pass

    def get_request(self) -> tuple[socket.socket, tuple[str, int]]:
        self.socket.recv(1)
        return self._waiting.get(), ("127.0.0.1", 0)

    def connect(self) -> socket.socket:
        ours, theirs = socket.socketpair()
        self._waiting.put(theirs)
        self._bell.send(b".")  # fails once the server has closed
        return ours


@pytest.fixture(autouse=True)
def _listening(monkeypatch: pytest.MonkeyPatch) -> None:
    if not CAN_LISTEN:
        monkeypatch.setattr(setup_server, "_HTTPServer", _PairedServer)


class _Connection(http.client.HTTPConnection):
    # The harness fakes socket.create_connection for the Sources; this one talks to the real server.
    def connect(self) -> None:
        if not CAN_LISTEN:
            if self.port not in _PairedServer.servers:
                raise ConnectionRefusedError(self.port)
            self.sock = _PairedServer.servers[self.port].connect()
            # One rung in as the server stops is never answered, as a closed port refuses it.
            self.sock.settimeout(5)
            return
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        self.sock.connect((self.host, self.port))


def call(url: str, path: str = "", *, method: str = "GET", body: Any = None,
         headers: dict[str, str] | None = None) -> Response:
    """A request to `path` under the page's address `url`, as the page's own script makes it."""
    parts = urlparse(url)
    conn = _Connection(parts.hostname, parts.port, timeout=5)
    sent = {"Host": parts.netloc}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        sent |= {"Content-Type": "application/json", "Origin": f"{parts.scheme}://{parts.netloc}"}
    sent |= headers or {}
    try:
        conn.request(method, parts.path + path, data, headers=sent)
        r = conn.getresponse()
        return Response(r.status, {k.lower(): v for k, v in r.getheaders()}, r.read())
    finally:
        conn.close()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(setup_server, "clock", c)
    monkeypatch.setattr(setup_server, "POLL_SECONDS", 0.01)
    return c


def config_file(harness) -> Path:
    return harness.home / ".family" / "config.yaml"


def progress_file(harness) -> Path:
    return harness.home / ".family" / "setup-progress.json"


@pytest.fixture
def page(harness, clock):
    """The setup page's server, serving in the background until the test ends."""
    server = setup_server.SetupServer(config_file(harness))
    thread = threading.Thread(target=server.serve_until_idle, daemon=True)
    thread.start()
    yield server
    server.stop()
    thread.join(5)


def wait_for(condition, seconds: float = 5) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def refuse_secrets(harness, code: int) -> None:
    """The secrets store refuses to write or delete, with the system's `code`."""
    reason = "not-reachable" if code == -25308 else "failed"
    harness.secrets.refuses = SecretsError("the store refused", reason, code)


def refused(url: str) -> bool:
    try:
        call(url)
    except OSError:
        return True
    return False


@pytest.fixture
def ai(harness, monkeypatch):
    """The Mac's Claude Code and Codex: neither installed until the test installs one."""
    bin_dir = harness.home / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(codex_backend, "CODEX_BUNDLED", ())  # not this Mac's own apps

    def install(name: str, where: Path = bin_dir) -> None:
        where.mkdir(parents=True, exist_ok=True)
        (where / name).write_text("#!/bin/sh\n")
        (where / name).chmod(0o755)
    return install


def welcome(url: str, **answers: Any) -> Response:
    """Welcome's answers, with the AI the page offers first on this system unless given."""
    return call(url, "api/welcome", method="POST",
                body={"ai": setup_server._ais()[0], "partner": None, "feedback": False, **answers})


def check_ai(url: str, name: str) -> Response:
    return call(url, "api/ai", method="POST", body={"ai": name})


ROOT = Path(__file__).resolve().parents[3]


def chat_installed(url: str, name: str) -> str:
    """How installing the plugin or skills for the AI `name` ended, once it has."""
    out: dict[str, str] = {}

    def done() -> bool:
        out["result"] = call(url, "api/state").json()["chat"].get(name, "installing")
        return out["result"] != "installing"
    wait_for(done)
    return out["result"]


def source(url: str, name: str, action: str) -> Response:
    return call(url, "api/source", method="POST", body={"source": name, "action": action})


def statuses(url: str) -> dict[str, str]:
    return call(url, "api/state").json()["progress"]["sources"]


def save_progress(harness, progress: dict[str, Any]) -> None:
    progress_file(harness).parent.mkdir(parents=True, exist_ok=True)
    progress_file(harness).write_text(json.dumps(progress))


APP_PASSWORD = "abcdefghijklmnop"


def assert_never_leaked(harness, responses: list[Response], caplog, capsys, secret: str) -> None:
    printed = "".join(capsys.readouterr())
    for value in {secret, secret.replace(" ", "")}:
        for r in responses:
            assert value.encode() not in r.body
        assert value not in printed
        assert value not in caplog.text
        assert not any(value in arg for cmd in harness.commands for arg in cmd)
        for f in (harness.home / ".family").glob("*"):
            assert value not in f.read_text(), f.name


def towns(url: str, query: str) -> Response:
    return call(url, "api/towns", method="POST", body={"query": query})


def inside(markup: str, element_id: str) -> str:
    """The markup inside the element with `element_id`, up to its own closing tag."""
    start = re.search(rf'<(\w+)[^>]*\bid="{element_id}"[^>]*>', markup)
    assert start, element_id
    depth = 1
    for tag in re.finditer(rf"<(/?){start.group(1)}\b[^>]*>", markup[start.end():]):
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            return markup[start.end():start.end() + tag.start()]
    raise AssertionError(f"{element_id} isn't closed")


def connect_done(harness, **config: Any) -> None:
    """Every Source done or skipped, with the config as Connect left it."""
    harness.config.update(city="Espoo", **config)
    for kid in harness.config["kids"]:
        kid.pop("myclub_ical_url", None)
    config_file(harness).parent.mkdir(parents=True, exist_ok=True)
    config_file(harness).write_text(yaml.safe_dump(harness.config, allow_unicode=True))
    save_progress(harness, {"phase": "connect", "source": None, "sources": {
        "wilma": "done", "gmail": "done", "ai": "done", "whatsapp": "done", "myclub": "skipped"}})


def more_progress(harness, **progress: Any) -> None:
    """Changes only what's given in setup's progress, as setup save does."""
    assert setup_save.save(config_file(harness), {"progress": progress})["result"] == "saved"


def at_the_check_step(harness, gmail_senders: list[dict[str, Any]] | None = None,
                      whatsapp_chats: list[dict[str, Any]] | None = None) -> None:
    connect_done(harness)
    more_progress(harness, **{"phase": "check", "gmail_senders": gmail_senders or [
        {"domain": "edu.espoo.fi", "count": 300, "example": "Opettaja", "likely": True},
        {"domain": "gmail.com", "count": 40, "example": "Friend", "likely": False, "public": True},
        {"domain": "shop.example.com", "count": 10, "example": "Shop", "likely": False}],
        "whatsapp_chats": whatsapp_chats if whatsapp_chats is not None else [
            {"name": "3B parents", "last": "2026-09-25", "archived": False,
             "hint": {"kids": ["Mia Virtanen"], "matched": ["3B"]}},
            {"name": "Kilo School families 🏫", "last": "2026-09-24", "archived": True,
             "hint": {"kids": ["Mia Virtanen", "Leo"], "matched": ["Kilo School"]}},
            {"name": "Neighbours ", "last": "2026-09-23", "archived": False}]})


def check_page(url: str) -> dict[str, Any]:
    return call(url, "api/state").json()["check"]


class Health:
    """Doctor's checks, as the server runs them: each (status, key, item, detail), with why it
    isn't OK where doctor says, or an ops.Check with the subject it's about, held until the test
    lets them go, so the page can be seen while they run."""

    def __init__(self) -> None:
        self.results = [(ops.OK, "config", "Config file", "~/.family/config.yaml, 2 kids"),
                        (ops.OK, "gmail", "Gmail", "parent@example.com signed in, 3 allowlisted emails"),
                        (ops.OK, "claude", "Claude", "call succeeded (auth: keychain-oauth)")]
        self.runs: list[tuple[str | None, bool]] = []
        self.go = threading.Event()
        self.go.set()

    def __call__(self, config: str | None, skip_llm: bool = False, whatsapp_only: bool = False,
                 show: bool = False, schedule: bool = True) -> list[ops.Check]:
        self.runs.append((config, schedule))
        assert self.go.wait(5)
        return [c if isinstance(c, ops.Check) else ops.Check(*c) for c in self.results]


@pytest.fixture
def health(monkeypatch) -> Health:
    h = Health()
    monkeypatch.setattr(ops, "health_checks", h)
    return h


def confirm(url: str, **answers: Any) -> Response:
    body = {"kids": [{"name": "Mia Virtanen", "everyday_name": "Mia"},
                     {"name": "Leo", "everyday_name": "Leo"}],
            "whatsapp": ["3B parents"], "senders": ["edu.espoo.fi", "espoo.fi"],
            "recipients": [{"address": "parent@example.com", "language": "zh"},
                           {"address": "partner@example.com", "language": "zh"}],
            "evening": "21:00", **answers}
    return call(url, "api/check", method="POST", body=body)


def health_result(url: str, seconds: float = 5) -> dict[str, Any]:
    out: dict[str, Any] = {}
    wait_for(lambda: out.update(
        call(url, "api/check/health", method="POST", body={}).json()) or out["result"] != "checking",
        seconds)
    return out


class Schedule:
    """The jobs and the wake as Finish meets them, in harness.jobs and harness.wake: what each
    install loaded, jobs that can't be loaded, and the administrator dialog held until the test
    lets it go."""

    def __init__(self, harness) -> None:
        self.harness = harness
        self.loads: list[str] = []
        self.load_fails = False
        self.go = threading.Event()
        self.go.set()
        install, wake_up = harness.jobs.install, harness.wake.set

        def installed(wanted: list[jobs.Job]) -> None:
            if self.load_fails:
                raise RuntimeError("the jobs couldn't be loaded")
            install(wanted)
            self.loads += [job.name for job in wanted]

        def held(hour: int, minute: int, replace: bool) -> str:
            result = wake_up(hour, minute, replace)
            assert self.go.wait(5)
            return result
        harness.jobs.install = installed
        harness.wake.set = held

    @property
    def dialogs(self) -> list[str]:
        return self.harness.wake.asked


@pytest.fixture
def schedule(harness, health) -> Schedule:
    return Schedule(harness)


def at_finish(harness, *, delivered: bool = True) -> None:
    """The first Brief sent to the setup parent, with the program installed."""
    connect_done(harness)
    more_progress(harness, phase="finish")
    install_program(harness)
    if delivered:
        state = State(Config.load(config_file(harness)).resolved_state_path())
        state.mark_delivered_now(["parent@example.com"])
        state.save()


def finish(url: str, replace_wake: bool = False) -> Response:
    return call(url, "api/finish", method="POST", body={"replace_wake": replace_wake})


def finished(url: str, seconds: float = 5) -> dict[str, Any]:
    out: dict[str, Any] = {}

    def ended() -> bool:
        out.clear()
        out.update(call(url, "api/finish/check", method="POST", body={}).json())
        return out["result"] not in ("installing", "checking")
    wait_for(ended, seconds)
    return out
