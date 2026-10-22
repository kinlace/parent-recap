"""The setup page in a real browser (ADR 0024): Chromium, driven by Playwright, clicks and types as
a family does, from the language to Finish, skipping a Source, and a Gmail sign-in that fails, and
each step as the page shows it on Windows and on a Mac. Each test checks what the page shows: its
visible text and which buttons it offers. These are the page script's tests: none reads its source.

Only the browser is real. Behind the page's server everything is the harness's fakes, with the
fake wilma CLI's town list, Gmail's IMAP server and MyClub's calendar from the HTTP tests. The
page as Windows shows it is the Mac's server with the system it names changed on its way to the
page. Without Playwright or its Chromium, or where no port can be listened on, these tests skip and
say why; `-m "e2e and not browser"` runs the rest of the layer."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from typing import Any, Iterator
from urllib.parse import urlparse

import pytest

try:
    from playwright.sync_api import Error as PlaywrightError, expect, sync_playwright
except ImportError:  # the browser fixture skips the tests and says why
    sync_playwright = None

import fake_node
from family_brief import ops, setup_connect
from family_brief.collectors import myclub
from setup_page import (CAN_LISTEN, PAGE_DIR, at_finish, at_the_check_step, progress_file,
                        save_progress)
from setup_page import ai, clock, health, page  # noqa: F401
from test_setup_ai import GATEWAY_KEY
from test_setup_gmail import TYPED
from test_setup_myclub import LINK as MYCLUB_LINK
from test_setup_page_windows import Web, school_mail
from test_setup_status import install_program
from test_setup_wilma import wilma_cli  # noqa: F401

pytestmark = pytest.mark.browser

PARENT = "parent@example.com"
TEXTS = json.loads((PAGE_DIR / "text.json").read_text(encoding="utf-8"))
SYSTEM = "windows" if sys.platform == "win32" else "macos"
# Health check, the first Brief and Finish each poll every two seconds, and the steps behind them
# take a few more on a CI runner.
SLOW_MS = 30_000


def text(key: str, *, system: str = SYSTEM, language: str = "en", **values: Any) -> str:
    """The page's text for `key` in `language`, as the page fills it in on `system`."""
    table = TEXTS[language]
    shown = (system == "windows" and table.get(f"{key}.windows")) or table[key]
    return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), m.group(0))), shown)


@pytest.fixture(scope="session")
def browser() -> Iterator[Any]:
    """Headless Chromium, one for the whole run."""
    if not CAN_LISTEN:
        pytest.skip("no port can be listened on here (a sandbox), and the browser needs one")
    if sync_playwright is None:
        pytest.skip("Playwright isn't installed: pip install -c constraints.txt -e '.[test]'")
    expect.set_options(timeout=SLOW_MS)
    playwright = sync_playwright().start()
    try:
        chromium = playwright.chromium.launch()
    except PlaywrightError as e:
        playwright.stop()
        pytest.skip(f"Playwright has no Chromium (python -m playwright install --only-shell "
                    f"chromium): {str(e).splitlines()[0]}")
    yield chromium
    chromium.close()
    playwright.stop()


@pytest.fixture
def family(harness, wilma_cli, monkeypatch):  # noqa: F811
    """A Mac the install line has just put Parent Recap on, in a family whose school doesn't use
    Wilma: Gmail takes only the App Password and lists the school's senders, MyClub's calendar
    link opens, and the first Brief has the school's mail to say. Setup picked English, for the
    tests that start further on."""
    install_program(harness)
    for var in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY", "TMUX"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(myclub.requests, "get", Web().get)
    school_mail(harness)
    harness.config["summary_language"] = "en"
    return harness


@pytest.fixture
def tab(browser, family, page):  # noqa: F811
    """The setup page, opened in a browser tab as the install line opens it. Once the test is done,
    nothing the page ran has thrown, so every text it showed exists, and it asked nothing of any
    address but its own server's."""
    context = browser.new_context(locale="en-US")
    opened = context.new_page()
    thrown: list[str] = []
    elsewhere: list[str] = []
    own = urlparse(page.url).netloc
    opened.on("pageerror", lambda e: thrown.append(str(e)))

    def asked(request: Any) -> None:
        if request.url.startswith("http") and urlparse(request.url).netloc != own:
            elsewhere.append(request.url)
    opened.on("request", asked)
    opened.goto(page.url)
    yield opened
    context.close()
    assert not thrown
    assert not elsewhere


State = dict[str, Any]


def as_on(tab, system: str, change: Callable[[State], State] = lambda state: state) -> None:
    """The page loaded again as it is on `system`: the server's state says that system, with
    `change` made to it, each time the page reads it."""
    def handle(route) -> None:
        route.fulfill(json=change({**route.fetch().json(), "system": system}))
    tab.route(re.compile(r"/api/state$"), handle)
    tab.reload()


# ── The steps, as a family takes them


def button(tab, key: str, *, system: str = SYSTEM, **values: Any):
    """The button showing `key`'s text, among those the page shows."""
    return tab.get_by_role("button", name=text(key, system=system, **values), exact=True)


def heading(tab, key: str):
    return tab.get_by_role("heading", name=text(key))


def in_english(tab) -> None:
    tab.get_by_role("radio", name="English").check()
    button(tab, "continue").click()
    expect(heading(tab, "welcome.title")).to_be_visible()


def in_english_with_the_gateway(tab) -> None:
    """The language, then Welcome with the Gateway Key's AI and the Brief only for the parent."""
    in_english(tab)
    tab.get_by_role("radio", name=text("welcome.ai.gateway")).check()
    expect(tab.get_by_text(text("ai.gateway.ready"))).to_be_visible()
    # Welcome offers to add a partner first, whose address it then needs.
    tab.get_by_role("radio", name=text("welcome.partner.only-me")).check()
    expect(button(tab, "continue")).to_be_enabled()
    button(tab, "continue").click()
    expect(heading(tab, "connect.title")).to_be_visible()


def without_wilma_in_espoo(tab) -> None:
    """Wilma's step for a school without it: only the town."""
    tab.get_by_role("radio", name=text("wilma.uses.no")).check()
    town = tab.get_by_label(text("wilma.search"))
    expect(town).to_be_enabled()  # once the town list is ready
    town.fill("Espoo")
    expect(tab.get_by_role("radio", name="Espoo", exact=True)).to_be_checked()
    # Its name beside the radio button, where a long one wraps.
    expect(tab.locator("#wilma-towns label .name")).to_have_text(["Espoo"])
    button(tab, "wilma.town.save").click()
    expect(tab.get_by_text(text("wilma.town-saved"))).to_be_visible()


def sign_in_to_gmail(tab, password: str) -> None:
    """Gmail's step: the parent's address and an App Password, tested and saved."""
    tab.get_by_label(text("gmail.address")).fill(PARENT)
    tab.get_by_label(text("gmail.password")).fill(password)
    button(tab, "gmail.save").click()


def source_listed(tab, source: str, status: str, language: str = "en"):
    """The Source's entry in Connect's list, named with the status it shows."""
    name = text("sources.button", language=language,
                source=text(f"source.{source}", language=language),
                status=text(f"status.{status}", language=language))
    return tab.get_by_role("button", name=name, exact=True)


def connect_up_to_whatsapp(tab) -> None:
    """Without Wilma, then Gmail and the Gateway Key."""
    in_english_with_the_gateway(tab)
    without_wilma_in_espoo(tab)
    sign_in_to_gmail(tab, TYPED)
    expect(tab.get_by_text(text("gmail.saved"))).to_be_visible()
    expect(tab.get_by_text(text("connect.handoff", done=text("source.gmail"),
                                next=text("source.ai")))).to_be_visible()
    tab.get_by_label(text("gateway.key")).fill(GATEWAY_KEY)
    button(tab, "gateway.save").click()
    # Not the step's own result: on macOS, WhatsApp's step checks at once and says so in its place.
    expect(source_listed(tab, "ai", "done")).to_be_visible()


def connect_up_to_myclub(tab) -> None:
    """As far as WhatsApp's step, and on macOS, the only system whose page lists WhatsApp,
    WhatsApp skipped."""
    connect_up_to_whatsapp(tab)
    if sys.platform == "darwin":  # WhatsApp for Mac, only on macOS
        button(tab, "source.skip").click()
        expect(source_listed(tab, "whatsapp", "skipped")).to_be_visible()
    expect(tab.get_by_label(text("myclub.add.name"))).to_be_visible()


def at_the_ai_step(tab, harness, name: str, system: str) -> None:
    """Connect's AI step for the AI `name`, as the page shows it on `system`."""
    in_english_with_the_gateway(tab)
    save_progress(harness, {**json.loads(progress_file(harness).read_text()), "source": "ai",
                            "sources": {"wilma": "done", "gmail": "done"}})
    as_on(tab, system, lambda state: {**state, "welcome": {**state["welcome"], "ai": name}})
    expect(heading(tab, "connect.title")).to_be_visible()


# ── The tests


def test_a_family_goes_from_the_language_to_finish_in_the_browser(tab):
    connect_up_to_myclub(tab)
    tab.get_by_label(text("myclub.add.name")).fill("Mia")
    button(tab, "myclub.add").click()
    expect(tab.get_by_text(text("myclub.added"))).to_be_visible()
    expect(tab.get_by_role("radio", name=re.compile("^Mia"))).to_be_checked()
    tab.get_by_label(text("myclub.link")).fill(MYCLUB_LINK)
    button(tab, "myclub.save").click()
    expect(tab.get_by_text(text("myclub.saved"))).to_be_visible()
    for source in ("gmail", "ai", "myclub"):
        expect(source_listed(tab, source, "done")).to_be_visible()
    expect(tab.get_by_text(text("connect.done"))).to_be_visible()
    button(tab, "continue").click()

    # Working reads Gmail's senders, then the check page lists what was found.
    expect(heading(tab, "check.title")).to_be_visible()
    expect(tab.get_by_role("checkbox", name=re.compile("^Mia"))).to_be_checked()
    expect(tab.get_by_role("checkbox", name=re.compile(r"^edu\.espoo\.fi"))).to_be_checked()
    # #362: Finish says the evening confirmed here, not the one the page loaded with.
    tab.get_by_label(text("check.evening.label")).fill("16:05")
    button(tab, "check.confirm").click()

    expect(heading(tab, "brief.title")).to_be_visible()
    expect(tab.get_by_text(text("brief.made"))).to_be_visible()
    button(tab, "brief.send").click()
    expect(tab.get_by_text(text("brief.send.sent", to=PARENT))).to_be_visible()
    button(tab, "brief.done").click()

    expect(heading(tab, "finish.title")).to_be_visible()
    button(tab, "finish.start").click()
    expect(tab.get_by_text(text("finish.done", evening="16:05"))).to_be_visible()
    expect(tab.get_by_text(text("finish.closed"))).to_be_visible()
    expect(button(tab, "finish.start")).to_be_hidden()
    expect(button(tab, "connect.back")).to_be_hidden()


def test_loading_the_page_logs_no_error_in_the_console(tab):
    # Such as a refused /favicon.ico, which the browser asks for at the root, outside the code.
    errors: list[str] = []
    tab.on("console", lambda message: message.type == "error" and errors.append(message.text))
    tab.reload()
    expect(tab.get_by_role("radio", name="English")).to_be_visible()
    tab.wait_for_load_state("networkidle")
    assert not errors


def test_the_steps_are_in_the_order_the_server_sends_them(tab, monkeypatch):
    offered = setup_connect.sources()
    reordered = [offered[0], "myclub", *[s for s in offered[1:] if s != "myclub"]]
    assert reordered != offered
    monkeypatch.setattr(setup_connect, "sources", lambda: reordered)
    tab.reload()

    in_english_with_the_gateway(tab)

    expect(tab.locator("#sources li button > span:first-child")).to_have_text(
        [text(f"source.{s}") for s in reordered])
    without_wilma_in_espoo(tab)
    # Without Wilma, saving the town skips it, and MyClub's step is next, not Gmail's.
    expect(source_listed(tab, "wilma", "skipped")).to_be_visible()
    expect(tab.get_by_label(text("myclub.add.name"))).to_be_visible()
    expect(button(tab, "source.skip")).to_be_visible()
    expect(tab.get_by_label(text("gmail.password"))).to_be_hidden()


def test_a_skipped_source_is_listed_as_skipped_and_can_be_opened_again(tab):
    connect_up_to_myclub(tab)

    button(tab, "source.skip").click()

    expect(source_listed(tab, "myclub", "skipped")).to_be_visible()
    expect(tab.get_by_label(text("myclub.add.name"))).to_be_hidden()
    expect(tab.get_by_text(text("connect.done"))).to_be_visible()
    expect(button(tab, "continue")).to_be_enabled()

    source_listed(tab, "myclub", "skipped").click()

    expect(source_listed(tab, "myclub", "to-do")).to_be_visible()
    expect(tab.get_by_label(text("myclub.add.name"))).to_be_visible()
    expect(button(tab, "source.skip")).to_be_visible()
    expect(tab.get_by_text(text("connect.done"))).to_be_hidden()


def test_the_check_page_links_back_to_connect_s_list_while_a_source_is_skipped(tab):
    connect_up_to_myclub(tab)
    button(tab, "source.skip").click()
    button(tab, "continue").click()
    expect(heading(tab, "check.title")).to_be_visible()

    button(tab, "connect.back").click()

    expect(heading(tab, "connect.title")).to_be_visible()
    expect(source_listed(tab, "myclub", "skipped")).to_be_visible()


def test_an_app_password_gmail_rejects_shows_why_and_the_way_to_fix_it(tab):
    in_english_with_the_gateway(tab)
    without_wilma_in_espoo(tab)

    sign_in_to_gmail(tab, "zyxw vuts rqpo nmlk")  # an App Password, but not this address's

    expect(tab.get_by_text(text("gmail.rejected"))).to_be_visible()
    expect(button(tab, "gmail.two-step")).to_be_visible()
    expect(source_listed(tab, "gmail", "to-do")).to_be_visible()
    expect(button(tab, "gmail.save")).to_be_enabled()
    expect(tab.get_by_label(text("gmail.password"))).to_have_value("")


def test_each_language_is_offered_by_its_own_name_and_the_list_follows_a_switch(tab):
    for name in ("Suomi", "English", "中文"):
        expect(tab.get_by_role("radio", name=name, exact=True)).to_be_visible()
    in_english_with_the_gateway(tab)
    expect(source_listed(tab, "wilma", "to-do")).to_be_visible()

    tab.locator("#language-switch").select_option("fi")

    expect(source_listed(tab, "wilma", "to-do", language="fi")).to_be_visible()
    expect(source_listed(tab, "wilma", "to-do")).to_be_hidden()


def test_the_town_box_says_why_it_waits_right_under_it_while_the_wilma_cli_installs(
        tab, monkeypatch):
    go = threading.Event()
    run = subprocess.run  # the fake wilma CLI's

    def held(cmd: list[str], *a: Any, **k: Any) -> Any:
        if fake_node.is_npm(cmd):
            assert go.wait(SLOW_MS / 1000)
        return run(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", held)
    town = tab.get_by_label(text("wilma.search"))
    wait = tab.locator("#wilma-search-wait")
    status = tab.locator("#source-message")
    try:
        in_english_with_the_gateway(tab)
        expect(town).to_be_disabled()
        expect(wait).to_have_text(text("wilma.ready.installing"))
        expect(wait).to_have_class(re.compile(r"\bbusy\b"))

        # Without Wilma it's only the town list that's being got ready.
        tab.get_by_role("radio", name=text("wilma.uses.no")).check()

        expect(wait).to_have_text(text("wilma.ready.installing.town"))
        expect(status).to_have_text(text("wilma.ready.installing.town"))
        # What the parent caused meanwhile stays in the status area as the answer changes.
        button(tab, "wilma.town.save").click()
        expect(status).to_have_text(text("wilma.pick-town"))
        tab.get_by_role("radio", name=text("wilma.uses.yes")).check()
        expect(wait).to_have_text(text("wilma.ready.installing"))
        expect(status).to_have_text(text("wilma.pick-town"))
    finally:
        go.set()
    expect(town).to_be_enabled()
    expect(wait).to_be_hidden()


def test_the_town_box_says_why_it_still_waits_when_the_wilma_cli_doesn_t_install(
        tab, wilma_cli):  # noqa: F811
    wilma_cli.npm_works = False

    in_english_with_the_gateway(tab)

    wait = tab.locator("#wilma-search-wait")
    expect(wait).to_have_text(text("wilma.ready.install-failed"))
    expect(wait).not_to_have_class(re.compile(r"\bbusy\b"))
    expect(tab.get_by_label(text("wilma.search"))).to_be_disabled()
    expect(button(tab, "wilma.again")).to_be_visible()


@pytest.mark.parametrize("on", ["macos", "windows"])
def test_the_wilma_step_offers_the_passwords_app_only_on_a_mac(tab, on):
    as_on(tab, on)
    in_english_with_the_gateway(tab)

    expect(tab.get_by_label(text("wilma.username"))).to_be_visible()
    passwords = button(tab, "wilma.passwords", system=on)
    if on == "macos":
        expect(passwords).to_be_visible()
    else:
        expect(passwords).to_be_hidden()


def test_on_windows_the_page_offers_only_the_server_s_ais_in_windows_words_without_a_chat(tab):
    as_on(tab, "windows", lambda state: {**state, "ais": ["claude", "gateway"]})
    in_english(tab)

    expect(tab.get_by_text(text("welcome.intro", system="windows"))).to_be_visible()
    def choice(name: str):
        return tab.get_by_role("radio", name=text(f"welcome.ai.{name}", system="windows"))
    choice("claude").check()
    expect(choice("gateway")).to_be_visible()
    expect(choice("codex")).to_be_hidden()
    # The chat skills set up a Mac.
    expect(button(tab, "chat.continue", system="windows")).to_be_hidden()


@pytest.mark.macos
def test_continue_in_the_chat_is_offered_once_a_language_is_picked_but_not_for_the_gateway(tab):
    expect(button(tab, "chat.continue")).to_be_hidden()
    in_english(tab)

    tab.get_by_role("radio", name=text("welcome.ai.claude")).check()
    expect(button(tab, "chat.continue")).to_be_visible()
    tab.get_by_role("radio", name=text("welcome.ai.gateway")).check()
    expect(button(tab, "chat.continue")).to_be_hidden()


@pytest.mark.parametrize("on", ["macos", "windows"])
@pytest.mark.parametrize("name", ["claude", "codex"])
def test_welcome_offers_to_check_again_only_while_it_can_t_go_on(tab, ai, on, name):
    as_on(tab, on)
    in_english(tab)
    again = button(tab, "welcome.ai.again", system=on)
    go_on = button(tab, "continue", system=on)

    # Not installed: on Windows, Claude's or ChatGPT's own step installs it, so Welcome goes on.
    tab.get_by_role("radio", name=text(f"welcome.ai.{name}", system=on)).check()

    expect(tab.get_by_text(text(f"ai.{name}.not-installed", system=on))).to_be_visible()
    if on == "windows":
        expect(again).to_be_hidden()
        expect(go_on).to_be_enabled()
    else:
        expect(again).to_be_visible()
        expect(go_on).to_be_disabled()
    tab.get_by_role("radio", name=text("welcome.ai.gateway", system=on)).check()
    expect(tab.get_by_text(text("ai.gateway.ready", system=on))).to_be_visible()
    expect(again).to_be_hidden()
    expect(go_on).to_be_enabled()


@pytest.mark.parametrize("on", ["macos", "windows"])
def test_chatgpt_s_step_installs_codex_on_windows_and_offers_the_chatgpt_app_on_a_mac(
        tab, family, ai, on):
    at_the_ai_step(tab, family, "codex", on)

    expect(tab.get_by_text(text("codex.not-installed", system=on))).to_be_visible()
    install = button(tab, "codex.install", system=on)
    download = button(tab, "codex.download", system=on)
    if on == "windows":
        expect(install).to_be_visible()
        expect(download).to_be_hidden()
    else:
        expect(download).to_be_visible()
        expect(install).to_be_hidden()


@pytest.mark.parametrize("on, asks", [("macos", "api/claude"),
                                      ("windows", "api/claude/install")])
def test_claude_s_step_installs_claude_code_on_windows_and_signs_in_on_a_mac(
        tab, family, ai, on, asks):
    at_the_ai_step(tab, family, "claude", on)

    with tab.expect_request(lambda r: r.method == "POST" and r.url.endswith(asks)):
        button(tab, "claude.start", system=on).click()


@pytest.mark.macos
def test_whatsapp_s_step_names_the_python_file_finder_selects(tab):
    python = os.path.basename(ops.full_disk_access_python())

    connect_up_to_whatsapp(tab)

    expect(tab.locator("#whatsapp-explain")).to_have_text(text("whatsapp.explain", python=python))
    expect(tab.locator("#whatsapp-drag")).to_have_text(text("whatsapp.picture.drag", python=python))
    expect(tab.locator("#source-whatsapp .python-file")).to_have_text([python, python])


@pytest.mark.macos
@pytest.mark.parametrize("reason, fix", [("no-access", "whatsapp.open"),
                                         ("missing-groups", "whatsapp.back")])
def test_the_check_page_says_why_whatsapp_isn_t_ok_and_offers_the_button_that_fixes_it(
        tab, family, health, reason, fix):
    at_the_check_step(family)
    health.results = [(ops.FAIL, "gmail", "Gmail", "IMAP login failed"),
                      (ops.WARN, "whatsapp", "WhatsApp", "not OK", reason, ("3B parents",))]
    tab.reload()
    expect(heading(tab, "check.title")).to_be_visible()

    button(tab, "check.confirm").click()

    python = os.path.basename(ops.full_disk_access_python())
    said = text(f"health.fail.whatsapp.{reason}", groups="3B parents", python=python)
    expect(tab.locator("#health-checks").get_by_text(said)).to_be_visible()
    expect(button(tab, fix)).to_be_visible()
    if reason == "missing-groups":  # back to WhatsApp's step, to read the groups again
        button(tab, fix).click()
        expect(heading(tab, "connect.title")).to_be_visible()


def test_finish_moves_connect_s_sign_ins_into_changing_the_ai(tab, family, health):
    at_finish(family)
    tab.reload()
    expect(heading(tab, "finish.title")).to_be_visible()

    button(tab, "finish.ai.open").click()
    tab.get_by_role("radio", name=text("welcome.ai.gateway")).check()
    button(tab, "finish.ai.continue").click()

    expect(tab.locator("#finish-ai-sign-in").get_by_label(text("gateway.key"))).to_be_visible()
