"""The hosted version's sign-up (ADR 0031), driven over HTTP as a family's phone drives it: the
page under an Invite Link, the Wilma check, each Kid's MyClub link, the Household made under its
own user with its first Brief sent, and the 404 every other request gets; then the maintainers'
command that removes a Household again.

The outside edges are faked: the service's wilma CLI is `test_setup_wilma`'s fake, which knows one
Wilma account; the household host is `fake_household_host`'s, whose users each have their own
home, secrets and jobs and run the real `signup household` and `run` in-process; MyClub's server,
the model and the gateway's Email Delivery are the harness's; the system is the harness's, set to
Linux. The service is the real one, on a free port or through socket pairs. Assertions are on the
pages, on what each Household's user was left with, on the email sent, on the store, and on every
place the Wilma password, a MyClub link and the Gateway Key must never reach.
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
from family_brief.last_run import path as last_run_path
from family_brief.signup import page, status
from family_brief.signup.invites import Recipient, Store
from family_brief.signup.remove import remove
from family_brief.signup.server import Service, SignupServer
from family_brief.system import jobs
from conftest import myclub_event
from fake_household_host import FakeHouseholdHost
from fake_system import LINUX
from setup_page import CAN_LISTEN, Response, _Connection, _PairedServer
from test_setup_wilma import ESPOO, WILMA_PASSWORD, WilmaCLI
from test_setup_wilma import wilma_cli  # noqa: F401

pytestmark = pytest.mark.posix  # the fake wilma CLI is a script on a shell-script Node

ESPOO_TOWN = "Espoo – Espoon kaupunki / Suomenkielisen opetuksen tulosyksikkö"
NOON = datetime(2026, 10, 30, 10, 0, tzinfo=timezone.utc)  # 12:00 in Helsinki
MIA_LINK = "webcal://kilonpallo.myclub.fi/ical/mia-1001-token.ics"


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
    """The page's first step: the Wilma sign-in and the Recipients."""
    answers = {"lang": "en", "town": ESPOO_TOWN, "username": "mia.parent",
               "password": WILMA_PASSWORD, "r0": "parent@example.com", "r0_language": "fi",
               "r1": "", "r1_language": "en"} | fields
    return request(s.url, f"/join/{code}", "POST", answers)


def links(s: SignUp, code: str, *given: str, lang: str = "en") -> Response:
    """The page's MyClub step: a link, or "" for none, for each Kid in turn."""
    return request(s.url, f"/join/{code}", "POST",
                   {"lang": lang, "step": "myclub"} | {f"m{i}": url for i, url in enumerate(given)})


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
    # The program installed, then `signup household` as the Household, the key on its stdin, and
    # its first Brief made from the last three days, as the setup page's is.
    program = str(user.home / "ParentRecap" / "app" / ".venv" / "bin" / "parent-recap")
    assert [c[1:] for c in signup.host.commands] == [
        ["bash", str(signup.server.service.release / "install.sh")],
        [program, "signup", "household"],
        [program, "run", "--first-brief", "--lookback-hours", "72"]]
    # The first Brief, from Parent Recap's own address with the Household's key, to each
    # Recipient in the language they read it in.
    assert {tuple(m.to) for m in harness.sent} == {("parent@example.com",), ("partner@example.com",)}
    assert set(harness.relay_keys) == {key}
    by_reader = {m.to[0]: m for m in harness.sent}
    assert "Parent Recap su 27.9." in by_reader["parent@example.com"].text
    assert "Parent Recap 9月27日 周日" in by_reader["partner@example.com"].text
    assert all(m.html for m in harness.sent)
    state = json.loads((user.home / ".family" / "state.json").read_text())
    assert set(state["brief_delivered_at"]) == {"parent@example.com", "partner@example.com"}
    cfg = Config.load(user.home / ".family" / "config.yaml")
    assert [k.name for k in cfg.kids] == ["Mia Virtanen", "Leo Virtanen"]  # as Wilma lists them
    assert [(t.address, t.language) for t in cfg.email.to] == [
        ("parent@example.com", "fi"), ("partner@example.com", "zh")]
    assert cfg.summary_language == "en" and cfg.city == "Espoo"
    assert cfg.llm.backend == "gateway"
    assert cfg.wilma.enabled and not cfg.whatsapp.enabled
    assert cfg.google_calendar.mode == "ics"
    assert (cfg.schedule.daily_hour, cfg.schedule.daily_minute) == (21, 0)
    assert user.secrets == {"gateway-key": key}
    assert user.jobs.installed() == {jobs.DAILY}
    assert not any(k.myclub_ical_url for k in cfg.kids)  # no link given: MyClub off for both
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
    assert {k.name: k.myclub_ical_url for k in cfg.kids} == {"Mia Virtanen": MIA_LINK,
                                                            "Leo Virtanen": None}
    # Downloaded once to check it, then once more by the first Brief, which reads it.
    https = MIA_LINK.replace("webcal://", "https://")
    assert mia_plays.gets == [https, https]
    [first] = harness.sent
    assert "MyClub 1 tapahtuma" in first.text  # "read tonight: MyClub 1 event"
    # The link is a secret: on no command line and in no log.
    assert not any(MIA_LINK in arg or https in arg for cmd in signup.host.commands for arg in cmd)
    assert "mia-1001-token" not in caplog.text


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


@pytest.mark.parametrize("step", ["create_user", "mint_key", "install", "jobs", "first_brief"])
def test_a_step_that_fails_takes_back_what_was_made_and_the_link_stays_good(
        signup, harness, step, caplog):
    code = signup.invite(Recipient("parent@example.com", "fi"))
    if step == "install":
        signup.host.install_exit = 1
    elif step == "first_brief":
        harness.relay_answer = (502, {"error": "Email Delivery refused the message"})
    else:
        signup.host.fails = step

    r = submit(signup, code)

    assert r.status == 500
    assert said("en", "failed") in text(r)
    # No user with its jobs, no key, no files.
    assert signup.host.users == {} and signup.host.keys == {}
    assert len(signup.host.removed) == 1 and signup.host.revoked == signup.host.removed
    assert not (signup.host.root / signup.host.removed[0]).exists()
    assert signup.store.households() == []
    assert harness.sent == []
    # The link is unused: it signs up once the step works again.
    assert request(signup.url, f"/join/{code}").status == 200
    assert "wasn't created" in caplog.text
    signup.host.fails, signup.host.install_exit, harness.relay_answer = None, 0, (200, {"sent": 1})
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

    program = str(user.home / "ParentRecap" / "app" / ".venv" / "bin" / "parent-recap")
    assert signup.host.log == [("run_as", name, program, "schedule", "uninstall"),
                               ("revoke_key", name), ("remove_user", name)]
    assert user.jobs.installed() == set()
    assert not user.home.exists()
    # Nothing of it stays; the other Household is as it was.
    assert list(signup.host.users) == [other] and list(signup.host.keys) == [other]
    assert signup.host.users[other].jobs.installed() == {jobs.DAILY}
    assert [h.user for h in signup.store.households()] == [other]
    out = capsys.readouterr().out.splitlines()
    assert [line.split(" ", 1)[0] for line in out] == ["✅"] * 4, out
    assert "jobs" in out[0] and "Gateway Key" in out[1] and str(user.home) in out[2]
    assert all(name in line for line in out)
    assert status.report(signup.store, signup.host, homes=signup.host.root) == 0
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
