"""How the Konnect Source is read: a Dutch childcare or after-school care (BSO) on Konnect's parent portal,
`<provider>.ouderportaal.nl`, read through the JSON its own web app reads, with the parent's
username and password (ADR 0050).

Each provider is its own address with sessions of its own, so a Household is one Konnect sign-in
per provider, as it is one eVaka sign-in per city. A parent signs in with
`PUT /auth-api/login`, a JSON body of `{username, password, childMinder: false}`, and gets a
token (`authToken`) that lasts 15 minutes, sent as `Authorization: Bearer` with every read under
`/restservices-parent/`. It isn't refreshed: each evening run signs in again. A sign-in the portal
takes but whose password it wants changed first answers `mustResetPassword`, which `doctor` says
with what to do. Some of its addresses check the User-Agent, so the reader sends a browser's.

The evening run signs in to each with the username in the config and the password in the system's
secret store (`system/secrets`), reads the parent's children, only their ids and names, and what
the account may see (`parent/authorizations`, as the portal's own app asks at sign-in). Then lists
only, never one item's own page, since opening an item marks it read:
- the Messages page's three lists, back to the lookback: the parent's conversations with the
  group (`logbook/overview`, each with its latest message in full), the news
  (`htmlnews/view`) and the newsletters and service messages (`newsletter`), the last two only
  as the start of their text the list gives. Each the daycare sent since the last run is a
  Message, a conversation for the Kid its children are; the family's own aren't;
- the Agenda (`calendar/<from>/until/<to>`) of the next LOOKAHEAD_DAYS days: each time a child
  is planned to be there, an option or a request included and an absence left out, as a
  calendar event for the Kid the child is.

The timeline is never read: it holds photos of the children, other children's often too, and the
day-rhythm entries, such as sleep, medication and temperature, which are health data. Nothing is
written to Konnect: nothing is marked read, nothing is answered.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import requests

from ....collectors.base import CalendarEvent, Collected, Message, retry_once_on_timeout, sign_in_failed
from ....system import secrets

if TYPE_CHECKING:
    from ....config import Config, Kid
    from ....state import State
    from .config import KonnectSignin

log = logging.getLogger(__name__)

LOOKAHEAD_DAYS = 14
DOMAIN = "ouderportaal.nl"
TIMEOUT = 30
SIGN_IN = "auth-api/login"
DATA = "restservices-parent/"
# A browser's, as the portal's own app is: some of its addresses turn away other User-Agents.
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
PORTAL_ZONE = ZoneInfo("Europe/Amsterdam")  # every ouderportaal is Dutch; a day is one there
# The secret a sign-in's password waits under while `setup konnect` checks it, so a password typed
# wrong never takes the place of one that works.
TYPED = "konnect-signin-typed"
MAX_BODY_CHARS = 8000

# What the account may see, as `parent/authorizations` lists it; the portal hides the rest, and
# its own app asks for nothing the account can't see.
MESSAGES = "WEB_PARENT_LOGBOOK"
NEWS = "WEB_HTML_NEWS_VIEW"
NEWSLETTERS = "WEB_ONLY_HTML_NEWSLETTER_ARCHIVE"
AGENDA = frozenset({"WEB_PARENT_BOOKING_OVERVIEW", "WEB_PARENT_ACTIVITY_DATABASE",
                    "WEB_PARENT_MEETINGS_VIEW", "WEB_ACTIVITY_PROGRAM_PARENT_OVERVIEW"})

# A planned time: its attendance code, `attend`, `absent` (ill, on holiday or a day swapped away)
# or `tentative` (an option, or a day still to confirm), and a request's state, as a translation
# key whose last part is the state.
ABSENT, TENTATIVE = "absent", "tentative"
UNDONE = frozenset({"rejected", "expired", "approval_error", "deleted", "cancelled"})
PENDING = frozenset({"pending_approval", "on_hold", "pending_parent_approval"})
# The event's title, in Dutch as the portal says it, as the .ics shows it; what makes a planned
# time differ from the usual one goes after it in brackets.
CARE = "Opvang"
NOTES = {"booking.type.extra": "extra dag", "booking.type.inter_change": "ruildag",
         "booking.type.change_group": "andere groep"}


class KonnectError(Exception):
    """Konnect couldn't be read at one provider. The message is short, for the Brief's coverage
    line: it names the portal's address and what went wrong, never the username or the password."""


class WrongPassword(KonnectError):
    """Konnect refused the username and password. The message has "login failed" in it, which the
    Brief says as a failed login, as of any Source."""


class MustResetPassword(KonnectError):
    """Konnect took the username and password but wants a new password set first, in a browser.
    Its message says "login failed" too: the family has to act, as for a wrong password."""


class Changed(KonnectError):
    """Konnect answered, but not as its portal did when this was written: its API has moved."""


@dataclass(frozen=True)
class Child:
    """One of the parent's children on Konnect: its id and names, all that is read of it."""
    id: str
    first_name: str
    last_name: str

    def names(self) -> list[str]:
        """The names a Kid in the config may be given as: the first name, and the full name
        either way round."""
        full = [f"{self.first_name} {self.last_name}", f"{self.last_name} {self.first_name}"] \
            if self.first_name and self.last_name else []
        return [n for n in (self.first_name, *full) if n]

    def called(self) -> str:
        return " ".join(n for n in (self.first_name, self.last_name) if n) or self.id

    def first(self) -> str:
        """The one name the child goes by, as a Kid may be added under it."""
        return (self.first_name.split() or [self.called()])[0]


def _host(text: str) -> str:
    """The host of an address or a link typed or pasted, or the text itself when it has none."""
    return re.sub(r"^[a-z]+://", "", text.strip().lower()).split("/", 1)[0].split(":", 1)[0]


def portal_of(text: str) -> str | None:
    """The provider's own part of its portal's address, `partou` for partou.ouderportaal.nl, from
    that part, the address or a link into it; None when it isn't one."""
    host = _host(text)
    portal = host[:-len(DOMAIN) - 1] if host.endswith("." + DOMAIN) else host
    return portal if re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", portal) else None


def is_website(text: str) -> bool:
    """Whether `text` is the address of a site that isn't a Konnect portal, such as the provider's
    own website, given where its portal's address was asked for."""
    host = _host(text)
    return (not host.endswith("." + DOMAIN)
            and re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", host) is not None)


def address(portal: str) -> str:
    return f"{portal}.{DOMAIN}"


def link(portal: str) -> str:
    """Where the family reads the portal's Messages page and Agenda in a browser."""
    return f"https://{address(portal)}/parent/"


def secret_name(portal: str, username: str) -> str:
    """The name of the secret that holds the password of the sign-in as `username` at `portal`.
    A username may be an email address, which no store's name may have."""
    key = f"{portal}/{username.strip().casefold()}"
    return "konnect-signin-" + hashlib.sha256(key.encode()).hexdigest()[:16]


def secrets_of(cfg: Config) -> list[str]:
    """The secret store's names of the Household's Konnect passwords, for uninstall."""
    return [secret_name(s.portal, s.username) for s in cfg.konnect.signins]


def _unwrap(answer: Any) -> Any:
    """The portal's `{result, payload, messages}` envelope's payload, or the answer itself when it
    has none, as the Messages page's conversations come."""
    if isinstance(answer, dict) and "payload" in answer and set(answer) <= {"result", "payload", "messages"}:
        return answer["payload"]
    return answer


class Portal:
    """One provider's parent portal, signed in to with a token of its own."""

    def __init__(self, portal: str) -> None:
        self.portal = portal
        self.where = address(portal)
        self.base = f"https://{self.where}/"
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json", "User-Agent": USER_AGENT,
                                     "Referer": link(portal)})

    def __enter__(self) -> Portal:
        return self

    def __exit__(self, *_a: Any) -> None:
        self.session.close()

    def _call(self, method: str, path: str, what: str, **kwargs: Any) -> Any:
        def call() -> requests.Response:
            return self.session.request(method, self.base + path, timeout=TIMEOUT, **kwargs)
        try:
            r = retry_once_on_timeout(call, (requests.Timeout,), f"Konnect {self.where}")
        except requests.RequestException as e:
            raise KonnectError(f"{self.where} didn't answer ({type(e).__name__})") from None
        if path == SIGN_IN and r.status_code in (400, 401, 403):
            # What the portal's login answers a wrong username or password.
            raise WrongPassword(f"login failed at {self.where}")
        if r.status_code in (401, 403):
            raise KonnectError(f"{self.where} signed the session out (HTTP {r.status_code})")
        if r.status_code == 404:
            raise Changed(f"{self.where} has no {what} where it was (HTTP 404)")
        if r.status_code >= 400:
            raise KonnectError(f"{self.where} answered HTTP {r.status_code}")
        try:
            answer = r.json()
        except ValueError:
            raise Changed(f"{self.where}'s {what} wasn't JSON") from None
        if isinstance(answer, dict) and answer.get("result") is False:
            raise Changed(f"{self.where} turned down its {what}")
        return _unwrap(answer)

    def sign_in(self, username: str, password: str) -> None:
        """Signs in as a parent. Raises WrongPassword when Konnect refuses them, and
        MustResetPassword when it wants a new password set first."""
        answer = self._call("PUT", SIGN_IN, "sign-in",
                            json={"username": username, "password": password, "childMinder": False})
        if isinstance(answer, dict) and answer.get("mustResetPassword"):
            raise MustResetPassword(f"login failed at {self.where}: it asks for a new password")
        token = answer.get("authToken") if isinstance(answer, dict) else None
        if not isinstance(token, str) or not token:
            raise Changed(f"{self.where}'s sign-in answered in a way it didn't before")
        self.session.headers["Authorization"] = f"Bearer {token}"

    def _get(self, path: str, what: str, **params: str) -> Any:
        return self._call("GET", DATA + path, what, params=params or None)

    def children(self) -> list[Child]:
        """The parent's children who go there now, by their ids and names only."""
        answer = self._get("children/", "list of children")
        listed = answer.get("activeChildren") if isinstance(answer, dict) else None
        if not isinstance(listed, list):
            raise Changed(f"{self.where}'s list of children isn't a list")
        out = []
        for item in listed:
            if not isinstance(item, dict) or not _id(item.get("id")) \
                    or not (item.get("firstName") or item.get("fullname")):
                raise Changed(f"{self.where}'s list of children has no names")
            first = _text(item.get("firstName")) or _text(item.get("fullname"))
            out.append(Child(_id(item["id"]), first, _text(item.get("lastName"))))
        return out

    def authorizations(self) -> set[str]:
        """What the account may see, such as WEB_PARENT_LOGBOOK."""
        answer = self._get("parent/authorizations", "authorizations")
        functions = answer.get("systemFunctions") if isinstance(answer, dict) else None
        if not isinstance(functions, list):
            raise Changed(f"{self.where}'s authorizations aren't a list")
        return {f for f in functions if isinstance(f, str)}

    def conversations(self, start: date, end: date) -> list[dict[str, Any]]:
        """The conversations whose latest message is from `start` to `end`, as the list gives them."""
        return self._list(self._get("logbook/overview", "messages", **_range(start, end)), "messages")

    def news(self, start: date, end: date) -> list[dict[str, Any]]:
        """The news published from `start` to `end`, as the list gives them."""
        answer = self._get("htmlnews/view", "news", **_range(start, end))
        return self._list(answer.get("newsItems") if isinstance(answer, dict) else None, "news")

    def newsletters(self, start: date, end: date) -> list[dict[str, Any]]:
        """The newsletters and service messages sent from `start` to `end`, as the list gives them."""
        answer = self._get("newsletter", "newsletters", **_range(start, end))
        return self._list(answer.get("newsletterItems") if isinstance(answer, dict) else None, "newsletters")

    def agenda(self, start: date, end: date) -> list[dict[str, Any]]:
        """The Agenda's days from `start` to `end`, both in, that have something planned."""
        answer = self._get(f"calendar/{start:%Y%m%d}/until/{end:%Y%m%d}", "agenda")
        return self._list(answer.get("days") if isinstance(answer, dict) else None, "agenda")

    def _list(self, items: Any, what: str) -> list[dict[str, Any]]:
        if items is None:
            return []
        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            raise Changed(f"{self.where}'s {what} aren't a list")
        return items


def _range(start: date, end: date) -> dict[str, str]:
    return {"from": f"{start:%Y%m%d}", "to": f"{end:%Y%m%d}"}


def _text(v: Any) -> str:
    return " ".join(v.split()) if isinstance(v, str) else ""


def _id(v: Any) -> str:
    """An id as the portal gives it, as text; empty for none."""
    if isinstance(v, bool) or not isinstance(v, (str, int)) or v in ("", 0):
        return ""
    return str(v)


def _when(v: Any) -> datetime | None:
    """One of the portal's times, epoch milliseconds, or an ISO time, in UTC; None for none."""
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
        try:
            return datetime.fromtimestamp(v / 1000, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(v, str) and v:
        try:
            at = datetime.fromisoformat(v)
        except ValueError:
            return None
        return (at if at.tzinfo else at.replace(tzinfo=PORTAL_ZONE)).astimezone(timezone.utc)
    return None


class _Text(HTMLParser):
    """A message's HTML as the portal shows it: a line break or a block starts a new line."""
    BREAKS = frozenset({"br", "p", "div", "li", "tr"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(re.sub(r"\s+", " ", data))

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self.BREAKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.BREAKS:
            self.parts.append("\n")


def text_of(html: str) -> str:
    """The text of a message's HTML, a line each, without the empty ones."""
    parser = _Text()
    parser.feed(html)
    parser.close()
    return "\n".join(line for line in (" ".join(p.split()) for p in "".join(parser.parts).splitlines()) if line)


def kid_of(child: Child, kids: Iterable[Kid]) -> str | None:
    """The Kid `child` is, by name: one of the Kid's names, aliases or everyday name is one of the
    child's, or the first word of the Kid's name is its first name. None when no Kid, or more than
    one, is."""
    names = {n.casefold() for n in child.names()}
    first = child.first_name.split()[0].casefold() if child.first_name else ""
    found = [k.name for k in kids
             if names & {t.casefold() for t in k.match_terms()}
             or (first and [w.casefold() for w in k.name.split()[:1]] == [first])]
    return found[0] if len(found) == 1 else None


def kids_of(signin: KonnectSignin, children: list[Child], kids: Iterable[Kid]) -> dict[str, str | None]:
    """Each of the sign-in's children, by id, as the Kid it is: the one its `kids` names, else the
    one its name matches."""
    kids = list(kids)
    known = {k.name for k in kids}
    return {c.id: (signin.kids.get(c.id) if signin.kids.get(c.id) in known else kid_of(c, kids))
            for c in children}


def _whose(ids: Iterable[str], kids: Mapping[str, str | None]) -> str | None:
    """The one Kid the children `ids` are, or None (the Household) for none, several or a child
    not one of the Kids."""
    whose = {kids.get(i) for i in ids} or {None}
    return whose.pop() if len(whose) == 1 else None


def _children(item: dict[str, Any]) -> list[str]:
    return [i for c in item.get("children") or [] if isinstance(c, dict) and (i := _id(c.get("id")))]


def messages_of(conversations: Iterable[dict[str, Any]], news: Iterable[dict[str, Any]],
                newsletters: Iterable[dict[str, Any]], portal: str,
                kids: Mapping[str, str | None]) -> list[Message]:
    """The Messages page's three lists at `portal` as Messages:
    - each conversation whose latest message the daycare wrote, that message in full, for the Kid
      its children are (none for several or none); one whose latest message is the family's own
      isn't. A new message in it is a Message of its own, by that message's id;
    - each news item and each newsletter or service message, by its title and the start of its
      text the list gives, for the Household. They went to every parent.
    An item without its time is left out; one without its id raises Changed."""
    where = f"{address(portal)}'s messages"
    url = link(portal)
    out: list[Message] = []
    for c in conversations:
        if c.get("lastFromParent") or c.get("type") == "from_parent_message":
            continue
        at = _when(c.get("date"))
        if at is None:
            continue
        message_id = next((i for f in ("lastLogMessageMessageId", "logMessageMessageId", "id")
                           if (i := _id(c.get(f)))), "")
        if not message_id:
            raise Changed(f"{where} has a message without its id")
        out.append(Message(
            source="konnect", external_id=f"message:{message_id}", timestamp=at,
            sender=_text(c.get("lastWritten")) or None, subject=_text(c.get("subject")) or None,
            body=text_of(c.get("message") if isinstance(c.get("message"), str) else "")[:MAX_BODY_CHARS],
            chat_name="message", kid_hint=_whose(_children(c), kids), url=url,
            metadata={"konnect_portal": portal, "kind": "message"}))
    for kind, items, id_fields, title, sent, chat in (
            ("news", news, ("id", "htmlNewsId", "htmlNewsItemId", "newsItemId"), "title", "publishDate", "news"),
            ("newsletter", newsletters, ("generatedHtmlNewsLetterId", "id"), "mailSubject", "sendDate",
             "newsletter")):
        for item in items:
            at = _when(item.get(sent))
            if at is None:
                continue
            item_id = next((i for f in id_fields if (i := _id(item.get(f)))), "")
            if not item_id:
                raise Changed(f"{where} has {kind} without its id")
            body = _text(item.get("contentSnippet"))
            if item.get("containsAttachment"):  # model input, so in English: the file is only there
                body += "\n\n[an attachment in Konnect]"
            service = _text(item.get("newsLetterType")).casefold() == "servicemessage"
            out.append(Message(
                source="konnect", external_id=f"{kind}:{item_id}", timestamp=at, sender=None,
                subject=_text(item.get(title)) or None, body=body.strip()[:MAX_BODY_CHARS],
                chat_name="service message" if service else chat, url=url,
                metadata={"konnect_portal": portal, "kind": kind}))
    return out


def sent_to_everyone(m: Message) -> bool:
    """Whether the Konnect message `m` went to every parent, so it is never held back for its
    words: a news item or a newsletter. A conversation may be about one child, so it is checked."""
    return m.metadata.get("kind") in ("news", "newsletter")


def events(days: Iterable[dict[str, Any]], portal: str, kids: Mapping[str, str | None]) -> list[CalendarEvent]:
    """The Agenda's `days` at `portal` as calendar events: each time a child is planned to be there,
    for the Kid the child is, an option or a request still to be answered said in its title. An
    absence, and a request that won't happen, aren't. A time without its start or end is left out."""
    out: dict[str, CalendarEvent] = {}
    for day in days:
        for planning in day.get("children") or []:
            if not isinstance(planning, dict):
                continue
            child = planning.get("child") if isinstance(planning.get("child"), dict) else {}
            child_id = _id(child.get("id"))
            if not child_id:
                continue
            for combined in planning.get("combinedPlanningParts") or []:
                parts = combined.get("planningParts") if isinstance(combined, dict) else None
                for part in parts if isinstance(parts, list) else []:
                    if isinstance(part, dict) and (e := _event(portal, child_id, part, kids)) is not None:
                        out.setdefault(e.external_id, e)
    return sorted(out.values(), key=lambda e: (e.start, e.kid or ""))


def _code(v: Any) -> str:
    return _text(v.get("code")) if isinstance(v, dict) else ""


def _event(portal: str, child: str, part: dict[str, Any], kids: Mapping[str, str | None]) -> CalendarEvent | None:
    start, end = _when(part.get("startTime")), _when(part.get("endTime"))
    code = _code(part.get("plannedAttendanceCode")) or ("attend" if part.get("attend") else ABSENT)
    status = _text(part.get("bookingStatus")).rsplit(".", 1)[-1]
    if start is None or end is None or end <= start or code == ABSENT or status in UNDONE:
        return None
    notes = ["optie"] if code == TENTATIVE else []
    if note := NOTES.get(_text(part.get("bookingType"))):
        notes.append(note)
    if status in PENDING:
        notes.append("aangevraagd")
    group = part.get("childGroup") if isinstance(part.get("childGroup"), dict) else {}
    place = group.get("location") if isinstance(group.get("location"), dict) else {}
    return CalendarEvent(
        source="konnect",
        # The same each night, so the .ics and Google Calendar get it once (calendar.event_hash).
        external_id="konnect:" + hashlib.sha256(f"{portal}|{child}|{start.isoformat()}|{end.isoformat()}"
                                                .encode()).hexdigest()[:16],
        title=f"{CARE} ({', '.join(notes)})" if notes else CARE, start=start, end=end,
        location=_text(place.get("name")) or None, description=_text(part.get("extraInfo")),
        kid=kids.get(child))


def password_of(signin: KonnectSignin) -> str:
    """The sign-in's password from the secret store. Raises WrongPassword when there is none."""
    try:
        password = secrets.get(secret_name(signin.portal, signin.username))
    except secrets.SecretsError as e:
        raise KonnectError(f"the password for {address(signin.portal)} couldn't be read from the "
                           f"{secrets.STORE}: {e}") from None
    if not password:
        raise WrongPassword(f"login failed at {address(signin.portal)}: no password in the {secrets.STORE}")
    return password


def today() -> date:
    """Today where the portal is, the day its lists and Agenda go by."""
    return datetime.now(PORTAL_ZONE).date()


@dataclass
class _Read:
    """What one sign-in gave tonight: its children with the Kid each is, its events, its messages,
    and why its messages couldn't be read, if they couldn't."""
    children: list[tuple[Child, str | None]]
    events: list[CalendarEvent]
    messages: list[Message]
    messages_failed: KonnectError | None = None


def read_all(portal: Portal, signin: KonnectSignin, cfg: Config, first: date, since: datetime) -> _Read:
    """Over a signed-in `portal`: the parent's children and the Kid each is, the Agenda's events
    from the day `first` to LOOKAHEAD_DAYS on, and the Messages page's items back to `since`, of
    those the account may see. Messages that can't be read leave the events."""
    children = portal.children()
    kids = kids_of(signin, children, cfg.kids)
    may = portal.authorizations()
    got = events(portal.agenda(first, first + timedelta(days=LOOKAHEAD_DAYS)), signin.portal, kids) \
        if may & AGENDA else []
    out = _Read([(c, kids[c.id]) for c in children], got, [])
    start, end = since.astimezone(PORTAL_ZONE).date(), first + timedelta(days=1)
    try:
        out.messages = messages_of(
            portal.conversations(start, end) if MESSAGES in may else [],
            portal.news(start, end) if NEWS in may else [],
            portal.newsletters(start, end) if NEWSLETTERS in may else [], signin.portal, kids)
    except KonnectError as e:
        out.messages_failed = e
    return out


def read(cfg: Config, signin: KonnectSignin, first: date, since: datetime) -> _Read:
    """The sign-in's events from the day `first` on and its messages back to `since` at least."""
    password = password_of(signin)
    with Portal(signin.portal) as portal:
        portal.sign_in(signin.username, password)
        return read_all(portal, signin, cfg, first, since)


def collect(cfg: Config, state: State, kid_terms: list[str]) -> Collected:
    """The coming planned times of each Konnect sign-in, to add to the calendar, and the messages
    its daycare sent within the lookback that no earlier run told. Events come again each night,
    so none is marked seen; a message is, so it is told once. A sign-in that fails while another
    works, or whose messages can't be read, is logged by its address, so the Brief says Konnect was
    partly read and where; when none works, Konnect wasn't read, with the first one's reason."""
    now = datetime.now(timezone.utc)
    on = today()
    since = now - timedelta(hours=cfg.konnect.lookback_hours)
    out: list[CalendarEvent] = []
    messages: list[Message] = []
    failed: list[KonnectError] = []
    several = len(cfg.konnect.signins) > 1
    for signin in cfg.konnect.signins:
        where = address(signin.portal)
        try:
            got = read(cfg, signin, on, since)
        except KonnectError as e:
            failed.append(e)
            if several:
                log.error("%s", sign_in_failed(where, str(e), signin.username))
            continue
        if got.messages_failed is not None:
            why = str(got.messages_failed)
            log.error("%s", sign_in_failed(where, why, signin.username) if several else why)
        events = [e for e in got.events if (e.end or e.start) >= now]
        new = [m for m in got.messages if not state.has_seen_message("konnect", m.external_id)]
        for m in new:  # one from before the lookback is seen untold, so a wider one never tells it late
            state.mark_message_seen("konnect", m.external_id)
        new = [m for m in new if m.timestamp >= since]
        log.info("Konnect: %d events (next %dd), %d new messages at %s", len(events), LOOKAHEAD_DAYS,
                 len(new), where)
        out += events
        messages += new
    if failed and len(failed) == len(cfg.konnect.signins):
        raise failed[0]
    return Collected(messages=messages, events=out)
