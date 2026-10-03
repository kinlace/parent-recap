"""The setup page's server, `family-brief setup page` (ADR 0006), driven with HTTP calls as the page
makes them.

The server runs in-process on a free port, inside the harness, so the config, the progress record,
`open` and the Mac's languages are the harness's. Assertions are on what the page and the family
see: the responses, the config and progress written, and what was opened."""
from __future__ import annotations

import base64
import http.client
import imaplib
import itertools
import json
import logging
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import keyring.errors
import pytest
import yaml

from family_brief import (__main__ as cli, ops, setup_ai, setup_save, setup_server, setup_steps,
                          setup_wilma, summarize)
from family_brief.collectors import whatsapp
from family_brief.config import Config
from family_brief.utils import keychain
from test_setup_whatsapp import (CHATS as WHATSAPP_CHATS, PYTHON as WHATSAPP_PYTHON, Mac,
                                 assert_read_only_through_bg, fake_mac)

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


def refused(url: str) -> bool:
    try:
        call(url)
    except OSError:
        return True
    return False


# ── the command


def test_the_command_opens_the_page_and_stops_after_30_idle_minutes(harness, clock, capsys,
                                                                    monkeypatch):
    monkeypatch.setattr(sys, "argv", ["family-brief", "-c", str(config_file(harness)), "setup", "page"])
    codes: list[int] = []
    thread = threading.Thread(target=lambda: codes.append(cli.main()), daemon=True)
    thread.start()
    wait_for(lambda: harness.opened)

    url = harness.opened[0]
    parts = urlparse(url)
    assert parts.scheme == "http" and parts.hostname == "127.0.0.1" and parts.port
    page = call(url)
    assert page.status == 200 and "text/html" in page.headers["content-type"]

    clock.now += 29 * 60
    assert call(url, "api/state").status == 200  # a request starts the 30 minutes again
    clock.now += 29 * 60
    time.sleep(0.1)
    assert thread.is_alive()
    clock.now += 2 * 60
    thread.join(5)

    assert not thread.is_alive() and codes == [0]
    assert refused(url)
    assert json.loads(capsys.readouterr().out) == {"result": "opened", "url": url}


def test_each_run_has_its_own_code_and_port(harness, clock):
    first, second = (setup_server.SetupServer(config_file(harness)) for _ in range(2))
    try:
        assert first.url != second.url
        codes = [urlparse(s.url).path.strip("/") for s in (first, second)]
        assert codes[0] != codes[1] and all(len(c) >= 32 for c in codes)
    finally:
        first.stop()
        second.stop()


def test_the_page_starts_without_looking_up_the_host_name(harness, monkeypatch):
    # http.server's own bind looks up the host name, which took over 5 seconds on CI's Mac.
    def look_up(*_):
        raise AssertionError("looked up the host name")
    monkeypatch.setattr(socket, "getfqdn", look_up)
    setup_server.SetupServer(config_file(harness)).stop()


# ── the safeguards


def test_requests_without_the_code_are_refused(harness, page):
    parts = urlparse(page.url)
    root = f"http://{parts.netloc}/"
    other_code = f"http://{parts.netloc}/{'x' * 43}/"

    for url, path in [(root, ""), (root, "page.js"), (root, "api/state"),
                      (other_code, ""), (other_code, "api/state")]:
        assert call(url, path).status == 403, url + path
    assert call(other_code, "api/language", method="POST", body={"language": "fi"}).status == 403
    assert not config_file(harness).exists()


def test_requests_for_another_host_or_from_another_origin_are_refused(harness, page):
    port = urlparse(page.url).port
    for host in ["evil.example", f"localhost:{port}", f"127.0.0.1:{port + 1}", "127.0.0.1"]:
        assert call(page.url, headers={"Host": host}).status == 403, host
        assert call(page.url, "api/state", headers={"Host": host}).status == 403, host
    for origin in ["https://evil.example", f"http://localhost:{port}", "null"]:
        assert call(page.url, "api/language", method="POST", body={"language": "fi"},
                    headers={"Origin": origin}).status == 403, origin
    assert not config_file(harness).exists()


def test_answers_must_come_from_the_page_itself(harness, page):
    parts = urlparse(page.url)
    for headers in [{"Content-Type": "text/plain"}, {"Origin": ""}]:
        r = call(page.url, "api/language", method="POST", body={"language": "fi"}, headers=headers)
        assert r.status == 403, headers
    assert not config_file(harness).exists()
    assert call(page.url, "api/language", method="POST", body={"language": "fi"},
                headers={"Origin": f"http://{parts.netloc}"}).status == 200


def test_it_listens_only_on_this_mac(page):
    assert page.address == ("127.0.0.1", urlparse(page.url).port)


def test_it_serves_only_the_page_s_own_files(page):
    for path in ["../setup_server.py", "%2e%2e/setup_server.py", "nothing.js", "page/index.html",
                 "api/nothing"]:
        assert call(page.url, path).status == 404, path
    for path in ["", "page.js", "page.css", "text.json"]:
        assert call(page.url, path).status == 200, path


def test_the_page_loads_nothing_from_the_internet(page):
    for path in ["", "page.js", "page.css", "text.json"]:
        r = call(page.url, path)
        assert not re.search(rb"(?:https?:)?//[A-Za-z0-9]", r.body), path
        csp = r.headers["content-security-policy"]
        assert "default-src 'none'" in csp
        assert "http" not in csp and "*" not in csp
        assert r.headers["referrer-policy"] == "no-referrer"  # the code stays on this Mac
    for f in PAGE_DIR.iterdir():
        assert not re.search(r"(?:https?:)?//[A-Za-z0-9]", f.read_text()), f.name


# ── the language


def test_it_offers_suomi_english_and_chinese_in_that_order(harness, page):
    state = call(page.url, "api/state").json()

    assert [(lang["code"], lang["name"]) for lang in state["languages"]] == \
        [("fi", "Suomi"), ("en", "English"), ("zh", "中文")]


@pytest.mark.parametrize("mac, preselected", [
    (["fi-FI", "en-FI"], "fi"),
    (["zh-Hans-FI", "en-FI"], "zh"),
    (["zh-Hant-TW"], "zh"),
    (["en-GB", "fi-FI"], "en"),
    (["sv-FI", "fi-FI"], "fi"),
    (["sv-FI", "de-DE"], "en"),
    ([], "en"),
])
def test_the_mac_s_language_is_preselected(harness, page, mac, preselected):
    harness.mac_languages = mac

    state = call(page.url, "api/state").json()

    assert state["preselected"] == preselected
    assert state["language"] is None  # not chosen yet


def test_the_language_picked_is_saved_as_the_setup_parent_s(harness, page):
    r = call(page.url, "api/language", method="POST", body={"language": "zh"})

    assert r.status == 200 and r.json()["result"] == "saved"
    cfg = Config.load(config_file(harness))
    assert cfg.summary_language == "zh"
    assert call(page.url, "api/state").json()["language"] == "zh"


def test_switching_the_language_later_keeps_the_rest_of_the_config(harness, page):
    config_file(harness).parent.mkdir(parents=True, exist_ok=True)
    config_file(harness).write_text(yaml.safe_dump(harness.config, allow_unicode=True))
    before = yaml.safe_load(config_file(harness).read_text())

    assert call(page.url, "api/language", method="POST", body={"language": "fi"}).status == 200

    after = yaml.safe_load(config_file(harness).read_text())
    assert after.pop("summary_language") == "fi"
    before.pop("summary_language")
    assert after == before


def test_a_language_the_page_does_not_offer_is_refused(harness, page):
    for body in [{"language": "sv"}, {"language": None}, {}, ["fi"], {"language": "fi", "x": 1}]:
        r = call(page.url, "api/language", method="POST", body=body)
        assert r.status == 400, body
        assert r.json()["result"] == "invalid-answers"
    own = f"http://{urlparse(page.url).netloc}"
    r = call(page.url, "api/language", method="POST",
             headers={"Content-Type": "application/json", "Origin": own})  # no body
    assert r.status == 400
    assert not config_file(harness).exists()


def test_every_page_text_exists_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    assert list(text) == ["fi", "en", "zh"]
    keys = set(text["en"])
    for language, table in text.items():
        assert set(table) == keys, language
        assert all(isinstance(v, str) and v.strip() for v in table.values()), language
    used = set()
    for f in ("index.html", "page.js"):
        used |= set(re.findall(r'data-text="([\w.-]+)"', (PAGE_DIR / f).read_text()))
        used |= set(re.findall(r'\bt\("([\w.-]+)"\)', (PAGE_DIR / f).read_text()))
    assert used and used <= keys, used - keys
    for phase in ("welcome", "connect", "working", "check", "first-brief", "finish"):
        assert f"phase.{phase}" in keys


# ── Welcome


@pytest.fixture
def ai(harness, monkeypatch):
    """The Mac's Claude Code and Codex: neither installed until the test installs one."""
    bin_dir = harness.home / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(summarize, "CODEX_BUNDLED", ())  # not this Mac's own apps

    def install(name: str, where: Path = bin_dir) -> None:
        where.mkdir(parents=True, exist_ok=True)
        (where / name).write_text("#!/bin/sh\n")
        (where / name).chmod(0o755)
    return install


def welcome(url: str, **answers: Any) -> Response:
    return call(url, "api/welcome", method="POST",
                body={"ai": "claude", "partner": None, "feedback": True, **answers})


def check_ai(url: str, name: str) -> Response:
    return call(url, "api/ai", method="POST", body={"ai": name})


def test_each_welcome_choice_has_a_default(harness, page):
    call(page.url, "api/language", method="POST", body={"language": "zh"})

    assert call(page.url, "api/state").json()["welcome"] == {
        "ai": "claude",
        "partner": {"add": True, "address": "", "language": "zh"},
        "feedback": True,
    }


def test_the_welcome_answers_are_saved(harness, page):
    call(page.url, "api/language", method="POST", body={"language": "fi"})

    r = welcome(page.url, ai="codex", partner={"address": "partner@example.com", "language": "zh"},
                feedback=False)

    assert r.status == 200 and r.json()["result"] == "saved"
    cfg = Config.load(config_file(harness))
    assert cfg.llm.backend == "codex"
    assert cfg.feedback.enabled is False
    progress = call(page.url, "api/state").json()["progress"]
    assert progress["phase"] == "connect" and progress["source"] == "wilma"
    assert call(page.url, "api/state").json()["welcome"] == {
        "ai": "codex",
        "partner": {"add": True, "address": "partner@example.com", "language": "zh"},
        "feedback": False,
    }


def test_the_partner_comes_after_the_parent_once_the_parent_s_gmail_is_known(harness, page):
    config_file(harness).parent.mkdir(parents=True, exist_ok=True)
    config_file(harness).write_text(yaml.safe_dump(harness.config, allow_unicode=True))

    welcome(page.url, partner={"address": "partner@example.com", "language": "fi"})

    to = Config.load(config_file(harness)).email.to
    assert [(r.address, r.language) for r in to] == \
        [("parent@example.com", None), ("partner@example.com", "fi")]


def test_only_me_leaves_the_parent_as_the_only_recipient(harness, page):
    config_file(harness).parent.mkdir(parents=True, exist_ok=True)
    config_file(harness).write_text(yaml.safe_dump(harness.config, allow_unicode=True))

    assert welcome(page.url, partner=None).status == 200

    assert [r.address for r in Config.load(config_file(harness)).email.to] == ["parent@example.com"]
    assert call(page.url, "api/state").json()["welcome"]["partner"]["add"] is False


def test_before_the_parent_s_gmail_is_known_no_recipient_is_written(harness, page):
    welcome(page.url, partner={"address": "partner@example.com", "language": "fi"})

    assert Config.load(config_file(harness)).email.to == []  # the partner isn't the first one


def test_the_partner_s_email_and_language_are_asked_only_when_a_partner_is_added(harness, page):
    for partner in [{"address": "", "language": "fi"}, {"address": "not an address", "language": "fi"},
                    {"address": "partner@example.com"}, {"address": "partner@example.com",
                                                         "language": "sv"}]:
        r = welcome(page.url, partner=partner)
        assert r.status == 400 and r.json()["result"] == "invalid-answers", partner
    for body in [{"ai": "gemini", "partner": None, "feedback": True},
                 {"ai": "claude", "partner": None, "feedback": "yes"},
                 {"ai": "claude", "feedback": True}]:
        r = call(page.url, "api/welcome", method="POST", body=body)
        assert r.status == 400, body
    assert not config_file(harness).exists()

    assert welcome(page.url, partner=None).status == 200  # only me: nothing more to ask


def test_a_missing_claude_code_gets_its_native_installer_and_is_checked_again(harness, page, ai):
    r = check_ai(page.url, "claude")

    assert r.status == 200
    assert r.json() == {"result": "not-installed", "ai": "claude",
                        "install": "curl -fsSL https://claude.ai/install.sh | bash"}

    ai("claude", harness.home / ".local" / "bin")  # the native installer's folder, not on PATH

    assert check_ai(page.url, "claude").json() == {"result": "ready", "ai": "claude"}
    assert ["auth", "status"] == harness.commands[-1][1:3]


def test_a_signed_out_claude_code_is_caught_and_checked_again(harness, page, ai):
    ai("claude")
    harness.signed_in["claude"] = False

    assert check_ai(page.url, "claude").json() == {"result": "signed-out", "ai": "claude"}

    harness.signed_in["claude"] = True
    assert check_ai(page.url, "claude").json() == {"result": "ready", "ai": "claude"}
    assert not harness.model_calls  # checked with its own status, not a call to the model


def test_a_missing_or_signed_out_codex_is_caught_and_checked_again(harness, page, ai):
    assert check_ai(page.url, "codex").json() == {"result": "not-installed", "ai": "codex"}

    ai("codex")
    harness.signed_in["codex"] = False
    assert check_ai(page.url, "codex").json() == {"result": "signed-out", "ai": "codex"}
    assert ["login", "status"] == harness.commands[-1][1:3]

    harness.signed_in["codex"] = True
    assert check_ai(page.url, "codex").json() == {"result": "ready", "ai": "codex"}


def test_an_ai_check_that_cannot_run_says_so(harness, page, ai, monkeypatch):
    ai("claude")
    run = setup_server.subprocess.run

    def fails(cmd, *a, **k):
        if cmd[1:3] == ["auth", "status"]:
            raise setup_server.subprocess.TimeoutExpired(cmd, 20)
        return run(cmd, *a, **k)
    monkeypatch.setattr(setup_server.subprocess, "run", fails)

    assert check_ai(page.url, "claude").json() == {"result": "check-failed", "ai": "claude"}


def test_only_claude_or_chatgpt_is_checked(page):
    for body in [{"ai": "gemini"}, {}, {"ai": "claude", "x": 1}]:
        assert call(page.url, "api/ai", method="POST", body=body).status == 400, body


def test_every_ai_result_says_what_to_do_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        for name in setup_server.AIS:
            for result in setup_server.AI_RESULTS:
                assert table.get(f"ai.{name}.{result}", "").strip(), (language, name, result)


def test_welcome_says_which_sources_come_next_and_to_have_the_wilma_login_ready():
    text = json.loads((PAGE_DIR / "text.json").read_text())
    used = (PAGE_DIR / "index.html").read_text()

    for language, table in text.items():
        assert "Wilma" in table["welcome.wilma"], language
        for source in ("Wilma", "Gmail", "WhatsApp", "MyClub"):
            assert source in table["welcome.sources"], (language, source)
    assert 'data-text="welcome.wilma"' in used and 'data-text="welcome.sources"' in used


# ── resuming


def test_running_it_again_resumes_at_the_saved_phase(harness, clock):
    first = setup_server.SetupServer(config_file(harness))
    thread = threading.Thread(target=first.serve_until_idle, daemon=True)
    thread.start()
    try:
        assert call(first.url, "api/state").json()["progress"]["phase"] == "welcome"
        call(first.url, "api/language", method="POST", body={"language": "fi"})
    finally:
        first.stop()
        thread.join(5)
    progress_file(harness).write_text(json.dumps({"phase": "connect", "source": "gmail",
                                                  "sources": {"wilma": "done"}}))

    second = setup_server.SetupServer(config_file(harness))
    thread = threading.Thread(target=second.serve_until_idle, daemon=True)
    thread.start()
    try:
        state = call(second.url, "api/state").json()
    finally:
        second.stop()
        thread.join(5)

    assert state["language"] == "fi"
    assert state["progress"]["phase"] == "connect"
    assert state["progress"]["source"] == "gmail"
    assert state["progress"]["sources"]["wilma"] == "done"


# ── Connect: the Source list


def source(url: str, name: str, action: str) -> Response:
    return call(url, "api/source", method="POST", body={"source": name, "action": action})


def statuses(url: str) -> dict[str, str]:
    return call(url, "api/state").json()["progress"]["sources"]


def save_progress(harness, progress: dict[str, Any]) -> None:
    progress_file(harness).parent.mkdir(parents=True, exist_ok=True)
    progress_file(harness).write_text(json.dumps(progress))


def test_the_source_list_shows_each_entry_with_its_status(harness, page):
    save_progress(harness, {"phase": "connect", "source": "gmail", "sources": {"wilma": "done"}})

    state = call(page.url, "api/state").json()

    assert [(s["name"], s["skippable"]) for s in state["connect"]["sources"]] == [
        ("wilma", True), ("gmail", False), ("ai", False), ("whatsapp", True), ("myclub", True)]
    assert state["progress"]["source"] == "gmail"
    assert state["progress"]["sources"] == {"wilma": "done", "gmail": "to-do", "ai": "to-do",
                                            "whatsapp": "to-do", "myclub": "to-do"}


def test_a_skipped_source_moves_setup_on_and_can_be_come_back_to(harness, page):
    save_progress(harness, {"phase": "connect", "source": "whatsapp",
                            "sources": {"wilma": "done", "gmail": "done", "ai": "done"}})

    r = source(page.url, "whatsapp", "skip")

    assert r.status == 200 and r.json()["result"] == "saved"
    assert r.json()["progress"]["source"] == "myclub"
    assert statuses(page.url)["whatsapp"] == "skipped"

    r = source(page.url, "whatsapp", "open")

    assert r.json()["progress"]["source"] == "whatsapp"
    assert statuses(page.url)["whatsapp"] == "to-do"


def test_skipping_the_last_source_leaves_none_to_do(harness, page):
    save_progress(harness, {"phase": "connect", "source": "myclub",
                            "sources": {"wilma": "done", "gmail": "done", "ai": "done",
                                        "whatsapp": "skipped"}})

    assert source(page.url, "myclub", "skip").json()["progress"]["source"] is None


def test_skipping_wilma_turns_it_off_for_a_household_that_does_not_use_it(harness, page):
    r = source(page.url, "wilma", "skip")

    assert r.json()["progress"]["source"] == "gmail"
    assert statuses(page.url)["wilma"] == "skipped"
    assert Config.load(config_file(harness)).wilma.enabled is False


def test_gmail_and_the_ai_sign_in_cannot_be_skipped(harness, page):
    for name in ("gmail", "ai"):
        r = source(page.url, name, "skip")
        assert r.status == 400 and r.json()["result"] == "invalid-answers", name
    for body in [{"source": "telegram", "action": "open"}, {"source": "gmail", "action": "done"},
                 {"source": "gmail"}, {"source": "gmail", "action": "open", "x": 1}, ["gmail"]]:
        assert call(page.url, "api/source", method="POST", body=body).status == 400, body
    assert set(statuses(page.url).values()) == {"to-do"}


def test_any_source_can_be_opened_from_the_list(harness, page):
    r = source(page.url, "gmail", "open")

    assert r.json()["progress"]["source"] == "gmail"
    assert statuses(page.url)["gmail"] == "to-do"


def test_a_source_already_done_stays_done_when_opened_again(harness, page):
    save_progress(harness, {"phase": "connect", "source": "ai", "sources": {"gmail": "done"}})

    assert source(page.url, "gmail", "open").json()["progress"]["sources"]["gmail"] == "done"


# ── Connect: Gmail

APP_PASSWORD = "abcdefghijklmnop"
TYPED = "abcd efgh ijkl mnop"  # how Google shows it, and how it's copied


class GmailServer:
    """Gmail's IMAP server for the test sign-in: accepts only APP_PASSWORD, or can't be reached."""

    def __init__(self) -> None:
        self.logins: list[tuple[str, str]] = []
        self.reachable = True

    def __call__(self, *_a: Any, **_k: Any) -> "GmailServer":
        if not self.reachable:
            raise OSError("nodename nor servname provided")
        return self

    def __enter__(self) -> "GmailServer":
        return self

    def __exit__(self, *_a: Any) -> None:
        pass

    def login(self, user: str, password: str) -> None:
        self.logins.append((user, password))
        if password != APP_PASSWORD:
            raise imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Invalid credentials (Failure)")

    def logout(self) -> None:
        pass


@pytest.fixture
def gmail(monkeypatch) -> GmailServer:
    server = GmailServer()
    monkeypatch.setattr(imaplib, "IMAP4_SSL", server)
    return server


def connect_gmail(url: str, address: str = "Parent@Example.com", password: str = TYPED) -> Response:
    return call(url, "api/gmail", method="POST", body={"address": address, "password": password})


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


def test_the_button_opens_google_s_app_passwords_page(harness, page):
    r = call(page.url, "api/open", method="POST", body={"site": "app-passwords"})

    assert r.status == 200 and r.json()["result"] == "opened"
    assert harness.opened == ["https://myaccount.google.com/apppasswords"]

    call(page.url, "api/open", method="POST", body={"site": "two-step"})
    assert harness.opened[-1] == "https://myaccount.google.com/signinoptions/two-step-verification"
    for body in [{"site": "https://evil.example"}, {}, {"site": "two-step", "x": 1}]:
        assert call(page.url, "api/open", method="POST", body=body).status == 400, body
    assert len(harness.opened) == 2


def test_a_valid_app_password_is_tested_stored_and_gmail_turns_done(harness, page, gmail, caplog,
                                                                    capsys):
    caplog.set_level(logging.DEBUG)
    welcome(page.url, partner={"address": "partner@example.com", "language": "fi"})
    source(page.url, "wilma", "skip")

    r = connect_gmail(page.url)

    assert r.status == 200
    out = r.json()
    assert out["result"] == "saved" and out["address"] == "parent@example.com"
    assert gmail.logins == [("parent@example.com", APP_PASSWORD)]
    assert harness.keychain == {"gmail-imap-parent@example.com": APP_PASSWORD}
    assert out["progress"]["sources"]["gmail"] == "done"
    assert out["progress"]["source"] == "ai"
    cfg = Config.load(config_file(harness))
    assert cfg.gmail.username == "parent@example.com"
    # Now the parent's address is known, the Recipients are saved with the parent first.
    assert [(t.address, t.language) for t in cfg.email.to] == \
        [("parent@example.com", None), ("partner@example.com", "fi")]
    assert call(page.url, "api/state").json()["gmail"] == {"address": "parent@example.com"}
    assert_never_leaked(harness, [r], caplog, capsys, TYPED)


def test_without_a_welcome_partner_the_parent_becomes_the_recipient(harness, page, gmail):
    assert connect_gmail(page.url).json()["result"] == "saved"

    assert [t.address for t in Config.load(config_file(harness)).email.to] == ["parent@example.com"]


@pytest.mark.parametrize("typed, result", [
    ("MyGooglePassword1", "not-an-app-password"),
    ("zyxwvutsrqponmlk", "rejected"),
])
def test_a_password_gmail_would_not_take_is_explained_and_not_stored(harness, page, gmail, caplog,
                                                                     capsys, typed, result):
    caplog.set_level(logging.DEBUG)

    r = connect_gmail(page.url, password=typed)

    assert r.status == 200 and r.json()["result"] == result
    assert harness.keychain == {}
    assert statuses(page.url)["gmail"] == "to-do"
    if result == "not-an-app-password":  # most likely the Google password: never sent to Google
        assert gmail.logins == []
    assert_never_leaked(harness, [r], caplog, capsys, typed)


def test_no_connection_to_gmail_says_so(harness, page, gmail):
    gmail.reachable = False

    assert connect_gmail(page.url).json()["result"] == "no-connection"
    assert harness.keychain == {}


def test_a_keychain_that_refuses_says_so(harness, page, gmail, monkeypatch):
    def refuses(*_a: Any) -> None:
        raise keyring.errors.KeyringError("denied")
    monkeypatch.setattr(keychain, "set_", refuses)

    assert connect_gmail(page.url).json()["result"] == "keychain-failed"
    assert statuses(page.url)["gmail"] == "to-do"


def test_an_address_that_is_not_one_is_explained(harness, page, gmail):
    for address in ["", "parent", "parent@", "a b@example.com"]:
        assert connect_gmail(page.url, address=address).json()["result"] == "no-address", address
    assert gmail.logins == []
    for body in [{"address": "parent@example.com"}, {"password": TYPED},
                 {"address": "parent@example.com", "password": 1},
                 {"address": "parent@example.com", "password": TYPED, "x": 1}]:
        r = call(page.url, "api/gmail", method="POST", body=body)
        assert r.status == 400 and r.json()["result"] == "invalid-answers", body
        assert TYPED.encode() not in r.body


def test_every_gmail_result_is_explained_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        for result in setup_server.GMAIL_RESULTS:
            assert table.get(f"gmail.{result}", "").strip(), (language, result)
        assert "Parent Recap" in table["gmail.explain"], language
        for name in setup_save.SOURCES:
            assert table.get(f"source.{name}", "").strip(), (language, name)
        for status in ("to-do", "done", "skipped"):
            assert table.get(f"status.{status}", "").strip(), (language, status)


def test_the_gmail_fields_are_the_page_s_own(page):
    html = call(page.url).body.decode()

    password = re.search(r'<input[^>]*id="gmail-password"[^>]*>', html).group(0)
    assert 'type="password"' in password
    address = re.search(r'<input[^>]*id="gmail-address"[^>]*>', html).group(0)
    assert 'type="email"' in address


# ── Connect: Wilma (ADR 0008)

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

# The pinned wilma CLI as setup uses it, reading its saved profile the way its dist/config.js
# (loadConfig, revealSecret) and dist/index.js (getProfileForCommandNonInteractive, then
# getStudentsForCommand, which saves the students back) do, and signing in to a fake Wilma
# that knows one account. What it says when Wilma turns a login down is the CLI's own.
FAKE_WILMA_CLI = """#!{python}
import base64, json, os, pathlib, sys
ctl = json.loads((pathlib.Path(os.path.realpath(__file__)).parent / "wilma.json").read_text())
if os.environ.get("WILMAI_CONFIG_PATH"):
    path = pathlib.Path(os.environ["WILMAI_CONFIG_PATH"])
else:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.environ["HOME"], ".config")
    path = pathlib.Path(base) / "wilmai" / "config.json"
if sys.argv[1:] != ["kids", "list", "--json"]:
    sys.exit(2)
try:
    config = json.loads(path.read_text())
except Exception:
    config = {{"profiles": []}}
if not config.get("profiles"):
    config = {{"profiles": []}}
if not config.get("lastProfileId"):
    print("No saved profile found. Run the interactive CLI first.", file=sys.stderr)
    sys.exit(0)
stored = next((p for p in config["profiles"] if p["id"] == config["lastProfileId"]), None)
if stored is None:
    print("Saved profile not found. Run the interactive CLI first.", file=sys.stderr)
    sys.exit(0)
try:
    decoded = base64.b64decode(stored["passwordObfuscated"]).decode()
except Exception:
    decoded = ""
if not decoded.startswith("wilmai::"):
    print("Stored password could not be decoded. Re-login interactively.", file=sys.stderr)
    sys.exit(0)
if ctl["fails"]:
    print("CLI error: " + ctl["fails"], file=sys.stderr)
    sys.exit(1)
if ctl["accounts"].get(stored["tenantUrl"] + "|" + stored["username"]) != decoded[8:]:
    print("CLI error: Wilma login failed", file=sys.stderr)
    sys.exit(1)
stored["students"] = [{{"studentNumber": s["studentNumber"], "name": s["name"]}}
                      for s in ctl["students"]]
path.write_text(json.dumps(config, indent=2) + "\\n")
print(json.dumps(ctl["students"], indent=2))
"""


class WilmaCLI:
    """The Mac's npm and the wilma CLI it installs globally, laid out as npm lays it out, with
    Wilma's tenant list inside it. Not installed until `npm install -g` or `install()`."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.prefix = home / "homebrew"
        self.bin_dir = self.prefix / "bin"
        self.bin_dir.mkdir(parents=True)
        self.accounts = {f"{ESPOO}|mia.parent": WILMA_PASSWORD}  # tenant|username → password
        self.students: list[dict[str, Any]] = WILMA_STUDENTS
        self.fails: str | None = None    # any other failure, in the CLI's words
        self.npm = True                  # whether Node's npm is on this Mac
        self.npm_works = True
        self.installs: list[list[str]] = []
        self.ships_tenants = True
        self.terminal: list[str] = []    # each script opened in Terminal
        self.signs_in_terminal = True    # the family signs in in the window

    @property
    def package(self) -> Path:
        return self.prefix / "lib" / "node_modules" / "@wilm-ai" / "wilma-cli"

    @property
    def profile_path(self) -> Path:
        return self.home / ".config" / "wilmai" / "config.json"

    def profile(self) -> dict[str, Any]:
        return json.loads(self.profile_path.read_text())

    def install(self) -> None:
        dist = self.package / "dist"
        dist.mkdir(parents=True, exist_ok=True)
        (self.package / "package.json").write_text(json.dumps(
            {"name": "@wilm-ai/wilma-cli", "version": "1.6.2"}))
        (dist / "index.js").write_text(FAKE_WILMA_CLI.format(python=sys.executable))
        (dist / "index.js").chmod(0o755)
        if self.ships_tenants:
            client = self.package / "node_modules" / "@wilm-ai" / "wilma-client"
            client.mkdir(parents=True, exist_ok=True)
            (client / "tenant_list.json").write_text(json.dumps(TENANTS, ensure_ascii=False))
        (self.bin_dir / "wilma").unlink(missing_ok=True)
        (self.bin_dir / "wilma").symlink_to(dist / "index.js")
        (dist / "wilma.json").write_text(json.dumps(
            {"accounts": self.accounts, "students": self.students, "fails": self.fails}))

    def signed_in_before(self, tenant: str, username: str = "old.parent",
                         password: str = "old") -> str:
        profile_id = f"{tenant}|{username}"
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        self.profile_path.write_text(json.dumps({"profiles": [{
            "id": profile_id, "tenantUrl": tenant, "tenantName": "Wilma", "username": username,
            "passwordObfuscated": setup_wilma.obfuscate(password), "students": [],
        }], "lastProfileId": profile_id}, indent=2) + "\n")
        return self.profile_path.read_text()


REAL_RUN = subprocess.run  # before the harness fakes it


@pytest.fixture
def wilma_cli(harness, monkeypatch) -> WilmaCLI:
    w = WilmaCLI(harness.home)
    for var in ("WILMAI_CONFIG_PATH", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("PATH", f"{w.bin_dir}:/usr/bin:/bin")
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    others = subprocess.run  # the harness's fakes

    def run(cmd: list[str], *a: Any, **k: Any) -> Any:
        name = Path(cmd[0]).name
        if name == "wilma":
            harness.commands.append(list(cmd))
            return REAL_RUN(cmd, *a, **k)
        if name == "npm":
            harness.commands.append(list(cmd))
            if not w.npm:
                raise FileNotFoundError(cmd[0])
            w.installs.append(cmd[1:])
            if not w.npm_works:
                return subprocess.CompletedProcess(cmd, 1, "", "npm error code E404")
            w.install()
            return subprocess.CompletedProcess(cmd, 0, "added 40 packages", "")
        if cmd[:3] == ["open", "-a", "Terminal"]:
            harness.commands.append(list(cmd))
            w.terminal.append(Path(cmd[3]).read_text())
            if w.signs_in_terminal:  # the family signs in in the window: the CLI saves its profile
                w.signed_in_before(ESPOO, "mia.parent", WILMA_PASSWORD)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return others(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", run)
    (w.bin_dir / "npm").write_text("#!/bin/sh\n")
    (w.bin_dir / "npm").chmod(0o755)
    monkeypatch.setattr(setup_wilma, "NPM_PLACES", ())  # only the PATH's
    return w


def towns(url: str, query: str) -> Response:
    return call(url, "api/towns", method="POST", body={"query": query})


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


def test_setup_installs_the_pinned_wilma_cli_when_it_is_missing(harness, page, wilma_cli):
    assert towns(page.url, "Espoo").json()["result"] == "not-installed"

    r = wilma_ready(page.url)

    assert r.status == 200 and r.json()["result"] == "installed"
    assert wilma_cli.installs == [["install", "-g", "@wilm-ai/wilma-cli@1.6.2"]]
    assert setup_steps.WILMA_INSTALL == "npm install -g @wilm-ai/wilma-cli@1.6.2"
    assert towns(page.url, "Espoo").json()["result"] == "found"

    assert wilma_ready(page.url).json()["result"] == "installed"
    assert len(wilma_cli.installs) == 1  # already there: not installed again


def test_without_node_it_says_how_to_install_it(harness, page, wilma_cli):
    (wilma_cli.bin_dir / "npm").unlink()

    assert wilma_ready(page.url).json() == {"result": "no-npm", "install": "brew install node"}

    (wilma_cli.bin_dir / "npm").write_text("#!/bin/sh\n")
    (wilma_cli.bin_dir / "npm").chmod(0o755)
    wilma_cli.npm_works = False
    assert wilma_ready(page.url).json()["result"] == "install-failed"


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
                           "students", "lastStudentNumber", "lastStudentName", "lastUsedAt"}
    assert (stored["tenantUrl"], stored["tenantName"], stored["username"]) == \
        (ESPOO, TENANTS["wilmat"][2]["name"], "mia.parent")
    assert stored["passwordObfuscated"] == \
        base64.b64encode(f"wilmai::{WILMA_PASSWORD}".encode()).decode()
    assert [s["name"] for s in stored["students"]] == ["Mia Virtanen", "Leo Virtanen"]
    assert wilma_cli.profile_path.stat().st_mode & 0o777 == 0o600
    assert wilma_cli.profile_path.parent.stat().st_mode & 0o777 == 0o700
    # The Household's answers: the Kids as Wilma spells them, its city, and Wilma on.
    cfg = Config.load(config_file(harness))
    assert [k.name for k in cfg.kids] == ["Mia Virtanen", "Leo Virtanen"]
    assert cfg.city == "Espoo" and cfg.wilma.enabled is True
    assert_never_leaked(harness, [r], caplog, capsys, WILMA_PASSWORD)


def test_signing_in_keeps_the_cli_s_other_profiles(harness, page, wilma_cli):
    wilma_cli.install()
    wilma_cli.signed_in_before("https://helsinki.inschool.fi")

    assert sign_in_wilma(page.url).json()["result"] == "signed-in"

    profiles = wilma_cli.profile()["profiles"]
    assert [p["id"] for p in profiles] == ["https://helsinki.inschool.fi|old.parent",
                                           f"{ESPOO}|mia.parent"]


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


@pytest.mark.parametrize("fails", ["Wilma HTTP 503 at /login", "MFA verification required"])
def test_any_other_failure_offers_the_terminal_window(harness, page, wilma_cli, fails):
    wilma_cli.fails = fails
    wilma_cli.install()

    r = sign_in_wilma(page.url)

    assert r.status == 200 and r.json() == {"result": "sign-in-failed"}
    assert not wilma_cli.profile_path.exists()
    assert statuses(page.url)["wilma"] == "to-do"


def test_an_account_without_kids_is_reported_and_not_kept(harness, page, wilma_cli):
    wilma_cli.students = []
    wilma_cli.install()

    assert sign_in_wilma(page.url).json() == {"result": "no-kids"}
    assert not wilma_cli.profile_path.exists()


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


def test_signing_in_without_the_cli_says_so(harness, page, wilma_cli):
    assert sign_in_wilma(page.url).json() == {"result": "not-installed"}
    assert not wilma_cli.profile_path.exists()


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
    [script] = wilma_cli.terminal
    assert "--language fi" in script
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


def test_a_terminal_window_without_a_sign_in_says_so(harness, page, wilma_cli, monkeypatch):
    monkeypatch.setattr(setup_server, "WILMA_WINDOW_SECONDS", 4)
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


def test_the_button_opens_the_passwords_app(harness, page):
    r = call(page.url, "api/open", method="POST", body={"site": "passwords"})

    assert r.status == 200 and r.json()["result"] == "opened"
    assert ["open", "-a", "Passwords"] in harness.commands


def test_the_wilma_fields_are_standard_login_fields(page):
    html = call(page.url).body.decode()

    form = re.search(r'<form id="wilma-form".*?</form>', html, re.S).group(0)
    username = re.search(r'<input[^>]*id="wilma-username"[^>]*>', form).group(0)
    assert 'autocomplete="username"' in username
    password = re.search(r'<input[^>]*id="wilma-password"[^>]*>', form).group(0)
    assert 'type="password"' in password and 'autocomplete="current-password"' in password
    assert 'id="wilma-passwords"' in form


def test_every_wilma_result_is_explained_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        for result in (*setup_server.WILMA_RESULTS, *setup_server.TOWN_RESULTS):
            assert table.get(f"wilma.{result}", "").strip(), (language, result)
        for result in setup_server.WILMA_READY_RESULTS:
            assert table.get(f"wilma.ready.{result}", "").strip(), (language, result)
        for result in setup_server.WILMA_WINDOW_RESULTS:
            assert table.get(f"wilma.window.{result}", "").strip(), (language, result)


def test_the_chat_setup_installs_the_same_pinned_cli():
    skill = (Path(__file__).resolve().parents[2] / "skills" / "setup" / "SKILL.md").read_text()

    assert setup_steps.WILMA_INSTALL in skill


# ── Connect: the AI sign-in for the evening Brief

CLAUDE_TOKEN = "sk-ant-oat01-Abc_123-xyzXYZ0987654321abcdefghijklmnopqrstuvwxyz-AA"

# `claude setup-token` as setup runs it: it opens Anthropic's Authorize page, and once the family
# has clicked Authorize it draws the token on Ink's screen, with its escape sequences, and ends.
# Without a terminal it stops, as Ink's does.
FAKE_CLAUDE = """#!{python}
import json, os, pathlib, sys, time
here = pathlib.Path(os.path.realpath(__file__)).parent
ctl = json.loads((here / "claude.json").read_text())
if sys.argv[1:] != ["setup-token"]:
    sys.exit(2)
(here / "setup-token.ran").write_text("")
out = sys.stdout
if ctl["terminal"] and not os.isatty(0):
    out.write("Error: Raw mode is not supported on the current process.stdin\\n")
    sys.exit(1)
out.write("\\x1b[?25l\\x1b[2K\\x1b[1GOpening browser to sign in\\u2026\\r\\n")
out.write("Browser didn't open? Use the url below to sign in:\\r\\n\\r\\n"
          "https://claude.ai/oauth/authorize?code=true&client_id=x&state=y\\r\\n")
out.flush()
if ctl["hangs"]:
    time.sleep(60)
if ctl["token"] is None:
    out.write("\\x1b[31mOAuth error: Request failed with status code 400\\x1b[39m\\r\\n")
    sys.exit(1)
out.write("\\x1b[2K\\x1b[32m\\u2713\\x1b[39m Long-lived authentication token created "
          "successfully!\\r\\n\\r\\nYour OAuth token (valid for 1 year):\\r\\n\\r\\n")
token = ctl["token"]
out.write("\\x1b[1m" + token[:20])
out.flush()
time.sleep(0.2)  # drawn in two parts: the first alone isn't the token
if ctl["stays"]:  # nothing but escape sequences after it, and it keeps running
    out.write(token[20:] + "\\x1b[22m\\x1b[?25h")
    out.flush()
    time.sleep(60)
out.write(token[20:] + "\\x1b[22m\\r\\n\\r\\nStore this token securely. You won't be able to "
          "see it again.\\r\\n\\x1b[?25h")
out.flush()
"""


def _can_open_a_pty() -> bool:
    try:
        for fd in os.openpty():
            os.close(fd)
        return True
    except OSError:
        return False


CAN_PTY = _can_open_a_pty()


class FakeClaude:
    """The Mac's `claude`: `setup-token` runs for real, in the pseudo-terminal setup gives it;
    its test call is the harness's. Not installed until `install()`."""

    def __init__(self, bin_dir: Path) -> None:
        self.bin_dir = bin_dir
        self.token: str | None = CLAUDE_TOKEN  # what Authorize gives; None if it fails
        self.hangs = False                      # the family never clicks Authorize
        self.stays = False                      # it keeps running once the token is shown
        self.started: list[list[str]] = []      # each process started in the background

    @property
    def ran(self) -> bool:
        return (self.bin_dir / "setup-token.ran").exists()

    def install(self) -> None:
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        (self.bin_dir / "claude").write_text(FAKE_CLAUDE.format(python=sys.executable))
        (self.bin_dir / "claude").chmod(0o755)
        (self.bin_dir / "claude.json").write_text(json.dumps(
            {"token": self.token, "hangs": self.hangs, "stays": self.stays, "terminal": CAN_PTY}))


def record_background_processes(harness, monkeypatch, started: list[list[str]]) -> None:
    popen = subprocess.Popen

    def recorded(cmd: list[str], *a: Any, **k: Any) -> Any:
        harness.commands.append(list(cmd))  # each command line, with the harness's
        started.append(list(cmd))
        return popen(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "Popen", recorded)


@pytest.fixture
def claude(harness, ai, monkeypatch) -> FakeClaude:
    fake = FakeClaude(harness.home / "bin")
    if not CAN_PTY:  # a sandbox: the same reading and writing, through a socket pair
        def pair() -> tuple[int, int]:
            ours, theirs = socket.socketpair()
            return ours.detach(), theirs.detach()
        monkeypatch.setattr(setup_ai, "open_terminal", pair)
    record_background_processes(harness, monkeypatch, fake.started)
    return fake


def claude_result(url: str) -> dict[str, Any]:
    out: dict[str, Any] = {}

    def done() -> bool:
        out.clear()
        out.update(call(url, "api/claude/check", method="POST", body={}).json())
        return out["result"] != "waiting"
    wait_for(done, 10)
    return out


def at_the_ai_step(harness, url: str, ai_name: str = "claude") -> None:
    welcome(url, ai=ai_name)
    save_progress(harness, {**json.loads(progress_file(harness).read_text()), "source": "ai",
                            "sources": {"wilma": "done", "gmail": "done"}})


def test_claude_s_token_is_read_tested_and_stored_with_nothing_copied(harness, page, claude,
                                                                       caplog, capsys):
    caplog.set_level(logging.DEBUG)
    claude.install()
    at_the_ai_step(harness, page.url)

    r = call(page.url, "api/claude", method="POST", body={})

    assert r.status == 200 and r.json() == {"result": "waiting"}
    out = claude_result(page.url)
    assert out["result"] == "saved"
    assert claude.ran and claude.started == [[str(claude.bin_dir / "claude"), "setup-token"]]
    assert harness.keychain == {"claude-oauth-token": CLAUDE_TOKEN}
    [test_call] = harness.model_calls  # with the token in its environment
    assert test_call.env["CLAUDE_CODE_OAUTH_TOKEN"] == CLAUDE_TOKEN
    assert out["progress"]["sources"]["ai"] == "done" and out["progress"]["source"] == "whatsapp"
    assert not any(c[:3] == ["open", "-a", "Terminal"] for c in harness.commands)
    assert_never_leaked(harness, [r, call(page.url, "api/claude/check", method="POST", body={}),
                                  call(page.url, "api/state")], caplog, capsys, CLAUDE_TOKEN)


def test_a_token_at_the_end_of_the_screen_is_read_once_the_screen_is_still(harness, page, claude):
    claude.stays = True
    claude.install()

    call(page.url, "api/claude", method="POST", body={})

    assert claude_result(page.url)["result"] == "saved"
    assert harness.keychain == {"claude-oauth-token": CLAUDE_TOKEN}


def test_a_claude_sign_in_already_running_is_not_started_twice(harness, page, claude):
    claude.hangs = True
    claude.install()

    for _ in range(2):
        assert call(page.url, "api/claude", method="POST", body={}).json() == {"result": "waiting"}

    wait_for(lambda: claude.ran)
    assert len(claude.started) == 1


@pytest.mark.parametrize("how, result", [("no token", "sign-in-failed"), ("hangs", "timeout")])
def test_a_claude_sign_in_without_a_token_offers_the_terminal_window(harness, page, claude,
                                                                    monkeypatch, how, result):
    monkeypatch.setattr(setup_server, "CLAUDE_SIGN_IN_SECONDS", 1)
    claude.token = None
    claude.hangs = how == "hangs"
    claude.install()

    call(page.url, "api/claude", method="POST", body={})

    assert claude_result(page.url) == {"result": result}
    assert harness.keychain == {} and statuses(page.url)["ai"] == "to-do"


def test_a_token_claude_does_not_accept_is_not_kept(harness, page, claude):
    harness.model_error = "Invalid bearer token"
    claude.install()

    call(page.url, "api/claude", method="POST", body={})

    assert claude_result(page.url) == {"result": "test-call-failed"}
    assert harness.keychain == {} and statuses(page.url)["ai"] == "to-do"


def test_the_fallback_opens_the_terminal_window_and_takes_the_pasted_token(harness, page, claude,
                                                                          caplog, capsys):
    caplog.set_level(logging.DEBUG)
    claude.install()
    at_the_ai_step(harness, page.url)

    r = call(page.url, "api/claude/terminal", method="POST", body={})

    assert r.status == 200 and r.json() == {"result": "opened"}
    [opened] = [c for c in harness.commands if c[:3] == ["open", "-a", "Terminal"]]
    script = Path(opened[3]).read_text()
    assert "setup-token" in script and "the field on the setup page" in script

    # Copied from the window, across the lines Terminal wrapped it on.
    pasted = f" {CLAUDE_TOKEN[:30]}\n{CLAUDE_TOKEN[30:]} "
    r = call(page.url, "api/claude/token", method="POST", body={"token": pasted})

    assert r.status == 200 and r.json()["result"] == "saved"
    assert harness.keychain == {"claude-oauth-token": CLAUDE_TOKEN}
    assert r.json()["progress"]["sources"]["ai"] == "done"
    assert not Path(opened[3]).exists()  # the window's script is gone once it has done its work
    assert_never_leaked(harness, [r], caplog, capsys, CLAUDE_TOKEN)


@pytest.mark.parametrize("pasted, result", [("my-claude-password", "not-a-token"),
                                            (CLAUDE_TOKEN, "test-call-failed")])
def test_a_pasted_token_that_does_not_work_is_explained_and_not_kept(harness, page, claude, caplog,
                                                                     capsys, pasted, result):
    caplog.set_level(logging.DEBUG)
    harness.model_error = f"Invalid bearer token {pasted}"
    claude.install()

    r = call(page.url, "api/claude/token", method="POST", body={"token": pasted})

    assert r.status == 200 and r.json() == {"result": result}
    assert harness.keychain == {}
    if result == "not-a-token":  # most likely a password: never sent to Claude
        assert not harness.model_calls
    assert_never_leaked(harness, [r], caplog, capsys, pasted)


def test_a_keychain_that_refuses_the_claude_token_says_so(harness, page, claude, monkeypatch):
    def refuses(*_a: Any) -> None:
        raise keyring.errors.KeyringError("denied")
    monkeypatch.setattr(keychain, "set_", refuses)
    claude.install()

    r = call(page.url, "api/claude/token", method="POST", body={"token": CLAUDE_TOKEN})

    assert r.json() == {"result": "keychain-failed"}
    assert statuses(page.url)["ai"] == "to-do"


def test_without_claude_code_its_sign_in_says_how_to_install_it(harness, page, claude):
    for path, body in [("api/claude", {}), ("api/claude/terminal", {}),
                       ("api/claude/token", {"token": CLAUDE_TOKEN})]:
        assert call(page.url, path, method="POST", body=body).json() == {
            "result": "not-installed", "install": "curl -fsSL https://claude.ai/install.sh | bash"}
    assert not harness.keychain


def test_the_claude_calls_take_only_what_they_need(harness, page, claude):
    claude.install()
    for path in ("api/claude", "api/claude/check", "api/claude/terminal"):
        r = call(page.url, path, method="POST", body={"token": CLAUDE_TOKEN})
        assert r.status == 400 and CLAUDE_TOKEN.encode() not in r.body, path
    for body in [{}, {"token": 1}, {"token": CLAUDE_TOKEN, "x": 1}]:
        r = call(page.url, "api/claude/token", method="POST", body=body)
        assert r.status == 400 and CLAUDE_TOKEN.encode() not in r.body, body
    assert call(page.url, "api/claude/check", method="POST", body={}).json() == \
        {"result": "no-sign-in"}
    assert not claude.started and not harness.keychain


FAKE_CODEX = """#!{python}
import os, pathlib, sys
if sys.argv[1:] == ["login"]:
    (pathlib.Path(os.path.realpath(__file__)).parent / "codex-login.ran").write_text("")
    sys.exit(int(os.environ.get("FAKE_CODEX_LOGIN_EXIT", "0")))
sys.exit(2)
"""


@pytest.fixture
def codex(harness, ai, monkeypatch) -> Path:
    """The Mac's `codex`: `login` runs for real, and its status is the harness's."""
    bin_dir = harness.home / "bin"
    (bin_dir / "codex").write_text(FAKE_CODEX.format(python=sys.executable))
    (bin_dir / "codex").chmod(0o755)
    record_background_processes(harness, monkeypatch, [])
    return bin_dir


def codex_check(url: str) -> dict[str, Any]:
    return call(url, "api/codex", method="POST", body={}).json()


def test_a_signed_in_codex_ticks_the_entry_itself(harness, page, codex):
    at_the_ai_step(harness, page.url, "codex")

    out = codex_check(page.url)

    assert out["result"] == "signed-in"
    assert out["progress"]["sources"]["ai"] == "done" and out["progress"]["source"] == "whatsapp"
    assert not (codex / "codex-login.ran").exists()  # no sign-in needed


def test_a_signed_out_codex_signs_in_and_the_entry_ticks_itself(harness, page, codex):
    at_the_ai_step(harness, page.url, "codex")
    harness.signed_in["codex"] = False
    assert codex_check(page.url) == {"result": "signed-out"}

    r = call(page.url, "api/codex/login", method="POST", body={})

    assert r.status == 200 and r.json() == {"result": "waiting"}
    assert [str(codex / "codex"), "login"] in harness.commands
    wait_for(lambda: (codex / "codex-login.ran").exists())
    harness.signed_in["codex"] = True  # the family signs in with ChatGPT in the browser
    out = codex_check(page.url)
    assert out["result"] == "signed-in" and out["progress"]["sources"]["ai"] == "done"
    assert statuses(page.url)["ai"] == "done"


def test_a_codex_sign_in_that_ends_signed_out_says_so(harness, page, codex, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_LOGIN_EXIT", "1")
    harness.signed_in["codex"] = False

    assert call(page.url, "api/codex/login", method="POST", body={}).json() == {"result": "waiting"}

    wait_for(lambda: page._codex.poll() is not None)
    assert codex_check(page.url) == {"result": "login-failed"}
    assert codex_check(page.url) == {"result": "signed-out"}  # said once; it can be started again
    assert statuses(page.url)["ai"] == "to-do"


def test_without_codex_its_sign_in_says_so(harness, page, ai):
    assert codex_check(page.url) == {"result": "not-installed"}
    assert call(page.url, "api/codex/login", method="POST", body={}).json() == \
        {"result": "not-installed"}
    for path in ("api/codex", "api/codex/login"):
        assert call(page.url, path, method="POST", body={"x": 1}).status == 400


def test_every_ai_sign_in_result_is_explained_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        for result in setup_server.CLAUDE_RESULTS:
            assert table.get(f"claude.{result}", "").strip(), (language, result)
        for result in setup_server.CLAUDE_WINDOW_RESULTS:
            assert table.get(f"claude.window.{result}", "").strip(), (language, result)
        for result in setup_server.CODEX_RESULTS:
            assert table.get(f"codex.{result}", "").strip(), (language, result)


def test_the_claude_token_field_is_the_page_s_own(page):
    html = call(page.url).body.decode()

    field = re.search(r'<input[^>]*id="claude-token"[^>]*>', html).group(0)
    assert 'type="password"' in field and 'autocomplete="off"' in field


# ── Connect: WhatsApp


@pytest.fixture
def mac(harness, monkeypatch) -> Mac:
    """The family's Mac as test_setup_whatsapp.py fakes it: launchctl runs each `bg` job
    in-process, against a WhatsApp database only those jobs can read, once the Mac has given
    the job's Python the permission. The harness stops the clock, so waiting moves it on."""
    m = fake_mac(harness, monkeypatch)
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    monkeypatch.setattr(time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    return m


def at_the_whatsapp_step(harness) -> None:
    config_file(harness).parent.mkdir(parents=True, exist_ok=True)
    config_file(harness).write_text(yaml.safe_dump(harness.config, allow_unicode=True))
    save_progress(harness, {"phase": "connect", "source": "whatsapp",
                            "sources": {"wilma": "done", "gmail": "done", "ai": "done"}})


def check_whatsapp(url: str) -> Response:
    return call(url, "api/whatsapp/check", method="POST", body={})


def test_a_whatsapp_the_job_can_read_ticks_the_entry_and_keeps_the_groups(harness, page, mac):
    harness.config["kids"][0]["name"] = "Mia Virtanen"
    at_the_whatsapp_step(harness)
    mac.install_whatsapp(WHATSAPP_CHATS)

    r = check_whatsapp(page.url)

    assert r.status == 200
    out = r.json()
    assert out["result"] == "readable"
    assert out["progress"]["sources"]["whatsapp"] == "done"
    assert out["progress"]["source"] == "myclub"
    found = [{"name": "3B parents", "last": "2026-09-25", "archived": False,
              "hint": {"kids": ["Mia Virtanen"], "matched": ["3B"]}},
             {"name": "Kilo School families 🏫", "last": "2026-09-24", "archived": True,
              "hint": {"kids": ["Mia Virtanen", "Leo"], "matched": ["Kilo School"]}},
             {"name": "Neighbours ", "last": "2026-09-23", "archived": False}]
    assert out["chats"] == found
    # Kept for the check page, which reads them from setup's progress, as the chat setup can.
    assert call(page.url, "api/state").json()["progress"]["whatsapp_chats"] == found
    assert setup_save.read(config_file(harness))["progress"]["whatsapp_chats"] == found
    assert harness.opened == []  # nothing for the family to do
    assert_read_only_through_bg(harness, mac)


def test_the_button_shows_the_python_and_opens_app_management(harness, page, mac):
    at_the_whatsapp_step(harness)

    r = call(page.url, "api/whatsapp/open", method="POST", body={})

    assert r.status == 200 and r.json() == {"result": "opened"}
    assert harness.opened == [WHATSAPP_PYTHON, ops.APP_MANAGEMENT_URL]
    assert ["open", "-R", WHATSAPP_PYTHON] in harness.commands


def test_without_the_permission_the_entry_ticks_itself_once_it_is_given(harness, page, mac):
    at_the_whatsapp_step(harness)
    mac.install_whatsapp(WHATSAPP_CHATS)
    mac.grants_after = 2  # the family turns the switch on while the page checks

    assert check_whatsapp(page.url).json() == {"result": "no-permission"}
    assert check_whatsapp(page.url).json() == {"result": "no-permission"}
    assert statuses(page.url)["whatsapp"] == "to-do"
    out = check_whatsapp(page.url).json()

    assert out["result"] == "readable" and len(out["chats"]) == 3
    assert statuses(page.url)["whatsapp"] == "done"
    assert mac.prompts == 1
    assert_read_only_through_bg(harness, mac)


def test_a_read_that_ends_after_the_parent_moved_on_keeps_their_choice(harness, page, mac,
                                                                      monkeypatch):
    at_the_whatsapp_step(harness)
    mac.install_whatsapp(WHATSAPP_CHATS)
    read = setup_steps.read_whatsapp_through_bg

    def slow(*a: Any) -> dict[str, Any]:  # the parent skips WhatsApp while it reads
        out = read(*a)
        save_progress(harness, {**json.loads(progress_file(harness).read_text()),
                                "source": "myclub", "sources": {"wilma": "done", "gmail": "done",
                                                                "ai": "done", "whatsapp": "skipped"}})
        return out
    monkeypatch.setattr(setup_steps, "read_whatsapp_through_bg", slow)

    assert check_whatsapp(page.url).json()["result"] == "readable"

    progress = call(page.url, "api/state").json()["progress"]
    assert progress["sources"]["whatsapp"] == "skipped" and progress["source"] == "myclub"

    monkeypatch.setattr(setup_steps, "read_whatsapp_through_bg", read)
    save_progress(harness, {**progress, "source": "gmail",  # the parent went back to Gmail
                            "sources": {**progress["sources"], "whatsapp": "to-do"}})
    assert check_whatsapp(page.url).json()["progress"]["source"] == "gmail"  # not moved on
    assert statuses(page.url)["whatsapp"] == "done"


def test_an_unanswered_allow_prompt_is_waiting(harness, page, mac):
    at_the_whatsapp_step(harness)
    mac.install_whatsapp(WHATSAPP_CHATS)
    mac.allow = None

    assert check_whatsapp(page.url).json() == {"result": "waiting"}
    assert not any(mac.reads)
    assert statuses(page.url)["whatsapp"] == "to-do"


def test_without_whatsapp_for_mac_it_says_so(harness, page, mac):
    at_the_whatsapp_step(harness)

    assert check_whatsapp(page.url).json() == {"result": "not-installed"}


def test_a_read_that_fails_says_so_without_the_error(harness, page, mac, monkeypatch):
    at_the_whatsapp_step(harness)
    mac.install_whatsapp(WHATSAPP_CHATS)
    monkeypatch.setattr(whatsapp, "check_access", lambda: "disk I/O error at /Users/x")

    assert check_whatsapp(page.url).json() == {"result": "unreadable"}

    mac.bootstrap_fails = True
    assert check_whatsapp(page.url).json() == {"result": "bg-failed"}
    assert statuses(page.url)["whatsapp"] == "to-do"


def test_system_settings_that_does_not_open_says_so(harness, page, mac, monkeypatch):
    at_the_whatsapp_step(harness)
    others = subprocess.run

    def run(cmd: list[str], *a: Any, **k: Any) -> subprocess.CompletedProcess:
        if Path(cmd[0]).name == "open":
            return subprocess.CompletedProcess(cmd, 1, "", "")
        return others(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", run)

    assert call(page.url, "api/whatsapp/open", method="POST", body={}).json() == \
        {"result": "not-opened"}


def test_the_whatsapp_calls_take_nothing(page):
    for path in ("api/whatsapp/check", "api/whatsapp/open"):
        assert call(page.url, path, method="POST", body={"x": 1}).status == 400, path


def test_the_whatsapp_step_shows_two_or_three_pictures_of_what_to_switch_on(page):
    html = call(page.url).body.decode()

    step = re.search(r'<div id="source-whatsapp".*?\n    </div>\n', html, re.S).group(0)
    figures = re.findall(r"<figure>.*?</figure>", step, re.S)
    assert 2 <= len(figures) <= 3
    for figure in figures:
        assert "<svg" in figure and re.search(r'<figcaption data-text="[\w.-]+">', figure)
    assert 'id="whatsapp-open"' in step
    assert "done" not in step.lower()  # it ticks itself: there's no Done to press


def test_every_whatsapp_result_is_explained_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        for result in setup_server.WHATSAPP_RESULTS:
            assert table.get(f"whatsapp.{result}", "").strip(), (language, result)
        for result in setup_server.WHATSAPP_OPEN_RESULTS:
            assert table.get(f"whatsapp.open.{result}", "").strip(), (language, result)
