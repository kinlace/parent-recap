"""The setup page's main paths in a real browser (ADR 0024): Chromium, driven by Playwright, clicks
and types as a family does, from the language to Finish, skipping a Source, and a Gmail sign-in
that fails. Each test checks what the page shows: its visible text and which buttons it offers.

Only the browser is real. Behind the page's server everything is the harness's fakes, with the
fake wilma CLI's town list, Gmail's IMAP server and MyClub's calendar from the HTTP tests. Without
Playwright or its Chromium, or where no port can be listened on, these tests skip and say why;
`-m "e2e and not browser"` runs the rest of the layer."""
from __future__ import annotations

import imaplib
import json
import re
import sys
from typing import Any, Iterator

import pytest

try:
    from playwright.sync_api import Error as PlaywrightError, expect, sync_playwright
except ImportError:  # the browser fixture skips the tests and says why
    sync_playwright = None

from conftest import msg
from family_brief.collectors import myclub
from setup_page import CAN_LISTEN, PAGE_DIR, _listening, clock, page  # noqa: F401
from test_setup_ai import GATEWAY_KEY
from test_setup_first_brief import BRIEF_TEXT
from test_setup_gmail import TYPED
from test_setup_myclub import LINK as MYCLUB_LINK
from test_setup_page_windows import SENDERS, Gmail, Web
from test_setup_status import install_program
from test_setup_wilma import wilma_cli  # noqa: F401

pytestmark = pytest.mark.browser

PARENT = "parent@example.com"
TEXT = json.loads((PAGE_DIR / "text.json").read_text(encoding="utf-8"))["en"]
# Health check, the first Brief and Finish each poll every two seconds, and the steps behind them
# take a few more on a CI runner.
SLOW_MS = 30_000


def text(key: str, **values: Any) -> str:
    """The page's English text for `key`, as the page fills it in on this system."""
    shown = (sys.platform == "win32" and TEXT.get(f"{key}.windows")) or TEXT[key]
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
    link opens, and the first Brief has the school's mail to say."""
    install_program(harness)
    for var in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY", "TMUX"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(imaplib, "IMAP4_SSL", Gmail(SENDERS))
    monkeypatch.setattr(myclub.requests, "get", Web().get)
    harness.sources["gmail"] = [msg("gmail", "g1", "2026-09-27T09:00:00+03:00", BRIEF_TEXT,
                                    sender="opettaja@edu.espoo.fi", subject="Trip")]
    harness.model_reply = {"per_kid": [], "calendar_events": [], "message_digest": BRIEF_TEXT}
    return harness


@pytest.fixture
def tab(browser, family, page):  # noqa: F811
    """The setup page, opened in a browser tab as the install line opens it."""
    context = browser.new_context(locale="en-US")
    opened = context.new_page()
    opened.goto(page.url)
    yield opened
    context.close()


# ── The steps, as a family takes them


def button(tab, key: str, **values: Any):
    """The button showing `key`'s text, among those the page shows."""
    return tab.get_by_role("button", name=text(key, **values), exact=True)


def in_english_with_the_gateway(tab) -> None:
    """The language, then Welcome with the Gateway Key's AI and the Brief only for the parent."""
    tab.get_by_role("radio", name="English").check()
    button(tab, "continue").click()
    expect(tab.get_by_role("heading", name=text("welcome.title"))).to_be_visible()
    tab.get_by_role("radio", name=text("welcome.ai.gateway")).check()
    expect(tab.get_by_text(text("ai.gateway.ready"))).to_be_visible()
    # Welcome offers to add a partner first, whose address it then needs.
    tab.get_by_role("radio", name=text("welcome.partner.only-me")).check()
    expect(button(tab, "continue")).to_be_enabled()
    button(tab, "continue").click()
    expect(tab.get_by_role("heading", name=text("connect.title"))).to_be_visible()


def without_wilma_in_espoo(tab) -> None:
    """Wilma's step for a school without it: only the town."""
    tab.get_by_role("radio", name=text("wilma.uses.no")).check()
    town = tab.get_by_label(text("wilma.search"))
    expect(town).to_be_enabled()  # once the town list is ready
    town.fill("Espoo")
    expect(tab.get_by_role("radio", name="Espoo", exact=True)).to_be_checked()
    button(tab, "wilma.town.save").click()
    expect(tab.get_by_text(text("wilma.town-saved"))).to_be_visible()


def sign_in_to_gmail(tab, password: str) -> None:
    """Gmail's step: the parent's address and an App Password, tested and saved."""
    tab.get_by_label(text("gmail.address")).fill(PARENT)
    tab.get_by_label(text("gmail.password")).fill(password)
    button(tab, "gmail.save").click()


def source_listed(tab, source: str, status: str):
    """The Source's entry in Connect's list, named with the status it shows."""
    return tab.get_by_role("button", name=text("sources.button", source=text(f"source.{source}"),
                                               status=text(f"status.{status}")), exact=True)


def connect_up_to_myclub(tab) -> None:
    """Without Wilma, Gmail and the Gateway Key, and on macOS, the only system whose page lists
    WhatsApp, WhatsApp skipped."""
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
    if sys.platform == "darwin":  # WhatsApp for Mac, only on macOS
        button(tab, "source.skip").click()
        expect(source_listed(tab, "whatsapp", "skipped")).to_be_visible()
    expect(tab.get_by_label(text("myclub.add.name"))).to_be_visible()


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
    expect(tab.get_by_role("heading", name=text("check.title"))).to_be_visible()
    expect(tab.get_by_role("checkbox", name=re.compile("^Mia"))).to_be_checked()
    expect(tab.get_by_role("checkbox", name=re.compile(r"^edu\.espoo\.fi"))).to_be_checked()
    evening = tab.get_by_label(text("check.evening.label")).input_value()
    button(tab, "check.confirm").click()

    expect(tab.get_by_role("heading", name=text("brief.title"))).to_be_visible()
    expect(tab.get_by_text(text("brief.made"))).to_be_visible()
    button(tab, "brief.send").click()
    expect(tab.get_by_text(text("brief.send.sent", to=PARENT))).to_be_visible()
    button(tab, "brief.done").click()

    expect(tab.get_by_role("heading", name=text("finish.title"))).to_be_visible()
    button(tab, "finish.start").click()
    expect(tab.get_by_text(text("finish.done", evening=evening))).to_be_visible()
    expect(tab.get_by_text(text("finish.closed"))).to_be_visible()
    expect(button(tab, "finish.start")).to_be_hidden()


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


def test_an_app_password_gmail_rejects_shows_why_and_the_way_to_fix_it(tab):
    in_english_with_the_gateway(tab)
    without_wilma_in_espoo(tab)

    sign_in_to_gmail(tab, "zyxw vuts rqpo nmlk")  # an App Password, but not this address's

    expect(tab.get_by_text(text("gmail.rejected"))).to_be_visible()
    expect(button(tab, "gmail.two-step")).to_be_visible()
    expect(source_listed(tab, "gmail", "to-do")).to_be_visible()
    expect(button(tab, "gmail.save")).to_be_enabled()
    expect(tab.get_by_label(text("gmail.password"))).to_have_value("")
