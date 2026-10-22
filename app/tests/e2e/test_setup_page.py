"""The setup page's server, `parent-recap setup page` (ADR 0006), driven with HTTP calls as the page
makes them: the command, the safeguards, the language, resuming, the Source list, and the same page
on Windows (ADR 0019).

The server runs in-process on a free port, inside the harness, as `setup_page.py` starts it, so the
config, the progress record, the secrets, the jobs, the wake and the desktop are the harness's.
Assertions are on what the page and the family see: the responses, the config and progress written,
and what was opened."""
from __future__ import annotations

import json
import re
import socket
import sys
import threading
import time
from http import HTTPStatus
from urllib.parse import urlparse

import pytest
import yaml

from family_brief import __main__ as cli, install_record, ops, setup_server
from family_brief.config import Config
from fake_system import MACOS, WINDOWS
from setup_page import (CAN_LISTEN, PAGE_DIR, ROOT, at_finish, call, check_ai, config_file, finish,
                        finished, new_server, progress_file, refused, save_progress, source,
                        statuses, wait_for, welcome)
from setup_page import ai, clock, health, page, schedule  # noqa: F401
from test_setup_whatsapp import mac  # noqa: F401


# ── the command


def serving(server: setup_server.SetupServer, codes: list[int]) -> threading.Thread:
    """`server` serving as `setup page` serves it, in the background, its exit code put in `codes`."""
    thread = threading.Thread(target=lambda: codes.append(setup_server.serve(server)), daemon=True)
    thread.start()
    return thread


@pytest.mark.skipif(not CAN_LISTEN, reason="the command listens on a port of its own")
def test_the_command_serves_the_page_for_its_config_until_it_stops(harness, capsys, monkeypatch):
    at_finish(harness)
    monkeypatch.setattr(sys, "argv", ["family-brief", "-c", str(config_file(harness)), "setup", "page"])
    codes: list[int] = []
    thread = threading.Thread(target=lambda: codes.append(cli.main()), daemon=True)
    thread.start()
    wait_for(lambda: harness.desktop.opened)

    url = harness.desktop.opened[0]
    parts = urlparse(url)
    assert parts.scheme == "http" and parts.hostname == "127.0.0.1" and parts.port
    page = call(url)
    assert page.status == 200 and "text/html" in page.headers["content-type"]
    # Setup at Finish is the config's own, so "Finish for now" stops the page.
    assert call(url, "api/finish/stop", method="POST", body={}).json() == {"result": "stopped"}
    thread.join(5)

    assert not thread.is_alive() and codes == [0]
    assert refused(url)
    opened, closed = capsys.readouterr().out.splitlines()
    assert json.loads(opened) == {"result": "opened", "url": url}
    # Terminal says the page has closed, so the family knows its window can be closed too: in the
    # config's language.
    assert closed == setup_server.closed_line("zh")


def test_the_page_stops_after_30_idle_minutes(harness, clock, capsys):
    codes: list[int] = []
    thread = serving(new_server(harness, clock), codes)
    wait_for(lambda: harness.desktop.opened)
    url = harness.desktop.opened[0]

    clock.now += 29 * 60
    assert call(url, "api/state").status == 200  # a request starts the 30 minutes again
    clock.now += 29 * 60
    time.sleep(0.1)
    assert thread.is_alive()
    clock.now += 2 * 60
    thread.join(5)

    assert not thread.is_alive() and codes == [0]
    assert refused(url)
    assert capsys.readouterr().out.splitlines()[-1] == setup_server.closed_line("en")


def test_terminal_says_the_page_has_closed_in_the_language_picked(harness, clock, capsys):
    thread = serving(new_server(harness, clock), [])
    wait_for(lambda: harness.desktop.opened)
    url = harness.desktop.opened[0]
    assert call(url, "api/language", method="POST", body={"language": "fi"}).status == 200

    clock.now += 31 * 60
    thread.join(5)

    assert capsys.readouterr().out.splitlines()[-1] == setup_server.closed_line("fi")


def test_in_tmux_or_ssh_the_command_warns_the_keychain_cannot_be_reached(harness, clock, capsys):
    harness.secrets.reachable_here = False
    thread = serving(new_server(harness, clock), [])
    wait_for(lambda: harness.desktop.opened)
    url = harness.desktop.opened[0]

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
    first, second = (new_server(harness, clock) for _ in range(2))
    try:
        assert first.url != second.url
        codes = [urlparse(s.url).path.strip("/") for s in (first, second)]
        assert codes[0] != codes[1] and all(len(c) >= 32 for c in codes)
    finally:
        first.stop()
        second.stop()


@pytest.mark.skipif(not CAN_LISTEN, reason="the page's own listener needs a port")
def test_the_page_starts_without_looking_up_the_host_name(harness, monkeypatch):
    # http.server's own bind looks up the host name, which took over 5 seconds on CI's Mac.
    def look_up(*_):
        raise AssertionError("looked up the host name")
    monkeypatch.setattr(socket, "getfqdn", look_up)
    setup_server.SetupServer(setup_server.Context(config_file(harness))).stop()


class _Echo(setup_server.Phase):
    """A phase the server isn't shipped with: one call, and its part of the page's state."""

    def routes(self) -> dict[str, setup_server.Action]:
        return {"api/echo": lambda raw: (HTTPStatus.OK, {"echo": raw})}

    def state(self, progress: dict) -> dict:
        return {"echo": {"phase": progress["phase"]}}


def test_a_phase_given_to_the_server_serves_its_calls_and_its_state(harness, clock):
    server = new_server(harness, clock, phases=(*setup_server.PHASES, _Echo))
    thread = threading.Thread(target=server.serve_until_idle, daemon=True)
    thread.start()
    try:
        echoed = call(server.url, "api/echo", method="POST", body={"a": 1})
        state = call(server.url, "api/state").json()
    finally:
        server.stop()
        thread.join(5)

    assert echoed.status == 200 and echoed.json() == {"echo": {"a": 1}}
    assert state["echo"] == {"phase": "welcome"}
    assert state["progress"]["phase"] == "welcome"  # the shipped phases' state is all still there
    assert "welcome" in state and "check" in state and "chat" in state


def test_two_phases_with_the_same_call_are_refused(harness, clock):
    with pytest.raises(ValueError, match="api/echo"):
        new_server(harness, clock, phases=(_Echo, _Echo))


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
    for path in ["../setup_server/server.py", "%2e%2e/setup_server/server.py", "nothing.js", "page/index.html",
                 "api/nothing"]:
        assert call(page.url, path).status == 404, path
    for path in ["", "page.js", "page.css", "text.json"]:
        assert call(page.url, path).status == 200, path


def test_the_page_loads_nothing_from_the_internet(page):
    # What the script asks for is in the browser tests, which see each request it makes.
    for path in ["", "page.js", "page.css", "text.json"]:
        r = call(page.url, path)
        if path != "page.js":
            assert not re.search(rb"(?:https?:)?//[A-Za-z0-9]", r.body), path
        csp = r.headers["content-security-policy"]
        assert "default-src 'none'" in csp
        assert "http" not in csp and "*" not in csp
        assert r.headers["referrer-policy"] == "no-referrer"  # the code stays on this Mac
    for f in PAGE_DIR.iterdir():
        if f.name != "page.js":
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
    harness.desktop.languages = mac

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
    # The page's own markup; a text its script shows that isn't there throws in the browser tests.
    used = set(re.findall(r'data-text="([\w.-]+)"', (PAGE_DIR / "index.html").read_text()))
    assert used and used <= keys, used - keys
    for phase in ("welcome", "connect", "working", "check", "first-brief", "finish"):
        assert f"phase.{phase}" in keys


# ── resuming


def test_running_it_again_resumes_at_the_saved_phase(harness, clock):
    first = new_server(harness, clock)
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

    second = new_server(harness, clock)
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


@pytest.mark.macos
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

    for language, table in text.items():
        name = table["sources.button"]
        assert "{source}" in name and "{status}" in name, language
        assert name.replace("{source}", "").replace("{status}", "").strip(), language


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


# Every call that completes, skips or reopens a Source step.
SOURCE_STEPS = ("api/source", "api/gmail", "api/wilma", "api/town", "api/claude/token",
                "api/gateway", "api/codex", "api/whatsapp/check", "api/whatsapp/back",
                "api/connect/back", "api/myclub", "api/myclub/kid", "api/myclub/done")


def test_a_malformed_answer_gets_the_same_refusal_from_every_source_step(harness, page):
    save_progress(harness, {"phase": "connect", "source": "gmail"})
    before = statuses(page.url)

    shapeless = [call(page.url, path, method="POST", body=["sekret"]) for path in SOURCE_STEPS]
    extra = [call(page.url, path, method="POST", body={"sekret": 1}) for path in SOURCE_STEPS]

    assert {r.status for r in shapeless + extra} == {400}
    assert len({r.body for r in shapeless}) == 1  # the same answer, byte for byte
    answer = shapeless[0].json()
    assert answer["result"] == "invalid-answers" and answer["errors"] == ["answers: should be an object"]
    for path, r in zip(SOURCE_STEPS, extra):
        assert {k: v for k, v in r.json().items() if k != "errors"} == \
            {k: v for k, v in answer.items() if k != "errors"}, path
        assert "sekret: Extra inputs are not permitted" in r.json()["errors"], path
    assert statuses(page.url) == before


def test_any_source_can_be_opened_from_the_list(harness, page):
    r = source(page.url, "gmail", "open")

    assert r.json()["progress"]["source"] == "gmail"
    assert statuses(page.url)["gmail"] == "to-do"


def test_a_source_already_done_stays_done_when_opened_again(harness, page):
    save_progress(harness, {"phase": "connect", "source": "ai", "sources": {"gmail": "done"}})

    assert source(page.url, "gmail", "open").json()["progress"]["sources"]["gmail"] == "done"


# ── Windows (ADR 0019): the same page, without what only macOS has


# The words for what only macOS has, in each language: the Mac, its Keychain, Terminal, Finder,
# System Settings and the Passwords app, and WhatsApp for Mac.
MACOS_WORDS = {
    "en": r"\bMac\b|macOS|Keychain|Terminal|Finder|System Settings|Passwords app|WhatsApp",
    "fi": r"Mac|macOS|avainnippu|Pääte|Terminal|Järjestelmäasetukset|Salasanat-sovellu|WhatsApp",
    "zh": r"Mac|macOS|钥匙串|终端|访达|系统设置|“密码”应用|WhatsApp",
}
# Texts the page shows only on macOS, such as Claude's sign-in read from its pseudo-terminal, or
# the chat's.
MACOS_ONLY_TEXTS = re.compile(
    r"^(whatsapp\.|health\.fail\.whatsapp|finish\.fail\.whatsapp|check\.whatsapp|working\.whatsapp"
    r"|source\.whatsapp|health\.check\.whatsapp|claude\.(waiting|sign-in-failed|timeout)$|chat\."
    r"|keychain\."
    r"|open\.app-not-opened$|wilma\.passwords$|(health|finish)\.fail\.ai$"
    r"|finish\.wake\.(set|other-schedule|cancelled|no-dialog)$|finish\.replace$)"
    r"|\.keychain-not-reachable$")


def on_windows(table: dict[str, str], key: str) -> str:
    """`key`'s text as the page shows it on Windows: its `.windows` twin, where it has one."""
    return table.get(f"{key}.windows", table[key])


def test_the_state_says_which_system_and_ais_the_page_offers(page):
    state = call(page.url, "api/state").json()

    assert state["system"] == ("windows" if sys.platform == "win32" else "macos")
    assert state["ais"] == (["gateway", "codex", "claude"] if sys.platform == "win32"
                            else ["claude", "codex", "gateway"])


def test_on_windows_welcome_offers_the_gateway_first_then_chatgpt_and_claude(harness, page):
    harness.system = WINDOWS

    state = call(page.url, "api/state").json()
    assert state["system"] == "windows" and state["ais"] == ["gateway", "codex", "claude"]
    assert state["welcome"]["ai"] == "gateway"  # the default, too
    assert check_ai(page.url, "gateway").json() == {"result": "ready", "ai": "gateway"}
    assert welcome(page.url, ai="gateway").status == 200
    assert Config.load(config_file(harness)).llm.backend == "gateway"


def test_on_windows_welcome_takes_claude_before_claude_code_is_installed(harness, page, ai,
                                                                        windows_programs):
    # No line to paste: Claude's own step installs it.
    assert check_ai(page.url, "claude").json() == {"result": "not-installed", "ai": "claude"}
    assert welcome(page.url, ai="claude").status == 200
    assert Config.load(config_file(harness)).llm.backend == "claude"
    assert call(page.url, "api/chat", method="POST", body={"ai": "claude"}).status == 400


def test_on_windows_chatgpt_has_no_chat_to_continue_in_since_the_skills_are_macos_only(
        harness, page):
    harness.system = WINDOWS
    install_record.add("plugin", str(ROOT))  # the install line's copy, with the skills in it

    assert welcome(page.url, ai="codex").status == 200

    assert call(page.url, "api/chat", method="POST", body={"ai": "codex"}).status == 400
    assert not (harness.home / ".agents" / "skills").exists()
    assert call(page.url, "api/state").json()["chat"] == {}
    for language, table in json.loads((PAGE_DIR / "text.json").read_text()).items():
        assert "$parent-recap" not in table["finish.changes.codex.windows"], language


def test_on_windows_chatgpt_s_texts_say_the_page_installs_codex_in_all_three_languages():
    for language, table in json.loads((PAGE_DIR / "text.json").read_text()).items():
        for key in ("ai.codex.not-installed", "ai.codex.signed-out", "codex.not-installed"):
            assert f"{key}.windows" in table, (language, key)


def test_on_windows_claude_s_texts_say_the_page_installs_claude_code_in_all_three_languages():
    for language, table in json.loads((PAGE_DIR / "text.json").read_text()).items():
        for key in ("ai.claude.not-installed", "ai.claude.signed-out", "claude.explain",
                    "claude.window.opened"):
            assert f"{key}.windows" in table, (language, key)


def test_on_windows_no_text_the_page_shows_names_what_only_macos_has():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        for key in table:
            if key.endswith(".windows"):
                assert key.removesuffix(".windows") in table, (language, key)  # a twin of a text
                continue
            if MACOS_ONLY_TEXTS.search(key):
                continue
            said = on_windows(table, key)
            assert not re.search(MACOS_WORDS[language], said), (language, key, said)


def test_on_windows_the_steps_say_where_secrets_go_and_what_to_paste_into():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    store = {"en": "Credential Manager", "fi": "Windowsin tunnistetietojen hallin", "zh": "凭据管理器"}
    for language, table in text.items():
        for key in ("gmail.saved", "gateway.saved", "gateway.explain", "claude.saved"):
            assert store[language] in on_windows(table, key), (language, key)
        for key in ("finish.missing.installed", "finish.stopped.gateway", "wilma.ready.no-node",
                    "error.closed", "wilma.terminal", "finish.stopped.claude", "claude.paste",
                    "claude.window.opened", "claude.terminal"):
            assert "PowerShell" in on_windows(table, key), (language, key)
        # Waking the PC is the family's own setting.
        for key in ("finish.wake.not-set", "finish.missing.wake"):
            assert re.search(r"Allow wake timers|herätysajastimet|唤醒定时器",
                             on_windows(table, key)), (language, key)


def test_on_windows_the_command_says_the_powershell_window_can_be_closed(harness):
    harness.system = WINDOWS

    for language in setup_server.context.LANGUAGES:
        line = setup_server.closed_line(language)
        assert "PowerShell" in line and "Terminal" not in line, language
    harness.system = MACOS
    assert "Terminal window" in setup_server.closed_line("en")


def test_finish_names_windows_sign_in_after_updates_and_leaves_wake_timers_to_its_outcome(
        harness, page, schedule, health):
    at_finish(harness)
    health.results += [(ops.WARN, "wake", "Wake", "Allow wake timers is off, so the PC doesn't wake"),
                       (ops.WARN, "sign-in-after-update", "Sign-in after updates",
                        "nobody is signed in after an update")]

    finish(page.url)
    out = finished(page.url)

    doctor = next(o for o in out["outcomes"] if o["outcome"] == "doctor")
    # The wake outcome says what the Wake check would, so it isn't said twice.
    assert doctor == {"outcome": "doctor", "ok": True,
                      "checks": [{"check": "sign-in-after-update", "status": "warn"}]}
    for language, table in json.loads((PAGE_DIR / "text.json").read_text()).items():
        assert re.search(r"update|päivity|更新", table["finish.fail.sign-in-after-update"]), language
