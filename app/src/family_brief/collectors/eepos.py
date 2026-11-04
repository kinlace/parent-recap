"""The Eepos Source: the calendar of a music or art school on Eepos, read through its guardian
portal's own JSON API (ADR 0038).

Each school is its own address, `<school>.eepos.fi`, with a session of its own, so a Household
with Kids at two Eepos schools has two Eepos sign-ins. Eepos has no calendar feed for guardians and
no documented API: the portal's AngularJS app signs in with `POST portal/api/auth/login`, a JSON
body of `{username, password}`, and then reads JSON over the session's cookie, every answer
prefixed with Angular's `)]}',` guard. Each query parameter is JSON, so a date goes as
`"2026-11-05"`, quotes and all, as the portal's own script sends it.

The evening run signs in to each with the username in the config and the password in the system's
secret store (`system/secrets`), reads the guardian's students, only their ids and names, and the
calendar of the next LOOKAHEAD_DAYS days: lessons, events such as concerts, and holidays. Each
becomes a calendar event in the Household's time zone, for the Kid its student is, or for the
Household when it names none or several. A cancelled lesson isn't an event; telling the family it
was cancelled is for a later slice (#755).

Nothing else of the students' answer is kept: it also has their addresses, phone numbers, birth
dates and the start of their henkilötunnus, which the collector never reads past the names.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import requests

from ..system import secrets
from .base import CalendarEvent, Collected, retry_once_on_timeout, sign_in_failed

if TYPE_CHECKING:
    from ..config import Config, EeposSignin, Kid
    from ..state import State

log = logging.getLogger(__name__)

LOOKAHEAD_DAYS = 14
DOMAIN = "eepos.fi"
XSSI_PREFIX = ")]}',"  # Angular's guard, before every JSON answer
TIMEOUT = 30
SIGN_IN = "auth/login"
# The secret a sign-in's password waits under while `setup eepos` checks it, so a password typed
# wrong never takes the place of one that works.
TYPED = "eepos-signin-typed"


class EeposError(Exception):
    """Eepos couldn't be read at one school. The message is short, for the Brief's coverage line:
    it names the school's address and what went wrong, never the username or the password."""


class WrongPassword(EeposError):
    """Eepos refused the username and password. The message has "login failed" in it, which the
    Brief says as a failed login, as of any Source."""


class Changed(EeposError):
    """Eepos answered, but not as its portal did when this was written: its API has moved."""


@dataclass(frozen=True)
class Student:
    """One of the guardian's students on Eepos: its id and names, all that is read of it."""
    id: str
    first_name: str
    last_name: str
    nickname: str

    def names(self) -> list[str]:
        """The names a Kid in the config may be given as: the first name, the nickname, and the
        full name either way round, as Eepos writes it in a lesson's title ("Zhao Harry, Piano")."""
        full = [f"{self.first_name} {self.last_name}", f"{self.last_name} {self.first_name}"] \
            if self.first_name and self.last_name else []
        return [n for n in (self.first_name, self.nickname, *full) if n]

    def called(self) -> str:
        return " ".join(n for n in (self.first_name, self.last_name) if n) or self.nickname or self.id


def school_of(text: str) -> str | None:
    """The school's own part of its Eepos address, `emo` for emo.eepos.fi, from that part, the
    address or a link into it; None when it isn't one."""
    host = re.sub(r"^[a-z]+://", "", text.strip().lower()).split("/", 1)[0]
    school = host[:-len(DOMAIN) - 1] if host.endswith("." + DOMAIN) else host
    return school if re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", school) else None


def address(school: str) -> str:
    return f"{school}.{DOMAIN}"


def secret_name(school: str, username: str) -> str:
    """The name of the secret that holds the password of the sign-in as `username` at `school`.
    A username may be an email address, which no store's name may have."""
    key = f"{school}/{username.strip().casefold()}"
    return "eepos-signin-" + hashlib.sha256(key.encode()).hexdigest()[:16]


def secrets_of(cfg: Config) -> list[str]:
    """The secret store's names of the Household's Eepos passwords, for uninstall."""
    return [secret_name(s.school, s.username) for s in cfg.eepos.signins]


def _json(text: str, what: str, where: str) -> Any:
    """An answer of the portal without its `)]}',` guard, as JSON."""
    text = text.lstrip("\ufeff")
    if text.startswith(XSSI_PREFIX):
        text = text[len(XSSI_PREFIX):]
    try:
        return json.loads(text)
    except ValueError:
        raise Changed(f"{where}'s {what} wasn't JSON") from None


class Portal:
    """One school's guardian portal, signed in to over a session of its own."""

    def __init__(self, school: str) -> None:
        self.school = school
        self.where = address(school)
        self.api = f"https://{self.where}/portal/api/"
        self.session = requests.Session()
        # As the portal's own script asks.
        self.session.headers.update({"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"})

    def __enter__(self) -> Portal:
        return self

    def __exit__(self, *_a: Any) -> None:
        self.session.close()

    def _call(self, method: str, path: str, what: str, **kwargs: Any) -> Any:
        def call() -> requests.Response:
            return self.session.request(method, self.api + path, timeout=TIMEOUT, **kwargs)
        try:
            r = retry_once_on_timeout(call, (requests.Timeout,), f"Eepos {self.where}")
        except requests.RequestException as e:
            raise EeposError(f"{self.where} didn't answer ({type(e).__name__})") from None
        if path == SIGN_IN and 400 <= r.status_code < 500 and r.status_code not in (404, 429):
            raise WrongPassword(f"login failed at {self.where}")
        if r.status_code in (401, 403):
            raise EeposError(f"{self.where} signed the session out (HTTP {r.status_code})")
        if r.status_code == 404:
            raise Changed(f"{self.where} has no {what} where it was (HTTP 404)")
        if r.status_code >= 400:
            raise EeposError(f"{self.where} answered HTTP {r.status_code}")
        return _json(r.text, what, self.where)

    def sign_in(self, username: str, password: str) -> None:
        """Signs in as a guardian. Raises WrongPassword when Eepos refuses them."""
        answer = self._call("POST", SIGN_IN, "sign-in",
                            json={"username": username, "password": password})
        if not (isinstance(answer, dict) and answer.get("success") is True):
            # The portal treats anything but success as a failed sign-in.
            if isinstance(answer, dict) and ("success" in answer or "error" in answer):
                raise WrongPassword(f"login failed at {self.where}")
            raise Changed(f"{self.where}'s sign-in answered in a way it didn't before")

    def students(self) -> list[Student]:
        """The guardian's students, by their ids and names only."""
        answer = self._call("GET", "guardian/students", "list of students")
        if not isinstance(answer, list):
            raise Changed(f"{self.where}'s list of students isn't a list")
        out = []
        for item in answer:
            person = item.get("person") if isinstance(item, dict) else None
            if not isinstance(person, dict) or item.get("id") is None:
                raise Changed(f"{self.where}'s list of students has no names")
            out.append(Student(str(item["id"]), _text(person.get("firstname")),
                               _text(person.get("lastname")), _text(person.get("nickname"))))
        return out

    def calendar(self, start: date, end: date) -> list[dict[str, Any]]:
        """The calendar's entries from `start` to `end`, both days in, as the portal gives them."""
        answer = self._call("GET", "calendar-events/all-by-date-range", "calendar",
                            params={"startDate": json.dumps(start.isoformat()),
                                    "endDate": json.dumps(end.isoformat())})
        if not isinstance(answer, list) or not all(isinstance(e, dict) for e in answer):
            raise Changed(f"{self.where}'s calendar isn't a list of entries")
        return answer


def _text(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def kid_of(student: Student, kids: Iterable[Kid]) -> str | None:
    """The Kid `student` is, by name: one of the Kid's names, aliases or everyday name is one of
    the student's, or the first word of the Kid's name is its first name. None when no Kid, or
    more than one, is."""
    names = {n.casefold() for n in student.names()}
    first = student.first_name.casefold()
    found = [k.name for k in kids
             if names & {t.casefold() for t in k.match_terms()}
             or (first and [w.casefold() for w in k.name.split()[:1]] == [first])]
    return found[0] if len(found) == 1 else None


def kids_of(signin: EeposSignin, students: list[Student], kids: Iterable[Kid]) -> dict[str, str | None]:
    """Each of the sign-in's students, by id, as the Kid it is: the one its `kids` names, else the
    one its name matches."""
    kids = list(kids)
    known = {k.name for k in kids}
    return {s.id: (signin.kids.get(s.id) if signin.kids.get(s.id) in known else kid_of(s, kids))
            for s in students}


def events(entries: list[dict[str, Any]], school: str, kids: Mapping[str, str | None],
           tz: str) -> list[CalendarEvent]:
    """The calendar's `entries` at `school` as calendar events, their times in `tz`: one for each
    entry but cancelled ones, an entry that comes twice (a holiday on both Kids' calendars) as
    one. Each goes to the Kid its students are, or to the Household when they are none, several,
    or not one of the Kids. Raises Changed for an entry without its day or title."""
    merged: dict[tuple, tuple[dict[str, Any], set[str | None]]] = {}
    for e in entries:
        day, title = e.get("date"), _text(e.get("title"))
        if not isinstance(day, str) or not title:
            raise Changed(f"{address(school)}'s calendar has an entry without its day or title")
        if e.get("isCanceled"):
            continue
        # Not by its id: the same holiday comes once for each student, maybe under an id of each.
        key = (e.get("sourceRefType"), title, day, e.get("startTime"), e.get("endTime"))
        students = {str(r.get("refId")) for r in e.get("relatedRefs") or []
                    if isinstance(r, dict) and r.get("refType") == "student"}
        merged.setdefault(key, (e, set()))[1].update({kids.get(s) for s in students} or {None})
    return [_event(school, key, e, whose, ZoneInfo(tz)) for key, (e, whose) in merged.items()]


def _event(school: str, key: tuple, entry: dict[str, Any], whose: set[str | None],
           zone: ZoneInfo) -> CalendarEvent:
    """One calendar entry, as `events` merged it, as a calendar event. One with no start, or
    from midnight to the day's last second, as the holidays come, is all day."""
    kind, title, day, start, end = key
    try:
        on, begins, ends = date.fromisoformat(day), _clock(start), _clock(end)
    except ValueError:
        raise Changed(f"{address(school)}'s calendar has a day or time it didn't have") from None
    all_day = bool(entry.get("isEntireDay")) or begins is None \
        or (begins == time(0) and ends in (None, time(23, 59, 59)))
    starts = datetime.combine(on, time(0) if all_day else begins, zone)
    ends_at = datetime.combine(on, ends, zone) if ends and not all_day else None
    return CalendarEvent(
        source="eepos",
        # The same each night, so the .ics and Google Calendar get it once (calendar.event_hash).
        external_id="eepos:" + hashlib.sha256(f"{school}|{kind}|{title}|{day}|{start}".encode()).hexdigest()[:16],
        title=title, start=starts, end=ends_at if ends_at and ends_at > starts else None,
        location=_text(entry.get("roomName")) or None, description=_text(entry.get("subTitle")),
        kid=whose.pop() if len(whose) == 1 else None, all_day=all_day)


def _clock(v: Any) -> time | None:
    """A time of day as the portal writes it, "15:15:00"; None for none."""
    if v in (None, ""):
        return None
    if not isinstance(v, str):
        raise ValueError(v)
    return time.fromisoformat(v)


def password_of(signin: EeposSignin) -> str:
    """The sign-in's password from the secret store. Raises WrongPassword when there is none."""
    try:
        password = secrets.get(secret_name(signin.school, signin.username))
    except secrets.SecretsError as e:
        raise EeposError(f"the password for {address(signin.school)} couldn't be read from the "
                         f"{secrets.STORE}: {e}") from None
    if not password:
        raise WrongPassword(f"login failed at {address(signin.school)}: no password in the {secrets.STORE}")
    return password


def today(tz: str) -> date:
    """Today in the Household's time zone `tz`, which a hosted server's own clock (UTC) isn't."""
    return datetime.now(ZoneInfo(tz)).date()


def read(cfg: Config, signin: EeposSignin, first: date) -> list[CalendarEvent]:
    """The events of the sign-in's school from the day `first` to LOOKAHEAD_DAYS on."""
    password = password_of(signin)
    with Portal(signin.school) as portal:
        portal.sign_in(signin.username, password)
        kids = kids_of(signin, portal.students(), cfg.kids)
        entries = portal.calendar(first, first + timedelta(days=LOOKAHEAD_DAYS))
    return events(entries, signin.school, kids, cfg.timezone)


def collect(cfg: Config, state: State, kid_terms: list[str]) -> Collected:
    """The coming events of each Eepos sign-in, to add to the calendar; Eepos has no Messages in
    this slice. The calendar gives every event again each night, so none is marked seen. A sign-in
    that fails while another works is logged by its school, so the Brief says Eepos was partly
    read and where; when none works, Eepos wasn't read, with the first one's reason."""
    now = datetime.now(timezone.utc)
    on = today(cfg.timezone)
    out: list[CalendarEvent] = []
    failed: list[EeposError] = []
    for signin in cfg.eepos.signins:
        try:
            got = read(cfg, signin, on)
        except EeposError as e:
            failed.append(e)
            if len(cfg.eepos.signins) > 1:
                log.error("%s", sign_in_failed(address(signin.school), str(e), signin.username))
            continue
        # Earlier today is gone by the evening, as for MyClub.
        got = [e for e in got if e.start >= now - timedelta(hours=6)]
        log.info("Eepos: %d events at %s (next %dd)", len(got), address(signin.school), LOOKAHEAD_DAYS)
        out += got
    if failed and len(failed) == len(cfg.eepos.signins):
        raise failed[0]
    return Collected(events=out)
