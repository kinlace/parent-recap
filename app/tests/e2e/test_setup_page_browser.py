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
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from typing import Any, Iterator
from urllib.parse import urlparse

import pytest
import yaml

try:
    from playwright.sync_api import Error as PlaywrightError, expect, sync_playwright
except ImportError:  # the browser fixture skips the tests and says why
    sync_playwright = None

import fake_node
from conftest import CLASS_GROUP, PIANO_GROUP
from family_brief import failure, gateway, linked_device, ops, setup_connect, setup_wilma
from family_brief.collectors import myclub
from family_brief.config import Config
from fake_system import MACOS
from setup_page import (CAN_LISTEN, PAGE_DIR, at_finish, at_the_check_step, config_file,
                        more_progress, progress_file, save_progress, wait_for)
from setup_page import ai, clock, health, page, schedule  # noqa: F401
from test_setup_ai import GATEWAY_KEY
from test_setup_ai import codex  # noqa: F401
from test_setup_first_brief import at_the_first_brief
from test_setup_gmail import TYPED
from test_setup_myclub import LINK as MYCLUB_LINK, STEPS as MYCLUB_STEPS
from test_setup_page_windows import Web, school_mail
from test_setup_status import install_program
from test_setup_whatsapp import CLASS, FOOTBALL, SCANNED, at_the_whatsapp_step
from test_setup_whatsapp import phone  # noqa: F401
from test_setup_wilma import timed_out, wilma_cli  # noqa: F401

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
    """The page loaded again as it is on `system`: the server's state says that system, and the
    AIs its page installs there, with `change` made to it, each time the page reads it."""
    installs = ["claude", "codex"] if system == "windows" else ["codex"]

    def handle(route) -> None:
        route.fulfill(json=change({**route.fetch().json(), "system": system, "installs": installs}))
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


def in_english_with_the_gateway(tab, brief_to: str | None = PARENT) -> None:
    """The language, then Welcome with the Gateway Key's AI and the Brief only for the parent,
    then Connect's first step, who gets the Brief: `brief_to`, or left to the test with None."""
    in_english(tab)
    tab.get_by_role("radio", name=text("welcome.ai.gateway")).check()
    expect(tab.get_by_text(text("ai.gateway.ready"))).to_be_visible()
    # Welcome offers to add a partner first, whose address it then needs.
    tab.get_by_role("radio", name=text("welcome.partner.only-me")).check()
    expect(button(tab, "continue")).to_be_enabled()
    button(tab, "continue").click()
    expect(heading(tab, "connect.title")).to_be_visible()
    if brief_to is not None:
        brief_goes_to(tab, brief_to)


def skip(tab, source: str):
    """The "Skip <Source> for now" link the page shows."""
    return button(tab, "source.skip", source=text(f"source.{source}"))


def without_wilma_in_espoo(tab) -> None:
    """Wilma's step for a school without it: a town picked, then Wilma skipped, which only skips
    it (#481): the town is for finding the family's Wilma."""
    town = tab.get_by_label(text("wilma.search"))
    expect(town).to_be_enabled()  # once the town list is ready
    town.fill("Espoo")
    # Each entry's name beside its radio button, where a long one wraps, with its town.
    expect(tab.locator("#wilma-towns label .name")).to_have_count(4)
    # The town's own Wilma says all its schools use it, so nobody looks for their school (#688).
    expect(tab.locator("#wilma-towns label").first).to_contain_text(text("wilma.town-wide"))
    expect(tab.locator("#wilma-towns .town-wide")).to_have_count(2)
    tab.get_by_role("radio", name=re.compile("^Espoon kaupunki")).check()
    skip(tab, "wilma").click()
    expect(source_listed(tab, "wilma", "skipped")).to_be_visible()


def recipient(tab, i: int = 0, within: str = "#recipients"):
    """The address field of the `i`th row of who gets the Brief, on Connect's step or at Finish."""
    return tab.locator(within).get_by_label(text("recipients.address"), exact=True).nth(i)


def recipient_language(tab, i: int, within: str = "#recipients"):
    return tab.locator(within).get_by_label(text("recipients.language"), exact=True).nth(i)


def brief_goes_to(tab, address: str = PARENT) -> None:
    """Connect's first step: who gets the Brief, which Parent Recap sends (ADR 0032), saved with
    no key asked for, as for a Household on the gateway; then the Source list (#708)."""
    recipient(tab).fill(address)
    button(tab, "recipients.save").click()
    expect(tab.get_by_text(text("recipients.to", addresses=address))).to_be_visible()


def connect_gmail_at_last(tab) -> None:
    """Gmail's step, last on the list (#708): Connect Gmail, beside its Skip, shows the App
    Password's steps."""
    expect(tab.get_by_text(text("gmail.explain"))).to_be_visible()
    expect(skip(tab, "gmail")).to_be_visible()
    expect(tab.get_by_label(text("gmail.password"))).to_be_hidden()
    button(tab, "gmail.connect").click()
    expect(tab.get_by_label(text("gmail.password"))).to_be_visible()


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


def connect_up_to_myclub(tab) -> None:
    """Who gets the Brief, then without Wilma, then the Gateway Key, then WhatsApp skipped, which
    comes after the AI sign-in on every system (ADR 0027)."""
    in_english_with_the_gateway(tab)
    without_wilma_in_espoo(tab)
    tab.get_by_label(text("gateway.key"), exact=True).fill(GATEWAY_KEY)
    button(tab, "gateway.save").click()
    expect(tab.get_by_text(text("connect.handoff", done=text("source.ai"),
                                next=text("source.whatsapp")))).to_be_visible()
    expect(source_listed(tab, "ai", "done")).to_be_visible()
    expect(tab.get_by_text(text("whatsapp.step.link"))).to_be_visible()
    skip(tab, "whatsapp").click()
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
    # Gmail comes last (#708).
    expect(tab.get_by_text(text("connect.handoff", done=text("source.myclub"),
                                next=text("source.gmail")))).to_be_visible()
    connect_gmail_at_last(tab)
    sign_in_to_gmail(tab, TYPED)
    expect(tab.get_by_text(text("gmail.saved"))).to_be_visible()
    for source in ("ai", "myclub", "gmail"):
        expect(source_listed(tab, source, "done")).to_be_visible()
    expect(tab.get_by_text(text("connect.done"))).to_be_visible()
    button(tab, "continue").click()

    # Working reads Gmail's senders, then the check page lists what was found.
    expect(heading(tab, "check.title")).to_be_visible()
    expect(tab.get_by_role("checkbox", name=re.compile("^Mia"))).to_be_checked()
    # Without Wilma, no town is saved, so the family ticks the city's school mail itself (#481).
    school = tab.get_by_role("checkbox", name=re.compile(r"^edu\.espoo\.fi"))
    expect(school).not_to_be_checked()
    school.check()
    # #362: Finish says the evening confirmed here, not the one the page loaded with.
    tab.get_by_label(text("check.evening.label")).fill("16:05")
    button(tab, "check.confirm").click()

    expect(heading(tab, "brief.title")).to_be_visible()
    expect(tab.get_by_text(text("brief.made"))).to_be_visible()
    button(tab, "brief.send").click()
    expect(tab.get_by_text(text("brief.send.sent", to=PARENT))).to_be_visible()
    # The evening Brief turns itself on now, and the page says why macOS's dialog opens (#733).
    expect(tab.get_by_text(text("brief.turning-on"))).to_be_visible()
    button(tab, "brief.done").click()

    # Nothing to press: the last step says it's on, and when.
    expect(heading(tab, "finish.title")).to_be_visible()
    expect(tab.get_by_text(text("finish.done"))).to_be_visible()
    expect(tab.get_by_text(text("finish.on", evening="16:05"))).to_be_visible()
    expect(tab.get_by_text(text("finish.closed"))).to_be_visible()
    expect(button(tab, "finish.start")).to_be_hidden()
    expect(button(tab, "finish.again")).to_be_hidden()
    expect(button(tab, "connect.back")).to_be_hidden()


def test_a_family_that_reads_no_mail_skips_gmail_and_setup_finishes_without_it(tab, family):
    # ADR 0032: a Hotmail family, whose Brief comes from Parent Recap's own address.
    hotmail = "parent@hotmail.com"
    in_english_with_the_gateway(tab, brief_to=None)
    # Who gets the Brief is Connect's first step, before any Source and naming none (#708).
    expect(heading(tab, "recipients.title")).to_be_visible()
    expect(tab.get_by_text(text("recipients.explain"))).to_be_visible()
    expect(tab.get_by_text(text("recipients.unread"))).to_be_visible()
    expect(source_listed(tab, "wilma", "to-do")).to_be_hidden()
    expect(tab.get_by_label(text("wilma.username"))).to_be_hidden()
    expect(tab.get_by_label(text("gmail.password"))).to_be_hidden()
    expect(tab.get_by_role("button", name=re.compile(r"^Skip "))).to_have_count(0)
    expect(tab.get_by_text(text("recipients.junk"))).to_be_hidden()
    # A page loaded again before they're saved comes back to this step.
    tab.reload()
    expect(heading(tab, "recipients.title")).to_be_visible()
    expect(source_listed(tab, "wilma", "to-do")).to_be_hidden()
    recipient(tab).fill(hotmail)
    expect(tab.get_by_text(text("recipients.junk"))).to_be_visible()

    button(tab, "recipients.save").click()

    expect(tab.get_by_text(text("recipients.to", addresses=hotmail))).to_be_visible()
    expect(heading(tab, "recipients.title")).to_be_hidden()
    expect(source_listed(tab, "wilma", "to-do")).to_be_visible()
    without_wilma_in_espoo(tab)
    tab.get_by_label(text("gateway.key"), exact=True).fill(GATEWAY_KEY)
    button(tab, "gateway.save").click()
    expect(source_listed(tab, "ai", "done")).to_be_visible()
    skip(tab, "whatsapp").click()
    tab.get_by_label(text("myclub.add.name")).fill("Mia")
    button(tab, "myclub.add").click()
    expect(tab.get_by_role("radio", name=re.compile("^Mia"))).to_be_checked()
    tab.get_by_label(text("myclub.link")).fill(MYCLUB_LINK)
    button(tab, "myclub.save").click()
    expect(tab.get_by_text(text("gmail.explain"))).to_be_visible()
    # Loaded again, the page is back at Gmail, the step reached, not at who gets the Brief.
    tab.reload()
    expect(source_listed(tab, "myclub", "done")).to_be_visible()
    expect(heading(tab, "recipients.title")).to_be_hidden()
    # Gmail, last, says what it reads, with Connect and Skip side by side and no App Password.
    expect(tab.get_by_text(text("gmail.explain"))).to_be_visible()
    expect(button(tab, "gmail.connect")).to_be_visible()
    expect(tab.get_by_label(text("gmail.password"))).to_be_hidden()
    expect(tab.get_by_text(text("gmail.open"))).to_be_hidden()
    for language in ("fi", "zh", "en"):
        tab.locator("#language-switch").select_option(language)
        for key, values in (("gmail.connect", {}), ("source.skip", {"source": "Gmail"})):
            expect(tab.get_by_role("button", name=text(key, language=language, **values),
                                   exact=True)).to_be_visible()
    skip(tab, "gmail").click()
    expect(source_listed(tab, "gmail", "skipped")).to_be_visible()
    expect(tab.get_by_text(text("connect.done"))).to_be_visible()
    button(tab, "continue").click()

    # No Gmail, so no senders to tick.
    expect(heading(tab, "check.title")).to_be_visible()
    expect(tab.get_by_text(text("check.senders"), exact=True)).to_be_hidden()
    button(tab, "check.confirm").click()

    expect(tab.get_by_text(text("brief.made"))).to_be_visible()
    button(tab, "brief.send").click()
    expect(tab.get_by_text(text("brief.send.sent", to=hotmail))).to_be_visible()
    expect(tab.get_by_text(text("brief.junk"))).to_be_visible()
    assert [e.to for e in family.sent] == [[hotmail]]
    button(tab, "brief.done").click()

    button(tab, "finish.start").click()
    expect(tab.get_by_text(text("finish.done", evening="21:00"))).to_be_visible()
    assert Config.load(config_file(family)).gmail.username is None


def test_gmail_opened_again_after_it_was_skipped_is_only_read(tab, family):
    in_english_with_the_gateway(tab, "parent@hotmail.com")
    source_listed(tab, "gmail", "to-do").click()
    skip(tab, "gmail").click()
    expect(source_listed(tab, "gmail", "skipped")).to_be_visible()

    source_listed(tab, "gmail", "skipped").click()

    # Who gets the Brief stays as saved, and can be changed from the list.
    expect(tab.get_by_text(text("recipients.to", addresses="parent@hotmail.com"))).to_be_visible()
    connect_gmail_at_last(tab)
    sign_in_to_gmail(tab, TYPED)
    expect(source_listed(tab, "gmail", "done")).to_be_visible()
    cfg = Config.load(config_file(family))
    assert cfg.gmail.username == PARENT
    assert [t.address for t in cfg.email.to] == ["parent@hotmail.com"]


def test_who_gets_the_brief_can_be_changed_from_the_source_list(tab, family):
    in_english_with_the_gateway(tab, "parent@hotmail.com")

    button(tab, "recipients.change").click()

    # Their own step again, with the Source list out of the way.
    expect(source_listed(tab, "wilma", "to-do")).to_be_hidden()
    expect(tab.get_by_label(text("wilma.username"))).to_be_hidden()
    address = recipient(tab)
    expect(address).to_have_value("parent@hotmail.com")
    brief_goes_to(tab, "parent@gmail.com")
    assert [t.address for t in Config.load(config_file(family)).email.to] == ["parent@gmail.com"]
    expect(tab.get_by_label(text("wilma.username"))).to_be_visible()
    # The Brief's own address is the likeliest Gmail to read, suggested only on Gmail's step.
    source_listed(tab, "gmail", "to-do").click()
    connect_gmail_at_last(tab)
    expect(tab.get_by_label(text("gmail.address"))).to_have_value("parent@gmail.com")


def test_after_only_me_on_welcome_who_gets_the_brief_adds_as_many_as_the_gateway_binds(tab, family):
    # #638, #699: Welcome said "Only me", and anyone else's address goes in on Connect's step for
    # who gets the Brief, in rows added one by one.
    in_english_with_the_gateway(tab, brief_to=None)
    rows = tab.locator("#recipients-rows .recipient")
    expect(rows).to_have_count(1)
    expect(button(tab, "recipients.remove")).to_be_hidden()  # at least one

    button(tab, "recipients.add").click()
    button(tab, "recipients.add").click()

    expect(rows).to_have_count(3)
    expect(recipient(tab, 2)).to_be_focused()
    expect(button(tab, "recipients.remove")).to_have_count(3)
    recipient(tab, 0).fill("parent@hotmail.com")
    recipient(tab, 1).fill("partner@gmail.com")
    recipient_language(tab, 1).select_option("fi")
    recipient(tab, 2).fill("grandma@example.fi")
    recipient_language(tab, 2).select_option("zh")
    button(tab, "recipients.save").click()
    expect(tab.get_by_text(text("recipients.to", addresses="parent@hotmail.com, "
                                "partner@gmail.com, grandma@example.fi"))).to_be_visible()
    to = Config.load(config_file(family)).email.to
    assert [(t.address, t.language) for t in to] == [
        ("parent@hotmail.com", None), ("partner@gmail.com", "fi"), ("grandma@example.fi", "zh")]

    # Up to the most the gateway binds a key to (ADR 0033), and no more.
    button(tab, "recipients.change").click()
    for _ in range(3):
        button(tab, "recipients.add").click()
    expect(rows).to_have_count(6)
    expect(button(tab, "recipients.add")).to_be_hidden()
    button(tab, "recipients.remove").last.click()
    expect(button(tab, "recipients.add")).to_be_visible()


def test_a_row_added_by_mistake_may_be_left_empty(tab, family):
    in_english_with_the_gateway(tab, brief_to=None)
    button(tab, "recipients.add").click()

    brief_goes_to(tab, "parent@hotmail.com")

    assert [t.address for t in Config.load(config_file(family)).email.to] == ["parent@hotmail.com"]


def test_welcome_s_partner_is_the_second_row_and_the_first_may_be_removed(tab, family):
    # #699: the setup parent's own address isn't forced in first.
    in_english(tab)
    tab.get_by_role("radio", name=text("welcome.ai.gateway")).check()
    tab.get_by_role("radio", name=text("welcome.partner.add")).check()
    tab.get_by_label(text("welcome.partner.address")).fill("partner@gmail.com")
    tab.get_by_label(text("welcome.partner.language")).select_option("fi")
    button(tab, "continue").click()
    expect(heading(tab, "connect.title")).to_be_visible()
    expect(recipient(tab, 0)).to_have_value("")
    expect(recipient(tab, 1)).to_have_value("partner@gmail.com")
    expect(recipient_language(tab, 1)).to_have_value("fi")

    button(tab, "recipients.remove").first.click()

    expect(tab.locator("#recipients-rows .recipient")).to_have_count(1)
    expect(button(tab, "recipients.remove")).to_be_hidden()
    button(tab, "recipients.save").click()
    expect(tab.get_by_text(text("recipients.to", addresses="partner@gmail.com"))).to_be_visible()
    to = Config.load(config_file(family)).email.to
    assert [(t.address, t.language) for t in to] == [("partner@gmail.com", "fi")]


def test_recipients_added_in_the_chat_are_rows_the_page_changes(tab, family):
    in_english_with_the_gateway(tab, "parent@hotmail.com")
    # A second and third added in the chat (manage), or by hand.
    saved = yaml.safe_load(config_file(family).read_text())
    saved["email"]["to"] += [{"address": "partner@gmail.com", "language": "fi"},
                             "grandma@example.fi"]
    config_file(family).write_text(yaml.safe_dump(saved, allow_unicode=True, sort_keys=False))
    tab.reload()
    expect(tab.get_by_text(text("recipients.to", addresses="parent@hotmail.com, "
                                "partner@gmail.com, grandma@example.fi"))).to_be_visible()

    button(tab, "recipients.change").click()
    expect(tab.locator("#recipients-rows .recipient")).to_have_count(3)
    recipient(tab, 1).fill("partner@outlook.com")
    button(tab, "recipients.save").click()

    expect(tab.get_by_text(text("recipients.to", addresses="parent@hotmail.com, "
                                "partner@outlook.com, grandma@example.fi"))).to_be_visible()
    to = Config.load(config_file(family)).email.to
    assert [(t.address, t.language) for t in to] == [
        ("parent@hotmail.com", None), ("partner@outlook.com", "fi"), ("grandma@example.fi", None)]


def test_with_the_gateway_as_the_ai_no_key_is_asked_for_sending(tab):
    in_english_with_the_gateway(tab, brief_to=None)

    expect(tab.get_by_text(text("recipients.unread"))).to_be_visible()
    expect(tab.locator("#recipients").get_by_label(text("recipients.key"))).to_be_hidden()
    expect(button(tab, "recipients.save")).to_be_visible()


@pytest.mark.macos
def test_with_codex_parent_recap_gets_a_key_for_sending_and_setup_finishes(tab, family,
                                                                                codex):
    # #565: a Household whose AI is its own Codex, which gets a Gateway Key for sending alone
    # from the gateway (#618).
    hotmail = "parent@hotmail.com"
    # Codex answers doctor's test call, made through the model as the Brief is, as it's asked to.
    brief = family.model_reply
    family.model_reply = lambda prompt: {"ok": True} if '{"ok": true}' in prompt else brief
    in_english(tab)
    tab.get_by_role("radio", name=text("welcome.ai.codex")).check()
    expect(tab.get_by_text(text("ai.codex.ready"))).to_be_visible()
    tab.get_by_role("radio", name=text("welcome.partner.only-me")).check()
    button(tab, "continue").click()
    expect(heading(tab, "connect.title")).to_be_visible()

    expect(tab.get_by_text(text("recipients.unread"))).to_be_visible()
    recipient(tab).fill(hotmail)
    # A key from the team can be pasted in the fold; one the relay doesn't know is said as the
    # gateway AI's field says it, and not kept.
    expect(tab.get_by_text(text("recipients.key.explain"))).to_be_hidden()
    tab.get_by_text(text("recipients.key.have")).click()
    expect(tab.get_by_text(text("recipients.key.explain"))).to_be_visible()
    key = tab.locator("#recipients").get_by_label(text("recipients.key"), exact=True)
    family.relay_answer = (401, {"error": "missing, unknown or revoked Gateway Key"})
    key.fill(GATEWAY_KEY)
    button(tab, "recipients.save").click()
    expect(tab.get_by_text(text("gateway.rejected"))).to_be_visible()
    expect(key).to_have_value("")
    expect(recipient(tab)).to_be_visible()  # still who gets the Brief
    # Without one, saving gets a send-only key from the gateway, which mails a link (ADR 0033).
    family.relay_answer = (200, {"sent": 1, "refused": 0})
    button(tab, "recipients.save").click()
    expect(tab.get_by_text(text("recipients.to", addresses=hotmail))).to_be_visible()
    expect(tab.get_by_text(text("gmail.confirm.sent", addresses=hotmail))).to_be_visible()
    assert family.confirmations == [(hotmail, "en")]
    button(tab, "gmail.confirm.resend").click()
    expect(tab.get_by_text(text("gmail.confirm.resent", addresses=hotmail))).to_be_visible()
    assert family.confirmations == [(hotmail, "en")] * 2
    family.confirm(hotmail)
    without_wilma_in_espoo(tab)
    # Codex's own sign-in, unchanged: it's signed in already, which its step checks at once.
    expect(source_listed(tab, "ai", "done")).to_be_visible()
    skip(tab, "whatsapp").click()
    tab.get_by_label(text("myclub.add.name")).fill("Mia")
    button(tab, "myclub.add").click()
    expect(tab.get_by_role("radio", name=re.compile("^Mia"))).to_be_checked()
    tab.get_by_label(text("myclub.link")).fill(MYCLUB_LINK)
    button(tab, "myclub.save").click()
    skip(tab, "gmail").click()
    expect(source_listed(tab, "gmail", "skipped")).to_be_visible()
    expect(tab.get_by_text(text("connect.done"))).to_be_visible()
    button(tab, "continue").click()

    expect(heading(tab, "check.title")).to_be_visible()
    button(tab, "check.confirm").click()
    expect(tab.get_by_text(text("brief.made"))).to_be_visible()
    button(tab, "brief.send").click()
    expect(tab.get_by_text(text("brief.send.sent", to=hotmail))).to_be_visible()
    assert [e.to for e in family.sent] == [[hotmail]]
    button(tab, "brief.done").click()

    button(tab, "finish.start").click()
    expect(tab.get_by_text(text("finish.done", evening="21:00"))).to_be_visible()
    cfg = Config.load(config_file(family))
    assert (cfg.llm.backend, cfg.gmail.username) == ("codex", None)
    assert family.secrets[gateway.ACCOUNT] == next(iter(family.made_keys))
    assert family.model.reached and "gateway.parentrecap.com" not in family.model.reached


# ── Each Recipient's confirmation as it happens (#712): the page asks every few seconds, and
# asking mails nobody.

PARTNER = "partner@example.com"
# The page asks every five seconds: a few rounds of it.
ASKING_MS = 12_000


def bound_key(family, *addresses: str) -> None:
    """A Gateway Key the gateway made on request (ADR 0033), bound to `addresses`, none of whom
    has confirmed yet."""
    key = "sk-bf-made-on-request-712"
    family.made_keys[key] = [{"address": a, "language": "en"} for a in addresses]
    family.secrets[gateway.ACCOUNT] = key


def asked_no_more(tab, family) -> None:
    """The page has stopped asking the gateway who confirmed."""
    asked = len(family.binds)
    tab.wait_for_timeout(ASKING_MS)
    assert len(family.binds) == asked


@pytest.mark.macos
def test_who_gets_the_brief_shows_each_recipient_confirm_by_itself_and_never_holds_setup_back(
        tab, family, codex):  # noqa: F811
    in_english(tab)
    tab.get_by_role("radio", name=text("welcome.ai.codex")).check()
    expect(tab.get_by_text(text("ai.codex.ready"))).to_be_visible()
    tab.get_by_role("radio", name=text("welcome.partner.only-me")).check()
    button(tab, "continue").click()
    expect(heading(tab, "connect.title")).to_be_visible()
    recipient(tab).fill(PARENT)
    button(tab, "recipients.add").click()
    recipient(tab, 1).fill(PARTNER)

    button(tab, "recipients.save").click()

    # Under who gets the Brief, above the Source list (#708).
    waiting = tab.locator("#recipients-confirmed-list li")
    expect(waiting).to_have_text([text("recipients.confirm.waiting", address=PARENT),
                                  text("recipients.confirm.waiting", address=PARTNER)])
    expect(tab.get_by_text(text("recipients.confirm.later"))).to_be_visible()
    # Nothing waits for it: the first Source's step goes on at once, and the list stays current.
    without_wilma_in_espoo(tab)
    expect(source_listed(tab, "wilma", "skipped")).to_be_visible()
    family.confirm(PARTNER)
    expect(waiting).to_have_text([text("recipients.confirm.waiting", address=PARENT),
                                  text("recipients.confirm.done", address=PARTNER)])
    family.confirm(PARENT)
    expect(waiting).to_have_text([text("recipients.confirm.done", address=PARENT),
                                  text("recipients.confirm.done", address=PARTNER)])
    expect(tab.get_by_text(text("gmail.confirm.confirmed"))).to_be_visible()
    expect(tab.get_by_text(text("recipients.confirm.later"))).to_be_hidden()
    # Once everyone has, the page stops asking; asking mailed nobody.
    asked_no_more(tab, family)
    assert family.confirmations == [(PARENT, "en"), (PARTNER, "en")]
    assert all(b["resend"] is False for b in family.binds)


def test_with_the_gateway_as_the_ai_who_gets_the_brief_shows_no_confirmation(tab, family):
    # That the page asks the gateway nothing is test_send_key_on_request.py's.
    in_english_with_the_gateway(tab)
    skip(tab, "wilma").click()

    expect(tab.locator("#recipients-confirmed")).to_be_hidden()
    expect(tab.locator("#recipients-confirm")).to_be_hidden()
    assert family.key_requests == []


def test_the_first_brief_reminds_the_parent_to_confirm_until_they_have(tab, family):
    at_the_first_brief(family)
    bound_key(family, PARENT, PARTNER)
    tab.reload()
    reminder = tab.get_by_text(text("brief.confirm.reminder", to=PARENT))

    # Before "Send it to me" is pressed, and above it.
    expect(reminder).to_be_visible()
    expect(tab.get_by_text(text("brief.made"))).to_be_visible()
    assert tab.locator("#brief-reminder ~ #brief-actions").count() == 1
    tab.locator("#brief-reminder").get_by_role("button", name=text("gmail.confirm.resend")).click()
    expect(tab.get_by_text(text("gmail.confirm.resent", addresses=f"{PARENT}, {PARTNER}"))).to_be_visible()
    family.confirm(PARENT)
    expect(reminder).to_be_hidden()
    button(tab, "brief.send").click()
    expect(tab.get_by_text(text("brief.send.sent", to=PARENT))).to_be_visible()
    # The partner's confirmation doesn't matter here: the page asks no more.
    asked_no_more(tab, family)


def test_a_closed_password_window_leaves_the_evening_brief_on_and_finish_offers_the_wake_up(
        tab, family, health):  # noqa: F811
    # #733: the evening Brief turns itself on once the first Brief is sent, on a Mac with its
    # wake-up through macOS's dialog, which the parent closes.
    family.system = MACOS
    at_the_first_brief(family)
    family.wake.dialog = "cancel"
    tab.reload()
    expect(tab.get_by_text(text("brief.made", system="macos"))).to_be_visible()
    button(tab, "brief.send", system="macos").click()
    expect(tab.get_by_text(text("brief.turning-on", system="macos"))).to_be_visible()
    button(tab, "brief.done", system="macos").click()

    expect(heading(tab, "finish.title")).to_be_visible()
    expect(tab.get_by_text(text("finish.wake.cancelled", system="macos"))).to_be_visible()
    expect(tab.get_by_text(text("finish.on", system="macos", evening="21:00"))).to_be_visible()
    expect(button(tab, "finish.start", system="macos")).to_be_hidden()
    family.wake.dialog = "allow"
    button(tab, "finish.retry", system="macos").click()

    expect(tab.locator("#finish-status")).to_have_attribute("data-result", "finish.done")
    expect(button(tab, "finish.retry", system="macos")).to_be_hidden()


def test_finish_names_each_recipient_who_won_t_get_tonight_s_brief(tab, family, health):  # noqa: F811
    at_finish(family)
    bound_key(family, PARENT, PARTNER)
    family.confirm(PARENT)
    tab.reload()
    partner = tab.get_by_text(text("finish.unconfirmed", address=PARTNER))

    expect(partner).to_be_visible()
    expect(tab.get_by_text(text("finish.unconfirmed", address=PARENT))).to_be_hidden()
    family.confirm(PARTNER)
    expect(partner).to_be_hidden()
    asked_no_more(tab, family)


def test_finish_names_nobody_while_the_brief_goes_from_the_family_s_gmail(tab, family, health):  # noqa: F811
    # ADR 0034: nobody confirms anything for the Household's own Gmail.
    family.config["email"]["from_gmail"] = True
    at_finish(family)
    family.secrets["gmail-imap-parent@example.com"] = "abcdefghijklmnop"
    bound_key(family, PARENT, PARTNER)
    tab.reload()

    expect(tab.get_by_text(text("finish.sending.current.gmail", address=PARENT))).to_be_visible()
    expect(tab.locator("#finish-unconfirmed")).to_be_hidden()
    # Back on the gateway, the partner who hasn't confirmed is named, by itself.
    button(tab, "finish.sending.off").click()
    expect(tab.get_by_text(text("finish.unconfirmed", address=PARTNER))).to_be_visible()


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
    # Without Wilma, MyClub's step is next, not the AI sign-in.
    expect(tab.get_by_label(text("myclub.add.name"))).to_be_visible()
    expect(skip(tab, "myclub")).to_be_visible()
    expect(tab.get_by_label(text("gateway.key"), exact=True)).to_be_hidden()


def test_a_skipped_source_is_listed_as_skipped_and_can_be_opened_again(tab):
    connect_up_to_myclub(tab)

    skip(tab, "myclub").click()

    expect(source_listed(tab, "myclub", "skipped")).to_be_visible()
    expect(tab.get_by_label(text("myclub.add.name"))).to_be_hidden()
    skip(tab, "gmail").click()
    expect(tab.get_by_text(text("connect.done"))).to_be_visible()
    expect(button(tab, "continue")).to_be_enabled()

    source_listed(tab, "myclub", "skipped").click()

    expect(source_listed(tab, "myclub", "to-do")).to_be_visible()
    expect(tab.get_by_label(text("myclub.add.name"))).to_be_visible()
    expect(skip(tab, "myclub")).to_be_visible()
    expect(tab.get_by_text(text("connect.done"))).to_be_hidden()


def test_myclub_s_link_is_shown_on_its_own_page_in_each_language_and_at_phone_width(tab):
    connect_up_to_myclub(tab)

    for language in ("en", "fi", "zh"):
        tab.locator("#language-switch").select_option(language)
        where = tab.locator("#source-myclub summary")
        expect(where).to_have_text(text("myclub.where", language=language))
        if not tab.locator("#source-myclub details").evaluate("d => d.open"):
            where.click()
        picture = tab.get_by_role("img", name=text("myclub.picture.alt", language=language))
        expect(picture).to_be_visible()
        assert picture.evaluate("img => img.complete && img.naturalWidth") > 0  # served, decoded
        expect(tab.locator("#source-myclub details ol li")).to_have_text(
            [text(f"myclub.picture.{s}", language=language) for s in MYCLUB_STEPS])
        for note in ("sign-in", "language", "one-link"):
            expect(tab.get_by_text(text(f"myclub.picture.{note}", language=language))).to_be_visible()

    # On a phone the picture shrinks to the screen.
    tab.set_viewport_size({"width": 375, "height": 812})
    picture = tab.locator("#source-myclub .screenshot img").bounding_box()
    assert picture["x"] >= 0 and picture["x"] + picture["width"] <= 375
    assert tab.evaluate("document.documentElement.scrollWidth") <= 375


def test_the_check_page_links_back_to_connect_s_list_while_a_source_is_skipped(tab):
    connect_up_to_myclub(tab)
    skip(tab, "myclub").click()
    skip(tab, "gmail").click()
    button(tab, "continue").click()
    expect(heading(tab, "check.title")).to_be_visible()

    button(tab, "connect.back").click()

    expect(heading(tab, "connect.title")).to_be_visible()
    expect(source_listed(tab, "myclub", "skipped")).to_be_visible()


def test_each_source_a_family_can_do_without_offers_skip_beside_its_main_button(tab):
    # #491: Wilma, WhatsApp, MyClub and Gmail each say "Skip <Source> for now" beside the step's
    # main button. Who gets the Brief, Connect's first step (#708), and the AI sign-in can't be
    # skipped.
    in_english_with_the_gateway(tab, brief_to=None)
    expect(recipient(tab)).to_be_visible()
    expect(tab.get_by_role("button", name=re.compile(r"^Skip "))).to_have_count(0)
    brief_goes_to(tab)
    actions = tab.locator("#wilma-form .actions")
    expect(actions.get_by_role("button", name=text("wilma.sign-in"))).to_be_visible()
    expect(actions.get_by_role("button", name=text("source.skip", source="Wilma"))).to_be_visible()

    skip(tab, "wilma").click()

    expect(source_listed(tab, "wilma", "skipped")).to_be_visible()
    expect(tab.get_by_label(text("gateway.key"), exact=True)).to_be_visible()
    expect(tab.get_by_role("button", name=re.compile(r"^Skip "))).to_have_count(0)
    tab.get_by_label(text("gateway.key"), exact=True).fill(GATEWAY_KEY)
    button(tab, "gateway.save").click()
    expect(source_listed(tab, "ai", "done")).to_be_visible()
    # Without the WhatsApp library installed here, the step says so beside its Skip.
    expect(tab.get_by_text(text("whatsapp.no-library"))).to_be_visible()
    skip(tab, "whatsapp").click()
    expect(source_listed(tab, "whatsapp", "skipped")).to_be_visible()
    # Without Wilma's Kids, MyClub first asks for them, with Skip beside Add the Kid.
    kid_form = tab.locator("#myclub-kid-form .actions")
    expect(kid_form.get_by_role("button", name=text("myclub.add"))).to_be_visible()
    expect(kid_form.get_by_role("button", name=text("source.skip", source="MyClub"))).to_be_visible()
    expect(tab.get_by_role("button", name=re.compile(r"^Skip "))).to_have_count(1)
    skip(tab, "myclub").click()
    # Gmail's Connect and Skip side by side, the same kind of button (#708).
    actions = tab.locator("#source-gmail > .actions")
    connect = actions.get_by_role("button", name=text("gmail.connect"))
    skip_gmail = actions.get_by_role("button", name=text("source.skip", source="Gmail"))
    expect(connect).to_be_visible()
    expect(skip_gmail).to_be_visible()
    assert connect.get_attribute("class") == skip_gmail.get_attribute("class")
    expect(tab.get_by_role("button", name=re.compile(r"^Skip "))).to_have_count(1)
    connect.click()
    expect(button(tab, "gmail.save")).to_be_visible()
    expect(skip_gmail).to_be_visible()
    sign_in_to_gmail(tab, TYPED)
    expect(source_listed(tab, "gmail", "done")).to_be_visible()
    expect(tab.get_by_role("button", name=re.compile(r"^Skip "))).to_have_count(0)


def sign_in_to_wilma(tab, town: str, entry: str, username: str, password: str) -> None:
    """Wilma's step: the town's Wilma picked from the list, then the guardian's login."""
    search = tab.get_by_label(text("wilma.search"))
    expect(search).to_be_enabled()  # once the town list is ready
    search.fill(town)
    tab.get_by_role("radio", name=re.compile(f"^{entry}")).check()
    tab.get_by_label(text("wilma.username")).fill(username)
    tab.get_by_label(text("wilma.password")).fill(password)
    button(tab, "wilma.sign-in").click()


def test_a_family_adds_a_second_wilma_and_removes_it(tab, family, wilma_cli):  # noqa: F811
    # #647: Kids at schools in two towns, each on its town's Wilma.
    espoo, helsinki = "https://espoo.inschool.fi", "https://helsinki.inschool.fi"
    wilma_cli.accounts = {f"{espoo}|mia.parent": "Salasana-1", f"{helsinki}|leo.parent": "Salasana-2"}
    wilma_cli.students_of = {f"{espoo}|mia.parent": [{"studentNumber": "1001", "name": "Mia Virtanen"}],
                             f"{helsinki}|leo.parent": [{"studentNumber": "2001", "name": "Leo Virtanen"}]}
    wilma_cli.install()
    in_english_with_the_gateway(tab)
    signins = tab.locator("#wilma-signin-list li")
    sign_in_to_wilma(tab, "Espoo", "Espoon kaupunki", "mia.parent", "Salasana-1")
    expect(source_listed(tab, "wilma", "done")).to_be_visible()

    # Opened again from the list, the step lists the Wilma with its Kids, and offers another.
    source_listed(tab, "wilma", "done").click()
    expect(signins).to_have_count(1)
    expect(signins.first).to_contain_text("mia.parent")
    expect(signins.first).to_contain_text("Mia Virtanen")
    expect(button(tab, "wilma.sign-in")).to_be_hidden()
    expect(button(tab, "wilma.remove")).to_have_count(0)  # the only one is skipped, not removed
    button(tab, "wilma.add").click()
    sign_in_to_wilma(tab, "Helsinki", "Helsingin kaupunki", "leo.parent", "Salasana-2")
    expect(tab.get_by_text(text("wilma.signed-in"))).to_be_visible()
    assert [k.name for k in Config.load(config_file(family)).kids] == ["Mia Virtanen", "Leo Virtanen"]

    source_listed(tab, "wilma", "done").click()
    expect(signins).to_have_count(2)
    signins.filter(has_text="leo.parent").get_by_role("button", name=text("wilma.remove")).click()

    expect(tab.get_by_text(text("wilma.removed"))).to_be_visible()
    expect(signins).to_have_count(1)
    expect(signins.first).to_contain_text("mia.parent")
    assert [k.name for k in Config.load(config_file(family)).kids] == ["Mia Virtanen"]


def test_skipping_wilma_with_a_town_picked_saves_no_city(tab, family):
    in_english_with_the_gateway(tab)

    without_wilma_in_espoo(tab)

    expect(tab.get_by_label(text("gateway.key"), exact=True)).to_be_visible()
    progress = json.loads(progress_file(family).read_text())
    assert progress["sources"]["wilma"] == "skipped"
    cfg = Config.load(config_file(family))
    assert cfg.city is None and not cfg.wilma.enabled


def test_the_app_passwords_button_asks_for_the_gmail_address_first(tab, family):
    # #501: the page opens in the family's Gmail, so the address comes before the button.
    in_english_with_the_gateway(tab)
    source_listed(tab, "gmail", "to-do").click()
    connect_gmail_at_last(tab)
    address = tab.get_by_label(text("gmail.address"))
    expect(tab.get_by_text(text("gmail.account"))).to_be_visible()
    # #528: where the first outside tester got stuck, said before the parent goes to Google, in
    # one line with the rest folded under it (#708).
    expect(tab.get_by_text(text("gmail.before"))).to_be_visible()
    expect(tab.get_by_text(text("gmail.before.reset"))).to_be_hidden()
    tab.get_by_text(text("gmail.before")).click()
    expect(tab.get_by_text(text("gmail.before.reset"))).to_be_visible()
    address.fill("")

    button(tab, "gmail.open").click()

    expect(tab.get_by_text(text("gmail.no-address"))).to_be_visible()
    expect(address).to_have_attribute("aria-invalid", "")
    expect(address).to_be_focused()
    assert family.desktop.opened == []

    address.fill(PARENT)
    button(tab, "gmail.open").click()

    wait_for(lambda: family.desktop.opened == [
        "https://myaccount.google.com/apppasswords?authuser=parent%40example.com"])
    expect(address).not_to_have_attribute("aria-invalid", "")
    expect(tab.get_by_text(text("gmail.no-address"))).to_be_hidden()


def test_an_app_password_gmail_rejects_shows_why_and_the_way_to_fix_it(tab):
    in_english_with_the_gateway(tab)
    without_wilma_in_espoo(tab)
    source_listed(tab, "gmail", "to-do").click()
    connect_gmail_at_last(tab)

    sign_in_to_gmail(tab, "zyxw vuts rqpo nmlk")  # an App Password, but not this address's

    expect(tab.get_by_text(text("gmail.rejected"))).to_be_visible()
    # #491: right under the field Gmail turned down, in red, and the field marked.
    status = tab.locator("#gmail-form .slot > #source-status")
    expect(status).to_have_class(re.compile(r"\bfail\b"))
    expect(status).to_have_attribute("role", "alert")
    expect(tab.get_by_label(text("gmail.password"))).to_have_attribute("aria-invalid", "")
    expect(button(tab, "gmail.two-step")).to_be_visible()
    # Gmail can be skipped, for a family whose school mail doesn't come there (ADR 0032).
    expect(skip(tab, "gmail")).to_be_visible()
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
    try:
        in_english_with_the_gateway(tab)
        expect(town).to_be_disabled()
        expect(wait).to_have_text(text("wilma.ready.installing"))
        expect(wait).to_have_class(re.compile(r"\bbusy\b"))
        # Said once, under the box: nothing else says it meanwhile.
        expect(tab.locator("#source-status")).to_be_hidden()
        # A town picked too early is said right under the box.
        button(tab, "wilma.sign-in").click()
        expect(tab.locator("#wilma-town-slot #source-message")).to_be_hidden()  # fields first
        tab.get_by_label(text("wilma.username")).fill("parent")
        tab.get_by_label(text("wilma.password")).fill("secret")
        button(tab, "wilma.sign-in").click()
        expect(tab.locator("#wilma-town-slot #source-message")).to_have_text(text("wilma.pick-town"))
    finally:
        go.set()
    expect(town).to_be_enabled()
    expect(wait).to_be_hidden()


def test_a_wilma_program_that_doesn_t_install_offers_to_skip_wilma_and_go_on(
        tab, wilma_cli):  # noqa: F811
    # #491: a macOS tester got stuck here. The box under the town says so, in red, with Skip and
    # Try again, in place of the sign-in that can't work, and Skip moves Connect on.
    wilma_cli.npm_works = False

    in_english_with_the_gateway(tab)

    status = tab.locator("#wilma-town-slot > #source-status")
    expect(status).to_be_visible()
    expect(status).to_have_class(re.compile(r"\bfail\b"))
    expect(status.locator("#source-message")).to_have_text(text("wilma.ready.install-failed"))
    expect(tab.locator("#wilma-search-wait")).to_be_hidden()
    expect(tab.get_by_label(text("wilma.search"))).to_be_disabled()
    expect(tab.get_by_label(text("wilma.username"))).to_be_hidden()
    expect(status.get_by_role("button", name=text("wilma.again"))).to_be_visible()
    skip_wilma = status.get_by_role("button", name=text("source.skip", source="Wilma"))
    expect(skip_wilma).to_be_visible()

    skip_wilma.click()

    expect(source_listed(tab, "wilma", "skipped")).to_be_visible()
    expect(tab.get_by_label(text("gateway.key"), exact=True)).to_be_visible()
    expect(status).to_be_hidden()


def test_a_wilma_cli_that_doesn_t_install_shows_why_under_the_message(tab, wilma_cli):  # noqa: F811
    wilma_cli.npm_works = False

    in_english_with_the_gateway(tab)

    # In a small block of its own under the message, as npm said it, for a photo to carry, folded
    # as the details for the Parent Recap team.
    message = tab.locator("#source-message")
    reason = tab.locator("#source-reason")
    expect(message).to_have_text(text("wilma.ready.install-failed"))
    expect(reason).to_be_hidden()
    tab.locator("#source-status").get_by_text(text("fail.details")).click()
    expect(reason).to_have_text("npm ended with exit code 1:\nnpm error code E404")
    expect(reason).to_have_css("white-space", "pre-wrap")
    assert "mono" in reason.evaluate("e => getComputedStyle(e).fontFamily").lower()

    # Trying again shows only the new reason, and none once it installs.
    wilma_cli.npm_works = True
    wilma_cli.npm = timed_out
    button(tab, "wilma.again").click()
    expect(reason).to_have_text(
        f"npm didn't finish within {setup_wilma.INSTALL_SECONDS} seconds.")
    wilma_cli.npm = None
    button(tab, "wilma.again").click()
    expect(tab.get_by_label(text("wilma.search"))).to_be_enabled()
    expect(reason).to_be_hidden()
    expect(reason).to_have_text("")


@pytest.mark.parametrize("on", ["macos", "windows"])
def test_the_wilma_step_offers_the_passwords_app_only_on_a_mac(tab, on):
    as_on(tab, on)
    in_english_with_the_gateway(tab)

    expect(tab.get_by_label(text("wilma.username"))).to_be_visible()
    tab.get_by_text(text("wilma.more", system=on)).click()  # folded under the step
    passwords = button(tab, "wilma.passwords", system=on)
    if on == "macos":
        expect(passwords).to_be_visible()
    else:
        expect(passwords).to_be_hidden()


@pytest.mark.parametrize("on", ["macos", "windows"])
def test_a_page_whose_window_was_closed_says_so_without_a_click(tab, page, on):  # noqa: F811
    """#601: the family is reading Welcome, its AI ready, so the page sends nothing, when its
    Terminal or PowerShell window is closed. The page says so on its own, and offers nothing that
    can't work any more."""
    as_on(tab, on)
    in_english(tab)
    gateway_ai = tab.get_by_role("radio", name=text("welcome.ai.gateway"))
    gateway_ai.check()
    expect(tab.get_by_text(text("ai.gateway.ready"))).to_be_visible()
    page.stop()

    expect(tab.get_by_text(text("error.closed", system=on))).to_be_visible()
    expect(button(tab, "continue")).to_be_disabled()
    expect(gateway_ai).to_be_disabled()


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

    def picked_shows(result: str) -> None:
        tab.get_by_role("radio", name=text(f"welcome.ai.{name}", system=on)).check()
        expect(tab.locator("#ai-message")).to_have_text(text(f"ai.{name}.{result}", system=on))
        if on == "windows" or name == "codex":
            expect(again).to_be_hidden()
            expect(go_on).to_be_enabled()
        else:
            expect(again).to_be_visible()
            expect(go_on).to_be_disabled()

    # Not installed: where the AI's own step installs it (Codex on both systems, Claude Code on
    # Windows), Welcome goes on; on a Mac the family installs Claude Code first.
    picked_shows("not-installed")
    tab.get_by_role("radio", name=text("welcome.ai.gateway", system=on)).check()
    expect(tab.get_by_text(text("ai.gateway.ready", system=on))).to_be_visible()
    expect(again).to_be_hidden()
    expect(go_on).to_be_enabled()
    # Signed out: the same AIs' own steps sign in, Codex's with `codex login` on a Mac as on
    # Windows (#707); on a Mac Claude Code's sign-in needs it installed from Terminal (#475).
    tab.route(re.compile(r"/api/ai$"), lambda route: route.fulfill(json={"result": "signed-out"}))
    picked_shows("signed-out")


@pytest.mark.parametrize("on", ["macos", "windows"])
def test_chatgpt_s_step_installs_codex_and_offers_the_button_again_when_that_fails(
        tab, family, ai, on):
    """Install Codex on a Mac as on Windows (#473); OpenAI's installer itself isn't run here, the
    server's answers are played."""
    at_the_ai_step(tab, family, "codex", on)
    expect(tab.get_by_text(text("codex.not-installed", system=on))).to_be_visible()
    install = button(tab, "codex.install", system=on)
    expect(install).to_be_visible()
    expect(tab.locator("#codex-download")).to_have_count(0)  # the ChatGPT app's download, before
    tab.route(re.compile(r"/api/codex/install$"), lambda route: route.fulfill(json={"result": "installing"}))
    tab.route(re.compile(r"/api/codex$"), lambda route: route.fulfill(json={"result": "install-failed"}))

    with tab.expect_request(lambda r: r.method == "POST" and r.url.endswith("api/codex/install")):
        install.click()

    expect(tab.get_by_text(text("codex.installing", system=on))).to_be_visible()
    expect(install).to_be_hidden()
    # The next check says the install failed: the page says so and offers the button again.
    expect(tab.get_by_text(text("codex.install-failed", system=on))).to_be_visible()
    expect(install).to_be_visible()


def test_while_codex_installs_its_step_shows_a_bar_and_the_time_so_far_in_each_language(
        tab, family, ai):
    """A slow install shows how far it has got (#709): a moving bar until the installer prints a
    share, the time so far, and past the usual time that it's still working. The server's answers
    are played, each until the test moves on."""
    at_the_ai_step(tab, family, "codex", SYSTEM)
    started = {"result": "installing", "seconds": 0, "usual": 120, "limit": 600}
    answer = {"out": started}
    tab.route(re.compile(r"/api/codex/install$"), lambda route: route.fulfill(json=answer["out"]))
    tab.route(re.compile(r"/api/codex$"), lambda route: route.fulfill(json=answer["out"]))
    bar = tab.locator("#source-install-bar")
    slow = tab.locator("#source-install-slow")

    button(tab, "codex.install").click()

    expect(bar).to_be_visible()
    expect(bar).not_to_have_attribute("value", re.compile(".*"))  # the browser's moving bar
    expect(tab.get_by_text(text("install.under-a-minute", limit=10))).to_be_visible()
    expect(slow).to_be_hidden()
    answer["out"] = {**started, "seconds": 150, "percent": 45}  # the next check, a few seconds on
    expect(bar).to_have_js_property("value", 45)
    for language in ("en", "fi", "zh"):
        tab.locator("#language-switch").select_option(language)
        expect(tab.get_by_text(text("install.so-far", language=language, minutes=2,
                                    limit=10))).to_be_visible()
        expect(tab.get_by_text(text("install.slow", language=language))).to_be_visible()
    answer["out"] = {"result": "install-failed", "reason": "The Codex installer ended with exit "
                                                           "code 1."}
    expect(tab.get_by_text(text("codex.install-failed", language="zh"))).to_be_visible()
    expect(tab.locator("#source-install-progress")).to_be_hidden()


def test_while_chatgpt_s_sign_in_waits_the_step_offers_to_sign_in_again(tab, family, ai):
    """OpenAI's page can fail in the browser, or its tab be closed, while `codex login` waits on:
    the step offers a fresh sign-in (#705). The server's answers are played."""
    at_the_ai_step(tab, family, "codex", SYSTEM)
    tab.route(re.compile(r"/api/codex$"), lambda route: route.fulfill(json={"result": "waiting"}))
    tab.route(re.compile(r"/api/codex/login$"),
              lambda route: route.fulfill(json={"result": "waiting"}))

    for language in ("en", "fi", "zh"):
        tab.locator("#language-switch").select_option(language)
        expect(tab.get_by_text(text("codex.waiting", language=language))).to_be_visible()
        expect(tab.get_by_role("button", name=text("codex.login-again", language=language),
                               exact=True)).to_be_visible()

    tab.locator("#language-switch").select_option("en")
    with tab.expect_request(lambda r: r.method == "POST" and r.url.endswith("api/codex/login")):
        button(tab, "codex.login-again").click()
    expect(tab.get_by_text(text("codex.waiting"))).to_be_visible()
    expect(button(tab, "codex.login-again")).to_be_visible()


def at_chatgpt_s_waiting_sign_in(tab, family) -> None:
    """Connect's AI step with ChatGPT picked on Welcome, whose sign-in waits for good: OpenAI asks
    for a phone number the family can't give. Codex's own answers are played."""
    in_english_with_the_gateway(tab)
    data = yaml.safe_load(config_file(family).read_text())
    data["llm"] = {"backend": "codex"}
    config_file(family).write_text(yaml.safe_dump(data))
    save_progress(family, {**json.loads(progress_file(family).read_text()), "source": "ai",
                           "sources": {"wilma": "done", "gmail": "done"}})
    tab.route(re.compile(r"/api/codex$"), lambda route: route.fulfill(json={"result": "waiting"}))
    tab.reload()
    expect(tab.get_by_text(text("codex.waiting"))).to_be_visible()


@pytest.mark.parametrize("language", ["fi", "en", "zh"])
def test_a_family_stuck_on_chatgpt_s_sign_in_uses_the_gateway_instead(tab, family, language):
    """#706: Connect has no way back to Welcome, so its AI step offers Welcome's choices itself."""
    at_chatgpt_s_waiting_sign_in(tab, family)
    tab.locator("#language-switch").select_option(language)

    def said(key: str):
        return tab.get_by_text(text(key, language=language), exact=True)

    def pressed(key: str):
        return tab.get_by_role("button", name=text(key, language=language), exact=True)
    pressed("connect.ai.other").click()
    tab.get_by_role("radio", name=re.compile("^" + re.escape(
        text("welcome.ai.gateway", language=language)))).check()
    with tab.expect_request(lambda r: r.method == "POST" and r.url.endswith("api/connect/ai")):
        pressed("finish.ai.continue").click()

    key = tab.get_by_label(text("gateway.key", language=language), exact=True)
    expect(key).to_be_visible()
    expect(said("codex.waiting")).to_be_hidden()
    expect(pressed("codex.login-again")).to_be_hidden()
    assert Config.load(config_file(family)).llm.backend == "gateway"
    # A page loaded again on Connect shows the new AI's sign-in.
    tab.reload()
    expect(key).to_be_visible()
    expect(pressed("connect.ai.other")).to_be_visible()
    key.fill(GATEWAY_KEY)
    pressed("gateway.save").click()
    expect(source_listed(tab, "ai", "done", language)).to_be_visible()


def test_a_family_stuck_on_chatgpt_s_sign_in_can_pick_claude_instead(tab, family):
    at_chatgpt_s_waiting_sign_in(tab, family)

    button(tab, "connect.ai.other").click()
    # Welcome's choices, with the one picked there checked.
    expect(tab.get_by_role("radio", name=re.compile("^" + re.escape(
        text("welcome.ai.codex"))))).to_be_checked()
    tab.get_by_role("radio", name=re.compile("^" + re.escape(text("welcome.ai.claude")))).check()
    button(tab, "finish.ai.continue").click()

    expect(button(tab, "claude.start")).to_be_visible()
    expect(button(tab, "connect.ai.other")).to_be_visible()  # and back again, if Claude fails too
    expect(tab.get_by_text(text("codex.waiting"))).to_be_hidden()
    assert Config.load(config_file(family)).llm.backend == "claude"


@pytest.mark.parametrize("on, asks", [("macos", "api/claude"),
                                      ("windows", "api/claude/install")])
def test_claude_s_step_installs_claude_code_on_windows_and_signs_in_on_a_mac(
        tab, family, ai, on, asks):
    at_the_ai_step(tab, family, "claude", on)

    with tab.expect_request(lambda r: r.method == "POST" and r.url.endswith(asks)):
        button(tab, "claude.start", system=on).click()


def copying(tab) -> None:
    """Copy writes to the clipboard, which a headless browser keeps to itself: kept here instead."""
    tab.evaluate("() => { window.copied = []; navigator.clipboard.writeText = "
                 "(t) => { window.copied.push(t); return Promise.resolve(); }; }")


def assert_report(tab, said: str, step: str) -> None:
    """What Copy put on the clipboard: what failed, then the version, the system, the step and
    when, as for a step that failed (#487)."""
    tab.wait_for_function("() => window.copied.length > 0")
    [report] = tab.evaluate("() => window.copied")
    about = failure.about()
    *lines, time = report.splitlines()
    assert lines == [said, "", f"Parent Recap {about['version']}", f"System: {about['os']}",
                     f"Step: {step}"]
    assert re.fullmatch(r"Time: \d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", time)


@pytest.mark.parametrize("where", ["check", "finish"])
def test_a_failed_check_without_a_reason_folds_what_it_said_under_its_hint_with_copy(
        tab, family, health, schedule, where):
    # #500: a check that fails in a way doctor has no reason for says why too.
    said = "google mode, but the token couldn't be refreshed: invalid_grant"
    (at_the_check_step if where == "check" else at_finish)(family)
    health.results.append((ops.FAIL, "calendar", "Calendar", said))
    tab.reload()
    copying(tab)

    if where == "check":
        button(tab, "check.confirm").click()
        item = tab.locator("#health-checks > li").filter(has_text=text("health.check.calendar"))
    else:
        button(tab, "finish.start").click()
        item = tab.locator("#finish-checklist .checks > li").filter(
            has_text=text("health.check.calendar"))
    prefix = "health" if where == "check" else "finish"

    # The general hint first, then what doctor said, folded until it's opened.
    expect(item.locator("p.hint").first).to_have_text(text(f"{prefix}.fail.calendar"))
    reason = item.locator(".reason")
    expect(reason).to_be_hidden()
    item.get_by_text(text("fail.details")).click()
    expect(reason).to_have_text(said)
    item.get_by_role("button", name=text("copy"), exact=True).click()
    assert_report(tab, said, f"{prefix}.fail.calendar")
    # Skipping is the check page's, for a Source Connect's list offers; the calendar isn't one.
    expect(item.locator("xpath=./button")).to_have_count(0)


def test_a_failed_check_of_a_source_the_family_can_do_without_offers_skip_and_setup_goes_on(
        tab, family, monkeypatch):
    # #500: a Source a family can do without never holds setup up. Doctor checks Wilma only while
    # it's on, as it does for real.
    at_the_check_step(family)
    gmail_fails = True

    def checks(config: str, **_k: Any) -> list[ops.Check]:
        wilma = Config.load(config).wilma.enabled
        return [ops.Check(ops.OK, "config", "Config file", "2 kids"),
                ops.Check(ops.FAIL, "gmail", "Gmail", "IMAP login failed") if gmail_fails
                else ops.Check(ops.OK, "gmail", "Gmail", "signed in"),
                *([ops.Check(ops.FAIL, "wilma", "Wilma", "wilma kids list failed")] if wilma else [])]
    monkeypatch.setattr(ops, "health_checks", checks)
    tab.reload()
    button(tab, "check.confirm").click()

    checks_shown = tab.locator("#health-checks > li")
    wilma = checks_shown.filter(has_text=text("health.check.wilma"))
    gmail = checks_shown.filter(has_text=text("health.check.gmail"))
    expect(wilma.locator("p.hint").first).to_have_text(text("health.fail.wilma"))
    expect(gmail.locator("p.hint").first).to_have_text(text("health.fail.gmail"))
    expect(skip(tab, "wilma")).to_have_count(1)
    expect(skip(tab, "gmail")).to_have_count(1)  # Gmail too, which only reads (ADR 0032)

    gmail_fails = False  # the parent fixed Gmail, and Wilma's school doesn't use it after all
    wilma.get_by_role("button", name=text("source.skip", source="Wilma")).click()

    expect(heading(tab, "brief.title")).to_be_visible()
    assert json.loads(progress_file(family).read_text())["sources"]["wilma"] == "skipped"
    assert not Config.load(config_file(family)).wilma.enabled


def test_the_check_page_reveals_add_a_kid_on_a_click_and_asks_no_feedback_label(
        tab, family, health):
    # #491: "Add a Kid" is a link until it's needed, and a pilot family has nothing to fill in.
    family.config["feedback"] = {"enabled": True}
    at_the_check_step(family)
    tab.reload()
    expect(heading(tab, "check.title")).to_be_visible()
    field = tab.get_by_label(text("check.kid.add.name"))
    expect(field).to_be_hidden()

    button(tab, "check.kid.add.open").click()

    expect(field).to_be_visible()
    expect(field).to_be_focused()
    expect(button(tab, "check.kid.add.open")).to_be_hidden()
    field.fill("Ella")
    button(tab, "check.kid.add").click()
    expect(tab.get_by_role("checkbox", name="Ella")).to_be_checked()
    expect(tab.locator("#check-household-label")).to_have_count(0)


@pytest.mark.macos
def test_continue_in_the_chat_is_one_line_at_the_bottom_of_the_step_shown(tab):
    in_english(tab)

    # The last thing in Welcome's card, after its Continue.
    line = tab.locator("#welcome > #chat:last-child")
    expect(line).to_be_visible()
    expect(line.locator("p").first).to_have_text(f'{text("chat.note")} {text("chat.continue")}')
    expect(line.get_by_role("button", name=text("chat.continue"))).to_be_visible()


def test_finish_offers_only_turn_it_on_and_no_failure_for_what_it_turns_on(tab, family, health):
    # #463: the evening job, not loaded before Turn it on, is no failure and no warning yet.
    at_finish(family)
    health.results.append((ops.WARN, "schedule", "Schedule", "not installed yet"))
    tab.reload()
    expect(heading(tab, "finish.title")).to_be_visible()

    # The nightly and the wake to-do say the same thing, so the hint is found by its key.
    expect(tab.locator('[data-text="finish.to-do.nightly"]')).to_be_visible()
    expect(tab.locator('[data-text="finish.to-do.nightly"]')).to_have_text(text("finish.to-do.nightly"))
    expect(button(tab, "finish.start")).to_be_visible()
    expect(button(tab, "finish.again")).to_be_hidden()
    expect(button(tab, "finish.stop")).to_be_hidden()
    checklist = tab.locator("#finish-checklist")
    expect(checklist.locator('li[data-status="fail"], li[data-status="warn"]')).to_have_count(0)
    expect(checklist.get_by_text(text("health.check.schedule"))).to_be_hidden()


def test_check_again_is_offered_only_once_turn_it_on_left_something_failing(tab, family, health):
    at_finish(family)
    health.results.append((ops.FAIL, "claude", "Claude", "call failed"))
    tab.reload()
    expect(button(tab, "finish.start")).to_be_visible()
    expect(button(tab, "finish.again")).to_be_hidden()

    button(tab, "finish.start").click()

    expect(tab.get_by_text(text("finish.not-done"))).to_be_visible()
    expect(tab.get_by_text(text("finish.fail.ai"))).to_be_visible()
    again = tab.locator("#finish-status").get_by_role("button", name=text("finish.again"))
    expect(again).to_be_visible()

    health.results.pop()  # the parent has fixed it
    again.click()

    expect(tab.locator("#finish-status")).to_have_attribute("data-result", "finish.done")
    expect(button(tab, "finish.again")).to_be_hidden()


def test_a_failed_step_shows_why_copy_says_where_and_trying_again_clears_it(
        tab, family, health, schedule):
    at_finish(family)
    schedule.load_fails = True
    tab.reload()
    # Copy writes to the clipboard, which a headless browser keeps to itself.
    tab.evaluate("() => { window.copied = []; navigator.clipboard.writeText = "
                 "(t) => { window.copied.push(t); return Promise.resolve(); }; }")

    button(tab, "finish.start").click()

    # The general message as it was, and under it why, in the block Wilma's install uses.
    status = tab.locator("#finish-status")
    expect(status.get_by_text(text("finish.install-failed"))).to_be_visible()
    reason = tab.locator("#finish-reason")
    said = "Turning on the evening Brief stopped: RuntimeError: the jobs couldn't be loaded"
    expect(reason).to_have_text(said)
    expect(reason).to_have_css("white-space", "pre-wrap")
    assert "mono" in reason.evaluate("e => getComputedStyle(e).fontFamily").lower()

    status.get_by_text(text("fail.details")).click()  # folded until opened
    status.get_by_role("button", name=text("copy"), exact=True).click()

    tab.wait_for_function("() => window.copied.length > 0")
    [report] = tab.evaluate("() => window.copied")
    about = failure.about()
    *lines, time = report.splitlines()
    assert lines == [said, "", f"Parent Recap {about['version']}", f"System: {about['os']}",
                     "Step: finish.install-failed"]
    assert re.fullmatch(r"Time: \d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", time)

    # Turn it on again: the reason goes as it starts, and once it works nothing more is shown.
    schedule.load_fails = False
    button(tab, "finish.start").click()
    expect(status).to_have_attribute("data-result", "finish.done")
    expect(reason).to_be_hidden()
    expect(status.get_by_role("button", name=text("copy"), exact=True)).to_be_hidden()


def test_finish_moves_connect_s_sign_ins_into_changing_the_ai(tab, family, health):
    at_finish(family)
    tab.reload()
    expect(heading(tab, "finish.title")).to_be_visible()

    button(tab, "finish.ai.open").click()
    tab.get_by_role("radio", name=text("welcome.ai.gateway")).check()
    button(tab, "finish.ai.continue").click()

    expect(tab.locator("#finish-ai-sign-in").get_by_label(text("gateway.key"), exact=True)).to_be_visible()


def test_finish_takes_the_key_for_sending_a_household_that_sent_from_gmail_has_none_of(
        tab, family, health):
    # ADR 0032: a Household on its own Claude Code that sent through its own Gmail before.
    at_finish(family)
    health.results.append(ops.Check(ops.FAIL, "gateway-sends", "Brief sending",
                                    "no Gateway Key in the Keychain", reason="no-key"))
    tab.reload()
    button(tab, "finish.start").click()
    expect(tab.get_by_text(text("finish.fail.gateway.no-key"))).to_be_visible()
    form = tab.locator("#finish-checklist #send-key-form")
    expect(form).to_be_visible()

    health.results.pop()  # doctor finds the key once it's stored
    form.get_by_label(text("recipients.key"), exact=True).fill(GATEWAY_KEY)
    form.get_by_role("button", name=text("gateway.save"), exact=True).click()

    expect(tab.locator("#finish-status")).to_have_attribute("data-result", "finish.done")
    assert family.secrets[gateway.ACCOUNT] == GATEWAY_KEY
    assert family.relay_keys == [GATEWAY_KEY] and family.sent == []


def test_finish_sends_the_brief_from_the_familys_gmail_and_back(tab, family, health):
    # ADR 0034: a Household on its own Claude Code that reads its Gmail, under "Later".
    at_finish(family)
    family.secrets["gmail-imap-parent@example.com"] = "abcdefghijklmnop"
    tab.reload()
    expect(tab.get_by_text(text("finish.sending.current.gateway"))).to_be_visible()

    button(tab, "finish.sending.on").click()

    expect(tab.get_by_text(text("finish.sending.saved.gmail"))).to_be_visible()
    expect(tab.get_by_text(text("finish.sending.current.gmail", address="parent@example.com"))).to_be_visible()
    assert Config.load(config_file(family)).email.from_gmail
    assert family.relay_keys == [] and family.key_requests == []

    button(tab, "finish.sending.off").click()

    expect(tab.get_by_text(text("finish.sending.saved.gateway"))).to_be_visible()
    # Back on the gateway, the Recipients confirm their address (ADR 0033), under the switch and,
    # for each who hasn't, at the top of Finish (#712).
    expect(tab.locator("#finish-sending-confirm").get_by_role(
        "button", name=text("gmail.confirm.resend"), exact=True)).to_be_visible()
    assert not Config.load(config_file(family)).email.from_gmail


def test_a_household_that_said_only_me_adds_and_removes_a_recipient_at_finish(tab, family,
                                                                             health):
    # #699: under "Later", in the rows of Gmail's step; a new address gets its link (ADR 0033).
    family.config["email"]["to"] = [PARENT]
    at_finish(family)
    tab.reload()
    finish = "#finish-recipients"
    expect(tab.locator(finish).get_by_text(text("recipients.to", addresses=PARENT))).to_be_visible()
    expect(recipient(tab, 0, finish)).to_be_hidden()

    tab.locator(finish).get_by_role("button", name=text("recipients.change"), exact=True).click()
    expect(recipient(tab, 0, finish)).to_have_value(PARENT)
    tab.locator(finish).get_by_role("button", name=text("recipients.add"), exact=True).click()
    recipient(tab, 1, finish).fill("grandma@example.fi")
    recipient_language(tab, 1, finish).select_option("fi")
    tab.locator(finish).get_by_role("button", name=text("recipients.save"), exact=True).click()

    expect(tab.locator(finish).get_by_text(text("finish.recipients.saved"))).to_be_visible()
    expect(tab.locator(finish).get_by_text(text("recipients.to", addresses=f"{PARENT}, grandma@example.fi"))) \
        .to_be_visible()
    expect(tab.locator(finish).get_by_text(text(
        "gmail.confirm.sent", addresses=f"{PARENT}, grandma@example.fi"))).to_be_visible()
    to = Config.load(config_file(family)).email.to
    assert [(t.address, t.language) for t in to] == [(PARENT, None), ("grandma@example.fi", "fi")]
    assert family.key_requests == [[{"address": PARENT, "language": "en"},
                                    {"address": "grandma@example.fi", "language": "fi"}]]

    tab.locator(finish).get_by_role("button", name=text("recipients.change"), exact=True).click()
    tab.locator(finish).get_by_role("button", name=text("recipients.remove"), exact=True).first \
        .click()
    tab.locator(finish).get_by_role("button", name=text("recipients.save"), exact=True).click()

    expect(tab.locator(finish).get_by_text(text("recipients.to", addresses="grandma@example.fi"))).to_be_visible()
    assert [t.address for t in Config.load(config_file(family)).email.to] == ["grandma@example.fi"]


def test_finish_offers_no_gmail_sending_with_the_gateway_as_the_ai(tab, family, health):
    family.config["llm"]["backend"] = "gateway"
    at_finish(family)
    tab.reload()
    expect(heading(tab, "finish.title")).to_be_visible()

    expect(tab.locator("#finish-sending")).to_be_hidden()


# ── WhatsApp, paired as a Linked Device on the page (ADR 0027)


def qr_code(tab):
    return tab.get_by_role("img", name=text("whatsapp.code"))


@pytest.mark.parametrize("on", ["macos", "windows"])
def test_whatsapp_pairs_with_a_qr_code_and_its_groups_are_ticked_on_the_check_page(
        tab, family, phone, health, on):  # noqa: F811
    at_the_whatsapp_step(family)
    phone.plays([{"qr": "2@first"}, {"wait": "next"}, {"qr": "2@second"}, {"wait": "scan"},
                 *SCANNED])
    as_on(tab, on)

    for line in ("whatsapp.step.open", "whatsapp.step.link", "whatsapp.step.scan",
                 "whatsapp.terms"):
        expect(tab.get_by_text(text(line, system=on))).to_be_visible()
    expect(qr_code(tab)).to_be_visible()
    expect(tab.get_by_text(text("whatsapp.qr", system=on))).to_be_visible()
    first = qr_code(tab).screenshot()
    phone.go("next")  # WhatsApp changes the code
    wait_for(lambda: qr_code(tab).screenshot() != first, 20)
    phone.go("scan")

    expect(tab.get_by_text(text("whatsapp.paired", system=on))).to_be_visible()
    expect(qr_code(tab)).to_be_hidden()
    expect(source_listed(tab, "whatsapp", "done")).to_be_visible()
    expect(tab.get_by_label(text("myclub.link"))).to_be_visible()  # on to the next Source

    more_progress(family, phase="check", sources={"myclub": "skipped"}, gmail_senders=[])
    tab.reload()
    expect(heading(tab, "check.title")).to_be_visible()
    expect(tab.get_by_role("checkbox", name=re.compile("^Luokka 3B"))).to_be_checked()
    expect(tab.get_by_role("checkbox", name=re.compile("^Futis P2017"))).not_to_be_checked()
    button(tab, "check.confirm").click()

    expect(heading(tab, "brief.title")).to_be_visible()
    chats = Config.load(config_file(family)).whatsapp.chats
    assert [(c.name, c.id) for c in chats] == [("Luokka 3B", CLASS)]
    assert family.jobs.always_running


def test_a_code_that_ran_out_offers_a_new_one_without_an_error(tab, family, phone, page):  # noqa: F811
    at_the_whatsapp_step(family)
    page.context.timeouts.whatsapp_pair = 1
    phone.plays([{"qr": "2@first"}])
    tab.reload()

    expect(tab.get_by_text(text("whatsapp.timed-out"))).to_be_visible()
    expect(qr_code(tab)).to_be_hidden()
    expect(tab.get_by_role("alert")).to_have_count(0)  # no failure, in red
    page.context.timeouts.whatsapp_pair = 20
    phone.plays([{"qr": "2@fresh"}, {"wait": "scan"}, *SCANNED])
    button(tab, "whatsapp.again").click()

    expect(qr_code(tab)).to_be_visible()
    expect(button(tab, "whatsapp.again")).to_be_hidden()
    phone.go("scan")
    expect(tab.get_by_text(text("whatsapp.paired"))).to_be_visible()
    assert [g["id"] for g in json.loads(progress_file(family).read_text())["whatsapp_chats"]] == [
        FOOTBALL, CLASS]


def test_a_link_that_ended_is_paired_again_and_whatsapp_turned_off_at_finish(
        tab, family, phone, health):  # noqa: F811
    at_finish(family)
    family.source_fakes["whatsapp"].status(paired=False, connected_since=None,
                                           last_connected=None,
                                           link_ended_at="2026-09-26T05:30:00.000Z")
    phone.plays([{"qr": "2@again"}, {"wait": "scan"}, *SCANNED])
    tab.reload()

    expect(tab.get_by_text(text("whatsapp.kept"))).to_be_visible()
    expect(qr_code(tab)).to_be_visible()
    phone.go("scan")

    # The groups it keeps reading, by name, before going back (#548).
    expect(tab.get_by_text(text("whatsapp.repaired"))).to_be_visible()
    expect(tab.locator("#whatsapp-groups li")).to_have_text(["3B parents", "Leo piano"])
    expect(button(tab, "whatsapp.change")).to_be_visible()
    expect(tab.get_by_text(text("whatsapp.step.scan"))).to_be_hidden()
    button(tab, "continue").click()
    expect(heading(tab, "finish.title")).to_be_visible()  # back where the family was
    assert [c.id for c in Config.load(config_file(family)).whatsapp.chats] == [
        CLASS_GROUP, PIANO_GROUP]
    expect(tab.locator("#finish-whatsapp-groups li")).to_have_text(["3B parents", "Leo piano"])
    phone.plays([{"open": True}])  # the logout's socket
    button(tab, "finish.whatsapp.off").click()

    expect(tab.get_by_text(text("whatsapp.off"))).to_be_visible()
    expect(button(tab, "finish.whatsapp.off")).to_be_hidden()
    expect(tab.get_by_text(text("finish.whatsapp.groups"))).to_be_hidden()  # none is read now
    expect(tab.locator("#finish-back")).to_be_visible()  # to add it again
    assert not linked_device.folder().exists()
    assert not Config.load(config_file(family)).whatsapp.enabled


def test_finish_names_the_whatsapp_groups_and_changes_them_with_nothing_skipped(
        tab, family, health, page):  # noqa: F811
    at_finish(family)
    more_progress(family, sources={"myclub": "done"})  # no Source skipped to add
    tab.reload()

    expect(heading(tab, "finish.title")).to_be_visible()
    expect(tab.get_by_text(text("finish.whatsapp.groups"))).to_be_visible()
    expect(tab.locator("#finish-whatsapp-groups li")).to_have_text(["3B parents", "Leo piano"])
    expect(tab.locator("#finish-back")).to_be_hidden()
    # Finish checks the outcomes as it opens, which going back waits for.
    wait_for(lambda: page.context.runs.get("finish", {}).get("result") not in
             (None, "installing", "checking"), 20)
    button(tab, "whatsapp.change").click()

    expect(heading(tab, "check.title")).to_be_visible()
    expect(tab.get_by_role("checkbox", name=re.compile("^3B parents"))).to_be_checked()
    piano = tab.get_by_role("checkbox", name=re.compile("^Leo piano"))
    expect(piano).to_be_checked()
    piano.uncheck()
    button(tab, "check.confirm").click()
    expect(heading(tab, "brief.title")).to_be_visible()
    expect(tab.get_by_text(text("brief.made"))).to_be_visible()
    button(tab, "brief.done").click()

    expect(heading(tab, "finish.title")).to_be_visible()  # without loading the page again
    expect(tab.locator("#finish-whatsapp-groups li")).to_have_text(["3B parents"])


def test_a_linked_whatsapp_opened_from_connect_s_list_goes_on_without_a_code(tab, family):
    at_the_whatsapp_step(family)
    more_progress(family, sources={"whatsapp": "done", "myclub": "skipped"})
    tab.reload()

    expect(tab.get_by_text(text("whatsapp.paired"))).to_be_visible()
    expect(tab.get_by_text(text("whatsapp.step.scan"))).to_be_hidden()
    expect(qr_code(tab)).to_be_hidden()
    expect(tab.locator("#connect-continue")).to_be_hidden()  # a Source is current
    tab.locator("#whatsapp-on").click()

    expect(tab.locator("#connect-continue")).to_be_visible()  # on to Check
    expect(source_listed(tab, "whatsapp", "done")).to_be_visible()

