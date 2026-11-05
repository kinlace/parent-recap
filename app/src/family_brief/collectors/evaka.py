"""The eVaka Source: a public daycare's calendar on eVaka, the cities' early-childhood education
system, read through the citizen API its own web app uses, with the guardian's email and password
(ADR 0039).

Each city runs eVaka at a host of its own (CITIES), with sessions of its own, so a Household is
one eVaka sign-in per city, as it is one Wilma sign-in per town. A guardian signs in with
`POST /api/citizen/auth/weak-login`, a JSON body of `{username, password}` and the header
`x-evaka-csrf: 1`, which every request but a GET must carry, and gets the session cookie
`evaka.eugw.session`. A session times out after 32 minutes idle and can't be refreshed, so each
evening run signs in again. Some cities email the guardian about a sign-in from a new device: the
device cookie eVaka sets on each sign-in (`__Host-evaka-device-user-<hash>`, for 90 days) is kept
between runs in an owner-only file beside the state (DEVICES), so they don't email every evening.

The evening run signs in to each with the username in the config and the password in the system's
secret store (`system/secrets`), reads the guardian's children, only their ids and names, and the
next LOOKAHEAD_DAYS days of:
- `calendar-events`: the unit's events, such as an excursion or a photo day, all day for the
  days they last, and a discussion time a guardian booked for a child;
- `reservations`: the days the unit is closed, a public holiday no child of the guardian's has
  reservable time on, and the deadline of the next week's reservations, which eVaka gives as the
  first day still open for them (`reservableRange`), and each city closes them a set number of
  hours before (CITIES); none for a week a holiday period has its own reservations for.
Each becomes a calendar event for the Kid its child is, or for the Household when it is for none,
several or all of them. Absences, messages and holiday questionnaires are for later slices (#768).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import requests

from ..system import secrets
from .base import CalendarEvent, Collected, retry_once_on_timeout, sign_in_failed

if TYPE_CHECKING:
    from ..config import Config, EvakaSignin, Kid
    from ..state import State

log = logging.getLogger(__name__)

LOOKAHEAD_DAYS = 28  # four weeks: a daycare announces closed days and trips well ahead
TIMEOUT = 30
SIGN_IN = "auth/weak-login"
SESSION_COOKIE = "evaka.eugw.session"
DEVICE_COOKIE = "__Host-evaka-device-user-"  # then a hash of the guardian's id
DEVICES = "evaka-devices.json"  # beside the state: each host's device cookies
WRONG_CREDENTIALS = (403, "Forbidden")  # what weak-login answers a wrong email or password
EVAKA_ZONE = ZoneInfo("Europe/Helsinki")  # the zone eVaka's own times are in
# The secret a sign-in's password waits under while `setup evaka` checks it, so a password typed
# wrong never takes the place of one that works.
TYPED = "evaka-signin-typed"

# The cities on eVaka's own code, by the name `setup evaka --city` takes: each one's host, and how
# many hours before the Monday of a week its reservations close, from each city's
# `citizenReservationThresholdHours` (service/src/main/kotlin/evaka/instance/, at 2ad3203f).
CITIES: dict[str, tuple[str, int]] = {
    "espoo": ("espoonvarhaiskasvatus.fi", 150),
    "tampere": ("varhaiskasvatus.tampere.fi", 6 * 24),
    "turku": ("evaka.turku.fi", 6 * 24 + 12),
    "oulu": ("varhaiskasvatus.ouka.fi", 165),
    "hameenkyro": ("evaka.hameenkyro.fi", 7 * 24 - 9),
    "orivesi": ("evaka.orivesi.fi", 7 * 24 - 9),
    "ylojarvi": ("evaka.ylojarvi.fi", 7 * 24 - 9),
    "pirkkala": ("evaka.pirkkala.fi", 7 * 24 - 9),
    "nokia": ("evaka.nokiankaupunki.fi", 7 * 24 - 9),
    "lempaala": ("evaka.lempaala.fi", (7 + 3) * 24 - 9),
    "vesilahti": ("evaka.vesilahti.fi", 7 * 24 - 9),
    "kangasala": ("evaka.kangasala.fi", 7 * 24 - 9),
}
CLOSED = "Päiväkoti suljettu"  # as the Brief's model reads it, and the .ics shows it


class EvakaError(Exception):
    """eVaka couldn't be read in one city. The message is short, for the Brief's coverage line: it
    names the city's host and what went wrong, never the username or the password."""


class WrongPassword(EvakaError):
    """eVaka refused the email and password. The message has "login failed" in it, which the Brief
    says as a failed login, as of any Source."""


class Changed(EvakaError):
    """eVaka answered, but not as it did when this was written: its API has moved."""


@dataclass(frozen=True)
class Child:
    """One of the guardian's children on eVaka: its id and names, all that is read of it."""
    id: str
    first_name: str  # all the given names, "Mia Emilia"
    last_name: str
    preferred_name: str

    def names(self) -> list[str]:
        """The names a Kid in the config may be given as: the preferred name, the given names and
        the first of them, and the full name either way round."""
        first = self.first_name.split()[0] if self.first_name else ""
        full = [f"{n} {self.last_name}" for n in dict.fromkeys((self.first_name, first, self.preferred_name))
                if n and self.last_name] + ([f"{self.last_name} {first}"] if first and self.last_name else [])
        return [n for n in dict.fromkeys((self.preferred_name, self.first_name, first, *full)) if n]

    def called(self) -> str:
        return " ".join(n for n in (self.preferred_name or self.first_name, self.last_name) if n) or self.id


def city_of(text: str) -> str | None:
    """The city a sign-in is in, as the config keeps it: its name in CITIES, from that name, its
    host or a link into it; or the host itself, for a city on eVaka that isn't in CITIES. None when
    it is neither."""
    given = text.strip().lower()
    if given in CITIES:
        return given
    host = re.sub(r"^[a-z]+://", "", given).split("/", 1)[0].split(":", 1)[0]
    for name, (known, _) in CITIES.items():
        if host == known:
            return name
    label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    return host if re.fullmatch(rf"{label}(?:\.{label})+", host) else None


def host_of(city: str) -> str:
    return CITIES[city][0] if city in CITIES else city


def deadline_hours(city: str) -> int | None:
    """How many hours before a week's Monday the city closes its reservations; None for a city
    not in CITIES."""
    return CITIES[city][1] if city in CITIES else None


def secret_name(city: str, username: str) -> str:
    """The name of the secret that holds the password of the sign-in as `username` in `city`.
    The username is an email address, which no store's name may have."""
    key = f"{city}/{username.strip().casefold()}"
    return "evaka-signin-" + hashlib.sha256(key.encode()).hexdigest()[:16]


def secrets_of(cfg: Config) -> list[str]:
    """The secret store's names of the Household's eVaka passwords, for uninstall."""
    return [secret_name(s.city, s.username) for s in cfg.evaka.signins]


def devices_path(cfg: Config) -> Path:
    return cfg.resolved_state_path().with_name(DEVICES)


def _devices(path: Path | None) -> dict[str, dict[str, str]]:
    """Each host's device cookies, by name, as the last sign-in there left them."""
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {h: c for h, c in data.items() if isinstance(c, dict)} if isinstance(data, dict) else {}


def _keep_devices(path: Path, host: str, cookies: dict[str, str]) -> None:
    """Keeps `host`'s device cookies in the owner-only file at `path`, in one step."""
    data = {**_devices(path), host: cookies}
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
        os.chmod(tmp, 0o600)  # whatever the umask
        json.dump(data, f, indent=2)
    tmp.replace(path)


class Portal:
    """One city's eVaka, signed in to over a session of its own. With `devices`, the file the
    device cookies are kept in, the sign-in sends the ones of an earlier sign-in and keeps the ones
    it gets."""

    def __init__(self, city: str, devices: Path | None = None) -> None:
        self.city = city
        self.where = host_of(city)
        self.api = f"https://{self.where}/api/citizen/"
        self.devices = devices
        self.session = requests.Session()
        # As eVaka's own web app sends them; without the second, any request but a GET is refused.
        self.session.headers.update({"Accept": "application/json", "x-evaka-csrf": "1"})
        for name, value in _devices(devices).get(self.where, {}).items():
            self.session.cookies.set(name, value, domain=self.where, path="/", secure=True)

    def __enter__(self) -> Portal:
        return self

    def __exit__(self, *_a: Any) -> None:
        self.session.close()

    def _call(self, method: str, path: str, what: str, **kwargs: Any) -> requests.Response:
        def call() -> requests.Response:
            return self.session.request(method, self.api + path, timeout=TIMEOUT, **kwargs)
        try:
            r = retry_once_on_timeout(call, (requests.Timeout,), f"eVaka {self.where}")
        except requests.RequestException as e:
            raise EvakaError(f"{self.where} didn't answer ({type(e).__name__})") from None
        if path == SIGN_IN and (r.status_code, r.text.strip()) == WRONG_CREDENTIALS:
            # What weak-login answers a wrong email or password (apigw's auth-weak-login.ts).
            raise WrongPassword(f"login failed at {self.where}")
        if r.status_code == 429:
            raise EvakaError(f"{self.where} refused more sign-ins for an hour (HTTP 429)")
        if r.status_code in (401, 403):
            raise EvakaError(f"{self.where} signed the session out (HTTP {r.status_code})")
        if r.status_code == 404:
            raise Changed(f"{self.where} has no {what} where it was (HTTP 404)")
        if r.status_code >= 400:
            raise EvakaError(f"{self.where} answered HTTP {r.status_code}")
        return r

    def _json(self, path: str, what: str, **params: str) -> Any:
        r = self._call("GET", path, what, params=params)
        try:
            return r.json()
        except ValueError:
            raise Changed(f"{self.where}'s {what} wasn't JSON") from None

    def sign_in(self, username: str, password: str) -> None:
        """Signs in as a guardian. Raises WrongPassword when eVaka refuses them."""
        self._call("POST", SIGN_IN, "sign-in", json={"username": username, "password": password})
        if not self.session.cookies.get(SESSION_COOKIE, domain=self.where):
            raise Changed(f"{self.where}'s sign-in answered in a way it didn't before")
        if self.devices is not None:
            kept = {c.name: c.value for c in self.session.cookies
                    if c.name.startswith(DEVICE_COOKIE) and c.value is not None}
            if kept:
                _keep_devices(self.devices, self.where, kept)

    def children(self) -> list[Child]:
        """The guardian's children, by their ids and names only."""
        answer = self._json("children", "list of children")
        if not isinstance(answer, list):
            raise Changed(f"{self.where}'s list of children isn't a list")
        out = []
        for item in answer:
            if not isinstance(item, dict) or not item.get("id") or not (item.get("firstName") or item.get("preferredName")):
                raise Changed(f"{self.where}'s list of children has no names")
            out.append(Child(str(item["id"]), _text(item.get("firstName")), _text(item.get("lastName")),
                             _text(item.get("preferredName"))))
        return out

    def calendar(self, start: date, end: date) -> list[dict[str, Any]]:
        """The calendar's events from `start` to `end`, both days in, as eVaka gives them."""
        answer = self._json("calendar-events", "calendar", start=start.isoformat(), end=end.isoformat())
        if not isinstance(answer, list) or not all(isinstance(e, dict) for e in answer):
            raise Changed(f"{self.where}'s calendar isn't a list of events")
        return answer

    def reservations(self, start: date, end: date) -> dict[str, Any]:
        """The reservation calendar from `start` to `end`, both days in, as eVaka gives it."""
        answer = self._json("reservations", "reservations", **{"from": start.isoformat(), "to": end.isoformat()})
        if not isinstance(answer, dict) or not isinstance(answer.get("days"), list) \
                or not isinstance(answer.get("reservableRange"), dict):
            raise Changed(f"{self.where}'s reservations aren't as they were")
        return answer


def _text(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def kid_of(child: Child, kids: Iterable[Kid]) -> str | None:
    """The Kid `child` is, by name: one of the Kid's names, aliases or everyday name is one of the
    child's, or the first word of the Kid's name is the child's first name or preferred name. None
    when no Kid, or more than one, is."""
    names = {n.casefold() for n in child.names()}
    firsts = {n.casefold() for n in (child.first_name.split()[:1] + [child.preferred_name]) if n}
    found = [k.name for k in kids
             if names & {t.casefold() for t in k.match_terms()}
             or (k.name.split()[:1] and k.name.split()[0].casefold() in firsts)]
    return found[0] if len(found) == 1 else None


def kids_of(signin: EvakaSignin, children: list[Child], kids: Iterable[Kid]) -> dict[str, str | None]:
    """Each of the sign-in's children, by id, as the Kid it is: the one its `kids` names, else the
    one its name matches."""
    kids = list(kids)
    known = {k.name for k in kids}
    return {c.id: (signin.kids.get(c.id) if signin.kids.get(c.id) in known else kid_of(c, kids))
            for c in children}


def _whose(ids: Iterable[str], kids: Mapping[str, str | None]) -> str | None:
    """The one Kid the children `ids` are, or None (the Household) for none, several or a child not
    one of the Kids."""
    whose = {kids.get(i) for i in ids} or {None}
    return whose.pop() if len(whose) == 1 else None


def _day(v: Any, where: str) -> date:
    try:
        return date.fromisoformat(v)
    except (TypeError, ValueError):
        raise Changed(f"{where} has a day or time it didn't have") from None


def _id(city: str, *parts: object) -> str:
    """The same each night, so the .ics and Google Calendar get it once (calendar.event_hash)."""
    return "evaka:" + hashlib.sha256("|".join(map(str, (city, *parts))).encode()).hexdigest()[:16]


def events(calendar: list[dict[str, Any]], city: str, kids: Mapping[str, str | None],
           tz: str) -> list[CalendarEvent]:
    """eVaka's `calendar` in `city` as calendar events, their times in `tz`: a unit's event all day
    for the days it lasts, for the Kid its children are, or the Household when they are several;
    and each discussion time a guardian booked for a child, at its time, for that child's Kid. A
    discussion survey with no time booked yet isn't one. Raises Changed for an event without its
    title or days."""
    where = f"{host_of(city)}'s calendar"
    zone = ZoneInfo(tz)
    out: list[CalendarEvent] = []
    for e in calendar:
        title, period = _text(e.get("title")), e.get("period")
        if not title or not isinstance(period, dict):
            raise Changed(f"{where} has an event without its title or days")
        first, last = _day(period.get("start"), where), _day(period.get("end"), where)
        children = e.get("attendingChildren") if isinstance(e.get("attendingChildren"), dict) else {}
        if e.get("eventType") == "DAYCARE_EVENT":
            out.append(CalendarEvent(
                source="evaka", external_id=_id(city, e.get("id"), first, last), title=title,
                start=datetime.combine(first, time(0), zone),
                end=datetime.combine(last, time(0), zone) if last > first else None,
                description=_text(e.get("description")), kid=_whose(children, kids), all_day=True))
            continue
        times = e.get("timesByChild") if isinstance(e.get("timesByChild"), dict) else {}
        for child, booked in times.items():
            for t in booked if isinstance(booked, list) else []:
                if not isinstance(t, dict) or t.get("childId") != child:
                    continue  # a time of the survey's that this child hasn't booked
                on = _day(t.get("date"), where)
                try:
                    begins, ends = time.fromisoformat(t["startTime"]), time.fromisoformat(t["endTime"])
                except (KeyError, TypeError, ValueError):
                    raise Changed(f"{where} has a day or time it didn't have") from None
                out.append(CalendarEvent(
                    source="evaka", external_id=_id(city, t.get("id"), on, begins), title=title,
                    start=datetime.combine(on, begins, EVAKA_ZONE).astimezone(zone),
                    end=datetime.combine(on, ends, EVAKA_ZONE).astimezone(zone),
                    description=_text(e.get("description")), kid=kids.get(child)))
    return out


def _reservable(child_day: dict[str, Any]) -> bool:
    """Whether a child's day has reservable time: the unit is open for it."""
    r = child_day.get("reservableTimeRange")
    if not isinstance(r, dict):
        return False
    return bool(r.get("range") or r.get("placementUnitOperationTime"))


def closures(reservations: dict[str, Any], city: str, kids: Mapping[str, str | None],
             tz: str) -> list[CalendarEvent]:
    """The days the unit is closed, as all-day events: a public holiday (`holiday`) on which none
    of the guardian's children has reservable time, for the Household, or one on which some have,
    for the Kid each of the others is."""
    where = f"{host_of(city)}'s reservations"
    zone = ZoneInfo(tz)
    everyone = [str(c.get("id")) for c in reservations.get("children") or [] if isinstance(c, dict)]
    out = []
    for day in reservations["days"]:
        if not isinstance(day, dict) or not day.get("holiday"):
            continue
        on = _day(day.get("date"), where)
        open_for = {str(c.get("childId")) for c in day.get("children") or []
                    if isinstance(c, dict) and _reservable(c)}
        closed = [c for c in everyone if c not in open_for]
        if not closed:
            continue
        out.append(CalendarEvent(
            source="evaka", external_id=_id(city, "closed", on), title=CLOSED,
            start=datetime.combine(on, time(0), zone),
            kid=None if len(closed) == len(everyone) else _whose(closed, kids), all_day=True))
    return out


def deadline(reservations: dict[str, Any], city: str, kids: Mapping[str, str | None],
             tz: str) -> CalendarEvent | None:
    """When the reservations of the first week still open for them close: eVaka's reservable range
    starts on that week's Monday, and the city closes them its CITIES hours before it, in eVaka's
    own time. For the Kid whose child needs reservations that week, or the Household when several
    do; None when none does, or for a city not in CITIES, whose hours aren't known. A day in a
    holiday period (`holidayPeriodEffect`) is reserved by the period's own rules, not by this
    deadline, so a week all in one has none. When the reservations reach no day of that week, a
    child needs them if it does on any day."""
    hours = deadline_hours(city)
    if hours is None:
        return None
    where = f"{host_of(city)}'s reservations"
    monday = _day(reservations["reservableRange"].get("start"), where)
    days = [d for d in reservations["days"] if isinstance(d, dict)]
    week = [d for d in days if monday <= _day(d.get("date"), where) < monday + timedelta(days=7)]
    reserving = {str(c.get("childId")) for day in week or days for c in day.get("children") or []
                 if isinstance(c, dict) and c.get("scheduleType") == "RESERVATION_REQUIRED"
                 and not c.get("holidayPeriodEffect")}
    if not reserving:
        return None
    closes = datetime.combine(monday, time(0), EVAKA_ZONE) - timedelta(hours=hours)
    week = monday.isocalendar().week
    return CalendarEvent(
        source="evaka", external_id=_id(city, "deadline", monday),
        title=f"Varaukset viikolle {week} sulkeutuvat",
        start=closes.astimezone(ZoneInfo(tz)), kid=_whose(reserving, kids),
        description=f"Varaa hoitoajat {monday.day}.{monday.month}. alkavalle viikolle eVakassa.")


def password_of(signin: EvakaSignin) -> str:
    """The sign-in's password from the secret store. Raises WrongPassword when there is none."""
    try:
        password = secrets.get(secret_name(signin.city, signin.username))
    except secrets.SecretsError as e:
        raise EvakaError(f"the password for {host_of(signin.city)} couldn't be read from the "
                         f"{secrets.STORE}: {e}") from None
    if not password:
        raise WrongPassword(f"login failed at {host_of(signin.city)}: no password in the {secrets.STORE}")
    return password


def today(tz: str) -> date:
    """Today in the Household's time zone `tz`, which a hosted server's own clock (UTC) isn't."""
    return datetime.now(ZoneInfo(tz)).date()


def read_all(portal: Portal, signin: EvakaSignin, cfg: Config, first: date
             ) -> tuple[list[Child], dict[str, str | None], list[CalendarEvent]]:
    """Over a signed-in `portal`: the guardian's children, the Kid each is, and the events from the
    day `first` to LOOKAHEAD_DAYS on."""
    children = portal.children()
    kids = kids_of(signin, children, cfg.kids)
    last = first + timedelta(days=LOOKAHEAD_DAYS)
    calendar, reservations = portal.calendar(first, last), portal.reservations(first, last)
    got = events(calendar, signin.city, kids, cfg.timezone) + closures(reservations, signin.city, kids, cfg.timezone)
    if (due := deadline(reservations, signin.city, kids, cfg.timezone)) is not None \
            and due.start.date() <= last:
        got.append(due)
    return children, kids, got


def read(cfg: Config, signin: EvakaSignin, first: date) -> list[CalendarEvent]:
    """The events of the sign-in's city from the day `first` to LOOKAHEAD_DAYS on."""
    password = password_of(signin)
    with Portal(signin.city, devices_path(cfg)) as portal:
        portal.sign_in(signin.username, password)
        return read_all(portal, signin, cfg, first)[2]


def collect(cfg: Config, state: State, kid_terms: list[str]) -> Collected:
    """The coming events of each eVaka sign-in, to add to the calendar; eVaka has no Messages in
    this slice. They come again each night, so none is marked seen. A sign-in that fails while
    another works is logged by its host, so the Brief says eVaka was partly read and where; when
    none works, eVaka wasn't read, with the first one's reason."""
    now = datetime.now(timezone.utc)
    on = today(cfg.timezone)
    out: list[CalendarEvent] = []
    failed: list[EvakaError] = []
    for signin in cfg.evaka.signins:
        try:
            got = read(cfg, signin, on)
        except EvakaError as e:
            failed.append(e)
            if len(cfg.evaka.signins) > 1:
                log.error("%s", sign_in_failed(host_of(signin.city), str(e), signin.username))
            continue
        # What is over by this evening is gone; an event of several days that goes on, isn't.
        got = [e for e in got if (e.end if e.all_day and e.end else e.start) >= now - timedelta(hours=6)]
        log.info("eVaka: %d events at %s (next %dd)", len(got), host_of(signin.city), LOOKAHEAD_DAYS)
        out += got
    if failed and len(failed) == len(cfg.evaka.signins):
        raise failed[0]
    return Collected(events=out)
