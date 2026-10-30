"""The hosted version's sign-up (ADR 0031), driven over HTTP as a family's phone drives it: the
page under an Invite Link, the Wilma check, the Household made under its own user, and the
404 every other request gets.

The outside edges are faked: the service's wilma CLI is `test_setup_wilma`'s fake, which knows one
Wilma account; the household host is `fake_household_host`'s, whose users each have their own
home, secrets and jobs and run the real `signup household` in-process; the system is the
harness's, set to Linux. The service is the real one, on a free port or through socket pairs.
Assertions are on the pages, on what each Household's user was left with, on the store, and on
every place the Wilma password and the Gateway Key must never reach.
"""
from __future__ import annotations

import http.client
import json
import logging
import stat
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse

import pytest
import yaml

from family_brief import setup_wilma
from family_brief.config import Config
from family_brief.signup import page
from family_brief.signup.invites import Recipient, Store
from family_brief.signup.server import Service, SignupServer
from family_brief.system import jobs
from fake_household_host import FakeHouseholdHost
from fake_system import LINUX
from setup_page import CAN_LISTEN, Response, _Connection, _PairedServer
from test_setup_wilma import ESPOO, WILMA_PASSWORD, WilmaCLI
from test_setup_wilma import wilma_cli  # noqa: F401

pytestmark = pytest.mark.posix  # the fake wilma CLI is a script on a shell-script Node

ESPOO_TOWN = "Espoo – Espoon kaupunki / Suomenkielisen opetuksen tulosyksikkö"
NOON = datetime(2026, 10, 30, 10, 0, tzinfo=timezone.utc)  # 12:00 in Helsinki


class Clock:
    def __init__(self) -> None:
        self.now = NOON

    def __call__(self) -> datetime:
        return self.now


class SignUp:
    """The service serving in the background, its store, its household host and its clock."""

    def __init__(self, tmp: Path) -> None:
        self.store = Store(tmp / "signup")
        self.host = FakeHouseholdHost(tmp / "home", lambda home: WilmaCLI(home).install())
        self.clock = Clock()
        release = tmp / "release"
        release.mkdir()
        (release / "install.sh").write_text("#!/bin/bash\n")
        self.server = SignupServer(Service(self.store, self.host, release, now=self.clock), 0,
                                   listener=None if CAN_LISTEN else _PairedServer)
        self.url = self.server.url

    def invite(self, *recipients: Recipient, language: str = "fi", days: int = 14) -> str:
        return self.store.mint(list(recipients), language, self.clock.now, days).code

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


def request(url: str, path: str, method: str = "GET", fields: dict[str, str] | None = None,
            content_type: str = "application/x-www-form-urlencoded") -> Response:
    parts = urlparse(url)
    conn = _Connection(parts.hostname, parts.port, timeout=10)
    body = urlencode(fields).encode() if fields is not None else None
    headers = {"Host": "gateway.example"} | ({"Content-Type": content_type} if body else {})
    try:
        conn.request(method, path, body, headers=headers)
        r = conn.getresponse()
        return Response(r.status, {k.lower(): v for k, v in r.getheaders()}, r.read())
    finally:
        conn.close()


def submit(s: SignUp, code: str, **fields: str) -> Response:
    answers = {"lang": "en", "town": ESPOO_TOWN, "username": "mia.parent",
               "password": WILMA_PASSWORD, "r0": "parent@example.com", "r0_language": "fi",
               "r1": "", "r1_language": "en"} | fields
    return request(s.url, f"/join/{code}", "POST", answers)


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
    assert '<input id="password" name="password" type="password" required ' \
           'autocomplete="current-password">' in html
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

def test_a_good_sign_up_makes_the_household_and_says_when_the_first_brief_comes(
        signup, harness, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    code = signup.invite(Recipient("parent@example.com", "fi"))

    r = submit(signup, code, r1="partner@example.com", r1_language="zh")

    assert r.status == 200, text(r)
    html = text(r)
    assert said("en", "first", day=page.TEXT["en"]["today"], time="21:00") in html
    assert '<html lang="en">' in html
    name, user = signup.user()
    key = signup.host.keys[name]
    # The program installed, then `signup household` as the Household, the key on its stdin.
    program = str(user.home / "ParentRecap" / "app" / ".venv" / "bin" / "parent-recap")
    assert [c[1:] for c in signup.host.commands] == [
        ["bash", str(signup.server.service.release / "install.sh")],
        [program, "signup", "household"]]
    cfg = Config.load(user.home / ".family" / "config.yaml")
    assert [k.name for k in cfg.kids] == ["Mia Virtanen", "Leo Virtanen"]  # as Wilma lists them
    assert [(t.address, t.language) for t in cfg.email.to] == [
        ("parent@example.com", "fi"), ("partner@example.com", "zh")]
    assert cfg.summary_language == "en" and cfg.city == "Espoo"
    assert (cfg.llm.backend, cfg.email.sender) == ("gateway", "gateway")
    assert cfg.wilma.enabled and not cfg.whatsapp.enabled
    assert cfg.google_calendar.mode == "ics"
    assert (cfg.schedule.daily_hour, cfg.schedule.daily_minute) == (21, 0)
    assert user.secrets == {"gateway-key": key}
    assert user.jobs.installed() == {jobs.DAILY}
    wilma = json.loads((user.home / ".config" / "wilmai" / "config.json").read_text())
    [profile] = wilma["profiles"]
    assert (profile["tenantUrl"], profile["username"]) == (ESPOO, "mia.parent")
    assert profile["passwordObfuscated"] == setup_wilma.obfuscate(WILMA_PASSWORD)
    assert [s["name"] for s in profile["students"]] == ["Mia Virtanen", "Leo Virtanen"]
    # Listed, without the family's addresses, and the link retired with its Recipients.
    [household] = signup.store.households()
    assert (household.user, household.evening) == (name, "21:00")
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


def test_a_sign_up_after_the_evening_s_slot_says_the_first_brief_comes_tomorrow(signup):
    signup.clock.now = NOON.replace(hour=19, minute=30)  # 21:30 in Helsinki
    code = signup.invite(Recipient("parent@example.com", "fi"))

    html = text(submit(signup, code, lang="zh"))

    assert said("zh", "first", day=page.TEXT["zh"]["tomorrow"], time="21:00") in html


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


@pytest.mark.parametrize("step", ["create_user", "mint_key", "install"])
def test_a_step_that_fails_takes_back_what_was_made_and_the_link_stays_good(signup, step, caplog):
    code = signup.invite(Recipient("parent@example.com", "fi"))
    if step == "install":
        signup.host.install_exit = 1
    else:
        signup.host.fails = step

    r = submit(signup, code)

    assert r.status == 500
    assert said("en", "failed") in text(r)
    assert signup.host.users == {} and signup.host.keys == {}
    assert len(signup.host.removed) == 1 and signup.host.revoked == signup.host.removed
    assert signup.store.households() == []
    assert request(signup.url, f"/join/{code}").status == 200
    assert "wasn't created" in caplog.text


def test_a_household_the_program_couldn_t_make_is_taken_back(signup):
    code = signup.invite(Recipient("parent@example.com", "fi"))
    # The install left no wilma CLI and no Node, so `signup household` can't install it.
    signup.host.installs = lambda home: None

    r = submit(signup, code)

    assert r.status == 500
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
    [written] = (tmp_path / "store" / "invites").iterdir()
    for p in (tmp_path / "store", written.parent, written):
        assert stat.S_IMODE(p.stat().st_mode) & 0o077 == 0, p
    assert store.valid(invite.code, datetime.now(timezone.utc) + timedelta(days=3)) is None


def test_two_invites_get_different_codes(tmp_path):
    store = Store(tmp_path)
    codes = {store.mint([], "fi", NOON).code for _ in range(5)}
    assert len(codes) == 5


def test_the_invite_command_refuses_a_recipient_it_can_t_read(harness, monkeypatch, tmp_path):
    monkeypatch.setenv("SIGNUP_STORE", str(tmp_path / "store"))

    assert harness.cli("signup", "invite", "--url", "https://g.example",
                       "--recipient", "parent@example.com:sv") == 2
    assert not (tmp_path / "store").exists()
