"""The hosted version's sign-up (ADR 0031), driven over HTTP as a family's phone drives it: the
page under an Invite Link, the name the family asks to be called, which joins the link's note
(#814), the Wilma check, each Kid's MyClub link and an Eepos and an eVaka
sign-in, the
Household made under its own user with its first Brief sent and the maintainers mailed of it
(through the household host, as `admin signed-up`), and the 404 every other request gets;
then the maintainers' command that removes a Household again.

The outside edges are faked: the service's wilma CLI is `test_setup_wilma`'s fake, which knows one
Wilma account; the household host is `fake_household_host`'s, whose users each have their own
home, secrets and jobs and run the real `signup household` and `run` in-process; MyClub's server,
Eepos's school portals, each city's eVaka, the model and the gateway's Email Delivery are the harness's; the system is the harness's, set to
Linux. The service is the real one, on a free port or through socket pairs. Assertions are on the
pages, on what each Household's user was left with, on the email sent, on the store, and on every
place the Wilma, Eepos and eVaka passwords, a MyClub link and the Gateway Key must never reach.
"""
from __future__ import annotations

import http.client
import json
import logging
import re
import shutil
import stat
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode, urlparse

import pytest
import yaml

from family_brief import setup_wilma
from family_brief.collectors import eepos, evaka
from family_brief.brief_text import TEXT
from family_brief.config import Config
from family_brief.last_run import path as last_run_path
from family_brief.signup import page, status
from family_brief.signup.host import PROGRAM
from family_brief.signup.invites import HouseholdRecord, Recipient, Store
from family_brief.signup.remove import remove
from family_brief.signup.server import Service, SignupServer
from family_brief.system import jobs
from conftest import eepos_entry, eepos_student, evaka_child, evaka_event, myclub_event
from fake_sources import EEPOS_PASSWORD, EVAKA_PASSWORD
from fake_household_host import FakeHouseholdHost
from fake_system import LINUX
from setup_page import CAN_LISTEN, Response, _Connection, _PairedServer
from test_setup_wilma import ESPOO, WILMA_PASSWORD, WilmaCLI
from test_setup_wilma import wilma_cli  # noqa: F401

pytestmark = pytest.mark.posix  # the fake wilma CLI is a script on a shell-script Node

REFRESH = '<meta http-equiv="refresh" content="3">'  # how the page asks again, with no script
STEPS = ("step_setup", "step_wilma", "step_brief")
NO_WILMA = {"town": "", "username": "", "password": ""}  # the Wilma fields left empty
ESPOO_TOWN = "Espoo – Espoon kaupunki / Suomenkielisen opetuksen tulosyksikkö"
NOON = datetime(2026, 10, 30, 10, 0, tzinfo=timezone.utc)  # 12:00 in Helsinki
MIA_LINK = "webcal://kilonpallo.myclub.fi/ical/mia-1001-token.ics"
EEPOS_USERNAME = "parent@example.com"
FAMILY = "Virtasen perhe"  # what the family asks to be called (#814)


class Clock:
    def __init__(self) -> None:
        self.now = NOON

    def __call__(self) -> datetime:
        return self.now


class SignUp:
    """The service serving in the background, its store, its household host and its clock."""

    def __init__(self, tmp: Path) -> None:
        self.store = Store(tmp / "signup")
        # The server's shared copy (ADR 0035), as gateway-setup.sh leaves it: its venv, its own
        # Node and the wilma CLI installed with it.
        WilmaCLI(tmp / "lib").install()
        shared = tmp / "lib" / "ParentRecap"
        (shared / "app" / ".venv" / "bin").mkdir(parents=True)
        self.host = FakeHouseholdHost(tmp / "home", shared)
        self.clock = Clock()
        self.server = SignupServer(Service(self.store, self.host, now=self.clock), 0,
                                   listener=None if CAN_LISTEN else _PairedServer)
        self.url = self.server.url

    def invite(self, *recipients: Recipient, language: str = "fi", days: int = 14,
               note: str = "") -> str:
        return self.store.mint(list(recipients), language, self.clock.now, days, note).code

    def user(self):
        assert len(self.host.users) == 1, self.host.users
        return next(iter(self.host.users.items()))


@pytest.fixture
def signup(harness, wilma_cli, tmp_path):  # noqa: F811
    harness.system = LINUX
    wilma_cli.install()  # the service's own, which checks the family's sign-in
    s = SignUp(tmp_path)
    thread = threading.Thread(target=s.server.serve_forever, daemon=True)
    thread.start()
    yield s
    s.server.stop()
    thread.join(5)
    all_made()  # nothing goes on making a Household after the test


def all_made() -> None:
    """Waits until no Household is being made, with no page asking how it went."""
    for maker in threading.enumerate():
        if maker.name == "household-maker":
            maker.join(30)


def request(url: str, path: str, method: str = "GET", fields: dict[str, str] | None = None,
            content_type: str = "application/x-www-form-urlencoded") -> Response:
    parts = urlparse(url)
    # A sign-up answers only after install, config and the first Brief, which a busy machine slows.
    conn = _Connection(parts.hostname, parts.port, timeout=60)
    body = urlencode(fields).encode() if fields is not None else None
    headers = {"Host": "gateway.example"} | ({"Content-Type": content_type} if body else {})
    try:
        conn.request(method, path, body, headers=headers)
        r = conn.getresponse()
        return Response(r.status, {k.lower(): v for k, v in r.getheaders()}, r.read())
    finally:
        conn.close()


def sign_in(s: SignUp, code: str, **fields: str) -> Response:
    """The page's first step: what to call the family, the Wilma sign-in and the Recipients."""
    answers = {"lang": "en", "name": FAMILY, "town": ESPOO_TOWN, "username": "mia.parent",
               "password": WILMA_PASSWORD, "r0": "parent@example.com", "r0_language": "fi",
               "r1": "", "r1_language": "en"} | fields
    return request(s.url, f"/join/{code}", "POST", answers)


def post_links(s: SignUp, code: str, *given: str, lang: str = "en", **fields: str) -> Response:
    """The page's MyClub step: a link, or "" for none, for each Kid in turn, and its other
    `fields`, such as the Eepos sign-in."""
    return request(s.url, f"/join/{code}", "POST",
                   {"lang": lang, "step": "myclub"} | {f"m{i}": url for i, url in enumerate(given)}
                   | fields)


def links(s: SignUp, code: str, *given: str, lang: str = "en", **fields: str) -> Response:
    """The MyClub step, then the page as it shows the steps, until it says how the sign-up
    ended; the step's own page when it doesn't move on."""
    r = post_links(s, code, *given, lang=lang, **fields)
    if r.status != 303:
        return r
    assert r.headers["location"] == f"/join/{code}"
    return result(s, code)


def result(s: SignUp, code: str) -> Response:
    """The page the sign-up shows, asked again as the browser does, until it no longer asks."""
    deadline = time.monotonic() + 60
    while True:
        r = request(s.url, f"/join/{code}")
        if REFRESH not in text(r):
            return r
        assert time.monotonic() < deadline, text(r)
        time.sleep(0.05)


def until(s: SignUp, code: str, shows: Callable[[str], bool]) -> str:
    """The page the sign-up shows, asked again until `shows` it, while it still asks again."""
    deadline = time.monotonic() + 30
    while True:
        html = text(request(s.url, f"/join/{code}"))
        assert REFRESH in html, html
        if shows(html):
            return html
        assert time.monotonic() < deadline, html
        time.sleep(0.05)


def marks(html: str, language: str, steps: tuple[str, ...] = STEPS) -> str:
    """Each step's mark on the page, in order: ✅ ended, ⏳ going on, ❌ failed, ⬜ not begun."""
    return "".join(re.search(rf"<li>(\S+) {re.escape(said(language, step))}</li>", html)[1]
                   for step in steps)


class Held:
    """Holds the Gateway Key's minting and each command run as a Household until the test lets
    it end, as a slow server takes its time."""

    def __init__(self, host: FakeHouseholdHost, monkeypatch) -> None:
        self.ends = threading.Semaphore(0)
        mint_key, run_as = host.mint_key, host.run_as

        def held_key(name: str) -> str:
            assert self.ends.acquire(timeout=30)
            return mint_key(name)

        def held(name: str, argv: list[str], stdin: str, timeout: int) -> tuple[int, str]:
            assert self.ends.acquire(timeout=30)
            return run_as(name, argv, stdin, timeout)
        monkeypatch.setattr(host, "mint_key", held_key)
        monkeypatch.setattr(host, "run_as", held)

    def end(self, commands: int = 1) -> None:
        for _ in range(commands):
            self.ends.release()


def submit(s: SignUp, code: str, myclub: tuple[str, ...] = ("", ""), **fields: str) -> Response:
    """Both steps, with no MyClub link unless `myclub` gives them; the first's page when it
    doesn't move on."""
    r = sign_in(s, code, **fields)
    if 'name="step" value="myclub"' not in text(r):
        return r
    return links(s, code, *myclub, lang=fields.get("lang", "en"))


def text(r: Response) -> str:
    return r.body.decode()


def said(language: str, key: str, **values: str) -> str:
    from html import escape
    return escape(page.TEXT[language][key].format(**values))


# ── The page

def test_the_page_is_a_phone_sized_form_with_the_invite_s_recipients_and_loads_nothing(signup):
    code = signup.invite(Recipient("parent@example.com", "fi"), Recipient("isa@example.com", "zh"))

    r = request(signup.url, f"/join/{code}")

    assert r.status == 200
    html = text(r)
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in html
    assert '<html lang="fi">' in html and said("fi", "title") in html
    assert 'value="parent@example.com"' in html and 'value="isa@example.com"' in html
    assert '<option value="zh" selected>' in html
    assert f'<option value="{ESPOO_TOWN}">' in html  # the towns of the CLI's own tenant list
    # The password only in its own field, which never has a value.
    assert html.count('type="password"') == 1
    assert '<input id="password" name="password" type="password" ' \
           'autocomplete="current-password">' in html
    # A family without Wilma gives the Kids' names instead, behind a part they open (#760).
    assert f"<details><summary>{said('fi', 'no_wilma')}</summary>" in html
    assert '<input id="k0" name="k0"' in html
    # Nothing to load: no script, no stylesheet, image or font, from anywhere.
    for loads in ("<script", "<link", "<img", "src=", "url(", "http://", "https://"):
        assert loads not in html
    policy = r.headers["content-security-policy"]
    assert policy.startswith("default-src 'none'; style-src 'sha256-")
    assert "script-src" not in policy and "form-action 'self'" in policy
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("language", ["fi", "en", "zh"])
def test_the_page_is_in_the_language_the_family_picks(signup, language):
    code = signup.invite(Recipient("parent@example.com", "fi"))

    html = text(request(signup.url, f"/join/{code}?lang={language}"))

    assert f'<html lang="{language}">' in html
    assert said(language, "password") in html and said(language, "submit") in html
    assert f'<input type="hidden" name="lang" value="{language}">' in html
    others = {"fi", "en", "zh"} - {language}
    assert all(f'href="?lang={other}"' in html for other in others)


# ── Signing up

def test_a_good_sign_up_makes_the_household_and_sends_its_first_brief(
        signup, harness, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    code = signup.invite(Recipient("parent@example.com", "fi"))

    r = submit(signup, code, r1="partner@example.com", r1_language="zh")

    assert r.status == 200, text(r)
    html = text(r)
    assert said("en", "sent", time="21:00") in html
    assert said("en", "junk") not in html  # no Hotmail or Outlook.com address among them
    assert '<html lang="en">' in html
    name, user = signup.user()
    key = signup.host.keys[name]
    # Nothing installed for it: the shared program's `signup household` as the Household, the key
    # on its stdin, and its first Brief made from the last three days, as the setup page's is.
    assert [c[1:] for c in signup.host.commands] == [
        [PROGRAM, "signup", "household"],
        [PROGRAM, "run", "--first-brief", "--lookback-hours", "72"]]
    assert sorted(p.name for p in user.home.iterdir()) == [".family", "ParentRecap"]
    assert not (user.home / "ParentRecap" / "app").exists()  # its archive and logs only
    assert not (user.home / "ParentRecap" / "runtime").exists()
    # The first Brief, from Parent Recap's own address with the Household's key, to each
    # Recipient in the language they read it in.
    assert {tuple(m.to) for m in harness.sent} == {("parent@example.com",), ("partner@example.com",)}
    assert set(harness.relay_keys) == {key}
    by_reader = {m.to[0]: m for m in harness.sent}
    assert "Parent Recap su 27.9." in by_reader["parent@example.com"].text
    assert "Parent Recap 9月27日 周日" in by_reader["partner@example.com"].text
    assert all(m.html for m in harness.sent)
    # Each says, in its reader's language, that it comes from the hosted version.
    assert TEXT["fi"].runs_hosted + "\n" in by_reader["parent@example.com"].text
    assert TEXT["zh"].runs_hosted + "\n" in by_reader["partner@example.com"].text
    state = json.loads((user.home / ".family" / "state.json").read_text())
    assert set(state["brief_delivered_at"]) == {"parent@example.com", "partner@example.com"}
    cfg = Config.load(user.home / ".family" / "config.yaml")
    assert [k.name for k in cfg.kids] == ["Mia Virtanen", "Leo Virtanen"]  # as Wilma lists them
    assert [(t.address, t.language) for t in cfg.email.to] == [
        ("parent@example.com", "fi"), ("partner@example.com", "zh")]
    assert cfg.summary_language == "en" and cfg.city == "Espoo"
    assert cfg.hosted
    assert cfg.llm.backend == "gateway"
    assert cfg.wilma.enabled and not cfg.whatsapp.enabled
    assert cfg.google_calendar.mode == "ics"
    assert (cfg.schedule.daily_hour, cfg.schedule.daily_minute) == (21, 0)
    account = setup_wilma.secret_name(f"{ESPOO}|mia.parent")
    assert set(user.secrets) == {"gateway-key", account}
    assert user.secrets["gateway-key"] == key
    assert user.jobs.installed() == {jobs.DAILY}
    # Its evening job runs the shared copy's Python, so a redeploy upgrades its next run.
    assert user.jobs.program(jobs.DAILY) == [str(signup.host.python), "-m", "family_brief", "run"]
    assert not any(k.myclub_ical_url for k in cfg.kids)  # no link given: MyClub off for both
    # The Wilma sign-in: its password in the secret store, the rest in setup's own file, and
    # nothing left of the config the first Brief's run read Wilma with (#710).
    wilma = json.loads((user.home / ".family" / "wilma-signins.json").read_text())
    [profile] = wilma["profiles"]
    assert (profile["tenantUrl"], profile["username"]) == (ESPOO, "mia.parent")
    assert "passwordObfuscated" not in profile
    assert user.secrets[account] == setup_wilma.obfuscate(WILMA_PASSWORD)
    assert [s["name"] for s in profile["students"]] == ["Mia Virtanen", "Leo Virtanen"]
    assert not list((user.home / ".family" / "wilma-runs").iterdir())
    # Listed, without the family's addresses, and the link retired with its Recipients.
    [household] = signup.store.households()
    # Its note is the name the family gave, as its link had none (#814).
    assert (household.user, household.evening, household.note) == (name, "21:00", FAMILY)
    assert "parent@example.com" not in (signup.store.folder / "households.json").read_text()
    assert not list((signup.store.folder / "invites").iterdir())
    # The password and the key: on no command line, in no log or output, in no page.
    printed = "".join(capsys.readouterr())
    for secret in (WILMA_PASSWORD, key):
        assert secret not in caplog.text and secret not in printed and secret.encode() not in r.body
        assert not any(secret in arg for cmd in [*signup.host.commands, *harness.commands]
                       for arg in cmd)
    assert "mia.parent" not in caplog.text and "partner@example.com" not in caplog.text
    assert f"Household {name} created" in caplog.text


def test_the_maintainers_note_goes_to_the_household_s_record_and_never_to_the_family(signup):
    code = signup.invite(Recipient("parent@example.com", "fi"), note="Zhao Hanbo, test family")

    shown = text(request(signup.url, f"/join/{code}"))
    r = submit(signup, code)

    assert r.status == 200, text(r)
    assert "Zhao Hanbo" not in shown and "Zhao Hanbo" not in text(r)
    [household] = signup.store.households()
    assert household.note == f"Zhao Hanbo, test family · {FAMILY}"  # with its name (#814)


# ── What the family is called, and the maintainers told (#814)

@pytest.mark.parametrize("language", ["fi", "en", "zh"])
def test_the_page_asks_what_to_call_the_family_and_says_only_the_maintainers_see_it(
        signup, language):
    code = signup.invite(Recipient("parent@example.com", "fi"))

    html = text(request(signup.url, f"/join/{code}?lang={language}"))

    assert f'<label for="name">{said(language, "name")}</label>' in html
    assert re.search(r'<input id="name" name="name"[^>]* required', html)
    assert said(language, "name_hint") in html
    assert html.index('id="name"') < html.index('id="town"')  # first, before Wilma


@pytest.mark.parametrize("language", ["fi", "en", "zh"])
@pytest.mark.parametrize("name", ["", "   ", "leo@example.com"])
def test_a_sign_up_without_a_name_is_refused_and_keeps_what_was_typed(signup, language, name):
    code = signup.invite(Recipient("parent@example.com", "fi"))

    r = sign_in(signup, code, name=name, lang=language, r1="partner@example.com")

    assert r.status == 400
    html = text(r)
    assert said(language, "invalid-name") in html
    assert f'value="{ESPOO_TOWN}"' in html and 'value="mia.parent"' in html
    assert 'value="parent@example.com"' in html and 'value="partner@example.com"' in html
    assert WILMA_PASSWORD not in html
    assert (signup.host.users, signup.host.keys, signup.host.told) == ({}, {}, [])
    assert request(signup.url, f"/join/{code}").status == 200  # the link still works


def test_a_name_that_won_t_do_is_shown_again_to_be_mended(signup):
    code = signup.invite(Recipient("parent@example.com", "fi"))

    html = text(sign_in(signup, code, name="leo@example.com"))

    assert '<input id="name" name="name"' in html and 'value="leo@example.com"' in html


def test_the_name_is_kept_on_one_line_and_short(signup):
    code = signup.invite(Recipient("parent@example.com", "fi"), note="via Zhao 2026-11-06 #3")

    assert submit(signup, code, name="  王家\n " + "x" * 100).status == 200

    [household] = signup.store.households()
    assert household.note == "via Zhao 2026-11-06 #3 · 王家 " + "x" * 57  # 60 in all


def test_the_maintainers_are_told_of_each_household_made_and_only_its_label(signup):
    for n in range(2):
        code = signup.invite(Recipient(f"parent{n}@example.com", "fi"))
        assert submit(signup, code).status == 200

    assert signup.host.told == [h.user for h in signup.store.households()]
    assert len(set(signup.host.told)) == 2


def test_a_sign_up_that_fails_tells_the_maintainers_nothing(signup):
    code = signup.invite(Recipient("parent@example.com", "fi"))
    signup.host.fails = "mint_key"

    assert submit(signup, code).status == 500

    assert signup.host.told == []


def test_a_mail_to_the_maintainers_that_can_t_go_is_logged_and_the_household_stays_made(
        signup, harness, caplog):
    caplog.set_level(logging.DEBUG)
    code = signup.invite(Recipient("parent@example.com", "fi"))
    signup.host.fails = "tell_maintainers"

    r = submit(signup, code)

    assert r.status == 200 and said("en", "sent", time="21:00") in text(r)
    name, _ = signup.user()
    assert [h.user for h in signup.store.households()] == [name]
    assert signup.host.keys and harness.sent  # its key kept, its first Brief sent
    assert not list((signup.store.folder / "invites").iterdir())  # its link retired
    assert f"The maintainers weren't mailed of Household {name}" in caplog.text
    assert "admin signed-up failed" in caplog.text


@pytest.mark.parametrize("address", ["mia.mum@hotmail.com", "isa@outlook.com", "leo@hotmail.fi",
                                     "dad@live.com"])
def test_a_hotmail_or_outlook_recipient_is_told_to_look_in_junk(signup, address):
    code = signup.invite(Recipient("parent@example.com", "fi"))

    html = text(submit(signup, code, lang="zh", r1=address, r1_language="zh"))

    assert said("zh", "sent", time="21:00") in html
    assert said("zh", "junk") in html


def test_the_first_step_asks_for_each_kid_s_myclub_link_and_shows_no_secret(signup):
    code = signup.invite(Recipient("parent@example.com", "fi"))

    r = sign_in(signup, code, lang="fi")

    assert r.status == 200
    html = text(r)
    assert said("fi", "myclub") in html and said("fi", "myclub_hint") in html
    assert '<label for="m0">Mia Virtanen</label>' in html
    assert '<label for="m1">Leo Virtanen</label>' in html
    assert '<input type="hidden" name="step" value="myclub">' in html
    assert WILMA_PASSWORD not in html and "mia.parent" not in html
    assert signup.host.users == {}  # nothing made until the MyClub step
    for loads in ("<script", "<link", "<img", "src="):
        assert loads not in html


@pytest.mark.parametrize(("language", "button", "skip"), [
    ("fi", "Valmis", "Etkö käytä MyClubia? Jätä kentät tyhjiksi ja paina Valmis."),
    ("en", "Done", "Don't use MyClub? Leave them empty and press Done."),
    ("zh", "完成", "不用 MyClub？留空，直接点“完成”。"),
])
def test_the_myclub_step_ends_with_done_and_says_a_family_without_myclub_leaves_it_empty(
        signup, language, button, skip):
    from html import escape
    code = signup.invite(Recipient("parent@example.com", "fi"))

    html = text(sign_in(signup, code, lang=language))

    assert f'<button type="submit">{button}</button>' in html
    assert f"{escape(skip)}</p>" in html  # the hint's last sentence
    assert said(language, "submit") not in html  # the first page's button stays its own
    r = links(signup, code, "", "", lang=language)
    assert r.status == 200, text(r)
    assert said(language, "sent", time="21:00") in text(r)
    _, user = signup.user()
    cfg = Config.load(user.home / ".family" / "config.yaml")
    assert [k.name for k in cfg.kids] == ["Mia Virtanen", "Leo Virtanen"]
    assert not any(k.myclub_ical_url for k in cfg.kids)


# ── The steps

@pytest.mark.parametrize("language", ["fi", "en", "zh"])
def test_the_page_marks_each_step_as_it_ends(signup, monkeypatch, language):
    held = Held(signup.host, monkeypatch)
    code = signup.invite(Recipient("parent@example.com", "fi"))
    assert sign_in(signup, code, lang=language).status == 200

    r = post_links(signup, code, "", "", lang=language)

    # Answered at once, with the page that shows the steps.
    assert (r.status, r.headers["location"]) == (303, f"/join/{code}")
    html = until(signup, code, lambda html: marks(html, language) == "⏳⬜⬜")
    assert f'<html lang="{language}">' in html
    assert said(language, "updates") in html and said(language, "queued") not in html
    for loads in ("<script", "<link", "<img", "src="):
        assert loads not in html
    held.end()  # the Gateway Key
    until(signup, code, lambda html: marks(html, language) == "✅⏳⬜")
    held.end()  # signup household
    until(signup, code, lambda html: marks(html, language) == "✅✅⏳")
    held.end()  # the first Brief

    r = result(signup, code)
    assert r.status == 200
    assert marks(text(r), language) == "✅✅✅"
    assert said(language, "sent", time="21:00") in text(r)
    # Seen, the steps are gone with the link.
    _not_found(request(signup.url, f"/join/{code}"))


def test_a_sign_up_behind_another_says_it_waits_and_starts_once_that_one_is_done(
        signup, monkeypatch):
    held = Held(signup.host, monkeypatch)
    first = signup.invite(Recipient("parent@example.com", "fi"))
    second = signup.invite(Recipient("other@example.com", "fi"))
    assert sign_in(signup, first).status == 200
    assert post_links(signup, first, "", "").status == 303
    until(signup, first, lambda html: marks(html, "en") == "⏳⬜⬜")

    assert sign_in(signup, second, lang="zh").status == 200
    assert post_links(signup, second, "", "", lang="zh").status == 303

    html = until(signup, second, lambda html: said("zh", "queued") in html)
    assert marks(html, "zh") == "⬜⬜⬜"  # no step begun
    # A third waits behind both, and goes after the second, which was posted first.
    third = signup.invite(Recipient("third@example.com", "fi"))
    assert sign_in(signup, third).status == 200
    assert post_links(signup, third, "", "").status == 303
    until(signup, third, lambda html: said("en", "queued") in html)
    held.end(9)
    assert marks(text(result(signup, first)), "en") == "✅✅✅"
    second_done = text(result(signup, second))
    assert marks(second_done, "zh") == "✅✅✅" and said("zh", "sent", time="21:05") in second_done
    assert said("en", "sent", time="21:10") in text(result(signup, third))


def test_a_phone_that_slept_through_the_steps_finds_how_they_ended(signup, monkeypatch):
    held = Held(signup.host, monkeypatch)
    code = signup.invite(Recipient("parent@example.com", "fi"))
    assert sign_in(signup, code, lang="fi").status == 200
    assert post_links(signup, code, "", "", lang="fi").status == 303
    until(signup, code, lambda html: marks(html, "fi") == "⏳⬜⬜")

    # The screen turns off: the page asks nothing while the Household is made.
    held.end(3)
    all_made()
    signup.clock.now += timedelta(hours=1)
    # It turns on, and the browser may post the form again: back to the steps, nothing new.
    r = post_links(signup, code, "", "", lang="fi")

    assert (r.status, r.headers["location"]) == (303, f"/join/{code}")
    r = result(signup, code)
    assert r.status == 200 and marks(text(r), "fi") == "✅✅✅"
    assert said("fi", "sent", time="21:00") in text(r)
    assert len(signup.host.users) == 1 and len(signup.host.commands) == 2
    _not_found(request(signup.url, f"/join/{code}"))  # once seen, gone


def test_how_a_sign_up_ended_is_kept_a_day_for_a_page_that_never_came_back(signup):
    code = signup.invite(Recipient("parent@example.com", "fi"))
    assert sign_in(signup, code).status == 200
    signup.host.fails = "mint_key"
    assert post_links(signup, code, "", "").status == 303
    all_made()

    signup.clock.now += timedelta(hours=25)

    # The steps are gone, and the link, never used, shows the form again.
    r = request(signup.url, f"/join/{code}")
    assert r.status == 200 and 'name="password"' in text(r) and "❌" not in text(r)


# ── MyClub

@pytest.fixture
def mia_plays(harness):
    """Mia Virtanen's MyClub calendar, at MIA_LINK on MyClub's fake server, with one practice."""
    harness.config["kids"] = [{"name": "Mia Virtanen", "myclub_ical_url": MIA_LINK}]
    harness.sources["myclub"] = [myclub_event("ev-1", "Kilon Pallo practice", "2026-09-28T17:00:00+03:00",
                                    "2026-09-28T18:30:00+03:00", kid="Mia Virtanen")]
    return harness.source_fakes["myclub"]


def test_a_kid_s_myclub_link_is_checked_once_and_saved_for_that_kid(
        signup, harness, mia_plays, caplog):
    caplog.set_level(logging.DEBUG)
    code = signup.invite(Recipient("parent@example.com", "fi"))

    r = submit(signup, code, myclub=(f"  {MIA_LINK} ", ""))

    assert r.status == 200, text(r)
    _, user = signup.user()
    cfg = Config.load(user.home / ".family" / "config.yaml")
    assert {k.name: k.myclub_ical_url for k in cfg.kids} == {"Mia Virtanen": [MIA_LINK],
                                                            "Leo Virtanen": []}
    # Downloaded once to check it, then once more by the first Brief, which reads it.
    https = MIA_LINK.replace("webcal://", "https://")
    assert mia_plays.gets == [https, https]
    [first] = harness.sent
    assert "MyClub 1 tapahtuma" in first.text  # "read tonight: MyClub 1 event"
    # The link is a secret: on no command line and in no log.
    assert not any(MIA_LINK in arg or https in arg for cmd in signup.host.commands for arg in cmd)
    assert "mia-1001-token" not in caplog.text


def test_a_kid_in_several_clubs_gives_a_link_for_each(signup, harness, mia_plays):
    """Under "Add another club", more fields for a Kid's other clubs (#810): each link checked
    once and all saved for that Kid, the first Brief reading every one."""
    swim = MIA_LINK.replace("kilonpallo", "uimaseura")
    harness.config["kids"][0]["myclub_ical_url"] = [MIA_LINK, swim]  # served by MyClub
    harness.sources["myclub"].append(myclub_event(
        "ev-2", "Uintikoulu", "2026-09-29T17:00:00+03:00", "2026-09-29T18:00:00+03:00",
        kid="Mia Virtanen"))
    mia_plays.feeds = {MIA_LINK: ["ev-1"], swim: ["ev-2"]}
    code = signup.invite(Recipient("parent@example.com", "fi"))
    html = text(sign_in(signup, code))
    assert f"<summary>{said('en', 'another_club')}</summary>" in html
    assert said("en", "another_club_link", kid="Mia Virtanen") in html
    for i in range(page.MAX_CLUBS - 1):
        assert f'name="m0_{i + 1}"' in html

    r = links(signup, code, "", "", m0_1=swim, m0_2=MIA_LINK, m1_1="")

    assert r.status == 200, text(r)
    _, user = signup.user()
    cfg = Config.load(user.home / ".family" / "config.yaml")
    assert {k.name: k.myclub_ical_url for k in cfg.kids} == {"Mia Virtanen": [swim, MIA_LINK],
                                                            "Leo Virtanen": []}
    [first] = harness.sent
    assert "MyClub 2 tapahtumaa" in first.text  # "read tonight: MyClub 2 events"


def test_one_kid_s_other_club_link_can_t_be_another_kid_s(signup, mia_plays):
    code = signup.invite(Recipient("parent@example.com", "fi"))
    assert sign_in(signup, code).status == 200

    r = links(signup, code, MIA_LINK, "", m1_1=MIA_LINK.replace("webcal://", "https://"))

    assert said("en", "same-link", kid="Leo Virtanen") in text(r)
    assert signup.host.users == {}  # nothing made


@pytest.mark.parametrize("given, error, status", [
    (MIA_LINK, "link-failed", 404),
    ("hunter2", "not-a-myclub-link", None),
    (MIA_LINK, "not-a-calendar", "<html>MyClub sign-in</html>"),
])
def test_a_myclub_link_that_won_t_do_is_said_and_nothing_is_made(
        signup, mia_plays, monkeypatch, given, error, status):
    code = signup.invite(Recipient("parent@example.com", "fi"))
    if isinstance(status, int):
        mia_plays.failures.append(status)
    elif status is not None:
        from fake_sources import _Feed
        monkeypatch.setattr(mia_plays, "get", lambda url, *a, **k: _Feed(200, status))
        from family_brief.collectors import myclub
        monkeypatch.setattr(myclub.requests, "get", mia_plays.get)
    assert sign_in(signup, code).status == 200

    r = links(signup, code, given, "")

    assert r.status == 200
    html = text(r)
    assert said("en", error, kid="Mia Virtanen") in html
    assert said("en", "paste_again") in html
    assert "mia-1001-token" not in html and "hunter2" not in html  # never shown back
    assert (signup.host.users, signup.host.keys) == ({}, {})
    # The checked sign-in still waits: leaving the link out signs up without MyClub.
    assert links(signup, code, "", "").status == 200
    _, user = signup.user()
    assert not any(k.myclub_ical_url for k in
                   Config.load(user.home / ".family" / "config.yaml").kids)


def test_one_link_for_two_kids_is_said(signup, mia_plays):
    code = signup.invite(Recipient("parent@example.com", "fi"))
    sign_in(signup, code)

    r = links(signup, code, MIA_LINK, MIA_LINK.replace("webcal://", "https://"))

    assert said("en", "same-link", kid="Leo Virtanen") in text(r)
    assert signup.host.users == {}


# ── Without Wilma (#760)

def test_a_family_without_wilma_signs_up_with_its_kids_names_and_a_myclub_link(
        signup, harness, mia_plays, caplog):
    caplog.set_level(logging.DEBUG)
    code = signup.invite(Recipient("parent@example.com", "fi"))

    first = sign_in(signup, code, **NO_WILMA, k0=" Mia  Virtanen ", k1="Leo", k2="Mia Virtanen")

    assert first.status == 200, text(first)
    html = text(first)
    assert said("en", "myclub_needed") in html and said("en", "myclub") not in html
    # Without Wilma, MyClub is what the Brief reads: not told it may leave every link empty.
    assert said("en", "myclub_needed_hint") in html and said("en", "myclub_hint") not in html
    assert '<label for="m0">Mia Virtanen</label>' in html
    assert '<label for="m1">Leo</label>' in html and 'for="m2"' not in html  # each name once
    r = links(signup, code, MIA_LINK, "")

    assert r.status == 200, text(r)
    assert marks(text(r), "en", page.NO_WILMA_STEPS) == "✅✅✅"
    assert said("en", "step_wilma") not in text(r)
    _, user = signup.user()
    cfg = Config.load(user.home / ".family" / "config.yaml")
    assert {k.name: k.myclub_ical_url for k in cfg.kids} == {"Mia Virtanen": [MIA_LINK], "Leo": []}
    assert not cfg.wilma.enabled and not cfg.whatsapp.enabled and cfg.hosted
    assert cfg.city is None
    # No Wilma sign-in anywhere: only the key in the secret store, and no sign-in file.
    assert set(user.secrets) == {"gateway-key"}
    assert not (user.home / ".family" / "wilma-signins.json").exists()
    [sent] = harness.sent
    assert "MyClub 1 tapahtuma" in sent.text
    assert "Wilma" not in sent.text.split("📥")[-1].splitlines()[0]  # read tonight: MyClub only
    assert "without Wilma" in caplog.text
    [household] = signup.store.households()
    assert household.user == signup.user()[0]


def test_without_wilma_a_myclub_link_eepos_or_evaka_is_needed_and_nothing_is_made_without_one(
        signup):
    code = signup.invite(Recipient("parent@example.com", "fi"))
    assert sign_in(signup, code, **NO_WILMA, lang="zh", k0="Mia").status == 200

    r = links(signup, code, "", lang="zh", **NO_EEPOS, **NO_EVAKA)

    assert r.status == 200
    assert said("zh", "need-myclub") in text(r)
    for language, names in (("en", ("MyClub", "Eepos", "eVaka")), ("zh", ("MyClub", "Eepos", "eVaka")),
                            ("fi", ("MyClub", "Eepokseen", "eVakaan"))):
        assert all(name in said(language, "need-myclub") for name in names)
    assert 'name="step" value="myclub"' in text(r)
    assert (signup.host.users, signup.host.keys) == ({}, {})
    assert request(signup.url, f"/join/{code}").status == 200  # the link still works


# ── Eepos (#769)

NO_EEPOS = {"eepos_school": "", "eepos_username": "", "eepos_password": ""}
EEPOS = {"eepos_school": "emo", "eepos_username": EEPOS_USERNAME, "eepos_password": EEPOS_PASSWORD}


@pytest.fixture
def emo(harness):
    """emo.eepos.fi on Eepos's fake portal, where the guardian's password is EEPOS_PASSWORD and
    Mia, Leo and Ella take lessons, and Mia has piano tomorrow."""
    fake = harness.source_fakes["eepos"]
    school = fake.school("emo")
    school.accounts[EEPOS_USERNAME] = EEPOS_PASSWORD
    school.students = [eepos_student(11, "Mia", "Virtanen"), eepos_student(12, "Leo", "Virtanen"),
                       eepos_student(13, "Ella", "Korhonen")]
    harness.sources["eepos"] = [eepos_entry("Virtanen Mia, Piano", "2026-09-28", students=(11,))]
    return fake


def test_a_family_without_wilma_signs_up_with_its_kids_names_and_an_eepos_sign_in(
        signup, harness, emo, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    code = signup.invite(Recipient("parent@example.com", "fi"))
    first = text(sign_in(signup, code, **NO_WILMA, k0="Mia Virtanen", k1="Leo"))
    # The MyClub step has the Eepos part, its password in a field that never has a value.
    assert said("en", "eepos_needed") in first and said("en", "eepos_hint") in first
    assert '<input id="eepos_password" name="eepos_password" type="password" ' \
           'autocomplete="off">' in first

    r = links(signup, code, "", "", eepos_school=" https://emo.eepos.fi/#/portal ",
              eepos_username=EEPOS_USERNAME, eepos_password=EEPOS_PASSWORD)

    assert r.status == 200, text(r)
    html = text(r)
    steps = page.steps(wilma=False, myclub=False, eepos=True)
    assert marks(html, "en", steps) == "✅✅✅" and said("en", "step_eepos") in html
    # Ella is none of the Kids: said, and her lessons go to the whole Household.
    assert said("en", "eepos_unmatched", names="Ella Korhonen") in html
    name, user = signup.user()
    config = yaml.safe_load((user.home / ".family" / "config.yaml").read_text())
    assert config["eepos"] == {"signins": [{"school": "emo", "username": EEPOS_USERNAME,
                                            "kids": {"11": "Mia Virtanen", "12": "Leo"}}]}
    cfg = Config.load(user.home / ".family" / "config.yaml")
    assert not cfg.wilma.enabled and not any(k.myclub_ical_url for k in cfg.kids)
    secret = eepos.secret_name("emo", EEPOS_USERNAME)
    assert set(user.secrets) == {"gateway-key", secret}
    assert user.secrets[secret] == EEPOS_PASSWORD
    [sent] = harness.sent
    assert "Eepos 1 tapahtuma" in sent.text  # "read tonight: Eepos 1 event"
    assert "without Wilma" in caplog.text and "and Eepos" in caplog.text
    # The password: on no command line, in no log or output, in no page, nor in the config.
    printed = "".join(capsys.readouterr())
    assert EEPOS_PASSWORD not in caplog.text + printed + first + html
    assert EEPOS_PASSWORD not in json.dumps(config)
    assert not any(EEPOS_PASSWORD in arg for cmd in [*signup.host.commands, *harness.commands]
                   for arg in cmd)
    assert EEPOS_USERNAME not in caplog.text and "Ella" not in caplog.text


def test_a_family_with_wilma_adds_eepos(signup, harness, emo):
    code = signup.invite(Recipient("parent@example.com", "fi"))
    assert said("en", "eepos") in text(sign_in(signup, code))

    r = links(signup, code, "", "", **EEPOS)

    assert r.status == 200, text(r)
    html = text(r)
    assert marks(html, "en", page.steps(wilma=True, myclub=False, eepos=True)) == "✅✅✅"
    _, user = signup.user()
    cfg = Config.load(user.home / ".family" / "config.yaml")
    assert cfg.wilma.enabled
    [signin] = cfg.eepos.signins
    assert signin.kids == {"11": "Mia Virtanen", "12": "Leo Virtanen"}  # as Wilma lists them
    assert user.secrets[eepos.secret_name("emo", EEPOS_USERNAME)] == EEPOS_PASSWORD
    assert setup_wilma.secret_name(f"{ESPOO}|mia.parent") in user.secrets
    assert "Eepos 1 tapahtuma" in harness.sent[0].text


def test_a_wrong_eepos_password_is_said_and_nothing_is_made(signup, emo, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    code = signup.invite(Recipient("parent@example.com", "fi"))
    assert sign_in(signup, code, **NO_WILMA, lang="fi", k0="Mia Virtanen").status == 200

    r = links(signup, code, "", lang="fi", **EEPOS | {"eepos_password": "wrong-eepos-salasana"})

    assert r.status == 200
    html = text(r)
    assert said("fi", "eepos-wrong-password") in html and said("fi", "paste_again") in html
    assert 'value="emo"' in html and f'value="{EEPOS_USERNAME}"' in html  # typed again, but
    assert b"wrong-eepos-salasana" not in r.body  # never the password
    assert "wrong-eepos-salasana" not in caplog.text + "".join(capsys.readouterr())
    assert (signup.host.users, signup.host.keys, signup.host.commands) == ({}, {}, [])
    assert signup.store.households() == []
    assert request(signup.url, f"/join/{code}").status == 200  # the link still works
    # The names still wait: the right password signs up.
    assert said("fi", "sent", time="21:00") in text(links(signup, code, "", lang="fi", **EEPOS))


@pytest.mark.parametrize("fields, answer, error", [
    ({"eepos_password": ""}, None, "eepos-incomplete"),
    ({"eepos_school": "emo.example.com"}, None, "eepos-not-a-school"),
    ({}, (200, ")]}',\n{}"), "eepos-changed"),
    ({}, (502, "Bad gateway"), "eepos-no-answer"),
    ({}, eepos.requests.ConnectionError("no route"), "eepos-no-answer"),
])
def test_an_eepos_sign_in_that_won_t_do_is_said_and_nothing_is_made(
        signup, emo, fields, answer, error):
    if answer is not None:
        emo.answers[("emo", "auth/login")] = answer
    code = signup.invite(Recipient("parent@example.com", "fi"))
    sign_in(signup, code)

    r = links(signup, code, "", "", **EEPOS | fields)

    assert r.status == 200
    assert said("en", error) in text(r)
    assert 'name="step" value="myclub"' in text(r)
    assert (signup.host.users, signup.host.keys) == ({}, {})


# ── eVaka (#790)

NO_EVAKA = {"evaka_city": "", "evaka_username": "", "evaka_password": ""}
EVAKA_USERNAME = "parent@example.com"
EVAKA = {"evaka_city": "Espoo", "evaka_username": EVAKA_USERNAME, "evaka_password": EVAKA_PASSWORD}
MIA_ID, ELLA_ID = "1a2b3c4d-0000-4000-8000-000000000011", "1a2b3c4d-0000-4000-8000-000000000013"


@pytest.fixture
def espoo(harness):
    """Espoo's eVaka on the fake, where the guardian's password is EVAKA_PASSWORD and Mia and
    Ella are in daycare, and Mia's group has a trip tomorrow."""
    fake = harness.source_fakes["evaka"]
    city = fake.city("espoo")
    city.accounts[EVAKA_USERNAME] = EVAKA_PASSWORD
    city.children = [evaka_child(MIA_ID, "Mia Emilia", "Virtanen"),
                     evaka_child(ELLA_ID, "Ella", "Korhonen")]
    harness.sources["evaka"] = [evaka_event("Retki Nuuksioon", "2026-09-28", children=(MIA_ID,))]
    return fake


def test_the_evaka_city_list_is_every_known_city_as_a_family_writes_it():
    assert [evaka.city_of(c) for c in page.EVAKA_CITIES] == list(evaka.CITIES)
    assert evaka.city_of(" Hämeenkyrö ") == "hameenkyro" and evaka.city_of("Ylöjärvi") == "ylojarvi"


def test_a_family_without_wilma_signs_up_with_its_kids_names_and_an_evaka_sign_in(
        signup, harness, espoo, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    code = signup.invite(Recipient("parent@example.com", "fi"))
    first = text(sign_in(signup, code, **NO_WILMA, k0="Mia Virtanen", k1="Leo"))
    # The MyClub step has the eVaka part: the cities to pick from, and its password in a field
    # that never has a value.
    assert said("en", "evaka_needed") in first and said("en", "evaka_hint") in first
    assert '<option value="Hämeenkyrö">' in first
    assert '<input id="evaka_password" name="evaka_password" type="password" ' \
           'autocomplete="off">' in first

    r = links(signup, code, "", "", **EVAKA)

    assert r.status == 200, text(r)
    html = text(r)
    steps = page.steps(wilma=False, myclub=False, eepos=False, evaka=True)
    assert marks(html, "en", steps) == "✅✅✅" and said("en", "step_evaka") in html
    # Ella is none of the Kids: said, and her daycare's events go to the whole Household.
    assert said("en", "evaka_unmatched", names="Ella Korhonen") in html
    name, user = signup.user()
    config = yaml.safe_load((user.home / ".family" / "config.yaml").read_text())
    assert config["evaka"] == {"signins": [{"city": "espoo", "username": EVAKA_USERNAME,
                                            "kids": {MIA_ID: "Mia Virtanen"}}]}
    assert "eepos" not in config
    secret = evaka.secret_name("espoo", EVAKA_USERNAME)
    assert set(user.secrets) == {"gateway-key", secret}
    assert user.secrets[secret] == EVAKA_PASSWORD
    # The device cookie the service's check got is the Household's, so the city sees one device.
    [checked, *_] = [q for q in espoo.requests if q["path"] == evaka.SIGN_IN]
    devices = user.home / ".family" / evaka.DEVICES
    kept = json.loads(devices.read_text())["espoonvarhaiskasvatus.fi"]
    assert kept and checked["devices"] == {}
    assert [q["devices"] for q in espoo.requests if q["path"] == evaka.SIGN_IN][1:] == [kept]
    assert not (harness.home / ".family" / evaka.DEVICES).exists()  # none left by the service
    [sent] = harness.sent
    # "read tonight: eVaka 2 events": the trip and when next week's reservations close
    assert "eVaka 2 tapahtumaa" in sent.text and "Retki Nuuksioon" in sent.text
    assert "without Wilma" in caplog.text and "and eVaka" in caplog.text
    # The password: on no command line, in no log or output, in no page, nor in the config.
    printed = "".join(capsys.readouterr())
    assert EVAKA_PASSWORD not in caplog.text + printed + first + html
    assert EVAKA_PASSWORD not in json.dumps(config)
    assert not any(EVAKA_PASSWORD in arg for cmd in [*signup.host.commands, *harness.commands]
                   for arg in cmd)
    assert EVAKA_USERNAME not in caplog.text and "Ella" not in caplog.text


def test_a_family_with_wilma_adds_eepos_and_evaka(signup, harness, emo, espoo):
    code = signup.invite(Recipient("parent@example.com", "fi"))
    assert said("fi", "evaka") in text(sign_in(signup, code, lang="fi"))

    r = links(signup, code, "", "", lang="fi", **EEPOS, **EVAKA | {"evaka_city": "espoo"})

    assert r.status == 200, text(r)
    html = text(r)
    steps = page.steps(wilma=True, myclub=False, eepos=True, evaka=True)
    assert marks(html, "fi", steps) == "✅✅✅"
    assert "Wilma, Eepos ja eVaka yhdistetään" in html
    assert said("fi", "evaka_unmatched", names="Ella Korhonen") in html
    _, user = signup.user()
    cfg = Config.load(user.home / ".family" / "config.yaml")
    assert cfg.wilma.enabled and len(cfg.eepos.signins) == 1
    [signin] = cfg.evaka.signins
    assert (signin.city, signin.kids) == ("espoo", {MIA_ID: "Mia Virtanen"})  # as Wilma lists her
    assert user.secrets[evaka.secret_name("espoo", EVAKA_USERNAME)] == EVAKA_PASSWORD
    assert user.secrets[eepos.secret_name("emo", EEPOS_USERNAME)] == EEPOS_PASSWORD
    assert "eVaka 2 tapahtumaa" in harness.sent[0].text and "Eepos 1 tapahtuma" in harness.sent[0].text


def test_the_steps_name_what_they_connect_in_each_language():
    assert page.TEXT["en"]["step_wilma_eepos_evaka"] == "Connecting Wilma, Eepos and eVaka"
    assert page.TEXT["zh"]["step_myclub_evaka"] == "连接 MyClub 和 eVaka"
    assert page.TEXT["fi"]["step_eepos"] == "Eepos yhdistetään"
    assert page.steps(wilma=True, myclub=True, eepos=False, evaka=False) == page.STEPS
    assert page.steps(wilma=False, myclub=True, eepos=False, evaka=False) == page.NO_WILMA_STEPS
    assert page.steps(wilma=False, myclub=True, eepos=True, evaka=True)[1] == "step_myclub_eepos_evaka"


def test_a_wrong_evaka_password_is_said_and_nothing_is_made(signup, espoo, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    code = signup.invite(Recipient("parent@example.com", "fi"))
    assert sign_in(signup, code, **NO_WILMA, lang="zh", k0="Mia Virtanen").status == 200

    r = links(signup, code, "", lang="zh", **EVAKA | {"evaka_password": "wrong-evaka-mima"})

    assert r.status == 200
    html = text(r)
    assert said("zh", "evaka-wrong-password") in html and said("zh", "paste_again") in html
    assert 'value="Espoo"' in html and f'value="{EVAKA_USERNAME}"' in html  # typed again, but
    assert b"wrong-evaka-mima" not in r.body  # never the password
    assert "wrong-evaka-mima" not in caplog.text + "".join(capsys.readouterr())
    assert (signup.host.users, signup.host.keys, signup.host.commands) == ({}, {}, [])
    assert signup.store.households() == []
    assert request(signup.url, f"/join/{code}").status == 200  # the link still works
    # The names still wait: the right password signs up.
    assert said("zh", "sent", time="21:00") in text(links(signup, code, "", lang="zh", **EVAKA))


@pytest.mark.parametrize("fields, answer, error", [
    ({"evaka_password": ""}, None, "evaka-incomplete"),
    ({"evaka_city": ""}, None, "evaka-incomplete"),
    ({"evaka_city": "Helsinki!"}, None, "evaka-not-a-city"),
    ({}, (200, "OK"), "evaka-changed"),  # no session cookie
    ({}, (502, "Bad gateway"), "evaka-no-answer"),
    ({}, evaka.requests.ConnectionError("no route"), "evaka-no-answer"),
])
def test_an_evaka_sign_in_that_won_t_do_is_said_and_nothing_is_made(
        signup, espoo, fields, answer, error):
    if answer is not None:
        espoo.answers[("espoo", evaka.SIGN_IN)] = answer
    code = signup.invite(Recipient("parent@example.com", "fi"))
    sign_in(signup, code)

    r = links(signup, code, "", "", **EVAKA | fields)

    assert r.status == 200
    assert said("en", error) in text(r)
    assert 'name="step" value="myclub"' in text(r)
    assert (signup.host.users, signup.host.keys) == ({}, {})


def test_neither_wilma_nor_names_is_said_and_nothing_is_made(signup):
    code = signup.invite(Recipient("parent@example.com", "fi"))

    r = sign_in(signup, code, **NO_WILMA, lang="fi")

    assert r.status == 400
    assert said("fi", "invalid-sign-in") in text(r)
    assert (signup.host.users, signup.host.keys) == ({}, {})


def test_names_given_beside_a_wilma_sign_in_are_left_to_wilma(signup):
    code = signup.invite(Recipient("parent@example.com", "fi"))

    r = sign_in(signup, code, k0="Somebody Else")

    html = text(r)
    assert '<label for="m0">Mia Virtanen</label>' in html and "Somebody Else" not in html


def test_a_myclub_step_left_too_long_starts_over_and_the_link_stays_good(signup):
    code = signup.invite(Recipient("parent@example.com", "fi"))
    sign_in(signup, code)
    signup.clock.now += timedelta(minutes=31)

    r = links(signup, code, "", "")

    assert r.status == 200
    html = text(r)
    assert said("en", "expired") in html and 'name="password"' in html
    assert signup.host.users == {}
    assert submit(signup, code).status == 200  # signing in again goes through


def test_each_household_s_evening_jobs_get_the_next_free_slot(signup):
    evenings = []
    for n in range(3):
        code = signup.invite(Recipient(f"parent{n}@example.com", "fi"))
        assert submit(signup, code).status == 200
        evenings.append(signup.store.households()[-1].evening)

    assert evenings == ["21:00", "21:05", "21:10"]
    configs = [Config.load(u.home / ".family" / "config.yaml").schedule
               for u in signup.host.users.values()]
    assert sorted((c.daily_hour, c.daily_minute) for c in configs) == [(21, 0), (21, 5), (21, 10)]
    assert len(set(signup.host.keys.values())) == 3


def test_a_wrong_wilma_password_is_said_on_the_page_and_nothing_is_made(signup, caplog):
    caplog.set_level(logging.DEBUG)
    code = signup.invite(Recipient("parent@example.com", "fi"))

    r = submit(signup, code, password="wrong-salasana", lang="fi")

    assert r.status == 200
    html = text(r)
    assert said("fi", "wrong-password") in html
    assert 'value="mia.parent"' in html and 'value="parent@example.com"' in html
    assert b"wrong-salasana" not in r.body and "wrong-salasana" not in caplog.text
    assert (signup.host.users, signup.host.keys, signup.host.commands) == ({}, {}, [])
    assert signup.store.households() == []
    assert request(signup.url, f"/join/{code}").status == 200  # the link still works
    assert submit(signup, code).status == 200  # and signs up with the right one


@pytest.mark.parametrize("fields, error", [
    ({"town": "Atlantis"}, "invalid-town"),
    ({"password": ""}, "invalid-sign-in"),
    ({"r0": "not an address"}, "invalid-recipients"),
    ({"r0": ""}, "invalid-recipients"),
])
def test_an_answer_that_won_t_do_is_said_and_nothing_is_made(signup, fields, error):
    code = signup.invite(Recipient("parent@example.com", "fi"))

    r = submit(signup, code, **fields)

    assert r.status == 400
    assert said("en", error) in text(r)
    assert (signup.host.users, signup.host.keys) == ({}, {})


def test_a_town_only_one_wilma_has_is_taken_as_typed(signup):
    code = signup.invite(Recipient("parent@example.com", "fi"))

    # Järvenpää has one Wilma; the fake CLI knows no account there, so Wilma refuses the login.
    r = submit(signup, code, town="järvenpää")

    assert r.status == 200 and said("en", "wrong-password") in text(r)


@pytest.mark.parametrize("step, marked", [("create_user", "❌⬜⬜"), ("mint_key", "❌⬜⬜"),
                                          ("jobs", "✅❌⬜"), ("first_brief", "✅✅❌")])
def test_a_step_that_fails_takes_back_what_was_made_and_the_link_stays_good(
        signup, harness, step, marked, caplog):
    code = signup.invite(Recipient("parent@example.com", "fi"))
    if step == "first_brief":
        harness.relay_answer = (502, {"error": "Email Delivery refused the message"})
    else:
        signup.host.fails = step

    r = submit(signup, code)

    assert r.status == 500
    assert marks(text(r), "en") == marked  # the step that failed, then why
    assert text(r).index("❌") < text(r).index(said("en", "failed"))
    # No user with its jobs, no key, no files.
    assert signup.host.users == {} and signup.host.keys == {}
    assert len(signup.host.removed) == 1 and signup.host.revoked == signup.host.removed
    assert not (signup.host.root / signup.host.removed[0]).exists()
    assert signup.store.households() == []
    assert harness.sent == []
    # The link is unused: it signs up once the step works again.
    assert request(signup.url, f"/join/{code}").status == 200
    assert "wasn't created" in caplog.text
    signup.host.fails, harness.relay_answer = None, (200, {"sent": 1})
    assert submit(signup, code).status == 200


def test_a_first_brief_one_recipient_didn_t_get_takes_the_sign_up_back(
        signup, harness, monkeypatch):
    from family_brief import gateway
    from fake_model import _response
    relay = gateway.post_brief  # the fake Email Delivery's

    def refuses_partner(url, key, body, timeout=0):
        if "partner@example.com" in body["to"]:
            return _response(502, {"error": "Email Delivery refused the message"})
        return relay(url, key, body, timeout)
    monkeypatch.setattr(gateway, "post_brief", refuses_partner)
    code = signup.invite(Recipient("parent@example.com", "fi"))

    r = submit(signup, code, r1="partner@example.com", r1_language="zh")

    # The parent's went out, but the page mustn't say the first Brief was sent to both.
    assert [m.to for m in harness.sent] == [["parent@example.com"]]
    assert r.status == 500 and said("en", "failed") in text(r)
    assert signup.host.users == {} and signup.host.keys == {}
    assert request(signup.url, f"/join/{code}").status == 200


def test_a_household_the_program_couldn_t_make_is_taken_back(signup):
    code = signup.invite(Recipient("parent@example.com", "fi"))
    # A shared copy without its Node and wilma CLI, so `signup household` can't install it.
    shutil.rmtree(signup.host.shared / "runtime")
    shutil.rmtree(signup.host.shared / "wilma")

    r = submit(signup, code)

    assert r.status == 500 and marks(text(r), "en") == "✅❌⬜"
    assert signup.host.users == {} and signup.host.keys == {}


# ── Links that aren't good

def _not_found(r: Response) -> None:
    assert (r.status, r.body, r.headers["content-type"]) == (
        404, b"Not found", "text/plain; charset=utf-8")


def test_an_invite_link_works_once(signup):
    code = signup.invite(Recipient("parent@example.com", "fi"))
    assert submit(signup, code).status == 200

    _not_found(request(signup.url, f"/join/{code}"))
    _not_found(submit(signup, code))
    assert len(signup.host.users) == 1


def test_an_expired_invite_link_is_not_found(signup):
    code = signup.invite(Recipient("parent@example.com", "fi"), days=7)
    signup.clock.now += timedelta(days=7)

    _not_found(request(signup.url, f"/join/{code}"))
    _not_found(submit(signup, code))
    assert signup.host.users == {}


@pytest.mark.parametrize("path", ["/join/" + "a" * 32, "/join/", "/join/../signup", "/",
                                  "/v1/messages", "/join/short"])
def test_a_missing_invite_link_or_any_other_address_is_not_found(signup, path):
    signup.invite(Recipient("parent@example.com", "fi"))

    _not_found(request(signup.url, path))
    _not_found(request(signup.url, path, "POST", {"password": WILMA_PASSWORD}))


def test_the_service_answers_its_health_check_on_localhost(signup):
    r = request(signup.url, "/health")

    assert (r.status, r.body) == (200, b"ok")


def test_a_sign_up_that_isn_t_the_page_s_form_is_not_found(signup):
    code = signup.invite(Recipient("parent@example.com", "fi"))

    _not_found(request(signup.url, f"/join/{code}", "POST", {"x": "y"},
                       content_type="application/json"))
    assert signup.host.users == {}


# ── The invite command

def test_the_invite_command_writes_an_owner_only_invite_and_prints_its_link(
        harness, wilma_cli, tmp_path, monkeypatch, capsys):  # noqa: F811
    monkeypatch.setenv("SIGNUP_STORE", str(tmp_path / "store"))
    monkeypatch.setenv("GATEWAY_HOST", "gateway.example")

    code = harness.cli("signup", "invite", "--recipient", "parent@example.com",
                       "--recipient", "isa@example.com:zh", "--language", "en", "--days", "3")

    assert code == 0
    link = capsys.readouterr().out.strip()
    assert link.startswith("https://gateway.example/join/")
    store = Store(tmp_path / "store")
    invite = store.valid(link.rsplit("/", 1)[1], datetime.now(timezone.utc))
    assert invite is not None and invite.language == "en"
    assert invite.recipients == [Recipient("parent@example.com", "en"),
                                 Recipient("isa@example.com", "zh")]
    assert invite.expires - datetime.now(timezone.utc) == timedelta(days=3)
    assert invite.note == ""  # none given
    [written] = (tmp_path / "store" / "invites").iterdir()
    for p in (tmp_path / "store", written.parent, written):
        assert stat.S_IMODE(p.stat().st_mode) & 0o077 == 0, p
    assert store.valid(invite.code, datetime.now(timezone.utc) + timedelta(days=3)) is None


def test_the_invite_command_keeps_the_maintainers_note_on_one_line(
        harness, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SIGNUP_STORE", str(tmp_path / "store"))

    assert harness.cli("signup", "invite", "--url", "https://g.example",
                       "--note", " 赵晗博,\n test   family ") == 0

    code = capsys.readouterr().out.strip().rsplit("/", 1)[1]
    invite = Store(tmp_path / "store").valid(code, datetime.now(timezone.utc))
    assert invite is not None and invite.note == "赵晗博, test family"


def test_a_household_record_round_trips_and_one_written_before_notes_still_loads(tmp_path):
    store = Store(tmp_path)
    store.add_household(HouseholdRecord("ph-0123abcd", "21:00", "2026-10-30T12:00:00+00:00",
                                        "赵晗博, test family"))
    assert store.households()[0].note == "赵晗博, test family"

    (tmp_path / "households.json").write_text(json.dumps(
        [{"user": "ph-0123abcd", "evening": "21:00", "created": "2026-10-30T12:00:00+00:00"}]))
    assert store.households() == [
        HouseholdRecord("ph-0123abcd", "21:00", "2026-10-30T12:00:00+00:00")]


def test_two_invites_get_different_codes(tmp_path):
    store = Store(tmp_path)
    codes = {store.mint([], "fi", NOON).code for _ in range(5)}
    assert len(codes) == 5


def test_the_invite_command_refuses_a_recipient_it_can_t_read(harness, monkeypatch, tmp_path):
    monkeypatch.setenv("SIGNUP_STORE", str(tmp_path / "store"))

    assert harness.cli("signup", "invite", "--url", "https://g.example",
                       "--recipient", "parent@example.com:sv") == 2
    assert not (tmp_path / "store").exists()


# ── Removing a Household

def _signed_up(signup) -> str:
    before = set(signup.host.users)
    assert submit(signup, signup.invite(Recipient("parent@example.com", "fi"))).status == 200
    [name] = set(signup.host.users) - before
    return name


def test_a_household_is_removed_jobs_key_user_and_home_in_that_order(signup, capsys,
                                                                     monkeypatch):
    name, other = _signed_up(signup), _signed_up(signup)
    user = signup.host.users[name]
    # What the first Brief left for the status command, in its home, goes with it.
    with monkeypatch.context() as mp:
        mp.setenv("HOME", str(user.home))  # as its config's ~ is
        last_run = last_run_path(Config.load(user.home / ".family" / "config.yaml"))
    assert last_run.is_relative_to(user.home) and last_run.exists()
    signup.host.log.clear()
    capsys.readouterr()

    assert remove(name, signup.store, signup.host) == 0

    assert signup.host.log == [("run_as", name, PROGRAM, "schedule", "uninstall"),
                               ("revoke_key", name), ("remove_user", name)]
    assert user.jobs.installed() == set()
    assert not user.home.exists()
    # Nothing of it stays; the other Household and the shared copy are as they were.
    assert signup.host.python.parent.is_dir() and (signup.host.shared / "wilma").is_dir()
    assert list(signup.host.users) == [other] and list(signup.host.keys) == [other]
    assert signup.host.users[other].jobs.installed() == {jobs.DAILY}
    assert [h.user for h in signup.store.households()] == [other]
    out = capsys.readouterr().out.splitlines()
    assert [line.split(" ", 1)[0] for line in out] == ["✅"] * 4, out
    assert "jobs" in out[0] and "Gateway Key" in out[1] and str(user.home) in out[2]
    assert all(name in line for line in out)
    assert status.report(signup.store, signup.host) == 0
    listed = capsys.readouterr().out
    assert f" {other} " in listed and name not in listed


@pytest.mark.parametrize("label", ["ph-0123abcd", "household-mia", "../ph-0123abcd"])
def test_a_label_that_is_no_household_is_refused_and_nothing_is_touched(signup, capsys, label):
    name = _signed_up(signup)
    signup.host.log.clear()
    capsys.readouterr()

    assert remove(label, signup.store, signup.host) != 0

    err = capsys.readouterr().err
    assert label in err and "Nothing was removed" in err
    assert signup.host.log == [] and list(signup.host.keys) == [name]
    assert [h.user for h in signup.store.households()] == [name]


@pytest.mark.parametrize("step", ["revoke_key", "remove_user"])
def test_a_household_removed_half_way_is_removed_again_to_the_end(signup, capsys, step):
    name = _signed_up(signup)
    home = signup.host.users[name].home
    signup.host.fails = step

    assert remove(name, signup.store, signup.host) == 1
    assert "Run the command again" in capsys.readouterr().err
    assert [h.user for h in signup.store.households()] == [name]  # still known

    signup.host.fails = None
    assert remove(name, signup.store, signup.host) == 0

    assert signup.host.users == {} and signup.host.keys == {} and not home.exists()
    assert signup.store.households() == []
    assert remove(name, signup.store, signup.host) == 1  # and now it is gone


def test_a_household_whose_user_is_gone_is_taken_off_the_list(signup, capsys):
    name = _signed_up(signup)
    signup.host.remove_user(name)
    signup.host.log.clear()
    capsys.readouterr()

    assert remove(name, signup.store, signup.host) == 0

    assert signup.host.log == [("revoke_key", name)]  # no command run as a user that isn't there
    assert signup.host.keys == {} and signup.store.households() == []
    out = capsys.readouterr().out
    assert "no jobs to stop" in out and "no user to delete" in out


def test_jobs_its_program_can_t_remove_still_go_with_its_user(signup, capsys, monkeypatch):
    name = _signed_up(signup)
    run_as = signup.host.run_as

    def no_bus(user: str, argv: list[str], stdin: str, timeout: int) -> tuple[int, str]:
        if argv[1:] == ["schedule", "uninstall"]:  # the user's systemd didn't answer
            return 1, "Failed to connect to bus: No medium found\n"
        return run_as(user, argv, stdin, timeout)
    monkeypatch.setattr(signup.host, "run_as", no_bus)
    capsys.readouterr()

    assert remove(name, signup.store, signup.host) == 0

    assert signup.host.users == {} and signup.store.households() == []
    out = capsys.readouterr().out
    assert "Failed to connect to bus" in out and "deleting its user below stops them" in out


def test_the_remove_command_needs_the_gateway_s_admin_login(harness, monkeypatch, tmp_path,
                                                            capsys):
    monkeypatch.setenv("SIGNUP_STORE", str(tmp_path / "store"))
    monkeypatch.delenv("BIFROST_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("BIFROST_ADMIN_PASSWORD", raising=False)

    assert harness.cli("signup", "remove", "ph-0123abcd") == 1
    assert "BIFROST_ADMIN_USERNAME and BIFROST_ADMIN_PASSWORD not set" in capsys.readouterr().err
