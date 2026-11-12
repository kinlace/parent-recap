"""The Social Schools Source: the announcements and calendar of a school on Social Schools, the
parent app many schools in the Netherlands use, read through the API its own apps read, with the
parent's email and password (ADR 0047).

Social Schools has no feed for parents and no published API. Its iOS app signs in through OpenID
Connect at login.socialschools.eu: an authorization request with PKCE, the normal login form
(`/Account/Login`, email and password), and a redirect to the app's own address with a code, which
the token endpoint turns into an access token. The Source does the same without a browser: it
follows the redirects itself, fills in the form, and stops at the redirect to the app. A sign-in
that leaves login.socialschools.eu (Google, Microsoft or Apple) or asks for a further step after
the password can't be done this way: it is Unsupported, and setup and `doctor` say so. The access
token lasts about an hour and no refresh token is asked for, so each evening run signs in again,
with the password in the system's secret store (`system/secrets`).

With the token, the run reads from api.socialschools.eu, and only reads (ALLOWED, all GETs):
- `useraccounts/current`: the schools the account is a parent at, and at each the children, only
  their names and the names of their groups;
- `communityposts`: each school's announcements, the latest first, back to the lookback. Each one
  posted since the last run becomes a Message for the Kid whose group it was posted in, or the
  Household when it was posted in several Kids' groups or the whole school's. Its attachments are
  named, never downloaded;
- `events`: each school's calendar for the next LOOKAHEAD_DAYS days, each a calendar event for the
  Kid whose group it is in, or the Household.
Nothing is written to Social Schools: the web app's "seen" statistics are never sent, a post's own
page is never opened, and the conversations inbox, whose messages may be marked read as they're
fetched, isn't read at all.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import re
import secrets as tokens
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from ..system import secrets
from .base import CalendarEvent, Collected, Message, retry_once_on_timeout, sign_in_failed

if TYPE_CHECKING:
    from ..config import Config, Kid, SocialSchoolsSignin
    from ..state import State

log = logging.getLogger(__name__)

NAME = "Social Schools"
ISSUER = "https://login.socialschools.eu"
LOGIN_HOST = "login.socialschools.eu"
API = "https://api.socialschools.eu/api/v1/"
API_HOST = "api.socialschools.eu"
WEB = "https://app.socialschools.eu"
# The iOS app's own client, as it signs in (HAChoresManager/ha-socialschools). Without
# offline_access no refresh token is made: the run signs in again each evening.
CLIENT_ID = "eu.socialschools.ios"
REDIRECT_URI = "eu.socialschools.ios:/oauthredirect"
SCOPES = "openid profile email SocsWebApi"
AUTHORIZE = "/connect/authorize"
CALLBACK = "/connect/authorize/callback"
TOKEN = "/connect/token"
LOGIN_FORM = "/Account/Login"
MAX_STEPS = 10  # redirects and pages from the authorization request to the app's address
# The only API paths the Source asks for, each with GET.
ALLOWED = ("useraccounts/current", "communityposts", "events")
PARENT_ROLE = 1  # a role of `useraccounts/current` whose type is this is a parent's
SCHOOL_ZONE = ZoneInfo("Europe/Amsterdam")  # Social Schools' times without an offset
TIMEOUT = 30
LOOKAHEAD_DAYS = 28
FEED_LIMIT = 20  # posts a page
FEED_PAGES = 5   # more than a school posts in the longest lookback
MAX_BODY_CHARS = 8000
MAX_ATTACHMENTS = 20
# The secret a sign-in's password waits under while `setup socialschools` checks it, so a password
# typed wrong never takes the place of one that works.
TYPED = "socialschools-signin-typed"
_REDIRECTS = (301, 302, 303, 307, 308)


class SocialSchoolsError(Exception):
    """Social Schools couldn't be read. The message is short, for the Brief's coverage line: it
    says what went wrong, never the email, the password or a token."""


class WrongPassword(SocialSchoolsError):
    """The login form came back after the email and password were sent: Social Schools didn't take
    them. So does an account that signs in only with Google, Microsoft or Apple, which has no
    password there. The message has "login failed" in it, which the Brief says as a failed login."""


class Unsupported(SocialSchoolsError):
    """The sign-in went somewhere this Source can't follow: to Google, Microsoft or Apple, or to a
    further step after the password, such as a code. Said as a failed login too."""


class Changed(SocialSchoolsError):
    """Social Schools answered, but not as it did when this was written: its sign-in or its API
    has moved."""


@dataclass(frozen=True)
class Child:
    """One of the parent's children at a school: its names and its groups' names, all that is
    read of it."""
    first_name: str
    display_name: str
    groups: frozenset[str]  # each group's name as _group makes it

    def called(self) -> str:
        return self.display_name or self.first_name

    def first(self) -> str:
        """The one name the child goes by, as a Kid may be added under it."""
        return self.first_name or (self.display_name.split() or [""])[0]

    def names(self) -> list[str]:
        return [n for n in dict.fromkeys((self.first_name, self.display_name, self.first())) if n]


@dataclass(frozen=True)
class School:
    """A school the account is a parent at, with the parent's children there."""
    id: int
    name: str
    children: tuple[Child, ...]


def _text(v: Any) -> str:
    return " ".join(v.split()) if isinstance(v, str) else ""


def _group(name: str) -> str:
    """A group's name for comparing: spaces collapsed, case ignored, so `Groep 3` is `groep  3`."""
    return " ".join(name.split()).casefold()


def _id(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


_BLOCKS = ("p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table", "blockquote")


def plain(html: Any) -> str:
    """The text of a post's or an event's HTML: a line for each paragraph, list item or line break,
    a list item after a dash, and the words of a line as they run, bold or not."""
    if not isinstance(html, str) or not html.strip():
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert(0, "- ")
    for block in soup.find_all(_BLOCKS):
        block.insert_after("\n")
    lines = [" ".join(line.split()) for line in soup.get_text().splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def secret_name(username: str) -> str:
    """The name of the secret that holds the password of the sign-in as `username`, an email
    address, which no store's name may have."""
    return "socialschools-signin-" + hashlib.sha256(username.strip().casefold().encode()).hexdigest()[:16]


def secrets_of(cfg: Config) -> list[str]:
    """The secret store's names of the Household's Social Schools passwords, for uninstall."""
    return [secret_name(s.username) for s in cfg.socialschools.signins]


class Portal:
    """Social Schools, signed in to as one parent: the sign-in's cookies on a session of its own,
    then the access token for the API."""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.token: str | None = None

    def __enter__(self) -> Portal:
        return self

    def __exit__(self, *_a: Any) -> None:
        self.session.close()

    def _send(self, method: str, url: str, what: str, **kwargs: Any) -> requests.Response:
        def call() -> requests.Response:
            return self.session.request(method, url, timeout=TIMEOUT, allow_redirects=False, **kwargs)
        try:
            return retry_once_on_timeout(call, (requests.Timeout,), f"Social Schools {what}")
        except requests.RequestException as e:
            host = urlsplit(url).hostname or LOGIN_HOST
            raise SocialSchoolsError(f"{host} didn't answer ({type(e).__name__})") from None

    # ── Signing in

    def sign_in(self, username: str, password: str) -> None:
        """Signs in as the iOS app does and keeps the access token. Raises WrongPassword when the
        login form comes back, Unsupported for a sign-in it can't follow."""
        verifier = _b64(tokens.token_bytes(32))
        state = _b64(tokens.token_bytes(32))
        query = {"client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI, "response_type": "code",
                 "scope": SCOPES, "state": state, "nonce": _b64(tokens.token_bytes(32)),
                 "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()),
                 "code_challenge_method": "S256"}
        url, form = f"{ISSUER}{AUTHORIZE}?{urlencode(query)}", None
        submitted = False
        for _ in range(MAX_STEPS):
            if form is None:
                r = self._send("GET", url, "sign-in", headers={"Accept": "text/html"})
            else:
                r = self._send("POST", url, "sign-in", data=form,
                               headers={"Accept": "text/html", "Origin": ISSUER})
                submitted, form = True, None
            if r.status_code in _REDIRECTS:
                target = urljoin(url, r.headers.get("Location") or "")
                if target.startswith(REDIRECT_URI.split(":", 1)[0] + ":"):
                    self.token = self._token(self._code(target, state), verifier)
                    return
                if urlsplit(target).hostname != LOGIN_HOST and not submitted:
                    raise Changed(f"{LOGIN_HOST}'s sign-in sent it somewhere it didn't before")
                if urlsplit(target).hostname != LOGIN_HOST:
                    raise Unsupported(f"login failed at {LOGIN_HOST}: the sign-in goes on elsewhere "
                                      "(Google, Microsoft or Apple), which Parent Recap can't follow")
                url = target
            elif r.status_code == 200:
                url, form = self._page(r.text, url, submitted, username, password, query)
            elif r.status_code >= 500:
                raise SocialSchoolsError(f"{LOGIN_HOST} answered HTTP {r.status_code}")
            else:
                raise Changed(f"{LOGIN_HOST}'s sign-in answered HTTP {r.status_code}")
        raise Changed(f"{LOGIN_HOST}'s sign-in took more steps than it did")

    def _page(self, html: str, url: str, submitted: bool, username: str, password: str,
              query: Mapping[str, str]) -> tuple[str, dict[str, str] | None]:
        """What follows a page of the sign-in: the page it refreshes to, or the login form filled
        in, as the URL and the form to post."""
        soup = BeautifulSoup(html, "html.parser")
        refresh = soup.find("meta", attrs={"http-equiv": re.compile("^refresh$", re.I)})
        if refresh is not None:
            found = re.match(r"\s*\d+\s*;\s*url\s*=\s*['\"]?([^'\"]+)", str(refresh.get("content") or ""), re.I)
            if found is None or urlsplit(target := urljoin(url, found.group(1).strip())).hostname != LOGIN_HOST:
                raise Changed(f"{LOGIN_HOST}'s sign-in refreshed to a page it didn't before")
            return target, None
        if submitted:
            if urlsplit(url).path.rstrip("/").casefold() == LOGIN_FORM.casefold():
                raise WrongPassword(f"login failed at {LOGIN_HOST}")
            raise Unsupported(f"login failed at {LOGIN_HOST}: it asks for a further step after the "
                              "password, which Parent Recap can't do")
        if urlsplit(url).path.rstrip("/").casefold() != LOGIN_FORM.casefold():
            raise Changed(f"{LOGIN_HOST}'s sign-in showed a page it didn't before")
        for f in soup.find_all("form"):
            inputs = {i.get("name"): i.get("value") or "" for i in f.find_all("input") if i.get("name")}
            if str(f.get("method") or "").lower() != "post" or not {"Username", "Password"} <= set(inputs) \
                    or not inputs.get("__RequestVerificationToken") or not inputs.get("ReturnUrl"):
                continue
            action = urljoin(url, str(f.get("action") or url))
            back = urljoin(url, inputs["ReturnUrl"])
            if urlsplit(action).hostname != LOGIN_HOST or urlsplit(back).hostname != LOGIN_HOST \
                    or urlsplit(back).path != CALLBACK \
                    or parse_qs(urlsplit(back).query).get("state") != [query["state"]]:
                raise Changed(f"{LOGIN_HOST}'s login form goes somewhere it didn't before")
            return action, {"Username": username, "Password": password,
                            "ReturnUrl": inputs["ReturnUrl"],
                            "__RequestVerificationToken": inputs["__RequestVerificationToken"],
                            "button": "login"}
        raise Changed(f"{LOGIN_HOST}'s login form isn't as it was")

    @staticmethod
    def _code(target: str, state: str) -> str:
        """The authorization code the redirect to the app carries, once its state is ours."""
        got = parse_qs(urlsplit(target).query)
        if "error" in got or got.get("state") != [state] or len(got.get("code") or []) != 1 \
                or got.get("iss", [ISSUER]) != [ISSUER]:
            raise Changed(f"{LOGIN_HOST}'s sign-in ended in a way it didn't before")
        return got["code"][0]

    def _token(self, code: str, verifier: str) -> str:
        r = self._send("POST", ISSUER + TOKEN, "sign-in", headers={"Accept": "application/json"},
                       data={"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
                             "redirect_uri": REDIRECT_URI, "client_id": CLIENT_ID})
        if r.status_code >= 500:
            raise SocialSchoolsError(f"{LOGIN_HOST} answered HTTP {r.status_code}")
        try:
            token = r.json().get("access_token") if r.status_code == 200 else None
        except (ValueError, AttributeError):
            token = None
        if not isinstance(token, str) or not token or any(c.isspace() for c in token):
            raise Changed(f"{LOGIN_HOST} gave no access token for the sign-in (HTTP {r.status_code})")
        return token

    # ── Reading

    def get(self, path: str, what: str, school: int | None = None, **params: str) -> Any:
        """The JSON at the API's `path`, one of ALLOWED, with GET, the only method it sends."""
        if path not in ALLOWED:
            raise ValueError(f"Social Schools is only read at {', '.join(ALLOWED)}")
        if self.token is None:
            raise SocialSchoolsError("not signed in to Social Schools")
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if school is not None:
            headers.update({"Schoolid": str(school), "Roletypeid": str(PARENT_ROLE)})
        r = self._send("GET", API + path, what, headers=headers, params=params)
        if r.status_code in (401, 403):
            raise SocialSchoolsError(f"{API_HOST} didn't take the sign-in (HTTP {r.status_code})")
        if r.status_code == 404:
            raise Changed(f"{API_HOST} has no {what} where it was (HTTP 404)")
        if r.status_code != 200:
            raise SocialSchoolsError(f"{API_HOST} answered HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError:
            raise Changed(f"{API_HOST}'s {what} wasn't JSON") from None

    def schools(self) -> list[School]:
        """The schools the account is a parent at, each with the parent's children there, by their
        names and their groups' names only."""
        account = self.get("useraccounts/current", "account")
        roles = account.get("roles") if isinstance(account, dict) else None
        if not isinstance(roles, list):
            raise Changed(f"{API_HOST}'s account has no roles")
        found: dict[int, School] = {}
        for role in roles:
            if not isinstance(role, dict) or _id(role.get("type")) != PARENT_ROLE \
                    or not isinstance(role.get("school"), dict) or _id(role["school"].get("id")) is None:
                continue
            school = role["school"]
            children = [c for c in (_child(c) for c in role.get("connections") or []) if c]
            had = found.get(school["id"])
            found[school["id"]] = School(
                school["id"], _text(school.get("displayName")) or _text(school.get("name")) or str(school["id"]),
                (*(had.children if had else ()), *children))
        return list(found.values())

    def posts(self, school: int, offset: int) -> list[dict[str, Any]]:
        """A page of the school's announcements, the latest first, from `offset` on."""
        answer = self.get("communityposts", "announcements", school, offset=str(offset),
                          limit=str(FEED_LIMIT), filterType="0")
        values = answer.get("values") if isinstance(answer, dict) else None
        if not isinstance(values, list):
            raise Changed(f"{API_HOST}'s announcements aren't as they were")
        return [p for p in values if isinstance(p, dict)]

    def events(self, school: int, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """The school's calendar from `start` to `end`."""
        def utc(at: datetime) -> str:
            return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        answer = self.get("events", "calendar", school, **{"from": utc(start), "until": utc(end)})
        values = answer.get("values") if isinstance(answer, dict) else answer
        if not isinstance(values, list):
            raise Changed(f"{API_HOST}'s calendar isn't a list of events")
        return [e for e in values if isinstance(e, dict)]


def _child(connection: Any) -> Child | None:
    """A child of a parent's role, as `useraccounts/current` lists it: its names and its groups,
    which come as names or as objects with one."""
    if not isinstance(connection, dict):
        return None
    first, shown = _text(connection.get("firstName")), _text(connection.get("displayName"))
    if not (first or shown):
        return None
    groups = connection.get("groups") if isinstance(connection.get("groups"), list) else []
    names = {_group(_text(g.get("name")) if isinstance(g, dict) else _text(g)) for g in groups}
    return Child(first, shown, frozenset(n for n in names if n))


def kid_of(child: Child, kids: Iterable[Kid]) -> str | None:
    """The Kid `child` is, by name: one of the Kid's names, aliases or everyday name is one of the
    child's, or the first word of the Kid's name is the child's first name. None when no Kid, or
    more than one, is."""
    names = {n.casefold() for n in child.names()}
    first = child.first().casefold()
    found = [k.name for k in kids
             if names & {t.casefold() for t in k.match_terms()}
             or (k.name.split()[:1] and k.name.split()[0].casefold() == first)]
    return found[0] if len(found) == 1 else None


def kids_of(signin: SocialSchoolsSignin, schools: list[School], kids: Iterable[Kid]) -> dict[Child, str | None]:
    """Each child at each school as the Kid it is: the one the sign-in's `kids` names for the
    child's name, else the one its name matches."""
    kids = list(kids)
    known = {k.name for k in kids}
    return {c: (signin.kids.get(c.called()) if signin.kids.get(c.called()) in known else kid_of(c, kids))
            for s in schools for c in s.children}


def _whose(groups: Iterable[str], school: School, kids: Mapping[Child, str | None]) -> str | None:
    """The one Kid whose group one of `groups` is, or None (the Household) for a post or an event
    in no Kid's group, such as the whole school's, or in several Kids' groups."""
    wanted = {_group(g) for g in groups}
    whose = {kids.get(c) for c in school.children if c.groups & wanted}
    return whose.pop() if len(whose) == 1 else None


def _groups(item: dict[str, Any]) -> list[str]:
    """The names of the groups a post or an event is in."""
    groups = item.get("communities") if isinstance(item.get("communities"), list) else []
    return list(dict.fromkeys(n for g in groups if isinstance(g, dict) and (n := _text(g.get("name")))))


def _at(v: Any) -> datetime | None:
    """One of Social Schools' times, aware: one without an offset is in the Netherlands."""
    if not isinstance(v, str) or not re.match(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", v.strip()):
        return None
    try:
        at = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=SCHOOL_ZONE)


def post_link(post_id: Any) -> str:
    """Where the family reads a post in Social Schools' own web app."""
    return f"{WEB}/communityposts/{post_id}"


def messages_of(posts: Iterable[dict[str, Any]], school: School, kids: Mapping[Child, str | None]) -> list[Message]:
    """The school's `posts` as Messages, each for the Kid whose group it was posted in (none for
    several or none), with its title as the subject and its groups as its chat, and its
    attachments by their names only. A post without its id or its time is skipped and logged."""
    out = []
    for post in posts:
        post_id, created = _id(post.get("id")), _at(post.get("created"))
        if post_id is None or created is None:
            log.warning("Social Schools: an announcement without its id or time at school %s was skipped",
                        school.id)
            continue
        groups = _groups(post)
        media = post.get("media") if isinstance(post.get("media"), list) else []
        files = [n for m in media if isinstance(m, dict) and (n := _text(m.get("fileName")))][:MAX_ATTACHMENTS]
        body = plain(post.get("body"))
        if files:  # model input, so in English: the files are only in Social Schools
            body += f"\n\n[Attachments in Social Schools: {', '.join(files)}]"
        author = post.get("author") if isinstance(post.get("author"), dict) else {}
        out.append(Message(
            source="socialschools", external_id=str(post_id), timestamp=created.astimezone(timezone.utc),
            sender=_text(author.get("displayName")) or None, subject=_text(post.get("title")) or None,
            body=body.strip()[:MAX_BODY_CHARS], chat_name=", ".join(groups) or school.name,
            kid_hint=_whose(groups, school, kids), url=post_link(post_id),
            metadata={"school_id": school.id}))
    return out


def _event_id(school: School, *parts: object) -> str:
    """The same each night, so the .ics and Google Calendar get it once (calendar.event_hash)."""
    return "socialschools:" + hashlib.sha256("|".join(map(str, (school.id, *parts))).encode()).hexdigest()[:16]


def events_of(calendar: Iterable[dict[str, Any]], school: School, kids: Mapping[Child, str | None],
              tz: str) -> list[CalendarEvent]:
    """The school's `calendar` as calendar events, their times in `tz`. A whole day is a date as
    Social Schools writes it, `2026-11-26T00:00:00Z` to an end the day after, never moved to
    another zone; a timed event without a later end has none. One without its title or start is
    skipped and logged."""
    zone = ZoneInfo(tz)
    out = []
    for e in calendar:
        title, start, end = _text(e.get("title")), _at(e.get("start")), _at(e.get("end"))
        if not title or start is None:
            log.warning("Social Schools: a calendar event without its title or start at school %s was skipped",
                        school.id)
            continue
        groups = _groups(e)
        common = dict(source="socialschools", title=title, description=plain(e.get("description")),
                      location=_text(e.get("location")) or None, kid=_whose(groups, school, kids))
        if e.get("allDay"):
            first = start.date()
            # The end is the day after the last as written, or the last day when it isn't at midnight.
            last = (end.date() - timedelta(days=1) if end.time() == time(0) else end.date()) if end else first
            out.append(CalendarEvent(
                external_id=_event_id(school, e.get("id"), first), all_day=True,
                start=datetime.combine(first, time(0), zone),
                end=datetime.combine(last, time(0), zone) if last > first else None, **common))
        else:
            out.append(CalendarEvent(
                external_id=_event_id(school, e.get("id"), start.astimezone(timezone.utc).isoformat()),
                start=start.astimezone(zone), end=end.astimezone(zone) if end and end > start else None,
                **common))
    return out


def password_of(signin: SocialSchoolsSignin) -> str:
    """The sign-in's password from the secret store. Raises WrongPassword when there is none."""
    try:
        password = secrets.get(secret_name(signin.username))
    except secrets.SecretsError as e:
        raise SocialSchoolsError(f"the Social Schools password couldn't be read from the {secrets.STORE}: "
                                 f"{e}") from None
    if not password:
        raise WrongPassword(f"login failed at {LOGIN_HOST}: no password in the {secrets.STORE}")
    return password


def read_events(portal: Portal, signin: SocialSchoolsSignin, cfg: Config, now: datetime
                ) -> tuple[list[School], dict[Child, str | None], list[CalendarEvent]]:
    """Over a signed-in `portal`: the schools with the children, the Kid each child is, and each
    school's events from `now` to LOOKAHEAD_DAYS on."""
    schools = portal.schools()
    kids = kids_of(signin, schools, cfg.kids)
    end = now + timedelta(days=LOOKAHEAD_DAYS)
    events = [e for s in schools for e in events_of(portal.events(s.id, now, end), s, kids, cfg.timezone)]
    return schools, kids, events


def posts_since(portal: Portal, school: School, since: datetime) -> list[dict[str, Any]]:
    """The school's announcements, page by page, the latest first, until a page reaches back before
    `since`, or FEED_PAGES pages."""
    out: list[dict[str, Any]] = []
    for page in range(FEED_PAGES):
        posts = portal.posts(school.id, page * FEED_LIMIT)
        out += posts
        times = [at for p in posts if (at := _at(p.get("created"))) is not None]
        if len(posts) < FEED_LIMIT or not times or min(times) < since:
            break
    return out


@dataclass
class _Read:
    """What one sign-in gave tonight: its events, its announcements, and why its announcements
    couldn't be read, if they couldn't."""
    events: list[CalendarEvent]
    messages: list[Message]
    messages_failed: SocialSchoolsError | None = None


def read(cfg: Config, signin: SocialSchoolsSignin, now: datetime, since: datetime) -> _Read:
    """The events of each of the sign-in's schools from `now` to LOOKAHEAD_DAYS on, and their
    announcements, back to `since` at least. Announcements that can't be read leave the events."""
    password = password_of(signin)
    with Portal() as portal:
        portal.sign_in(signin.username, password)
        schools, kids, events = read_events(portal, signin, cfg, now)
        try:
            messages = [m for s in schools for m in messages_of(posts_since(portal, s, since), s, kids)]
        except SocialSchoolsError as e:
            return _Read(events, [], e)
    return _Read(events, messages)


def collect(cfg: Config, state: State, kid_terms: list[str]) -> Collected:
    """The coming events of each sign-in's schools, to add to the calendar, and the announcements
    posted within the lookback that no earlier run told. Events come again each night, so none is
    marked seen; an announcement is, so it is told once. A sign-in that fails while another works,
    or whose announcements can't be read, is logged, so the Brief says Social Schools was partly
    read; when none works, it wasn't read, with the first one's reason."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=cfg.socialschools.lookback_hours)
    events: list[CalendarEvent] = []
    messages: list[Message] = []
    failed: list[SocialSchoolsError] = []
    several = len(cfg.socialschools.signins) > 1
    for signin in cfg.socialschools.signins:
        try:
            got = read(cfg, signin, now, since)
        except SocialSchoolsError as e:
            failed.append(e)
            if several:
                log.error("%s", sign_in_failed(NAME, str(e), signin.username))
            continue
        if got.messages_failed is not None:
            why = str(got.messages_failed)
            log.error("%s", sign_in_failed(NAME, why, signin.username) if several else why)
        # What is over by this evening is gone; an event of several days that goes on, isn't.
        kept = [e for e in got.events if (e.end if e.all_day and e.end else e.start) >= now - timedelta(hours=6)]
        new = [m for m in got.messages if not state.has_seen_message("socialschools", m.external_id)]
        for m in new:  # one from before the lookback is seen untold, so a wider one never tells it late
            state.mark_message_seen("socialschools", m.external_id)
        new = [m for m in new if m.timestamp >= since]
        log.info("Social Schools: %d events (next %dd), %d new announcements", len(kept), LOOKAHEAD_DAYS, len(new))
        events += kept
        messages += new
    if failed and len(failed) == len(cfg.socialschools.signins):
        raise failed[0]
    return Collected(messages=messages, events=events)


def sent_to_everyone(m: Message) -> bool:
    """A Social Schools post is an announcement to a group or the whole school, never to one
    family, so it is never held back for its words (ADR 0013)."""
    return True
