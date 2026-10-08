"""The setup page's server, `parent-recap setup page` (ADR 0006), driven with HTTP calls as the page
makes them.

The server runs in-process on a free port, inside the harness, so the config, the progress record,
`open` and the Mac's languages are the harness's. Assertions are on what the page and the family
see: the responses, the config and progress written, and what was opened."""
from __future__ import annotations

import base64
import html
import http.client
import imaplib
import itertools
import json
import logging
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
import requests
import yaml

from family_brief import (__main__ as cli, feedback, install_record, ops, run_lock, setup_ai,
                          setup_save, setup_server, setup_status, setup_steps, setup_wilma,
                          summarize)
from family_brief.collectors import myclub, whatsapp
from family_brief.config import Config
from family_brief.state import State
import fake_node
from conftest import PILOT_FORM_FIELDS, PILOT_FORM_URL, msg, program
from test_nightly_run import FEEDBACK, FORM, feedback_links
from test_setup_status import install_program
from test_setup_steps import HeaderImap
from test_setup_myclub import LINK as MYCLUB_LINK, TOKEN as MYCLUB_TOKEN, MyClubServer
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
    opened, closed = capsys.readouterr().out.splitlines()
    assert json.loads(opened) == {"result": "opened", "url": url}
    # Terminal says the page has closed, so the family knows its window can be closed too.
    assert closed == setup_server.CLOSED_LINES["en"]


def test_terminal_says_the_page_has_closed_in_the_language_picked(harness, clock, capsys,
                                                                  monkeypatch):
    monkeypatch.setattr(sys, "argv", ["family-brief", "-c", str(config_file(harness)), "setup", "page"])
    thread = threading.Thread(target=cli.main, daemon=True)
    thread.start()
    wait_for(lambda: harness.opened)
    url = harness.opened[0]
    assert call(url, "api/language", method="POST", body={"language": "fi"}).status == 200

    clock.now += 31 * 60
    thread.join(5)

    assert capsys.readouterr().out.splitlines()[-1] == setup_server.CLOSED_LINES["fi"]
    assert set(setup_server.CLOSED_LINES) == set(setup_server.LANGUAGES)


@pytest.mark.parametrize("var", ["TMUX", "SSH_CONNECTION"])
def test_in_tmux_or_ssh_the_command_warns_the_keychain_cannot_be_reached(harness, clock, capsys,
                                                                       monkeypatch, var):
    monkeypatch.setenv(var, "/private/tmp/tmux-501/default,1234,0" if var == "TMUX"
                       else "10.0.0.2 52000 10.0.0.1 22")
    monkeypatch.setattr(sys, "argv", ["family-brief", "-c", str(config_file(harness)), "setup", "page"])
    thread = threading.Thread(target=cli.main, daemon=True)
    thread.start()
    wait_for(lambda: harness.opened)
    url = harness.opened[0]

    # Said once, on the page too, before the family types anything.
    assert call(url, "api/state").json()["keychain"] == {"reachable": False}
    clock.now += 31 * 60
    thread.join(5)

    out, err = capsys.readouterr()
    opened = json.loads(out.splitlines()[0])
    assert opened["result"] == "opened" and "outside tmux or SSH" in opened["warning"]
    assert (out + err).count("outside tmux or SSH") == 1


def test_outside_tmux_and_ssh_the_keychain_is_reachable(page):
    assert call(page.url, "api/state").json()["keychain"] == {"reachable": True}


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


def test_a_refused_request_s_body_is_read_before_its_answer(harness, page):
    # Answering and closing with the body still coming resets the connection, a broken pipe at
    # the sender's end. A body larger than the sockets' buffers makes that happen every time.
    big = {"language": "fi", "padding": "x" * (8 * 1024 * 1024)}
    other_code = f"http://{urlparse(page.url).netloc}/{'x' * 43}/"
    for url, headers in [(page.url, {"Origin": "https://evil.example"}),
                         (page.url, {"Host": "evil.example"}),
                         (page.url, {"Content-Type": "text/plain"}), (other_code, {})]:
        assert call(url, "api/language", method="POST", body=big, headers=headers).status == 403
    assert call(page.url, "api/language", method="POST", body=big).status == 400  # too long
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


def test_the_page_has_the_setup_proposal_look_in_the_system_font():
    css = (PAGE_DIR / "page.css").read_text()

    for token in ["--bg: #EDF0F3", "--panel: #FFFFFF", "--ink: #12161B", "--muted: #6A7380",
                  "--accent: #C8731A", "--accent-soft: #FBEAD5", "--good: #2C7A57",
                  "--chip-on: #12161B"]:
        assert token in css, token
    assert "@font-face" not in css and "@import" not in css and "url(" not in css
    assert "var(--sys)" in re.search(r"^body\s*\{[^}]*\}", css, re.M).group(0)
    # Six phases in a row, and two rows of three at phone width.
    assert re.search(r"\.phases\s*\{[^}]*grid-template-columns:\s*repeat\(6,", css)
    phone = re.search(r"@media \(max-width: \d+px\)\s*\{(.*?)\n\}", css, re.S).group(1)
    assert re.search(r"\.phases\s*\{[^}]*grid-template-columns:\s*repeat\(3,", phone)
    # The Source list's marks: to do, the one being worked on, done.
    for mark in ['"○"', '"◐"', '"✓"']:
        assert mark in css, mark


def test_only_radio_buttons_and_checkboxes_are_sized_as_one():
    css = (PAGE_DIR / "page.css").read_text()

    rules = re.findall(r"([^{}]+)\{([^}]*)\}", css)
    sized = [sel for sel, body in rules if re.search(r"(?<!-)width:\s*16px", body)]
    assert sized
    for selectors in sized:
        for selector in selectors.split(","):
            selector = selector.strip()
            assert re.search(r'input\[type="(radio|checkbox)"\]', selector), selector
    # So a field inside a fieldset, like the partner's email, keeps its own look and width.
    for selectors, _ in rules:
        for selector in selectors.split(","):
            assert not re.fullmatch(r"\s*fieldset input(:[\w-]+)?\s*", selector), selector


def test_a_long_wilma_entry_wraps_beside_its_radio_button():
    css = (PAGE_DIR / "page.css").read_text()

    towns = re.search(r"^\.towns label\s*\{([^}]*)\}", css, re.M).group(1)
    assert "flex-wrap: wrap" not in towns
    assert "align-items: flex-start" in towns
    name = re.search(r"^\.towns \.name\s*\{([^}]*)\}", css, re.M).group(1)
    assert "flex: 1" in name and "min-width: 0" in name
    script = (PAGE_DIR / "page.js").read_text()
    rows = script.split("function renderTowns()", 1)[1].split("\n}\n", 1)[0]
    assert 'name.className = "name"' in rows


# ── the language


def test_it_offers_suomi_english_and_chinese_in_that_order(harness, page):
    state = call(page.url, "api/state").json()

    assert [(lang["code"], lang["name"]) for lang in state["languages"]] == \
        [("fi", "Suomi"), ("en", "English"), ("zh", "中文")]


def test_each_language_choice_is_named_as_shown_not_by_its_code():
    script = (PAGE_DIR / "page.js").read_text()

    rows = script.split("function renderChoices()", 1)[1].split("\n}\n", 1)[0]
    assert 'input.setAttribute("aria-label", name)' in rows


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
                body={"ai": "claude", "partner": None, "feedback": False, **answers})


def check_ai(url: str, name: str) -> Response:
    return call(url, "api/ai", method="POST", body={"ai": name})


def test_each_welcome_choice_has_a_default(harness, page):
    harness.ship_pilot_form()
    call(page.url, "api/language", method="POST", body={"language": "zh"})

    assert call(page.url, "api/state").json()["welcome"] == {
        "ai": "claude",
        "partner": {"add": True, "address": "", "language": "zh"},
        "feedback": True,
    }


def test_the_welcome_answers_are_saved(harness, page):
    harness.ship_pilot_form()
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


def test_a_pilot_household_s_opt_in_writes_the_whole_feedback_section(harness, page):
    harness.ship_pilot_form()
    config_file(harness).parent.mkdir(parents=True, exist_ok=True)
    config_file(harness).write_text(yaml.safe_dump(harness.config, allow_unicode=True))

    assert welcome(page.url, feedback=True).status == 200

    cfg = Config.load(config_file(harness))
    assert cfg.feedback.active()
    assert cfg.feedback.prefill_base_url == PILOT_FORM_URL
    assert cfg.feedback.fields.model_dump() == PILOT_FORM_FIELDS
    assert cfg.feedback.household_label == "parent"  # the setup parent's, until changed on Check


def test_without_a_shipped_pilot_form_welcome_doesnt_ask_about_feedback(harness, page):
    assert call(page.url, "api/state").json()["welcome"]["feedback"] is None
    assert welcome(page.url, feedback=True).status == 400  # nothing to opt in to
    assert not config_file(harness).exists()
    assert welcome(page.url, feedback=False).status == 200

    assert "feedback" not in yaml.safe_load(config_file(harness).read_text())


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


def test_the_install_box_shows_only_with_an_install_line_in_it(harness, page, ai):
    html = (PAGE_DIR / "index.html").read_text()
    css = (PAGE_DIR / "page.css").read_text()

    box = re.search(r'<div id="ai-install"[^>]*>.*?</div>', html, re.S).group(0)
    assert " hidden" in box.split(">", 1)[0] and 'id="ai-copy"' in box and 'id="ai-install-line"' in box
    # `hidden` wins over the box's own `display: flex`, here and in every other step.
    assert re.search(r"^\[hidden\]\s*\{\s*display:\s*none\s*!important;\s*\}", css, re.M)

    ai("claude")
    assert "install" not in check_ai(page.url, "claude").json()  # ready: nothing to install


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


@pytest.mark.parametrize("name", ["claude", "codex"])
def test_an_ai_only_the_login_shell_finds_is_ready(harness, page, ai, login_shell, name):
    folder = harness.home / "tools" / "bin"  # on the family's shell's PATH, not the page's
    ai(name, folder)
    login_shell(folder)

    assert check_ai(page.url, name).json() == {"result": "ready", "ai": name}
    assert harness.commands[-1][0] == str(folder / name)


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


# ── Welcome: what lets the family ask for changes in the chat later (#98)

ROOT = Path(__file__).resolve().parents[2]


def chat_installed(url: str, name: str) -> str:
    """How installing the plugin or skills for the AI `name` ended, once it has."""
    out: dict[str, str] = {}

    def done() -> bool:
        out["result"] = call(url, "api/state").json()["chat"].get(name, "installing")
        return out["result"] != "installing"
    wait_for(done)
    return out["result"]


def test_picking_claude_installs_the_claude_code_plugin_from_the_stable_marketplace(harness, page,
                                                                                    ai):
    ai("claude")
    assert call(page.url, "api/state").json()["chat"] == {}

    assert welcome(page.url, ai="claude").status == 200

    assert chat_installed(page.url, "claude") == "installed"
    assert harness.plugin_calls == [
        ["plugin", "list", "--json"], ["plugin", "marketplace", "list", "--json"],
        ["plugin", "marketplace", "add", "kinlace/parent-recap#stable"],
        ["plugin", "install", "parent-recap@kinlace", "--scope", "user"],
    ]
    assert not (harness.home / ".agents").exists() and not harness.model_calls
    # So uninstall removes them.
    assert install_record.entries("claude-plugin") == ["parent-recap@kinlace"]
    assert install_record.entries("claude-marketplace") == ["kinlace"]


def test_a_claude_code_plugin_already_there_is_updated(harness, page, ai):
    ai("claude")
    harness.claude_marketplaces = [{"name": "kinlace", "source": "github",
                                    "repo": "kinlace/parent-recap"}]
    harness.claude_plugins = [{"id": "parent-recap@kinlace", "scope": "user"}]

    welcome(page.url, ai="claude")

    assert chat_installed(page.url, "claude") == "installed"
    assert harness.plugin_calls[2:] == [["plugin", "marketplace", "update", "kinlace"],
                                        ["plugin", "update", "parent-recap@kinlace"]]
    # Installed before setup: uninstall leaves them.
    assert install_record.entries("claude-plugin") == []
    assert install_record.entries("claude-marketplace") == []


def test_a_plugin_from_an_unzipped_release_or_its_old_name_is_switched_to_stable(harness, page, ai):
    ai("claude")
    harness.claude_marketplaces = [{"name": "family-brief"},
                                   {"name": "kinlace", "source": "directory",
                                    "path": "/Users/mum/ParentRecap/plugin"}]
    harness.claude_plugins = [{"id": "family-brief@family-brief"}, {"id": "parent-recap@kinlace"}]

    welcome(page.url, ai="claude")

    assert chat_installed(page.url, "claude") == "installed"
    assert harness.plugin_calls[2:] == [
        ["plugin", "uninstall", "family-brief@family-brief"],
        ["plugin", "marketplace", "remove", "family-brief"],
        ["plugin", "uninstall", "parent-recap@kinlace"],
        ["plugin", "marketplace", "remove", "kinlace"],
        ["plugin", "marketplace", "add", "kinlace/parent-recap#stable"],
        ["plugin", "install", "parent-recap@kinlace", "--scope", "user"],
    ]
    assert install_record.entries("claude-plugin") == []  # the family's own, from the release zip
    assert install_record.entries("claude-marketplace") == []


def test_a_plugin_install_that_fails_says_so(harness, page, ai):
    ai("claude")
    harness.claude_plugins = {"error": "not signed in"}  # type: ignore[assignment]

    welcome(page.url, ai="claude")

    assert chat_installed(page.url, "claude") == "install-failed"
    assert install_record.entries("claude-plugin") == []


def test_without_claude_code_the_plugin_is_not_installed(harness, page, ai):
    welcome(page.url, ai="claude")

    assert chat_installed(page.url, "claude") == "not-installed"
    assert harness.plugin_calls == []


def test_picking_chatgpt_installs_the_codex_skills_from_the_install_line_s_copy(harness, page):
    install_record.add("plugin", str(ROOT))  # what install.sh recorded, run from the line's copy

    welcome(page.url, ai="codex")

    assert chat_installed(page.url, "codex") == "installed"
    skills = harness.home / ".agents" / "skills"
    assert sorted(p.name for p in skills.iterdir()) == ["parent-recap-manage", "parent-recap-setup"]
    setup = (skills / "parent-recap-setup" / "SKILL.md").read_text()
    assert "\nname: parent-recap-setup\n" in setup and "$parent-recap-manage" in setup
    assert f"PLUGIN (the plugin root folder) is `{ROOT}`" in setup
    assert install_record.entries("codex-skill") == [str(skills / "parent-recap-setup"),
                                                     str(skills / "parent-recap-manage")]
    assert harness.plugin_calls == []


def test_without_the_install_line_s_copy_the_codex_skills_are_not_installed(harness, page):
    welcome(page.url, ai="codex")

    assert chat_installed(page.url, "codex") == "no-plugin-copy"
    assert not (harness.home / ".agents").exists()


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


def test_every_source_list_button_is_named_after_its_source_and_status_in_each_language():
    text = json.loads((PAGE_DIR / "text.json").read_text())
    script = (PAGE_DIR / "page.js").read_text()

    for language, table in text.items():
        name = table["sources.button"]
        assert "{source}" in name and "{status}" in name, language
        assert name.replace("{source}", "").replace("{status}", "").strip(), language
    # Rebuilt with the rest of the list whenever the language or a status changes.
    rows = script.split("function renderSources()", 1)[1].split("\n}\n", 1)[0]
    assert 'button.setAttribute("aria-label"' in rows and 't("sources.button")' in rows


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


def test_a_keychain_that_refuses_says_so(harness, page, gmail):
    harness.keychain_refuses = -128  # errSecUserCanceled

    assert connect_gmail(page.url).json()["result"] == "keychain-failed"
    assert statuses(page.url)["gmail"] == "to-do"


def test_a_keychain_out_of_reach_says_so_with_its_code(harness, page, gmail):
    harness.keychain_refuses = -25308  # errSecInteractionNotAllowed

    assert connect_gmail(page.url).json() == {"result": "keychain-not-reachable", "code": -25308}
    assert statuses(page.url)["gmail"] == "to-do"
    harness.keychain_refuses = -25293
    assert connect_gmail(page.url).json() == {"result": "keychain-failed", "code": -25293}


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
    """Parent Recap's own Node and the wilma CLI its npm installs into ~/ParentRecap/wilma,
    laid out as npm lays it out, with Wilma's tenant list inside it. The CLI isn't installed
    until npm or `install()` installs it."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.prefix = fake_node.wilma_folder(home)
        self.node = fake_node.pinned_node(home)
        self.accounts = {f"{ESPOO}|mia.parent": WILMA_PASSWORD}  # tenant|username → password
        self.students: list[dict[str, Any]] = WILMA_STUDENTS
        self.fails: str | None = None    # any other failure, in the CLI's words
        self.npm_works = True
        self.installs: list[list[str]] = []
        self.npm_env: dict[str, str] = {}  # the last npm's
        self.ships_tenants = True
        self.terminal: list[str] = []    # each script opened in Terminal
        self.signs_in_terminal = True    # the family signs in in the window

    @property
    def package(self) -> Path:
        return fake_node.wilma_package(self.home)

    @property
    def profile_path(self) -> Path:
        return self.home / ".config" / "wilmai" / "config.json"

    def profile(self) -> dict[str, Any]:
        return json.loads(self.profile_path.read_text())

    def install(self) -> None:
        fake_node.install_wilma(self.home, FAKE_WILMA_CLI.format(python=sys.executable))
        (self.package / "package.json").write_text(json.dumps(
            {"name": "@wilm-ai/wilma-cli", "version": "1.6.2"}))
        if self.ships_tenants:
            client = self.package / "node_modules" / "@wilm-ai" / "wilma-client"
            client.mkdir(parents=True, exist_ok=True)
            (client / "tenant_list.json").write_text(json.dumps(TENANTS, ensure_ascii=False))
        (self.package / "dist" / "wilma.json").write_text(json.dumps(
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
    monkeypatch.setenv("PATH", "/usr/bin:/bin")  # no Node of the Mac's own
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    others = subprocess.run  # the harness's fakes

    def run(cmd: list[str], *a: Any, **k: Any) -> Any:
        if fake_node.is_wilma(cmd):
            harness.commands.append(list(cmd))
            return REAL_RUN(cmd, *a, **k)
        if fake_node.is_npm(cmd):
            harness.commands.append(list(cmd))
            w.installs.append(cmd[2:])
            w.npm_env = k["env"]
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


def test_setup_installs_the_pinned_wilma_cli_with_its_own_node_when_it_is_missing(
        harness, page, wilma_cli):
    assert towns(page.url, "Espoo").json()["result"] == "not-installed"

    r = wilma_ready(page.url)

    assert r.status == 200 and r.json()["result"] == "installed"
    prefix = str(wilma_cli.prefix)
    assert wilma_cli.installs == [["install", "-g", "--prefix", prefix, "@wilm-ai/wilma-cli@1.6.2"]]
    [npm] = [c for c in harness.commands if fake_node.is_npm(c)]
    assert npm[0] == str(wilma_cli.node)
    # Its package scripts find that Node too, and its cache leaves nothing behind.
    assert wilma_cli.npm_env["PATH"].split(":")[0] == str(wilma_cli.node.parent)
    assert not Path(wilma_cli.npm_env["npm_config_cache"]).exists()
    assert towns(page.url, "Espoo").json()["result"] == "found"

    assert wilma_ready(page.url).json()["result"] == "installed"
    assert len(wilma_cli.installs) == 1  # already there: not installed again
    assert install_record.entries("wilma-cli") == [prefix]  # so uninstall removes it


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


def test_without_its_own_node_it_says_to_run_the_install_again(harness, page, wilma_cli):
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
    # So uninstall removes the profile, and nothing of the password goes in the record.
    assert install_record.entries("wilma-profile") == [f"{ESPOO}|mia.parent"]
    assert WILMA_PASSWORD not in install_record.path().read_text()


def test_signing_in_keeps_the_cli_s_other_profiles(harness, page, wilma_cli):
    wilma_cli.install()
    wilma_cli.signed_in_before("https://helsinki.inschool.fi")

    assert sign_in_wilma(page.url).json()["result"] == "signed-in"

    profiles = wilma_cli.profile()["profiles"]
    assert [p["id"] for p in profiles] == ["https://helsinki.inschool.fi|old.parent",
                                           f"{ESPOO}|mia.parent"]
    assert install_record.entries("wilma-profile") == [f"{ESPOO}|mia.parent"]


def test_a_profile_from_before_setup_is_not_recorded(harness, page, wilma_cli):
    wilma_cli.install()
    wilma_cli.signed_in_before(ESPOO, "mia.parent", "older-password")

    assert sign_in_wilma(page.url).json()["result"] == "signed-in"

    assert install_record.entries("wilma-profile") == []


def test_a_failed_sign_in_records_no_profile(harness, page, wilma_cli):
    wilma_cli.install()

    assert sign_in_wilma(page.url, password="wrong").json()["result"] == "wrong-password"

    assert install_record.entries("wilma-profile") == []


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

    # `setup wilma` installs it, as the page does, so it's in setup's record for uninstall.
    assert "wilma-cli" not in skill and "`setup wilma` installs" in skill


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


def test_a_keychain_that_refuses_the_claude_token_says_so(harness, page, claude):
    harness.keychain_refuses = -128  # errSecUserCanceled
    claude.install()

    r = call(page.url, "api/claude/token", method="POST", body={"token": CLAUDE_TOKEN})

    assert r.json() == {"result": "keychain-failed", "code": -128}
    assert statuses(page.url)["ai"] == "to-do"


def test_a_keychain_out_of_reach_of_the_claude_token_says_so_with_its_code(harness, page, claude):
    harness.keychain_refuses = -25308  # errSecInteractionNotAllowed
    claude.install()

    r = call(page.url, "api/claude/token", method="POST", body={"token": CLAUDE_TOKEN})

    assert r.json() == {"result": "keychain-not-reachable", "code": -25308}
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


# Where each AI's consumer plans let the family turn off training on their chats, named as the
# app names it (ADR 0013).
TRAINING_SETTINGS = {"claude": ("Settings > Privacy", "Help improve Claude"),
                     "codex": ("Settings > Data controls", "Improve the model for everyone")}


def test_the_ai_sign_in_says_where_to_turn_off_training_on_the_chats():
    # Both AIs may train on the family's chats by default, and Parent Recap can't check the
    # setting, so the sign-in for the AI the family chose names it, as a hint beside the step.
    page_html = (PAGE_DIR / "index.html").read_text()
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for name, (menu, setting) in TRAINING_SETTINGS.items():
        assert f'<p class="hint" data-text="{name}.training"></p>' in inside(page_html, f"ai-{name}")
        for language, table in text.items():
            assert menu in table[f"{name}.training"], (language, name)
            assert setting in table[f"{name}.training"], (language, name)


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


def test_the_button_shows_the_python_and_opens_full_disk_access(harness, page, mac):
    at_the_whatsapp_step(harness)

    r = call(page.url, "api/whatsapp/open", method="POST", body={})

    assert r.status == 200 and r.json() == {"result": "opened"}
    assert harness.opened == [WHATSAPP_PYTHON, ops.FULL_DISK_ACCESS_URL]
    assert ["open", "-R", WHATSAPP_PYTHON] in harness.commands


def test_without_full_disk_access_the_entry_ticks_itself_once_it_is_given(harness, page, mac):
    at_the_whatsapp_step(harness)
    mac.install_whatsapp(WHATSAPP_CHATS)
    mac.full_disk_access_after = 2  # the family turns the switch on while the page checks

    assert check_whatsapp(page.url).json() == {"result": "no-permission"}
    assert check_whatsapp(page.url).json() == {"result": "no-permission"}
    assert statuses(page.url)["whatsapp"] == "to-do"
    out = check_whatsapp(page.url).json()

    assert out["result"] == "readable" and len(out["chats"]) == 3
    assert statuses(page.url)["whatsapp"] == "done"
    assert mac.prompts == 0
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


def test_an_allow_click_cannot_tick_the_entry_without_full_disk_access(harness, page, mac):
    # On macOS 26 Allow lets one read through and macOS asks again on the next (ADR 0012).
    at_the_whatsapp_step(harness)
    mac.install_whatsapp(WHATSAPP_CHATS)
    mac.full_disk_access_after = 10_000
    mac.allow = "allow"

    assert check_whatsapp(page.url).json() == {"result": "no-permission"}
    assert check_whatsapp(page.url).json() == {"result": "no-permission"}
    assert mac.prompts == 0 and mac.reads == []  # macOS never asked
    assert statuses(page.url)["whatsapp"] == "to-do"


def test_a_read_that_does_not_finish_is_waiting(harness, page, mac):
    at_the_whatsapp_step(harness)
    mac.install_whatsapp(WHATSAPP_CHATS)
    mac.hangs = True

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


def test_the_whatsapp_step_names_full_disk_access_and_never_allow(page):
    # ADR 0012: only Full Disk Access lasts; an Allow click lets one read through.
    text = json.loads((PAGE_DIR / "text.json").read_text())
    names = {"en": "Full Disk Access", "fi": "Täysi levyn käyttöoikeus", "zh": "完全磁盘访问权限"}
    allow = {"en": ("App Management", "Allow"), "fi": ("Apin hallinta", "Salli"),
             "zh": ("App 管理", "允许")}

    for language, table in text.items():
        whatsapp = {k: v for k, v in table.items() if k.startswith("whatsapp.")}
        assert names[language] in whatsapp["whatsapp.explain"], language
        for key, value in whatsapp.items():
            assert not any(word in value for word in allow[language]), (language, key)
    html = call(page.url).body.decode()
    step = re.search(r'<div id="source-whatsapp".*?\n    </div>\n', html, re.S).group(0)
    assert "allow" not in step.lower()


# ── Connect: MyClub


@pytest.fixture
def myclub_server(harness, monkeypatch) -> MyClubServer:
    """MyClub's server, as test_setup_myclub.py fakes it, with no Kid's link saved yet."""
    s = MyClubServer()
    monkeypatch.setattr(myclub.requests, "get", s.get)
    for kid in harness.config["kids"]:
        kid.pop("myclub_ical_url", None)
    return s


def at_the_myclub_step(harness) -> None:
    config_file(harness).parent.mkdir(parents=True, exist_ok=True)
    config_file(harness).write_text(yaml.safe_dump(harness.config, allow_unicode=True))
    save_progress(harness, {"phase": "connect", "source": "myclub", "sources": {
        "wilma": "done", "gmail": "done", "ai": "done", "whatsapp": "done"}})


def save_link(url: str, kid: str, link: str = MYCLUB_LINK) -> Response:
    return call(url, "api/myclub", method="POST", body={"kid": kid, "link": link})


def add_kid(url: str, name: str) -> Response:
    return call(url, "api/myclub/kid", method="POST", body={"name": name})


def myclub_links(harness) -> dict[str, str | None]:
    return {k["name"]: k.get("myclub_ical_url")
            for k in yaml.safe_load(config_file(harness).read_text())["kids"]}


def test_the_button_opens_the_page_to_download_the_chatgpt_app(harness, page):
    r = call(page.url, "api/open", method="POST", body={"site": "codex-app"})

    assert r.status == 200 and r.json()["result"] == "opened"
    assert harness.opened == ["https://developers.openai.com/codex/app"]


def test_the_button_opens_myclub_s_site(harness, page):
    r = call(page.url, "api/open", method="POST", body={"site": "myclub"})

    assert r.status == 200 and r.json()["result"] == "opened"
    assert harness.opened == ["https://id.myclub.fi"]


def test_each_kid_known_so_far_is_listed_without_its_link(harness, page, myclub_server):
    harness.config["kids"][0]["myclub_ical_url"] = MYCLUB_LINK
    at_the_myclub_step(harness)

    r = call(page.url, "api/state")

    assert r.json()["myclub"] == {"kids": [{"name": "Mia", "linked": True},
                                           {"name": "Leo", "linked": False}], "add": False}
    assert MYCLUB_TOKEN.encode() not in r.body


def test_a_good_link_is_checked_and_saved_for_the_right_kid(harness, page, myclub_server, caplog,
                                                           capsys):
    caplog.set_level(logging.DEBUG)
    harness.config["kids"][0]["myclub_ical_url"] = "https://example.myclub.fi/ical/mia-old"
    at_the_myclub_step(harness)

    r = save_link(page.url, "Leo", f"  {MYCLUB_LINK}\n")

    assert r.status == 200
    out = r.json()
    assert out["result"] == "saved" and out["kid"] == "Leo" and out["events"] == 2
    assert out["kids"] == [{"name": "Mia", "linked": True}, {"name": "Leo", "linked": True}]
    assert myclub_server.downloads == [MYCLUB_LINK.replace("webcal://", "https://")]
    assert myclub_links(harness) == {"Mia": "https://example.myclub.fi/ical/mia-old",
                                     "Leo": MYCLUB_LINK}
    # Every Kid has a link now, so MyClub is done and setup moves on.
    assert out["progress"]["sources"]["myclub"] == "done" and out["progress"]["source"] is None
    # The config is where the link is kept, and only there.
    printed = "".join(capsys.readouterr())
    for shown in [r.body.decode(), call(page.url, "api/state").body.decode(), printed, caplog.text,
                  progress_file(harness).read_text(), *(a for c in harness.commands for a in c)]:
        assert MYCLUB_TOKEN not in shown


def test_with_a_kid_still_without_a_link_the_step_stays_until_continue(harness, page,
                                                                       myclub_server):
    at_the_myclub_step(harness)

    out = save_link(page.url, "Mia").json()

    assert out["progress"]["sources"]["myclub"] == "done"
    assert out["progress"]["source"] == "myclub"  # Leo's link can come next
    r = call(page.url, "api/myclub/done", method="POST", body={})
    assert r.status == 200 and r.json()["progress"]["source"] is None
    assert myclub_links(harness) == {"Mia": MYCLUB_LINK, "Leo": None}


def test_continue_needs_a_link_saved_first(harness, page, myclub_server):
    at_the_myclub_step(harness)

    for body in [{}, {"x": 1}]:
        assert call(page.url, "api/myclub/done", method="POST", body=body).status == 400
    assert call(page.url, "api/state").json()["progress"]["source"] == "myclub"


@pytest.mark.parametrize("pasted, answer, result", [
    ("my-myclub-password", None, "not-a-myclub-link"),
    ("https://example.com/ical/x", None, "not-a-myclub-link"),
    (MYCLUB_LINK, (404, ""), "link-failed"),
    (MYCLUB_LINK, (200, "<html>Sign in</html>"), "not-a-calendar"),
])
def test_a_link_that_does_not_work_is_explained_and_not_saved(harness, page, myclub_server,
                                                              caplog, capsys, pasted, answer,
                                                              result):
    caplog.set_level(logging.DEBUG)
    at_the_myclub_step(harness)
    if answer:
        myclub_server.status, myclub_server.text = answer

    r = save_link(page.url, "Mia", pasted)

    assert r.status == 200 and r.json() == {"result": result}
    if answer is None:
        assert myclub_server.downloads == []  # never sent anywhere
    assert myclub_links(harness) == {"Mia": None, "Leo": None}
    assert statuses(page.url)["myclub"] == "to-do"
    assert_never_leaked(harness, [r], caplog, capsys, MYCLUB_TOKEN)
    assert_never_leaked(harness, [r], caplog, capsys, "my-myclub-password")


def test_a_slow_myclub_holds_up_no_other_save(harness, page, myclub_server, monkeypatch):
    at_the_myclub_step(harness)
    downloading, answer = threading.Event(), threading.Event()
    get = myclub_server.get

    def slow(url: str, **k: Any):
        downloading.set()
        answer.wait(5)
        return get(url, **k)
    monkeypatch.setattr(myclub.requests, "get", slow)
    saved: list[Response] = []
    thread = threading.Thread(target=lambda: saved.append(save_link(page.url, "Mia")))
    thread.start()
    downloading.wait(5)

    assert call(page.url, "api/language", method="POST", body={"language": "fi"}).status == 200

    answer.set()
    thread.join(5)
    assert saved[0].json()["result"] == "saved"
    assert myclub_links(harness)["Mia"] == MYCLUB_LINK
    assert Config.load(config_file(harness)).summary_language == "fi"


def test_a_link_that_cannot_be_reached_says_so(harness, page, myclub_server):
    at_the_myclub_step(harness)
    myclub_server.error = requests.ConnectionError(f"no route to {MYCLUB_LINK}")

    assert save_link(page.url, "Mia").json() == {"result": "link-failed"}


def test_a_link_is_saved_only_for_a_kid_the_household_has(harness, page, myclub_server):
    at_the_myclub_step(harness)

    for body in [{"kid": "Ada", "link": MYCLUB_LINK}, {"kid": "Mia"}, {"link": MYCLUB_LINK},
                 {"kid": "Mia", "link": 1}, {"kid": "Mia", "link": MYCLUB_LINK, "x": 1}]:
        r = call(page.url, "api/myclub", method="POST", body=body)
        assert r.status == 400 and r.json()["result"] == "invalid-answers", body
        assert MYCLUB_TOKEN not in r.body.decode()
    assert myclub_server.downloads == []


def test_a_household_without_wilma_adds_its_kids_here(harness, page, myclub_server):
    harness.config |= {"kids": [], "wilma": {"enabled": False}}
    at_the_myclub_step(harness)
    assert call(page.url, "api/state").json()["myclub"] == {"kids": [], "add": True}

    r = add_kid(page.url, "  Mia ")

    assert r.status == 200
    assert r.json()["result"] == "added"
    assert r.json()["kids"] == [{"name": "Mia", "linked": False}]
    assert add_kid(page.url, "小狮").json()["kids"] == [{"name": "Mia", "linked": False},
                                                        {"name": "小狮", "linked": False}]
    assert add_kid(page.url, "mia").json() == {"result": "kid-exists"}
    assert [k.name for k in Config.load(config_file(harness)).kids] == ["Mia", "小狮"]
    assert save_link(page.url, "小狮").json()["result"] == "saved"
    assert myclub_links(harness) == {"Mia": None, "小狮": MYCLUB_LINK}


def test_a_household_with_wilma_gets_its_kids_from_wilma(harness, page, myclub_server):
    at_the_myclub_step(harness)

    for body in [{"name": "Ada"}, {"name": "  "}, {}, {"name": 1}]:
        assert call(page.url, "api/myclub/kid", method="POST", body=body).status == 400, body
    assert myclub_links(harness) == {"Mia": None, "Leo": None}


def test_skipping_myclub_saves_no_link_and_moves_on(harness, page, myclub_server):
    at_the_myclub_step(harness)

    out = source(page.url, "myclub", "skip").json()

    assert out["progress"]["sources"]["myclub"] == "skipped" and out["progress"]["source"] is None
    assert myclub_links(harness) == {"Mia": None, "Leo": None}
    assert myclub_server.downloads == []


def test_the_myclub_step_shows_where_the_link_is_and_takes_it_in_the_page_s_own_field(page):
    html = call(page.url).body.decode()

    step = re.search(r'<div id="source-myclub".*?\n    </div>\n', html, re.S).group(0)
    figures = re.findall(r"<figure>.*?</figure>", step, re.S)
    assert 2 <= len(figures) <= 3
    for figure in figures:
        assert "<svg" in figure and re.search(r'<figcaption data-text="[\w.-]+">', figure)
    assert 'id="myclub-open"' in step
    field = re.search(r'<input[^>]*id="myclub-link"[^>]*>', step).group(0)
    assert 'type="password"' in field and 'autocomplete="off"' in field


def test_every_myclub_result_is_explained_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        for result in (*setup_server.MYCLUB_RESULTS, *setup_server.MYCLUB_KID_RESULTS):
            assert table.get(f"myclub.{result}", "").strip(), (language, result)


# ── Working: Parent Recap reads the Sources, with progress shown


class GatedHeaderImap(HeaderImap):
    """Gmail's IMAP server for the sender scan, which holds each batch of headers after the
    first until the test lets it go, so the page can be seen while it reads."""

    def __init__(self, senders: list[str]) -> None:
        super().__init__(senders)
        self.go = threading.Event()
        self.batches = 0

    def fetch(self, ids: bytes, what: str) -> tuple[str, list[Any]]:
        self.batches += 1
        if self.batches > 1:
            assert self.go.wait(5)
        return super().fetch(ids, what)


SENDERS = (["Opettaja <opettaja@edu.espoo.fi>"] * 300 + ["Coach <coach@tapiolan-seura.fi>"] * 100
           + ["Friend <friend@gmail.com>"] * 40 + ["Shop <news@shop.example.com>"] * 10)


@pytest.fixture
def senders(harness, monkeypatch) -> GatedHeaderImap:
    imap = GatedHeaderImap(SENDERS)
    monkeypatch.setattr(imaplib, "IMAP4_SSL", imap)
    harness.keychain["gmail-imap-parent@example.com"] = APP_PASSWORD
    return imap


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


def start_reading(url: str) -> Response:
    return call(url, "api/working", method="POST", body={})


def reading(url: str) -> dict[str, Any]:
    return call(url, "api/working/check", method="POST", body={}).json()


def read_to_the_end(url: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    wait_for(lambda: out.update(reading(url)) or out["result"] != "reading")
    return out


def test_once_every_source_is_done_the_page_reads_them_and_shows_how_far_it_is(harness, page,
                                                                             senders):
    connect_done(harness)

    r = start_reading(page.url)

    assert r.status == 200
    assert r.json()["result"] == "reading" and r.json()["progress"]["phase"] == "working"
    wait_for(lambda: reading(page.url).get("read") == 200)
    assert reading(page.url) == {"result": "reading", "read": 200, "total": 450}
    assert start_reading(page.url).json()["result"] == "reading"  # not started twice
    senders.go.set()
    out = read_to_the_end(page.url)
    assert out["result"] == "read"
    assert out["progress"]["phase"] == "check"
    # Kept for the check page, as `discover gmail-senders --json` gives them.
    assert [(s["domain"], s["count"], s["likely"]) for s in out["progress"]["gmail_senders"]] == [
        ("edu.espoo.fi", 300, True), ("tapiolan-seura.fi", 100, True), ("gmail.com", 40, False),
        ("shop.example.com", 10, False)]
    assert senders.batches == 3
    assert setup_save.read(config_file(harness))["progress"]["phase"] == "check"


def test_reading_waits_until_every_source_is_done_or_skipped(harness, page, senders):
    connect_done(harness)
    save_progress(harness, {"phase": "connect", "source": "ai", "sources": {"ai": "to-do"}})

    r = start_reading(page.url)

    assert r.status == 400 and r.json()["result"] == "invalid-answers"
    assert setup_save.read(config_file(harness))["progress"]["phase"] == "connect"
    assert senders.batches == 0
    for body in [{"x": 1}, []]:
        assert call(page.url, "api/working", method="POST", body=body).status == 400


def test_reading_picks_up_again_after_the_page_was_restarted(harness, page, senders):
    connect_done(harness)
    more_progress(harness, phase="working")
    senders.go.set()

    assert reading(page.url)["result"] == "no-read"  # nothing reading in this server yet
    assert start_reading(page.url).json()["result"] == "reading"
    assert read_to_the_end(page.url)["result"] == "read"


def test_a_gmail_that_cannot_be_read_says_so_and_can_be_tried_again(harness, page, senders):
    connect_done(harness)
    senders.go.set()
    del harness.keychain["gmail-imap-parent@example.com"]

    start_reading(page.url)
    out = read_to_the_end(page.url)

    assert out == {"result": "read-failed"}  # without the error, which names the address
    assert setup_save.read(config_file(harness))["progress"]["phase"] == "working"
    harness.keychain["gmail-imap-parent@example.com"] = APP_PASSWORD
    start_reading(page.url)
    assert read_to_the_end(page.url)["result"] == "read"


def test_every_working_result_is_explained_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        for result in setup_server.WORKING_RESULTS:
            assert table.get(f"working.{result}", "").strip(), (language, result)


# ── Check: one page with everything found, to confirm


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
    """Doctor's checks, as the server runs them: each (status, item, detail), held until the test
    lets them go, so the page can be seen while they run."""

    def __init__(self) -> None:
        self.results = [(ops.OK, "Config file", "~/.family/config.yaml, 2 kids"),
                        (ops.OK, "Gmail", "parent@example.com signed in, 3 allowlisted emails"),
                        (ops.OK, "Claude", "call succeeded (auth: keychain-oauth)")]
        self.runs: list[tuple[str | None, bool]] = []
        self.go = threading.Event()
        self.go.set()

    def __call__(self, config: str | None, skip_llm: bool = False, whatsapp_only: bool = False,
                 show: bool = False, schedule: bool = True) -> list[tuple[str, str, str]]:
        self.runs.append((config, schedule))
        assert self.go.wait(5)
        return list(self.results)


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


def health_result(url: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    wait_for(lambda: out.update(
        call(url, "api/check/health", method="POST", body={}).json()) or out["result"] != "checking")
    return out


def test_the_kids_come_all_ticked_with_their_first_name_as_everyday_name(harness, page):
    harness.config["kids"][0]["name"] = "Mia Virtanen"
    harness.config["kids"][1].update(name="Leo Matti Virtanen", everyday_name="Leksa")
    at_the_check_step(harness)

    out = check_page(page.url)

    assert out["kids"] == [{"name": "Mia Virtanen", "everyday_name": "Mia"},
                           {"name": "Leo Matti Virtanen", "everyday_name": "Leksa"}]
    # Nothing about schools or classes is asked.
    assert not {"school", "class_name", "grade"} & {k for kid in out["kids"] for k in kid}


def test_the_whatsapp_groups_linked_to_a_kid_come_ticked_the_others_unticked(harness, page):
    harness.config["kids"][0]["name"] = "Mia Virtanen"
    harness.config["whatsapp"]["chats"] = [{"name": "Leo piano", "kid": "Leo", "label": "piano"}]
    at_the_check_step(harness)

    groups = check_page(page.url)["whatsapp"]

    assert [(g["name"], g["ticked"], g["kids"]) for g in groups] == [
        ("3B parents", True, ["Mia"]),
        ("Kilo School families 🏫", True, ["Mia", "Leo"]),
        ("Neighbours ", False, []),
        ("Leo piano", True, ["Leo"]),  # picked before, though not found this time
    ]


def test_without_whatsapp_there_are_no_groups_to_tick(harness, page):
    at_the_check_step(harness, whatsapp_chats=[])
    more_progress(harness, sources={"whatsapp": "skipped"}, whatsapp_chats=None)

    assert check_page(page.url)["whatsapp"] is None


def test_school_city_and_club_senders_come_ticked_and_public_mail_is_left_out(harness, page):
    at_the_check_step(harness)

    senders = check_page(page.url)["senders"]

    assert [(s["domain"], s["ticked"]) for s in senders] == [
        ("edu.espoo.fi", True), ("shop.example.com", False),
        ("espoo.fi", True),  # the city's starting allowlist, though no mail came from it yet
        ("kilo.example.fi", True)]  # already on the allowlist
    assert senders[0] == {"domain": "edu.espoo.fi", "count": 300, "example": "Opettaja",
                          "ticked": True}


def test_each_recipient_comes_with_their_language_and_the_evening_at_21(harness, page):
    harness.config["email"]["to"] = ["parent@example.com",
                                     {"address": "partner@example.com", "language": "fi"}]
    at_the_check_step(harness)

    out = check_page(page.url)

    assert out["recipients"] == [{"address": "parent@example.com", "language": "zh"},
                                 {"address": "partner@example.com", "language": "fi"}]
    assert out["evening"] == "21:00"


def test_confirming_saves_everything_through_setup_save_and_runs_the_health_check(harness, page,
                                                                                health):
    harness.config["kids"][0]["name"] = "Mia Virtanen"
    harness.config["kids"][0]["aliases"] = ["米娅"]
    at_the_check_step(harness)
    health.go.clear()

    r = confirm(page.url, kids=[{"name": "Mia Virtanen", "everyday_name": "Mimi"},
                                {"name": "Aino", "everyday_name": "Aino"}],
                whatsapp=["3B parents", "Kilo School families 🏫"],
                recipients=[{"address": "parent@example.com", "language": "fi"},
                            {"address": "partner@example.com", "language": "zh"}],
                evening="20:30")

    assert r.status == 200 and r.json()["result"] == "checking"
    cfg = Config.load(config_file(harness))
    assert [(k.name, k.everyday_name, k.aliases) for k in cfg.kids] == [
        ("Mia Virtanen", "Mimi", ["米娅"]),  # what the page doesn't show stays as it was
        ("Aino", "Aino", [])]  # Leo unticked, Aino added by her everyday name
    assert [(c.name, c.kid, c.label) for c in cfg.whatsapp.chats] == [
        ("3B parents", "Mia Virtanen", "class"),
        ("Kilo School families 🏫", "Mia Virtanen", "school")]  # Leo is no longer a Kid
    assert cfg.whatsapp.enabled
    assert cfg.gmail.allowlist_domains == ["edu.espoo.fi", "espoo.fi"]
    # The parent's language now differs from the page's, so it's their own; the partner reads
    # the Household's.
    assert [(t.address, t.language) for t in cfg.email.to] == \
        [("parent@example.com", "fi"), ("partner@example.com", None)]
    assert cfg.summary_language == "zh"
    assert (cfg.schedule.daily_hour, cfg.schedule.daily_minute) == (20, 30)

    assert call(page.url, "api/check/health", method="POST", body={}).json() == \
        {"result": "checking"}
    assert confirm(page.url).status == 409  # not again while it runs
    health.go.set()
    out = health_result(page.url)
    assert out["result"] == "ok"
    assert out["checks"] == [{"check": "config", "status": "ok"}, {"check": "gmail", "status": "ok"},
                             {"check": "ai", "status": "ok"}]
    assert out["progress"]["phase"] == "first-brief"
    # The schedule is Finish's: it isn't checked before it's installed.
    assert health.runs == [(str(config_file(harness)), False)]


def test_a_health_check_that_fails_keeps_the_page_and_says_which_check(harness, page, health):
    at_the_check_step(harness)
    health.results = [
        (ops.OK, "Config file", "~/.family/config.yaml, 2 kids"),
        (ops.FAIL, "Gmail", "IMAP login failed: parent@example.com rejected"),
        (ops.WARN, "Pilot feedback", "feedback is on but prefill_base_url or fields is missing"),
        (ops.FAIL, "MyClub (Mia)", "subscription link doesn't open: https://x.myclub.fi/secret")]

    assert confirm(page.url).json()["result"] == "checking"
    out = health_result(page.url)

    assert out == {"result": "not-ok", "checks": [
        {"check": "config", "status": "ok"}, {"check": "gmail", "status": "fail"},
        {"check": "feedback", "status": "warn"},
        {"check": "myclub", "status": "fail", "kid": "Mia"}]}
    assert "parent@example.com" not in json.dumps(out) and "secret" not in json.dumps(out)
    assert setup_save.read(config_file(harness))["progress"]["phase"] == "check"
    # Fixed, it's run again by confirming again.
    health.results = health.results[:1]
    confirm(page.url)
    assert health_result(page.url)["result"] == "ok"


def test_warnings_alone_let_setup_move_on(harness, page, health):
    at_the_check_step(harness)
    health.results.append((ops.WARN, "Pilot feedback", "feedback.household_label is empty"))

    confirm(page.url)

    assert health_result(page.url)["result"] == "ok"


def test_a_pilot_household_sees_its_feedback_label_and_can_change_it(harness, page, health):
    harness.config["feedback"] = {**FEEDBACK, "household_label": "parent"}
    at_the_check_step(harness)

    assert check_page(page.url)["feedback"] == {"household_label": "parent"}
    assert confirm(page.url, household_label=" ").status == 400

    assert confirm(page.url, household_label=" Virtanen family ").json()["result"] == "checking"
    assert Config.load(config_file(harness)).feedback.household_label == "Virtanen family"


def test_a_household_not_in_the_pilot_has_no_feedback_label_to_give(harness, page, health):
    at_the_check_step(harness)

    assert check_page(page.url)["feedback"] is None
    assert confirm(page.url, household_label="Virtanen family").status == 400
    assert confirm(page.url).json()["result"] == "checking"


def test_the_health_check_runs_for_real_with_the_sources_confirmed(harness, page, ai, monkeypatch):
    ai("claude")
    harness.keychain["claude-oauth-token"] = "sk-ant-oat01-token"
    harness.keychain["gmail-imap-parent@example.com"] = APP_PASSWORD
    harness.config["wilma"]["enabled"] = False
    monkeypatch.setattr(imaplib, "IMAP4_SSL", HeaderImap(SENDERS))
    at_the_check_step(harness)

    confirm(page.url, whatsapp=[])

    out = health_result(page.url)
    assert out["result"] == "ok", out
    assert {c["check"] for c in out["checks"]} >= {"config", "gmail", "ai", "calendar"}
    assert not Config.load(config_file(harness)).whatsapp.enabled  # no group ticked


@pytest.mark.parametrize("answers", [
    {"senders": ["gmail.com"]},  # public mail is never offered
    {"senders": ["anything.example"]},
    {"whatsapp": ["Another group"]},
    {"kids": []},
    {"kids": [{"name": "Mia", "everyday_name": " "}]},
    {"kids": [{"name": "Mia", "everyday_name": "A"}, {"name": "mia", "everyday_name": "B"}]},
    {"recipients": [{"address": "stranger@example.com", "language": "fi"}]},
    {"recipients": [{"address": "parent@example.com", "language": "klingon!"},
                    {"address": "partner@example.com", "language": "zh"}]},
    {"evening": "25:00"},
    {"x": 1},
])
def test_answers_the_check_page_did_not_offer_are_refused(harness, page, health, answers):
    at_the_check_step(harness)
    before = config_file(harness).read_text()

    r = confirm(page.url, **answers)

    assert r.status == 400 and r.json()["result"] == "invalid-answers", answers
    assert config_file(harness).read_text() == before
    assert health.runs == []


def test_without_whatsapp_confirming_leaves_it_as_it_was(harness, page, health):
    at_the_check_step(harness, whatsapp_chats=[])
    more_progress(harness, sources={"whatsapp": "skipped"}, whatsapp_chats=None)
    harness_chats = yaml.safe_load(config_file(harness).read_text())["whatsapp"]

    assert confirm(page.url, whatsapp=["3B parents"]).status == 400
    assert confirm(page.url, whatsapp=None).json()["result"] == "checking"
    assert yaml.safe_load(config_file(harness).read_text())["whatsapp"] == harness_chats


def test_the_check_page_has_all_options_and_lets_kids_be_added(page):
    html = (PAGE_DIR / "index.html").read_text()

    for element in ('id="check-whatsapp-all"', 'id="check-senders-all"', 'id="check-kid-form"',
                    'id="check-evening"', 'type="time"'):
        assert element in html, element


def test_every_check_and_health_result_is_explained_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        for result in setup_server.HEALTH_RESULTS:
            assert table.get(f"health.{result}", "").strip(), (language, result)
        for check in {*setup_server.HEALTH_CHECKS.values(), "other"}:
            assert table.get(f"health.check.{check}", "").strip(), (language, check)
            assert table.get(f"health.fail.{check}", "").strip(), (language, check)


def test_every_health_check_doctor_runs_has_a_name_on_the_page():
    source = Path(ops.__file__).read_text()
    items = {re.split(r" ?[{(]", item)[0]
             for item in re.findall(r'\badd\([^,]+, f?"([^"]+)"', source)}
    items -= setup_server.SCHEDULE_CHECKS

    assert items and items <= set(setup_server.HEALTH_CHECKS), items - set(setup_server.HEALTH_CHECKS)


# ── First Brief: the real Brief in the page, then sent to the setup parent only


BRIEF_TEXT = "Bring the signed trip form to school on Tuesday"


def at_the_first_brief(harness, **config: Any) -> None:
    """Check confirmed, with three days of school mail for the Brief."""
    connect_done(harness, **config)
    more_progress(harness, phase="first-brief")
    harness.sources["gmail"] = [msg("gmail", "g1", "2026-09-27T09:00:00+03:00", BRIEF_TEXT,
                                    sender="teacher@edu.espoo.fi", subject="Trip")]
    harness.model_reply = {"per_kid": [], "calendar_events": [], "message_digest": BRIEF_TEXT}
    harness.keychain["gmail-imap-parent@example.com"] = APP_PASSWORD


def make_brief(url: str) -> Response:
    return call(url, "api/brief", method="POST", body={})


def brief_made(url: str) -> dict[str, Any]:
    out: dict[str, Any] = {}

    def made() -> bool:
        out.clear()
        out.update(call(url, "api/brief/check", method="POST", body={}).json())
        return out["result"] != "making"
    wait_for(made)
    return out


def send_it_to_me(url: str) -> Response:
    return call(url, "api/brief/send", method="POST", body={})


def test_the_preview_makes_the_real_brief_and_sends_nothing(harness, page, mac):
    at_the_first_brief(harness)

    r = make_brief(page.url)

    assert r.status == 200 and r.json()["result"] == "making"
    out = brief_made(page.url)
    assert out["result"] == "made"
    # The email's own HTML, as the evening Brief renders it.
    assert out["html"].startswith("<html><body") and BRIEF_TEXT in out["html"]
    assert "Parent Recap" in out["html"]
    assert harness.sent == [] and harness.imessages == []
    assert not harness.state_path.exists()  # nothing recorded: the evening Brief reads it again
    assert not list(harness.home.glob("ParentRecap/*.md"))
    # Through a bg job, so WhatsApp can be read, over the last three days.
    assert mac.jobs[-1][5:] == ["run", "--preview", "--lookback-hours", "72"]
    assert harness.lookback_hours["gmail"] == [72]


def test_the_preview_is_shown_in_a_sandboxed_frame(harness, page, mac):
    at_the_first_brief(harness)
    assert call(page.url, "brief.html").status == 404  # nothing made yet

    make_brief(page.url)
    out = brief_made(page.url)

    r = call(page.url, "brief.html")
    assert r.status == 200 and r.headers["content-type"] == "text/html; charset=utf-8"
    csp = r.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "sandbox" in csp and "frame-ancestors 'self'" in csp
    assert "script-src" not in csp
    # The email's HTML, with its links kept from taking the frame anywhere.
    assert r.body.decode() == out["html"].replace(
        "<html>", '<html><head><base target="_blank"></head>', 1)
    assert "frame-src 'self'" in call(page.url).headers["content-security-policy"]
    page_html = (PAGE_DIR / "index.html").read_text()
    assert re.search(r'<iframe id="brief-frame"[^>]* sandbox[ >]', page_html)


def test_three_quiet_days_still_make_a_brief(harness, page, mac):
    at_the_first_brief(harness)
    harness.sources["gmail"] = []

    make_brief(page.url)
    out = brief_made(page.url)

    assert out["result"] == "made" and "Parent Recap" in out["html"]
    assert harness.model_calls == []
    assert harness.sent == []


def test_the_page_shows_progress_while_the_brief_is_made(harness, page, monkeypatch):
    at_the_first_brief(harness)
    go = threading.Event()
    lines = ['{"preview": "reading", "sources": ["gmail", "wilma", "whatsapp"]}',
             '{"preview": "reading", "source": "gmail"}',
             '{"preview": "reading", "source": "wilma"}']

    def job(cmd_args: list[str], config: str | None, timeout: int = 900, echo: bool = True,
            output=None) -> tuple[int, str]:
        output("12:00:00 INFO family_brief | Running collectors\n" + "\n".join(lines) + "\n")
        assert go.wait(5)
        lines.extend(['{"preview": "writing"}',
                      json.dumps({"preview": "made", "subject": "Parent Recap · 2026-09-27",
                                  "text": "Brief", "html": "<html><body>Brief</body></html>",
                                  "feedback": None})])
        return 0, "\n".join(lines) + "\n"
    monkeypatch.setattr(ops, "run_as_job", job)

    make_brief(page.url)

    check = call(page.url, "api/brief/check", method="POST", body={}).json()
    assert check == {"result": "making", "sources": ["gmail", "wilma", "whatsapp"], "step": "wilma"}
    assert make_brief(page.url).json()["result"] == "making"  # not made twice at once
    go.set()
    assert brief_made(page.url)["result"] == "made"


def test_a_brief_that_cannot_be_made_says_so_and_can_be_made_again(harness, page, mac):
    at_the_first_brief(harness)
    mac.bootstrap_fails = True

    make_brief(page.url)
    assert brief_made(page.url) == {"result": "make-failed"}  # without the error
    assert call(page.url, "brief.html").status == 404

    mac.bootstrap_fails = False
    make_brief(page.url)
    assert brief_made(page.url)["result"] == "made"


def test_the_brief_is_made_only_once_check_is_confirmed(harness, page, mac):
    at_the_check_step(harness)

    r = make_brief(page.url)

    assert r.status == 400 and r.json()["result"] == "invalid-answers"
    assert mac.jobs == []
    assert call(page.url, "api/brief/check", method="POST", body={}).json() == {"result": "no-brief"}
    for body in [{"x": 1}, []]:
        assert call(page.url, "api/brief", method="POST", body=body).status == 400


def test_send_it_to_me_reaches_only_the_setup_parent(harness, page, mac):
    harness.config["email"]["to"] = ["parent@example.com",
                                     {"address": "partner@example.com", "language": "fi"}]
    at_the_first_brief(harness)
    make_brief(page.url)
    made = brief_made(page.url)

    r = send_it_to_me(page.url)

    assert r.status == 200
    assert r.json()["result"] == "sent" and r.json()["to"] == "parent@example.com"
    [email] = harness.sent
    assert email.to == ["parent@example.com"]  # never the partner: theirs is the evening one
    assert email.html == made["html"] and BRIEF_TEXT in email.text
    assert email.subject == "Parent Recap · 2026-09-27"
    assert email.from_addr == "parent@example.com"
    # The outcome check counts it as the first Brief delivered.
    state = harness.state()
    assert list(state["brief_delivered_at"]) == ["parent@example.com"]
    assert not state["seen_message_ids"]  # the evening Brief still reads these three days
    assert setup_status._brief(Config.load(config_file(harness))).ok


def test_after_the_first_brief_setup_moves_on_to_finish(harness, page, mac):
    at_the_first_brief(harness)
    assert call(page.url, "api/brief/done", method="POST", body={}).status == 400  # not yet sent
    make_brief(page.url)
    brief_made(page.url)
    send_it_to_me(page.url)

    out = call(page.url, "api/brief/done", method="POST", body={}).json()

    assert out["result"] == "saved" and out["progress"]["phase"] == "finish"
    assert setup_save.read(config_file(harness))["progress"]["phase"] == "finish"


def test_nothing_is_sent_before_the_brief_is_made(harness, page, mac):
    at_the_first_brief(harness)

    r = send_it_to_me(page.url)

    assert r.status == 400 and r.json()["result"] == "invalid-answers"
    assert harness.sent == []
    make_brief(page.url)
    brief_made(page.url)
    assert call(page.url, "api/brief/send", method="POST",
                body={"to": "partner@example.com"}).status == 400
    assert harness.sent == []


def test_a_send_that_fails_says_so_and_can_be_tried_again(harness, page, mac):
    at_the_first_brief(harness)
    make_brief(page.url)
    brief_made(page.url)
    harness.email_error = OSError("SMTP connection refused for parent@example.com")

    r = send_it_to_me(page.url)

    assert r.json() == {"result": "send-failed"}  # without the error, which names the address
    assert not harness.state_path.exists()
    harness.email_error = None
    assert send_it_to_me(page.url).json()["result"] == "sent"


def test_it_is_not_sent_while_an_evening_run_is_going(harness, page, mac):
    at_the_first_brief(harness)
    make_brief(page.url)
    brief_made(page.url)

    with run_lock.exclusive(Config.load(config_file(harness))):
        assert send_it_to_me(page.url).json() == {"result": "busy"}

    assert harness.sent == []
    assert send_it_to_me(page.url).json()["result"] == "sent"


def test_a_pilot_household_can_say_something_is_wrong_through_the_feedback_form(harness, page,
                                                                                 mac):
    at_the_first_brief(harness, feedback=FEEDBACK)
    make_brief(page.url)

    out = brief_made(page.url)

    assert out["feedback"] is True
    assert FORM not in json.dumps({k: v for k, v in out.items() if k != "html"})
    r = call(page.url, "api/brief/feedback", method="POST", body={})
    assert r.json() == {"result": "opened"}
    # The Brief's own Digest link, pre-filled with tonight's Digest.
    [(_, answers, _)] = feedback_links(f'<a href="{html.escape(harness.opened[-1])}">x</a>')
    assert answers["verdict"] == feedback.DIGEST_WRONG and answers["item_text"] == BRIEF_TEXT
    assert answers["household"] == "王家"


def test_only_a_pilot_household_has_the_feedback_button(harness, page, mac):
    at_the_first_brief(harness, feedback={**FEEDBACK, "enabled": False})
    make_brief(page.url)

    assert brief_made(page.url)["feedback"] is False
    r = call(page.url, "api/brief/feedback", method="POST", body={})
    assert r.status == 400 and harness.opened == []


def test_every_first_brief_result_is_explained_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        for result in setup_server.BRIEF_RESULTS:
            assert table.get(f"brief.{result}", "").strip(), (language, result)
        for result in setup_server.SEND_RESULTS:
            assert table.get(f"brief.send.{result}", "").strip(), (language, result)


# ── Finish: the evening job and the wake-up, then the outcome checklist


OTHER_WAKE = "wakepoweron at 7:00AM weekdays only"


class Schedule:
    """The Mac as Finish meets it: launchctl loads, unloads and lists the jobs; pmset says
    whether the Mac sleeps and lists its repeating wake schedule; and macOS's administrator dialog
    runs `pmset repeat` once the family types their Mac password there, held until the test lets
    it go. Every other process is the harness's."""

    def __init__(self, run) -> None:
        self._run = run
        self.loaded: set[str] = set()
        self.loads: list[str] = []
        self.load_fails = False
        self.sleep = "AC Power:\n sleep                1\n"
        self.repeating: list[str] = []
        self.dialog = "allow"  # or "cancel", or "unavailable"
        self.dialogs: list[str] = []
        self.go = threading.Event()
        self.go.set()

    def __call__(self, cmd: list[str], *a: Any, **k: Any) -> subprocess.CompletedProcess:
        prog = Path(cmd[0]).name
        if prog == "launchctl" and cmd[1] == "list":
            listed = "".join(f"-\t0\t{label}\n" for label in sorted(self.loaded))
            return subprocess.CompletedProcess(cmd, 0, listed, "")
        if prog == "launchctl":
            label = Path(cmd[2]).stem
            if cmd[1] == "load":
                if self.load_fails:
                    raise subprocess.CalledProcessError(5, cmd)
                self.loads.append(label)
                self.loaded.add(label)
            else:
                self.loaded.discard(label)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if prog == "pmset":
            if cmd[1:] == ["-g", "custom"]:
                return subprocess.CompletedProcess(cmd, 0, self.sleep, "")
            listed = "".join(f"  {line}\n" for line in self.repeating)
            return subprocess.CompletedProcess(
                cmd, 0, f"Repeating power events:\n{listed}" if listed else "", "")
        if prog == "osascript" and "administrator privileges" in cmd[-1]:
            self.dialogs.append(cmd[-1])
            assert self.go.wait(5)
            if self.dialog == "cancel":
                return subprocess.CompletedProcess(cmd, 1, "", "execution error: User canceled. (-128)")
            if self.dialog == "unavailable":
                return subprocess.CompletedProcess(
                    cmd, 1, "", "execution error: No user interaction allowed. (-1713)")
            hh, mm = cmd[-1].split("MTWRFSU ", 1)[1][:5].split(":")
            h = int(hh)
            self.repeating = [f"wakepoweron at {h % 12 or 12}:{mm}{'AM' if h < 12 else 'PM'} every day"]
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return self._run(cmd, *a, **k)


@pytest.fixture
def schedule(harness, health, monkeypatch) -> Schedule:
    s = Schedule(subprocess.run)
    monkeypatch.setattr(subprocess, "run", s)
    monkeypatch.setattr(ops, "LAUNCH_AGENTS", harness.home / "Library" / "LaunchAgents")
    return s


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


def finished(url: str) -> dict[str, Any]:
    out: dict[str, Any] = {}

    def ended() -> bool:
        out.clear()
        out.update(call(url, "api/finish/check", method="POST", body={}).json())
        return out["result"] not in ("installing", "checking")
    wait_for(ended)
    return out


def checklist(out: dict[str, Any]) -> dict[str, bool]:
    return {o["outcome"]: o["ok"] for o in out["outcomes"]}


def test_finish_installs_the_evening_job_and_the_wake_up_through_macos_s_dialog(harness, page,
                                                                               schedule, health):
    at_finish(harness)

    r = finish(page.url)

    assert r.status == 200 and r.json() == {"result": "installing"}
    out = finished(page.url)
    assert out["result"] == "done" and out["wake"] == "set"
    assert checklist(out) == {"installed": True, "doctor": True, "brief": True, "nightly": True,
                              "wake": True}
    assert ops.JOB_DAILY in schedule.loaded
    assert (ops.LAUNCH_AGENTS / f"{ops.JOB_DAILY}.plist").exists()
    # The Mac password goes into macOS's own dialog, which says who is asking and why.
    [dialog] = schedule.dialogs
    assert "pmset repeat wakeorpoweron MTWRFSU 20:55:00" in dialog and "Parent Recap" in dialog
    assert "with administrator privileges" in dialog
    # Doctor's checks run with the evening job's own, now that it's installed.
    assert health.runs[-1][1] is True


def test_finish_takes_no_password(page):
    for body in [{}, {"replace_wake": "yes"}, {"replace_wake": False, "password": "hunter2"},
                 ["replace_wake"]]:
        r = call(page.url, "api/finish", method="POST", body=body)
        assert r.status == 400 and r.json()["result"] == "invalid-answers", body
    html_text = (PAGE_DIR / "index.html").read_text()
    finish_section = html_text.split('<section id="finish"', 1)[1].split("</section>", 1)[0]
    assert "<input" not in finish_section


def test_the_server_stops_once_every_outcome_is_true(harness, page, schedule):
    at_finish(harness)

    finish(page.url)
    assert finished(page.url)["result"] == "done"

    wait_for(lambda: refused(page.url))


def test_an_outcome_still_false_keeps_the_page_and_says_which(harness, page, schedule):
    at_finish(harness, delivered=False)

    finish(page.url)
    out = finished(page.url)

    assert out["result"] == "not-done"
    assert checklist(out) == {"installed": True, "doctor": True, "brief": False, "nightly": True,
                              "wake": True}
    # Only each outcome and whether it's true: their reasons name addresses, in English.
    assert "parent@example.com" not in json.dumps(out)
    assert call(page.url, "api/state").status == 200  # still serving


def test_a_failed_health_check_names_the_checks_that_aren_t_ok(harness, page, schedule, health):
    at_finish(harness)
    health.results += [(ops.FAIL, "Gmail", "IMAP login failed for parent@example.com"),
                       (ops.WARN, "Last run", "40 hours ago"),
                       (ops.FAIL, "MyClub (Mia)", "subscription link doesn't open: secret"),
                       (ops.WARN, "Something new", "a check the page doesn't know")]

    finish(page.url)
    out = finished(page.url)

    assert out["result"] == "not-done"
    doctor = next(o for o in out["outcomes"] if o["outcome"] == "doctor")
    assert doctor == {"outcome": "doctor", "ok": False, "checks": [
        {"check": "gmail", "status": "fail"}, {"check": "last-run", "status": "warn"},
        {"check": "myclub", "status": "fail", "kid": "Mia"}, {"check": "other", "status": "warn"}]}
    # Only their names: doctor's details can name an address or quote an error.
    assert "parent@example.com" not in json.dumps(out) and "secret" not in json.dumps(out)
    # The outcomes that are true and have no warnings carry nothing more.
    assert all(set(o) == {"outcome", "ok"} for o in out["outcomes"] if o["ok"])


def test_warnings_alone_dont_hold_finish_up_and_are_listed(harness, page, schedule, health):
    at_finish(harness)
    health.results += [(ops.WARN, "Pilot feedback", "feedback.household_label is empty"),
                       (ops.WARN, "Language sv", "the Brief's own text in sv isn't ready yet")]

    finish(page.url)
    out = finished(page.url)

    assert out["result"] == "done"
    doctor = next(o for o in out["outcomes"] if o["outcome"] == "doctor")
    assert doctor == {"outcome": "doctor", "ok": True, "checks": [
        {"check": "feedback", "status": "warn"},
        {"check": "language", "status": "warn", "language": "sv"}]}


def test_finish_for_now_stops_the_page_once_a_try_has_ended(harness, page, schedule):
    at_finish(harness, delivered=False)
    finish(page.url)
    assert finished(page.url)["result"] == "not-done"

    for body in [{"x": 1}, []]:
        assert call(page.url, "api/finish/stop", method="POST", body=body).status == 400
    r = call(page.url, "api/finish/stop", method="POST", body={})

    assert r.status == 200 and r.json() == {"result": "stopped"}
    wait_for(lambda: refused(page.url))


def test_finish_for_now_waits_while_finish_is_installing(harness, page, schedule):
    at_finish(harness)
    schedule.go.clear()
    finish(page.url)
    wait_for(lambda: schedule.dialogs)

    r = call(page.url, "api/finish/stop", method="POST", body={})

    assert r.status == 200 and r.json() == {"result": "installing"}
    assert call(page.url, "api/state").status == 200  # still serving: the install isn't cut off
    schedule.go.set()
    finished(page.url)


def test_finish_for_now_is_only_for_finish(harness, page, schedule):
    at_finish(harness)
    more_progress(harness, phase="first-brief")

    r = call(page.url, "api/finish/stop", method="POST", body={})

    assert r.status == 400 and r.json()["result"] == "invalid-answers"
    assert call(page.url, "api/state").status == 200


def test_another_wake_schedule_is_replaced_only_once_the_family_agrees(harness, page, schedule):
    at_finish(harness)
    schedule.repeating = [OTHER_WAKE]

    finish(page.url)
    out = finished(page.url)

    assert out["result"] == "not-done" and out["wake"] == "other-schedule"
    assert out["other"] == [OTHER_WAKE]
    assert schedule.dialogs == []  # the family decides before any dialog opens
    assert checklist(out)["wake"] is False and checklist(out)["nightly"] is True

    finish(page.url, replace_wake=True)
    out = finished(page.url)
    assert out["result"] == "done" and out["wake"] == "set"
    assert len(schedule.dialogs) == 1


@pytest.mark.parametrize("dialog, result", [("cancel", "cancelled"), ("unavailable", "no-dialog")])
def test_a_wake_up_that_isn_t_set_says_why_and_can_be_tried_again(harness, page, schedule, dialog,
                                                                  result):
    at_finish(harness)
    schedule.dialog = dialog

    finish(page.url)
    out = finished(page.url)

    assert out["result"] == "not-done" and out["wake"] == result
    assert checklist(out)["wake"] is False
    assert "sudo" not in json.dumps(out)  # the password goes into macOS's dialog only
    schedule.dialog = "allow"
    finish(page.url)
    assert finished(page.url)["result"] == "done"


def test_a_mac_that_never_sleeps_needs_no_wake_up(harness, page, schedule):
    at_finish(harness)
    schedule.sleep = "AC Power:\n sleep                0\n"

    finish(page.url)
    out = finished(page.url)

    assert out["result"] == "done" and out["wake"] == "never-sleeps"
    assert schedule.dialogs == []


def test_while_macos_asks_for_the_password_the_page_waits(harness, page, schedule):
    at_finish(harness)
    schedule.go.clear()

    finish(page.url)

    wait_for(lambda: schedule.dialogs)
    assert call(page.url, "api/finish/check", method="POST", body={}).json() == {
        "result": "installing"}
    assert finish(page.url).json() == {"result": "installing"}  # not asked twice
    schedule.go.set()
    assert finished(page.url)["result"] == "done"
    assert len(schedule.dialogs) == 1


def test_jobs_that_cannot_be_loaded_say_so(harness, page, schedule):
    at_finish(harness)
    schedule.load_fails = True

    finish(page.url)

    assert finished(page.url) == {"result": "install-failed"}
    assert schedule.dialogs == []
    schedule.load_fails = False
    finish(page.url)
    assert finished(page.url)["result"] == "done"


def test_finish_waits_for_the_first_brief(harness, page, schedule):
    at_finish(harness)
    more_progress(harness, phase="first-brief")

    for path in ["api/finish", "api/finish/outcomes"]:
        body = {"replace_wake": False} if path == "api/finish" else {}
        r = call(page.url, path, method="POST", body=body)
        assert r.status == 400 and r.json()["result"] == "invalid-answers", path
    assert call(page.url, "api/finish/check", method="POST", body={}).json() == {
        "result": "no-finish"}
    assert schedule.loads == [] and schedule.dialogs == []


def test_a_finished_household_sees_only_the_checklist(harness, page, schedule):
    at_finish(harness)
    schedule.loaded.add(ops.JOB_DAILY)
    schedule.repeating = ["wakepoweron at 8:55PM every day"]

    r = call(page.url, "api/finish/outcomes", method="POST", body={})

    assert r.status == 200 and r.json() == {"result": "checking"}
    out = finished(page.url)
    assert out["result"] == "done" and "wake" not in out
    assert all(checklist(out).values())
    # Checked only: nothing installed again, and no dialog.
    assert schedule.loads == [] and schedule.dialogs == []
    wait_for(lambda: refused(page.url))


def test_a_household_not_finished_yet_is_checked_without_installing(harness, page, schedule):
    at_finish(harness)

    call(page.url, "api/finish/outcomes", method="POST", body={})
    out = finished(page.url)

    assert out["result"] == "not-done"
    assert checklist(out)["nightly"] is False and checklist(out)["wake"] is False
    assert schedule.loads == [] and schedule.dialogs == []
    for body in [{"x": 1}, []]:
        assert call(page.url, "api/finish/outcomes", method="POST", body=body).status == 400


def check_again(url: str) -> Response:
    return call(url, "api/finish/outcomes", method="POST", body={"again": True})


def test_check_again_rechecks_without_installing_or_touching_the_wake_up(harness, page, schedule,
                                                                         health):
    at_finish(harness)
    health.results += [(ops.FAIL, "Claude", "call failed")]
    finish(page.url)
    assert finished(page.url)["result"] == "not-done"
    loads, dialogs = list(schedule.loads), list(schedule.dialogs)
    runs = len(health.runs)
    health.results.pop()

    r = check_again(page.url)

    assert r.status == 200 and r.json() == {"result": "checking"}
    out = finished(page.url)
    assert out["result"] == "done" and all(checklist(out).values())
    assert len(health.runs) == runs + 1  # the health check ran again
    # Checked only: the evening job isn't loaded again, and no dialog sets the wake-up.
    assert schedule.loads == loads and schedule.dialogs == dialogs
    wait_for(lambda: refused(page.url))  # every outcome true: All done, and the page stops


def test_check_again_keeps_saying_what_s_still_missing(harness, page, schedule, health):
    at_finish(harness)
    health.results += [(ops.FAIL, "Claude", "call failed")]
    finish(page.url)
    finished(page.url)

    check_again(page.url)
    out = finished(page.url)

    assert out["result"] == "not-done" and out["tried"] is True and out["wake"] == "set"
    doctor = next(o for o in out["outcomes"] if o["outcome"] == "doctor")
    assert doctor["ok"] is False and {"check": "ai", "status": "fail"} in doctor["checks"]
    assert call(page.url, "api/state").status == 200  # still serving
    for body in [{"again": False}, {"again": "yes"}, {"again": 1}, {"again": True, "x": 1}]:
        assert call(page.url, "api/finish/outcomes", method="POST", body=body).status == 400


def test_check_again_still_offers_to_replace_another_wake_schedule(harness, page, schedule):
    at_finish(harness)
    schedule.repeating = [OTHER_WAKE]
    finish(page.url)
    finished(page.url)

    check_again(page.url)
    out = finished(page.url)

    assert out["result"] == "not-done" and out["wake"] == "other-schedule"
    assert out["other"] == [OTHER_WAKE] and schedule.dialogs == []


def test_check_again_before_turning_it_on_says_what_s_missing_without_installing(
        harness, page, schedule):
    at_finish(harness)

    check_again(page.url)
    out = finished(page.url)

    assert out["result"] == "not-done" and out["tried"] is True
    assert checklist(out)["nightly"] is False
    assert schedule.loads == [] and schedule.dialogs == []


def test_finish_offers_check_again_in_every_language():
    html_text = (PAGE_DIR / "index.html").read_text()
    finish_section = html_text.split('<section id="finish"', 1)[1].split("</section>", 1)[0]
    text = json.loads((PAGE_DIR / "text.json").read_text())

    assert 'id="finish-again"' in finish_section and 'data-text="finish.again"' in finish_section
    assert text["en"]["finish.again"] == "Check again"
    for table in text.values():
        assert table["finish.again"] in table["finish.not-done"]
        # Turn it on is for installing, not for checking.
        for key in ["finish.fail.ai", "finish.fail.whatsapp", "finish.fail.weekend",
                    "finish.fail.other"]:
            assert table["finish.again"] in table[key], key
            assert table["finish.start"] not in table[key], key


def test_the_finish_page_shows_the_checklist_the_restart_reminder_and_where_changes_go(page):
    html_text = (PAGE_DIR / "index.html").read_text()
    finish_section = html_text.split('<section id="finish"', 1)[1].split("</section>", 1)[0]

    for needed in ['id="finish-checklist"', 'data-text="finish.restart"', 'id="finish-changes"',
                   'id="finish-start"', 'id="finish-replace"', 'id="finish-stop"',
                   'id="finish-stopped"']:
        assert needed in finish_section, needed


def test_every_finish_text_is_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        keys = [*(f"finish.{r}" for r in setup_server.FINISH_RESULTS),
                *(f"finish.wake.{r}" for r in ops.WAKE_RESULTS),
                *(f"finish.outcome.{o}" for o in setup_status.TITLES),
                *(f"finish.missing.{o}" for o in setup_status.TITLES),
                *(f"finish.changes.{a}" for a in setup_server.AIS),
                *(f"finish.stopped.{a}" for a in setup_server.AIS),
                *(f"health.check.{c}" for c in {*setup_server.FINISH_CHECKS.values(), "other"}),
                *(f"finish.fail.{c}" for c in {*setup_server.FINISH_CHECKS.values(), "other"}),
                "finish.title", "finish.intro", "finish.start", "finish.replace", "finish.restart",
                "finish.checklist", "finish.closed", "finish.stop", "finish.warnings",
                "finish.again"]
        for key in keys:
            assert table.get(key, "").strip(), (language, key)


def test_finish_says_a_mac_that_s_shut_down_makes_no_brief():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    assert "shut down" in text["en"]["finish.restart"]
    assert "sammutettu" in text["fi"]["finish.restart"]
    assert "关机" in text["zh"]["finish.restart"]


# ── Continue in the chat


FAKE_CHAT_CLAUDE = """#!/bin/sh
printf '%s\\n' "$PWD" "$@" > "${0%/*}/claude-chat.ran"
"""


def continue_in_chat(url: str, ai_name: str) -> Response:
    return call(url, "api/chat", method="POST", body={"ai": ai_name})


def test_continue_in_the_chat_opens_claude_code_with_the_setup_skill_in_terminal(harness, page,
                                                                                 ai):
    bin_dir = harness.home / "bin"
    ai("claude")
    (bin_dir / "claude").write_text(FAKE_CHAT_CLAUDE)

    r = continue_in_chat(page.url, "claude")

    assert r.status == 200 and r.json() == {"result": "opened"}
    [opened] = [c for c in harness.commands if c[:3] == ["open", "-a", "Terminal"]]
    script = Path(opened[3])
    assert script.suffix == ".command" and os.access(script, os.X_OK)
    # Terminal runs the script: Claude Code starts in the family's home folder, at the setup skill.
    subprocess.Popen(["/bin/sh", str(script)], env={**os.environ, "HOME": str(harness.home)},
                     stdout=subprocess.DEVNULL).wait(10)
    assert (bin_dir / "claude-chat.ran").read_text().splitlines() == \
        [str(harness.home), "/parent-recap:setup"]
    page.stop()
    assert not script.exists()  # the script's folder goes once the page stops


def test_continue_in_the_chat_before_welcome_installs_the_plugin_first(harness, page, ai):
    ai("claude")

    assert continue_in_chat(page.url, "claude").json() == {"result": "opened"}

    assert ["plugin", "install", "parent-recap@kinlace", "--scope", "user"] in harness.plugin_calls
    assert call(page.url, "api/state").json()["chat"] == {"claude": "installed"}


def test_continue_in_the_chat_tells_a_codex_family_what_to_type(harness, page, ai):
    ai("codex")
    install_record.add("plugin", str(ROOT))

    r = continue_in_chat(page.url, "codex")

    assert r.status == 200 and r.json() == {"result": "open-codex", "type": "$parent-recap-setup"}
    assert not harness.opened
    assert (harness.home / ".agents" / "skills" / "parent-recap-setup" / "SKILL.md").is_file()


def test_continue_in_the_chat_without_claude_code_says_how_to_install_it(harness, page, ai):
    assert continue_in_chat(page.url, "claude").json() == {
        "result": "not-installed", "install": "curl -fsSL https://claude.ai/install.sh | bash"}
    assert not harness.opened


def test_continue_in_the_chat_without_terminal_says_what_to_type_there(harness, page, ai,
                                                                       monkeypatch):
    ai("claude")
    run = setup_server.subprocess.run

    def no_terminal(cmd, *a, **k):
        if cmd[:3] == ["open", "-a", "Terminal"]:
            return subprocess.CompletedProcess(cmd, 1, "", "")
        return run(cmd, *a, **k)
    monkeypatch.setattr(setup_server.subprocess, "run", no_terminal)

    assert continue_in_chat(page.url, "claude").json() == {
        "result": "no-terminal", "type": f"{harness.home / 'bin' / 'claude'} /parent-recap:setup"}


def test_continue_in_the_chat_takes_only_the_ai(page):
    for body in [{}, {"ai": "gemini"}, {"ai": "claude", "x": 1}, ["claude"]]:
        r = call(page.url, "api/chat", method="POST", body=body)
        assert r.status == 400 and r.json()["result"] == "invalid-answers", body


def test_the_chat_reads_the_progress_and_answers_the_page_saved(harness, page, capsys,
                                                                monkeypatch):
    call(page.url, "api/language", method="POST", body={"language": "fi"})
    welcome(page.url, ai="codex", partner={"address": "partner@example.com", "language": "zh"})
    source(page.url, "wilma", "skip")
    shown = call(page.url, "api/state").json()["progress"]
    capsys.readouterr()

    monkeypatch.setattr(sys, "argv", ["family-brief", "-c", str(config_file(harness)), "setup",
                                      "save", "--read"])
    cli.main()

    read = json.loads(capsys.readouterr().out)
    assert read["progress"] == shown
    assert (read["progress"]["phase"], read["progress"]["source"]) == ("connect", "gmail")
    assert read["progress"]["partner"] == {"address": "partner@example.com", "language": "zh"}
    cfg = Config.load(config_file(harness))
    assert (cfg.summary_language, cfg.llm.backend, cfg.wilma.enabled) == ("fi", "codex", False)


def test_the_setup_skill_carries_on_from_the_page_s_step():
    skill = (Path(__file__).resolve().parents[2] / "skills" / "setup" / "SKILL.md").read_text()
    [section] = re.findall(r"\n## Coming from the setup page\n(.*?)\n## ", skill, re.S)

    for needed in ("Continue in the chat", "setup save --read", "`phase`", "`source`",
                   "summary_language", "partner", "whatsapp_chats"):
        assert needed in section, needed
    assert "Coming from the setup page" in skill.split("## Rules", 1)[1].split("\n## ", 1)[0]


def test_every_step_of_the_page_has_continue_in_the_chat():
    html = (PAGE_DIR / "index.html").read_text()
    script = (PAGE_DIR / "page.js").read_text()

    # Outside every step's own section, so it's under each of them, and shown once a language is.
    after = html.rsplit("</section>", 1)[1]
    assert 'id="chat-continue"' in after and 'data-text="chat.continue"' in after
    assert 'getElementById("chat").hidden = !page.chosen' in script


def test_every_chat_result_is_explained_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        for name, results in setup_server.CHAT_RESULTS.items():
            for result in results:
                assert table.get(f"chat.{name}.{result}", "").strip(), (language, name, result)
