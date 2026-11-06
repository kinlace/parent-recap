"""The sign-up service's HTTP side (ADR 0031): `GET /join/<code>` shows the page under a valid
Invite Link, `POST /join/<code>` signs the Household up, and anything else, a code that isn't
good any more included, gets the gateway's web server's usual 404, so the service says nothing
of which links exist.

Signing up takes two posts of the page. The first checks the Wilma sign-in before anything is
made, so a wrong password is said at once and the link stays good, and answers with MyClub
calendar link fields for each Kid Wilma listed, one per club (#810). The answers wait in memory
only, for the second, up to PENDING_MINUTES: the password is never written down or put back in a
page. The second post
checks each link given by downloading it once, so a link that doesn't open is said at once, and
an empty one leaves MyClub off for that Kid. It may also give an Eepos sign-in (#769), checked as
`setup eepos` checks one: signed in to, its students read and matched to the Kids by name, a
student who is none of them left to the whole Household, and an eVaka sign-in (#790), checked as
`setup evaka` checks one, its children matched the same way. A wrong password is said at once, and
the answers still wait; the Eepos and eVaka passwords, too, are never written down or put back in a
page. The device cookie eVaka sets on the check is handed to the Household, so its evening runs
sign in as the same device and a city that emails about new devices emails once.

A family without Wilma, such as at a private school, leaves the Wilma fields empty and gives the
Kids' names instead (#760): nothing is checked at the first post, and the second needs at least one
MyClub link, the Eepos sign-in or the eVaka sign-in, the only things its Brief would read. Then the
Household is made through the household host: its Linux user, its Gateway Key, `signup household`
run as it, by the server's shared copy of the program (ADR 0035), with the answers, the Wilma, Eepos
and eVaka sign-ins, the links and the key on its stdin, and its first Brief, made and sent to its Recipients by
`run --first-brief` as it, as the setup page's first Brief step makes one (ADR 0031). A step that
fails takes back the user with its files and jobs and the key, and the link stays good. Once made, the Household is listed in the store, its link retired,
and the page says its first Brief was sent.

The Household is made in the background: the second post is answered at once with a redirect to
the link's own address, where the page shows the steps as each ends and asks again every few
seconds, until it shows how the sign-up ended. Which step it is on waits in memory only, nothing
secret in it, until the page has seen how it ended, or a day, so a phone asleep for a while still
finds it. Posting the form again meanwhile goes back to the steps and starts nothing.

Sign-ups go one at a time: each takes a minute or two, which ten Households can wait, and so two
never take the same evening slot or the same link. One waiting behind another says so.

The service logs one line per sign-up, saying how it ended, and nothing of the request: not its
address, which carries the code, nor its body, which carries the Wilma, Eepos and eVaka passwords.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import socketserver
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs

from .. import failure, setup_eepos, setup_evaka, setup_myclub, setup_wilma
from ..collectors import eepos, evaka
from ..config import Config, EeposSignin, EvakaSignin, Kid
from ..setup_server.first_brief import BRIEF_LOOKBACK_HOURS
from . import page
from .host import PROGRAM, HostError, HouseholdHost, user_name
from .invites import LANGUAGES, HouseholdRecord, Recipient, Store

log = logging.getLogger("family_brief.signup")

PREFIX = "/join/"
NOT_FOUND = b"Not found"  # as the Caddyfile's `respond "Not found" 404`
HEALTH = "/health"  # for gateway-setup.sh's check on localhost; Caddy sends only /join/ here
MAX_BODY = 16 * 1024
# The Households' evening jobs, one slot each in turn, so ten runs don't all start at once on a
# server the gateway shares (ADR 0029).
SLOTS = tuple(f"21:{minute:02d}" for minute in range(0, 60, 5))
HOUSEHOLD_SECONDS = 600  # `signup household` signs in to Wilma and installs the evening jobs
FIRST_BRIEF_SECONDS = 900  # as long as the setup page gives its first Brief
PENDING_MINUTES = 30  # how long the checked answers wait for the MyClub step
RESULT_HOURS = 24  # how long how a sign-up ended waits for its page to see it
# Hotmail and Outlook.com, by their domains' first part: they may put a new sender in Junk.
JUNK_PRONE = ("hotmail", "outlook", "live", "msn")
EMAIL = re.compile(r"[^@\s<>,;\"']+@[^@\s<>,;\"']+\.[^@\s<>,;\"']+")
HEADERS = {
    # The page's only style is its inline <style>, by its hash; no script at all.
    "Content-Security-Policy": "default-src 'none'; style-src 'sha256-"
    + base64.b64encode(hashlib.sha256(page.STYLE.encode()).digest()).decode()
    + "'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",  # the address carries the code
    "X-Content-Type-Options": "nosniff",
    "X-Robots-Tag": "noindex",
    "Cache-Control": "no-store",
}


@dataclass
class Service:
    store: Store
    host: HouseholdHost
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._towns: dict[str, tuple[dict[str, Any], str]] | None = None
        # Each link's checked answers, the Wilma password among them, until its MyClub step.
        self._pending: dict[str, _Pending] = {}
        # Each link's Household being made, until its page has seen how that ended.
        self._progress: dict[str, _Progress] = {}
        self._progress_lock = threading.Lock()  # the page reads _progress without _lock
        # The Households waiting to be made, in the order posted: the first is being made.
        self._turns = threading.Condition()
        self._queue: list[_Progress] = []

    # ---------------------------------------------------------------- the page

    def show(self, code: str, language: str | None) -> tuple[HTTPStatus, str | None]:
        if (shown := self._shown(code)) is not None:
            return shown
        invite = self.store.valid(code, self.now())
        if invite is None:
            return HTTPStatus.NOT_FOUND, None
        language = language if language in LANGUAGES else invite.language
        return HTTPStatus.OK, page.form(language, page.Typed(recipients=invite.recipients),
                                        list(self.towns()))

    def towns(self) -> dict[str, tuple[dict[str, Any], str]]:
        """What the town field offers, each with the Wilma and the town it stands for: the town
        and its Wilma's name, since a town may have several."""
        if self._towns is None:
            listed = setup_wilma.tenants()
            if listed is None:
                return {}  # without the CLI's list, tried again on the next page
            self._towns = {f"{town} – {t['name']}": (t, town)
                           for t in listed for town in setup_wilma.towns_of(t)}
        return self._towns

    def _picked(self, typed: str) -> tuple[dict[str, Any], str] | None:
        """The Wilma and town `typed` picks: an entry of the list, or a town only one Wilma has."""
        towns = self.towns()
        if typed in towns:
            return towns[typed]
        same = [v for v in towns.values() if v[1].casefold() == typed.strip().casefold()]
        return same[0] if len(same) == 1 else None

    # ---------------------------------------------------------------- signing up

    def _shown(self, code: str) -> tuple[HTTPStatus, str] | None:
        """The page while the link's Household is made, or how that ended, which is then gone;
        None when none is."""
        with self._progress_lock:
            now = self.now()
            self._progress = {c: p for c, p in self._progress.items()
                              if p.ended is None or p.ended > now - timedelta(hours=RESULT_HOURS)}
            progress = self._progress.get(code)
            if progress is None:
                return None
            if progress.page is None:
                return HTTPStatus.OK, page.progress(progress.language, progress.steps,
                                                    not progress.started, progress.names)
            del self._progress[code]
            return progress.page

    def sign_up(self, code: str, fields: dict[str, str]) -> tuple[HTTPStatus, str | None]:
        with self._lock:
            with self._progress_lock:
                if code in self._progress:  # posted again: back to the steps
                    return HTTPStatus.SEE_OTHER, ""
            now = self.now()
            invite = self.store.valid(code, now)
            if invite is None:
                return HTTPStatus.NOT_FOUND, None
            self._pending = {c: p for c, p in self._pending.items() if p.expires > now}
            language = fields.get("lang") if fields.get("lang") in LANGUAGES else invite.language
            if fields.get("step") == "myclub":
                if code in self._pending:
                    return self._make(code, fields)
                return HTTPStatus.OK, page.form(language, page.Typed(recipients=invite.recipients),
                                                list(self.towns()), "expired")
            self._pending.pop(code, None)  # signing in again starts over
            typed = page.Typed(fields.get("town", "").strip(), fields.get("username", "").strip(),
                               _recipients(fields, language), _kids(fields))
            password = fields.get("password", "")

            def again(error: str, status: HTTPStatus = HTTPStatus.OK) -> tuple[HTTPStatus, str]:
                log.info("A sign-up wasn't taken: %s", error)
                return status, page.form(language, typed, list(self.towns()), error)
            without_wilma = not (typed.town or typed.username or password)
            if without_wilma and not typed.kids:
                return again("invalid-sign-in", HTTPStatus.BAD_REQUEST)
            picked = None if without_wilma else self._picked(typed.town)
            if not without_wilma and picked is None:
                return again("invalid-town", HTTPStatus.BAD_REQUEST)
            if not without_wilma and (not typed.username or not password):
                return again("invalid-sign-in", HTTPStatus.BAD_REQUEST)
            if not typed.recipients or any(not EMAIL.fullmatch(r.address)
                                           for r in typed.recipients):
                return again("invalid-recipients", HTTPStatus.BAD_REQUEST)
            expires = now + timedelta(minutes=PENDING_MINUTES)
            recipients = [{"address": r.address, "language": r.language}
                          for r in typed.recipients]
            if picked is None:
                self._pending[code] = _Pending(language, expires, {
                    "language": language, "recipients": recipients,
                    "kids": [{"name": name} for name in typed.kids],
                }, invite.note)
                return HTTPStatus.OK, page.myclub(language, typed.kids, wilma=False)
            tenant, town = picked
            checked, students = setup_wilma.check(tenant, typed.username, password)
            if checked["result"] in ("wrong-password", "no-kids"):
                return again(checked["result"])
            if checked["result"] != "signed-in":
                log.warning("Wilma couldn't be signed in to: %s",
                            checked.get("reason") or checked["result"])
                return again("sign-in-failed")

            self._pending[code] = _Pending(language, expires, {
                "language": language, "city": town, "recipients": recipients,
                "kids": [{"name": k["name"],
                          **({"school": k["school"]} if k.get("school") else {}),
                          **({"class_name": k["class"]} if k.get("class") else {})}
                         for k in checked["kids"]],
                "wilma": {"tenant": {"url": tenant["url"], "name": tenant["name"]},
                          "username": typed.username, "password": password,
                          "students": students},
            }, invite.note)
            return HTTPStatus.OK, page.myclub(language, self._pending[code].kids())

    def _make(self, code: str, fields: dict[str, str]) -> tuple[HTTPStatus, str]:
        """The MyClub step: checks each link given, then has the Household made from the answers
        the first step checked, and sends the page to its steps."""
        pending = self._pending[code]
        language, kids, wilma = pending.language, pending.kids(), pending.wilma()
        typed = (fields.get("eepos_school", "").strip(), fields.get("eepos_username", "").strip())
        typed_evaka = (fields.get("evaka_city", "").strip(), fields.get("evaka_username", "").strip())

        def again(error: str, kid: str | None = None) -> tuple[HTTPStatus, str]:
            return HTTPStatus.OK, page.myclub(language, kids, error, kid, wilma, typed, typed_evaka)
        # Each Kid's links, one per club (#810); an empty field leaves it out, and a Kid without
        # one has MyClub off. One link for two Kids would write every event twice, under both
        # names (#209).
        result, kid, checked = setup_myclub.check_links({kid: [
            fields.get(f"m{i}" + (f"_{n}" if n else ""), "") for n in range(page.MAX_CLUBS)]
            for i, kid in enumerate(kids)})
        if result != "checked":
            log.info("A sign-up's MyClub link wasn't taken: %s", result)
            return again(result, kid)
        links = {kid: urls for kid, urls in checked.items() if urls}
        error, signin = _eepos(kids, *typed, fields.get("eepos_password", ""))
        if error:
            return again(error)
        error, daycare = _evaka(kids, *typed_evaka, fields.get("evaka_password", ""))
        if error:
            return again(error)
        if not wilma and not links and signin is None and daycare is None:  # nothing to read
            log.info("A sign-up without Wilma gave no MyClub link, Eepos or eVaka sign-in")
            return again("need-myclub")
        del self._pending[code]
        if signin is not None:
            pending.answers["eepos"], pending.unmatched = signin
        if daycare is not None:
            pending.answers["evaka"], pending.evaka_unmatched = daycare
        progress = _Progress(language, page.steps(wilma, bool(links), signin is not None,
                                                  daycare is not None))
        with self._progress_lock:
            self._progress[code] = progress
        threading.Thread(target=self._make_in_turn, args=(code, pending, links, progress),
                         name="household-maker", daemon=True).start()
        return HTTPStatus.SEE_OTHER, ""

    def _make_in_turn(self, code: str, pending: _Pending, links: dict[str, list[str]],
                      progress: _Progress) -> None:
        """Makes the Household once those posted before it are made; the page stops asking
        however that ends."""
        with self._turns:
            self._queue.append(progress)
            self._turns.wait_for(lambda: self._queue[0] is progress)
        progress.started = True
        try:
            self._make_household(code, pending, links, progress)
        finally:
            if progress.page is None:  # stopped by what no step expects
                progress.end(self.now(), HTTPStatus.INTERNAL_SERVER_ERROR,
                             page.failed(pending.language, steps=progress.names))
            with self._turns:
                self._queue.pop(0)
                self._turns.notify_all()

    def _make_household(self, code: str, pending: _Pending, links: dict[str, list[str]],
                        progress: _Progress) -> None:
        """Makes the Household, marking each step on `progress` as it ends, or takes back what
        was made."""
        language, kids = pending.language, pending.kids()
        name = user_name()
        evening = _evening(self.store.households())
        answers = {**pending.answers, "evening": evening, "myclub": links}
        try:
            self._create(name, answers, progress)
        except Exception as e:  # noqa: BLE001 - whatever it was, the family hears and it's undone
            log.error("Household %s wasn't created, and what was made is taken back: %s",
                      name, failure.raised(e, "Signing up"))
            self._take_back(name)
            progress.end(self.now(), HTTPStatus.INTERNAL_SERVER_ERROR,
                         page.failed(language, progress.steps, progress.names))
            return
        self.store.add_household(HouseholdRecord(name, evening, self.now().isoformat(),
                                                 pending.note))
        self.store.retire(code)
        log.info("Household %s created from an Invite Link, %s, with MyClub for %d of %d Kids%s; "
                 "its first Brief was sent, and the next come at %s", name,
                 "with Wilma" if pending.wilma() else "without Wilma", len(links), len(kids),
                 "".join(f" and {said}" for key, said in (("eepos", "Eepos"), ("evaka", "eVaka"))
                         if key in answers), evening)
        progress.end(self.now(), HTTPStatus.OK, page.done(language, evening, any(
            _junk_prone(r["address"]) for r in answers["recipients"]), progress.names,
            pending.unmatched, pending.evaka_unmatched))

    def _create(self, name: str, answers: dict[str, Any], progress: _Progress) -> None:
        """Makes the Household, one of its steps after another, `progress.steps` of them ended."""
        self.host.create_user(name)
        key = self.host.mint_key(name)
        progress.steps += 1
        code, out = self.host.run_as(name, [PROGRAM, "signup", "household"],
                                     json.dumps({**answers, "gateway_key": key}),
                                     HOUSEHOLD_SECONDS)
        said = _last_json(out)
        if code != 0 or said.get("result") != "created":
            raise HostError(f"signup household said {said.get('result', code)}"
                            + (f": {said['reason']}" if said.get("reason") else ""))
        progress.steps += 1
        # Its output isn't quoted: the run's log can name a Recipient.
        code, _ = self.host.run_as(name, [PROGRAM, "run", "--first-brief", "--lookback-hours",
                                          str(BRIEF_LOOKBACK_HOURS)], "", FIRST_BRIEF_SECONDS)
        if code != 0:
            raise HostError(f"the first Brief wasn't sent: run ended with {code}")
        progress.steps += 1

    def _take_back(self, name: str) -> None:
        for undo in (self.host.revoke_key, self.host.remove_user):
            try:
                undo(name)
            except Exception as e:  # noqa: BLE001 - the other is still taken back
                log.error("%s couldn't be taken back for %s: %s", undo.__name__, name,
                          failure.raised(e, "Taking back"))


@dataclass
class _Pending:
    """A sign-up between its two steps: what the first checked, waiting for the MyClub links."""
    language: str
    expires: datetime
    answers: dict[str, Any]
    note: str = ""  # the Invite Link's, for the Household's record
    unmatched: list[str] = field(default_factory=list)  # Eepos students who are none of the Kids
    evaka_unmatched: list[str] = field(default_factory=list)  # eVaka children who are none of them

    def kids(self) -> list[str]:
        return [k["name"] for k in self.answers["kids"]]

    def wilma(self) -> bool:
        return "wilma" in self.answers


@dataclass
class _Progress:
    """A Household being made: how many of its steps have ended, and, once it has, the page
    that says how. Nothing secret, so the page may ask for it as often as it likes."""
    language: str
    names: tuple[str, ...] = page.STEPS  # its steps, which page.NO_WILMA_STEPS are without Wilma
    started: bool = False  # False while it waits its turn
    steps: int = 0  # of `names`
    page: tuple[HTTPStatus, str] | None = None
    ended: datetime | None = None

    def end(self, now: datetime, status: HTTPStatus, html: str) -> None:
        self.ended, self.page = now, (status, html)


def _recipients(fields: dict[str, str], language: str) -> list[Recipient]:
    out = []
    for i in range(page.MAX_RECIPIENTS):
        address = fields.get(f"r{i}", "").strip()
        chosen = fields.get(f"r{i}_language")
        if address:
            out.append(Recipient(address, chosen if chosen in LANGUAGES else language))
    return out


def _kids(fields: dict[str, str]) -> list[str]:
    """The names a family without Wilma gave, each once, in the order given."""
    names = [" ".join(fields.get(f"k{i}", "").split())[:60] for i in range(page.MAX_KIDS)]
    return list(dict.fromkeys(n for n in names if n))


def _eepos(kids: list[str], school_typed: str, username: str,
           password: str) -> tuple[str | None, tuple[dict[str, Any], list[str]] | None]:
    """The Eepos sign-in the MyClub step gave, checked as `setup eepos` checks one: why it won't
    do, or its answer for `signup household`, the students matched to `kids` by id, with the names
    of those who are none of them. None for both when none was given."""
    if not (school_typed or username or password):
        return None, None
    if not (school_typed and username and password):
        return "eepos-incomplete", None
    school = eepos.school_of(school_typed)
    if school is None:
        return "eepos-not-a-school", None
    try:
        checked = setup_eepos.check(Config(kids=[Kid(name=k) for k in kids]),
                                    EeposSignin(school=school, username=username), password)
    except eepos.WrongPassword:
        log.info("A sign-up's Eepos sign-in wasn't taken: wrong-password")
        return "eepos-wrong-password", None
    except eepos.Changed as e:  # names the school's address, never the username or password
        log.warning("Eepos answered a sign-up in a way it didn't before: %s", e)
        return "eepos-changed", None
    except eepos.EeposError as e:
        log.warning("Eepos couldn't be read for a sign-up: %s", e)
        return "eepos-no-answer", None
    return None, ({"school": school, "username": username, "password": password,
                   "kids": {s.id: kid for s, kid in checked["students"] if kid}},
                  [s.called() for s, kid in checked["students"] if not kid])


def _evaka(kids: list[str], city_typed: str, username: str,
           password: str) -> tuple[str | None, tuple[dict[str, Any], list[str]] | None]:
    """The eVaka sign-in the MyClub step gave, checked as `setup evaka` checks one: why it won't
    do, or its answer for `signup household`, the children matched to `kids` by id and the device
    cookie the check got, with the names of the children who are none of them. None for both when
    none was given."""
    if not (city_typed or username or password):
        return None, None
    if not (city_typed and username and password):
        return "evaka-incomplete", None
    city = evaka.city_of(city_typed)
    if city is None:
        return "evaka-not-a-city", None
    # The check's device cookie goes in a folder of its own, for the Household to keep, never in
    # the service's home.
    with tempfile.TemporaryDirectory(prefix="evaka-check-") as folder:
        state = Path(folder) / "state.json"
        try:
            checked = setup_evaka.check(Config(kids=[Kid(name=k) for k in kids],
                                               state_path=str(state)),
                                        EvakaSignin(city=city, username=username), password)
        except evaka.WrongPassword:
            log.info("A sign-up's eVaka sign-in wasn't taken: wrong-password")
            return "evaka-wrong-password", None
        except evaka.Changed as e:  # names the city's host, never the username or password
            log.warning("eVaka answered a sign-up in a way it didn't before: %s", e)
            return "evaka-changed", None
        except evaka.EvakaError as e:
            log.warning("eVaka couldn't be read for a sign-up: %s", e)
            return "evaka-no-answer", None
        devices = evaka._devices(state.with_name(evaka.DEVICES)).get(evaka.host_of(city), {})
    return None, ({"city": city, "username": username, "password": password,
                   "kids": {c.id: kid for c, kid in checked["children"] if kid},
                   "devices": devices},
                  [c.called() for c, kid in checked["children"] if not kid])


def _evening(households: list[HouseholdRecord]) -> str:
    """The slot fewest Households' Briefs come at, the earliest of those."""
    taken = [h.evening for h in households]
    return min(SLOTS, key=lambda slot: (taken.count(slot), SLOTS.index(slot)))


def _junk_prone(address: str) -> bool:
    """Whether `address` is at Hotmail or Outlook.com (hotmail.fi, outlook.com, live.com, …)."""
    return address.rpartition("@")[2].lower().split(".")[0] in JUNK_PRONE


def _last_json(out: str) -> dict[str, Any]:
    for line in reversed(out.strip().splitlines()):
        try:
            said = json.loads(line)
        except ValueError:
            continue
        if isinstance(said, dict):
            return said
    return {}


# ---------------------------------------------------------------- HTTP

class SignupServer:
    def __init__(self, service: Service, port: int,
                 listener: type[ThreadingHTTPServer] | None = None) -> None:
        self.service = service
        self.httpd = (listener or _HTTPServer)(("127.0.0.1", port), _Handler)
        self.httpd.service = service  # type: ignore[attr-defined]
        self.port = self.httpd.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def serve_forever(self) -> None:
        self.httpd.serve_forever(poll_interval=0.1)

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class _HTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self) -> None:  # without the look-up of the host's name
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name, self.server_port = str(host), int(port)


class _Handler(BaseHTTPRequestHandler):
    server_version = "ParentRecap"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:
        pass  # a request's address carries the code

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def _handle(self, method: str) -> None:
        service: Service = self.server.service  # type: ignore[attr-defined]
        body = self._read_body()
        path, _, query = self.path.partition("?")
        code = path.removeprefix(PREFIX) if path.startswith(PREFIX) else ""
        status, html = HTTPStatus.NOT_FOUND, None
        if method == "GET" and path == HEALTH:
            return self._send(HTTPStatus.OK, b"ok", "text/plain; charset=utf-8")
        if code and "/" not in code:
            if method == "GET":
                language = parse_qs(query).get("lang", [None])[0]
                status, html = service.show(code, language)
            elif (body is not None
                  and self.headers.get_content_type() == "application/x-www-form-urlencoded"):
                status, html = service.sign_up(code, _fields(body))
        if html is None:
            return self._send(HTTPStatus.NOT_FOUND, NOT_FOUND, "text/plain; charset=utf-8")
        if status == HTTPStatus.SEE_OTHER:  # to the link's own address, where the steps show
            return self._send(status, b"", "text/plain; charset=utf-8", PREFIX + code)
        self._send(status, html.encode(), "text/html; charset=utf-8")

    def _read_body(self) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length < 0 or length > MAX_BODY:
            return None
        return self.rfile.read(length)

    def _send(self, status: HTTPStatus, body: bytes, kind: str, location: str = "") -> None:
        self.send_response(status)
        for name, value in {**HEADERS, "Content-Type": kind, "Content-Length": str(len(body)),
                            **({"Location": location} if location else {})}.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


def _fields(body: bytes) -> dict[str, str]:
    try:
        parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=40)
    except (UnicodeDecodeError, ValueError):
        return {}
    return {k: v[0] for k, v in parsed.items()}
