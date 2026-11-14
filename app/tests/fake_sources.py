"""Each Source's fake, at its transport, where the real thing varies (ADR 0022): Gmail's IMAP
server, the MyClub feed over HTTP, the wilma CLI on Parent Recap's own Node, the Linked
Device's message store and status file, each Eepos school's portal, each city's eVaka, Social
Schools' sign-in and API and each Konnect parent portal over HTTP, and the Local Files folder.
FakeSources, the `sources` fixture, installs the fake of each Source in the registry, so the real
collectors run in every test of the evening run; FAKES is the list it reads.

A fake serves what `sources.tonight[name]` holds when the collector reaches it: tonight's Messages
and events, an exception its transport raises, or a function returning either. A Message is
written the way its transport carries it and read back by the real collector. So a test gives it
only the fields its transport carries, and the id the collector makes of it:
  - Gmail: its X-GM-MSGID, digits only;
  - Wilma: `message:<id>` or `news:<id>`, or `message:<tag>:<id>` once a Wilma is signed in to;
  - WhatsApp: its message id;
  - MyClub: the event's UID;
  - Eepos: a calendar entry as its portal's JSON has it (conftest's eepos_entry), not an event;
  - eVaka: a calendar event as its citizen API's JSON has it (conftest's evaka_event);
  - Social Schools: a post or an event as its API's JSON has it (conftest's socialschools_post and
    socialschools_event);
  - Konnect: a planned time as its Agenda's JSON has it (conftest's konnect_planned), not an event;
  - Local Files: the id local_files.message_id makes of it (conftest's local_message).
A Message older than the Source's lookback is skipped, as the collector skips it.
"""
from __future__ import annotations

import base64
import html
import imaplib
import json
import re
import subprocess
import hashlib
import secrets as tokens
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import icalendar
import pytest
import requests

import fake_node
from family_brief import linked_device, own_node, sources as registry
from family_brief.collectors import gmail, local_files
from family_brief.collectors.base import CalendarEvent, Collected, Message
from family_brief.sources.fi.eepos import collector as eepos
from family_brief.sources.fi.evaka import collector as evaka
from family_brief.sources.fi.myclub import collector as myclub
from family_brief.sources.fi.myclub.config import myclub_links
from family_brief.sources.nl.konnect import collector as konnect
from family_brief.sources.nl.socialschools import collector as socialschools
from fake_system import FakeSecrets

# Where a Gmail Message's url points: Gmail's search for its Message-ID header.
GMAIL_LINK = "https://mail.google.com/mail/u/0/#search/rfc822msgid:"
APP_PASSWORD = "app-password"


def _carries(m: Message, source: str, fields: set[str], metadata: set[str] | None = None) -> None:
    """Fails the test when `m` has a field, or metadata, its Source's transport doesn't carry,
    which the real collector would never give back."""
    assert m.source == source, f"a {m.source} Message among {source}'s"
    given = {f for f in ("sender", "recipient", "subject", "chat_name", "kid_hint", "url")
             if getattr(m, f) is not None}
    assert given <= fields, f"{source} doesn't carry {sorted(given - fields)} ({m.external_id})"
    extra = set(m.metadata) - (metadata or set())
    assert not extra, f"{source} doesn't carry metadata {sorted(extra)} ({m.external_id})"


class SourceFake:
    """What every Source's fake has: tonight's items, what setup left for the Source on the
    family's computer, and the commands it answers."""
    name: str

    def __init__(self, sources: FakeSources) -> None:
        self.sources = sources

    def set_up(self) -> None:
        """Puts in place what the Source's setup leaves, if the test hasn't: a secret, a program,
        a permission. Before each evening run, so that tests of setup, doctor and uninstall
        start without them."""

    def tonight(self) -> list[Any]:
        """What the Source has when it is read: raises the exception it holds, if it does."""
        v = self.sources.tonight.get(self.name, [])
        v = v() if callable(v) else v
        if isinstance(v, BaseException):
            raise v
        return list(v)

    def run(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess | None:
        """The answer to a command this Source's transport is, or None for any other."""
        return None


# ── Gmail: the IMAP server

class GmailFolders:
    """Gmail's folders, as its IMAP server lists them and lets them be selected. All Mail is
    `all_mail`, its name in the account's language ("[Gmail]/&YkBnCZCuTvY-" in Chinese), and
    selecting a folder that isn't there fails, as on Gmail. A search before a folder is selected
    is refused, as imaplib refuses it."""
    all_mail = "[Gmail]/All Mail"
    selected = False

    def list(self, *_a: Any) -> tuple[str, list[bytes]]:
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasChildren \\Noselect) "/" "[Gmail]"',
                      f'(\\All \\HasNoChildren) "/" "{self.all_mail}"'.encode(),
                      b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"']

    def select(self, mailbox: str = "INBOX", readonly: bool = False) -> tuple[str, list[bytes]]:
        self.selected = mailbox.strip('"') in (self.all_mail, "INBOX")
        if self.selected:
            return "OK", [b"1"]
        return "NO", [b"[NONEXISTENT] Unknown Mailbox (Failure)"]

    def searching(self) -> None:
        if not self.selected:
            raise imaplib.IMAP4.error("command SEARCH illegal in state AUTH, only allowed in states SELECTED")


class Gmail(SourceFake):
    """Gmail's IMAP server, which takes the app password setup stored, or only `password` when
    it is set. Every Message on it matches any search, whose X-GM-RAW query is kept in
    `searches`, and its sender is what setup's sender scan reads. `connections` says what each
    next connection does instead of serving tonight's mail as it is: an exception connecting
    raises, or a dict from a Message's id to what fetching its source gives instead (raw bytes, or
    an exception it raises, as a dropped connection does). Its All Mail is named `all_mail`."""
    name = "gmail"
    all_mail = GmailFolders.all_mail

    def __init__(self, sources: FakeSources, mp: pytest.MonkeyPatch) -> None:
        super().__init__(sources)
        self.password: str | None = None
        self.connections: list[BaseException | dict[str, bytes | BaseException]] = []
        self.searches: list[str] = []
        mp.setattr(imaplib, "IMAP4_SSL", self.connect)

    def set_up(self) -> None:
        username = self.sources.config.get("gmail", {}).get("username")
        if username:
            self.sources.secrets.setdefault(gmail.keychain_account(username), APP_PASSWORD)

    def connect(self, *_a: Any, **_k: Any) -> _Imap:
        instead = self.connections.pop(0) if self.connections else {}
        if isinstance(instead, BaseException):
            raise instead
        mail = {str(i + 1).encode(): m for i, m in enumerate(self.tonight())}
        return _Imap(self, mail, instead)


class _Imap(GmailFolders):
    """One connection to the fake Gmail, as imaplib.IMAP4_SSL's methods answer."""

    def __init__(self, server: Gmail, mail: dict[bytes, Message],
                 instead: dict[str, bytes | BaseException]) -> None:
        self.server, self.mail, self.instead = server, mail, instead
        self.all_mail = server.all_mail

    def __enter__(self) -> _Imap:
        return self

    def __exit__(self, *_a: Any) -> None:
        pass

    def login(self, _user: str, password: str) -> tuple[str, list[bytes]]:
        if self.server.password is not None and password != self.server.password:
            raise imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Invalid credentials (Failure)")
        return "OK", [b"LOGIN completed"]

    def search(self, _charset: Any, *criteria: str) -> tuple[str, list[bytes]]:
        self.searching()
        self.server.searches.append(" ".join(criteria))
        return "OK", [b" ".join(self.mail)]

    def fetch(self, imap_id: bytes, what: str) -> tuple[str, list[Any]]:
        if "HEADER.FIELDS (FROM)" in what:  # a batch of ids, as the sender scan asks
            rows: list[Any] = []
            for i in imap_id.split(b","):
                sender = f"From: {self.mail[i].sender}\r\n\r\n".encode()
                rows += [(i + f" (BODY[HEADER.FIELDS (FROM)] {{{len(sender)}}}".encode(), sender), b")"]
            return "OK", rows
        m = self.mail[imap_id]
        if what == "(X-GM-MSGID)":
            return "OK", [imap_id + f" (X-GM-MSGID {m.external_id})".encode()]
        raw = self.instead.get(m.external_id, _rfc822(m))
        if isinstance(raw, BaseException):
            raise raw
        if raw == b"":
            return "OK", [b"garbled response"]  # not the (envelope, bytes) pair
        return "OK", [(imap_id + f" (RFC822 {{{len(raw)}}}".encode(), raw)]

    def logout(self) -> None:
        pass


def _rfc822(m: Message) -> bytes:
    """`m` as the email Gmail keeps: its sender, subject and date in headers, its body as text,
    its link's Message-ID, and a List-Id when it went to a mailing list."""
    _carries(m, "gmail", {"sender", "subject", "url"}, {"mailing_list"})
    assert m.external_id.isdigit(), f"Gmail's ids are X-GM-MSGIDs, digits only: {m.external_id}"
    e = EmailMessage()
    if m.sender is not None:
        e["From"] = m.sender
    if m.subject is not None:
        e["Subject"] = m.subject
    e["Date"] = format_datetime(m.timestamp)
    if m.url is not None:
        assert m.url.startswith(GMAIL_LINK), f"a Gmail link is {GMAIL_LINK}<Message-ID>: {m.url}"
    e["Message-ID"] = f"<{m.url[len(GMAIL_LINK):] if m.url else f'{m.external_id}@mail.gmail.com'}>"
    if m.metadata.get("mailing_list"):
        e["List-Id"] = "<news.kilo.example.fi>"
    e.set_content(m.body)
    return e.as_bytes()


# ── MyClub: each Kid's feed over HTTP

class MyClub(SourceFake):
    """MyClub's server, which gives at a Kid's subscription link (in the config when it is read)
    the events whose `kid` is that Kid, as iCal. Each event's title is the feed's SUMMARY behind
    the ⚽ the collector puts before it. A link in `feeds`, one of a Kid's clubs (#810), gives
    instead the events listed for it, of tonight's. `failures` lists what each next download does
    instead: an exception it raises, or an HTTP status the server answers with. `gets` is each
    link downloaded. Any other address goes to whatever requests.get was before."""
    name = "myclub"

    def __init__(self, sources: FakeSources, mp: pytest.MonkeyPatch) -> None:
        super().__init__(sources)
        self.failures: list[BaseException | int] = []
        self.gets: list[str] = []
        self.feeds: dict[str, list[str]] = {}  # a link: the external ids of its events
        self._other = requests.get
        mp.setattr(myclub.requests, "get", self.get)

    def get(self, url: str, *a: Any, **k: Any) -> Any:
        # A webcal:// link is downloaded over https://.
        def https(link: str) -> str:
            return re.sub(r"^webcal://", "https://", link)
        kid = next((kid["name"] for kid in self.sources.config.get("kids", [])
                    if any(url == https(link)
                           for link in myclub_links(kid.get("myclub_ical_url")))), None)
        if kid is None:
            return self._other(url, *a, **k)
        feed = next((ids for link, ids in self.feeds.items() if url == https(link)), None)
        self.gets.append(url)
        failure = self.failures.pop(0) if self.failures else None
        if isinstance(failure, BaseException):
            raise failure
        if failure is not None:
            return _Feed(failure, "")
        return _Feed(200, _ics([e for e in self.tonight() if e.kid == kid
                                and (feed is None or e.external_id in feed)]))


class _Feed:
    def __init__(self, status: int, text: str) -> None:
        self.status_code, self.text = status, text


def _ics(events: list[CalendarEvent]) -> str:
    cal = icalendar.Calendar()
    cal.add("prodid", "-//MyClub//fake//EN")
    cal.add("version", "2.0")
    for e in events:
        assert e.source == "myclub" and not e.all_day, e
        assert e.title.startswith("⚽ "), f"the collector puts ⚽ before a MyClub title: {e.title}"
        ev = icalendar.Event()
        ev.add("uid", e.external_id)
        ev.add("summary", e.title[2:])
        ev.add("dtstart", e.start.astimezone(timezone.utc))
        if e.end:
            ev.add("dtend", e.end.astimezone(timezone.utc))
        if e.location:
            ev.add("location", e.location)
        if e.description:
            ev.add("description", e.description)
        cal.add_component(ev)
    return cal.to_ical().decode()


# ── Wilma: the wilma CLI on Parent Recap's own Node

STUDENT_NUMBER = "7731905"  # a Kid's in Wilma, when a Message doesn't give one


class Wilma(SourceFake):
    """The wilma CLI (2.x) installed on Parent Recap's own Node, printing tonight's Messages as
    `messages list`, `news list` and their `read`s print them: each Message under its Kid (its
    kid_hint), whose student number is its metadata's, and with as many addressees as its
    metadata's addressee_count. `answers` maps the start of a command (without node, the CLI and
    --json) to what the CLI prints instead: JSON, an exit status and JSON, an exception it
    raises, or a function of the command's arguments giving one of those, which may read the CLI's
    config the command was given in place of its own (`config`). `ran` is each command run,
    `read` the config each read, as it was then (None: the CLI's own), and `envs` the environment
    each ran in. Any other command lists nothing."""
    name = "wilma"

    def __init__(self, sources: FakeSources, mp: pytest.MonkeyPatch) -> None:
        super().__init__(sources)
        self.answers: dict[tuple[str, ...], Any] = {}
        self.ran: list[list[str]] = []
        self.config: str | None = None  # the running command's WILMAI_CONFIG_PATH, if any
        self.read: list[tuple[str, dict] | None] = []  # each command's config: path, contents
        self.envs: list[dict[str, str]] = []

    def set_up(self) -> None:
        if own_node.wilma() is None:
            fake_node.pinned_node(self.sources.home)
            fake_node.install_wilma(self.sources.home, "#!/bin/sh\n")

    def run(self, cmd: list[str], **_k: Any) -> subprocess.CompletedProcess | None:
        if not fake_node.is_wilma(cmd):
            return None
        args = cmd[2:-1]
        self.ran.append(args)
        self.envs.append(dict(_k.get("env") or {}))
        self.config = (_k.get("env") or {}).get("WILMAI_CONFIG_PATH")
        self.read.append((self.config, json.loads(Path(self.config).read_text()))
                         if self.config else None)
        answer = next((v for start, v in self.answers.items() if tuple(args[:len(start)]) == start),
                      self.prints)
        if callable(answer):
            answer = answer(args)
        if isinstance(answer, BaseException):
            raise answer
        status, out = answer if isinstance(answer, tuple) else (0, answer)
        return subprocess.CompletedProcess(cmd, status, json.dumps(out), "")

    def prints(self, args: list[str]) -> Any:
        """What the CLI prints for `args` from tonight's Messages."""
        kind = {"messages": "message", "news": "news"}.get(args[0])
        if kind is None or len(args) < 2:
            return {"students": []}
        mine = [m for m in self.tonight() if _wilma_kind(m) == kind]
        if args[1] == "list":
            students: dict[tuple, list[dict]] = {}
            for m in mine:
                students.setdefault(_student(m), []).append(_listed(m))
            return {"students": [{"student": {"studentNumber": number, "name": name}, args[0]: items}
                                 for (number, name), items in students.items()]}
        m = next((m for m in mine if str(_raw_id(m)) == args[2]), None) if args[1] == "read" else None
        if m is not None:
            number, name = _student(m)
            count = m.metadata.get("addressee_count")
            item = {**_listed(m), "content": m.body,
                    "recipients": [f"Huoltaja{n}" for n in range(count)] if count else None}
            return {"student": {"studentNumber": number, "name": name}, kind: item}
        return {"students": []}


def _wilma_kind(m: Message) -> str:
    """Whether `m` is a Wilma message or news item, failing the test when the CLI couldn't print
    it as given."""
    _carries(m, "wilma", {"sender", "subject", "kid_hint", "chat_name"}, {"student_number", "addressee_count"})
    kind = m.external_id.split(":", 1)[0]
    assert kind in ("message", "news") and re.fullmatch(r"[a-z]+:([0-9a-f]{8}:)?\d+", m.external_id), \
        f"Wilma's ids are message:<id> or news:<id>: {m.external_id}"
    assert m.chat_name in (None, kind), f"Wilma's chat_name is its kind, {kind}: {m.chat_name}"
    return kind


def _raw_id(m: Message) -> int:
    return int(m.external_id.rsplit(":", 1)[1])


def _student(m: Message) -> tuple[str, str | None]:
    return str(m.metadata.get("student_number") or STUDENT_NUMBER), m.kid_hint


def _listed(m: Message) -> dict[str, Any]:
    return {"wilmaId": _raw_id(m), "subject": m.subject or "", "sentAt": m.timestamp.isoformat(),
            "senderName": m.sender or "", "folder": "inbox", "unread": True, "replyCount": 0}


# ── WhatsApp: the Linked Device's message store and status file

class WhatsApp(SourceFake):
    """The Linked Device's always-running job, as what it leaves in its folder (ADR 0026): the
    status file, paired and connected unless `paired` is False, and the message store, to which
    tonight's Messages are appended as follow keeps them, each time the collector reads it. A
    Message's id is its WhatsApp message id, its chat_name its group's subject, and its group the
    picked group of that name in the config, or the id `group_ids` gives the name, as for a group
    renamed since it was picked. Its sender is the member's name, or "me" for one the family sent,
    and its kid_hint the picked group's Kid, as the collector gives it. An exception tonight is
    raised reading the store."""
    name = "whatsapp"

    def __init__(self, sources: FakeSources, mp: pytest.MonkeyPatch) -> None:
        super().__init__(sources)
        self.paired = True
        self.group_ids: dict[str, str] = {}
        read = linked_device.records

        def records() -> list[dict[str, Any]]:
            self.keep(*(self.record(m) for m in self.tonight()))
            return read()
        mp.setattr(linked_device, "records", records)

    def set_up(self) -> None:
        picked = (self.sources.config.get("whatsapp") or {}).get("chats")
        if self.paired and picked and not linked_device.status_file().exists():
            now = datetime.now(timezone.utc).isoformat()
            self.status(paired=True, connected_since=now, last_connected=now, link_ended_at=None)

    def status(self, **link: Any) -> None:
        """Writes the status file as follow does."""
        self._private(linked_device.status_file()).write_text(json.dumps(link) + "\n")

    def keep(self, *records: dict[str, Any]) -> None:
        """Appends `records` to the store as follow does."""
        store = self._private(linked_device.store())
        with store.open("a", encoding="utf-8") as f:
            f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in records)

    def record(self, m: Message) -> dict[str, Any]:
        """`m` as follow keeps it."""
        _carries(m, "whatsapp", {"sender", "chat_name", "kid_hint"}, {"from_me"})
        assert m.chat_name, f"a WhatsApp Message is in a group: {m.external_id}"
        picked = {c["name"]: c.get("id") for c in self.sources.config.get("whatsapp", {}).get("chats", [])}
        group = self.group_ids.get(m.chat_name) or picked.get(m.chat_name) or f"{m.chat_name}@g.us"
        from_me = m.sender == "me"
        return {"id": m.external_id, "group_id": group, "group_subject": m.chat_name,
                "sender": None if from_me else m.sender, "timestamp": m.timestamp.timestamp(),
                "text": m.body, "from_me": from_me}

    @staticmethod
    def _private(path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.touch(mode=0o600)
        return path


# ── Eepos: each school's guardian portal over HTTP

EEPOS_PASSWORD = "eepos-password"


class EeposSchool:
    """One school's portal: its guardians' passwords by username, and the students it lists to each
    of them, as the portal lists them, the whole person with address and all."""

    def __init__(self) -> None:
        self.accounts: dict[str, str] = {}
        self.students: list[dict[str, Any]] = []


class Eepos(SourceFake):
    """Each school's guardian portal at <school>.eepos.fi (`schools`, by the part before
    .eepos.fi), as its JSON API answers, every answer behind Angular's `)]}',` line. A session
    signs in with POST auth/login, a JSON body of the username and the password, which the
    school's `accounts` must have, and gets a cookie; with it, guardian/students lists the
    school's `students`, and calendar-events/all-by-date-range tonight's entries at that school
    from its startDate to its endDate, each sent as JSON ("2026-09-27", quotes and all), as the
    portal's script sends them. Without it, or with another school's, the portal answers 401.

    Tonight's items are calendar entries as the portal gives them (conftest's eepos_entry), each
    with the `school` it is at, which the portal's own entries don't carry and the fake leaves out.
    `answers` maps a school and a path to what the portal answers there instead: an HTTP status
    and the text, or an exception the request raises. `requests` records each request: its
    method, school, path, JSON body and query. Any other address goes to requests as before."""
    name = "eepos"

    def __init__(self, sources: FakeSources, mp: pytest.MonkeyPatch) -> None:
        super().__init__(sources)
        self.schools: dict[str, EeposSchool] = {}
        self.answers: dict[tuple[str, str], tuple[int, str] | BaseException] = {}
        self.requests: list[dict[str, Any]] = []
        self._cookies: dict[str, tuple[str, str]] = {}  # a session cookie: its school and username
        other = requests.Session.request

        def request(session: requests.Session, method: str, url: str, **k: Any) -> requests.Response:
            host = urlsplit(url).hostname or ""
            if not host.endswith("." + eepos.DOMAIN):
                return other(session, method, url, **k)
            return self.answer(session, method, url, **k)
        mp.setattr(requests.Session, "request", request)

    def school(self, school: str) -> EeposSchool:
        return self.schools.setdefault(school, EeposSchool())

    def set_up(self) -> None:
        for signin in (self.sources.config.get("eepos") or {}).get("signins") or []:
            school = eepos.school_of(signin["school"]) or signin["school"]
            self.school(school).accounts.setdefault(signin["username"], EEPOS_PASSWORD)
            self.sources.secrets.setdefault(eepos.secret_name(school, signin["username"]), EEPOS_PASSWORD)

    def answer(self, session: requests.Session, method: str, url: str, **k: Any) -> requests.Response:
        parts = urlsplit(requests.Request(method, url, params=k.get("params")).prepare().url)
        school = (parts.hostname or "").removesuffix("." + eepos.DOMAIN)
        assert parts.path.startswith("/portal/api/"), f"the guardian portal's API is under /portal/api/: {url}"
        path = parts.path.removeprefix("/portal/api/")
        query = {key: json.loads(v) for key, [v] in parse_qs(parts.query).items()}
        self.requests.append({"method": method, "school": school, "path": path, "body": k.get("json"),
                              "query": query, "headers": dict(session.headers)})
        instead = self.answers.get((school, path))
        if isinstance(instead, BaseException):
            raise instead
        if instead is not None:
            return _response(*instead)
        portal = self.schools.get(school)
        if portal is None:
            return _response(404, "<html>Not found</html>")
        if (method, path) == ("POST", "auth/login"):
            body = k.get("json") or {}
            if portal.accounts.get(body.get("username")) != body.get("password") or not body.get("password"):
                return _response(400, json.dumps({"error": "loginFailed"}))  # as emo.eepos.fi does
            cookie = tokens.token_hex(8)
            self._cookies[cookie] = (school, body["username"])
            session.cookies.set("eepos_session", cookie, domain=parts.hostname)
            return _portal({"success": True})
        signed_in = self._cookies.get(session.cookies.get("eepos_session", domain=parts.hostname) or "")
        if signed_in is None or signed_in[0] != school:
            return _response(401, json.dumps({"error": "noauth"}))
        if (method, path) == ("GET", "auth/status"):
            return _portal({"authenticated": True, "type": 3})
        if (method, path) == ("GET", "guardian/students"):
            return _portal(portal.students)
        if (method, path) == ("GET", "calendar-events/all-by-date-range"):
            first, last = date.fromisoformat(query["startDate"]), date.fromisoformat(query["endDate"])
            return _portal([{k: v for k, v in e.items() if k != "school"} for e in self.tonight()
                            if e["school"] == school and first <= date.fromisoformat(e["date"]) <= last])
        return _response(404, "<html>Not found</html>")


def _portal(data: Any) -> requests.Response:
    """A JSON answer of the portal, behind Angular's guard."""
    return _response(200, ")]}',\n" + json.dumps(data, ensure_ascii=False))


def _response(status: int, text: str) -> requests.Response:
    r = requests.Response()
    r.status_code, r._content, r.encoding = status, text.encode(), "utf-8"
    return r


# ── eVaka: each city's citizen API over HTTP

EVAKA_PASSWORD = "evaka-password"


class EvakaCity:
    """One city's eVaka: its guardians' passwords by email, the children it lists to each of them,
    whole, and its reservation calendar: the public `holidays` (ISO days), the children `open_on_holidays`
    (shift care), those on a `fixed_schedule` who make no reservations, and the first day still open
    for reservations, `reservable_from`, by default the Monday eVaka's own rule gives with the
    city's hours in evaka.CITIES, as of the clock. `threads` are the guardian's received message
    threads (conftest's evaka_thread and evaka_sensitive_thread), which `messages/received` gives
    ten a page, the latest first, as eVaka does."""

    def __init__(self, city: str) -> None:
        self.city = city
        self.accounts: dict[str, str] = {}
        self.children: list[dict[str, Any]] = []
        self.holidays: set[str] = set()
        self.open_on_holidays: set[str] = set()
        self.fixed_schedule: set[str] = set()
        self.reservable_from: str | None = None
        self.threads: list[dict[str, Any]] = []

    def received(self, page: int) -> dict[str, Any]:
        """A page of the received threads as eVaka's getReceivedMessages gives it: ten a page,
        by the time of each one's last message, the latest first."""
        def last(t: dict[str, Any]) -> datetime:
            at = t.get("lastMessageSentAt") or max(m["sentAt"] for m in t["messages"])
            return datetime.fromisoformat(at)
        threads = sorted(self.threads, key=last, reverse=True)
        return {"data": threads[(page - 1) * 10:page * 10], "total": len(threads),
                "pages": (len(threads) + 9) // 10}

    def first_reservable_monday(self) -> date:
        """eVaka's getNextReservableMonday: the first Monday after now plus the city's hours."""
        if self.reservable_from:
            return date.fromisoformat(self.reservable_from)
        now = datetime.now(evaka.EVAKA_ZONE)
        hours = evaka.deadline_hours(self.city) or 7 * 24
        monday = now.date() + timedelta(days=7 - now.weekday())
        while not monday > (now + timedelta(hours=hours)).date():
            monday += timedelta(weeks=1)
        return monday

    def day(self, on: date) -> dict[str, Any]:
        """One day of the reservation calendar: the children whose unit is open, each with its
        opening hours, as eVaka leaves out a child whose unit is closed that day."""
        holiday = on.isoformat() in self.holidays
        children = [] if on.weekday() >= 5 else [
            {"childId": c["id"], "absence": None, "attendances": [], "holidayPeriodEffect": None,
             "reservableTimeRange": {"type": "NORMAL", "range": {"start": "06:30", "end": "17:30"}},
             "reservations": [], "shiftCare": c["id"] in self.open_on_holidays, "usedService": None,
             "scheduleType": "FIXED_SCHEDULE" if c["id"] in self.fixed_schedule else "RESERVATION_REQUIRED"}
            for c in self.children if not holiday or c["id"] in self.open_on_holidays]
        return {"date": on.isoformat(), "holiday": holiday, "children": children}


class Evaka(SourceFake):
    """Each city's eVaka at its own host (`cities`, by the city's name in the config), as its
    citizen API answers under /api/citizen/. A session signs in with POST auth/weak-login, a JSON
    body of the email and the password, which the city's `accounts` must have, and the header
    `x-evaka-csrf`; a wrong one is answered 403 "Forbidden", as apigw does. A sign-in gets the
    session cookie and a device cookie for the guardian. With the session, `children` lists the
    city's children, `messages/received` a page of the city's message threads, `calendar-events` tonight's events there that overlap its start to its end,
    and `reservations` the city's reservation calendar from its from to its to. Without it, or
    with another city's, eVaka answers 401.

    Tonight's items are calendar events as eVaka gives them (conftest's evaka_event), each with
    the `city` it is in, which the fake leaves out. `answers` maps a city and a path to what eVaka
    answers there instead: an HTTP status and the text, or an exception the request raises.
    `requests` records each request: its method, city, path, JSON body, query, headers and the
    device cookies it sent. Any other address goes to requests as before."""
    name = "evaka"

    def __init__(self, sources: FakeSources, mp: pytest.MonkeyPatch) -> None:
        super().__init__(sources)
        self.cities: dict[str, EvakaCity] = {}
        self.answers: dict[tuple[str, str], tuple[int, str] | BaseException] = {}
        self.requests: list[dict[str, Any]] = []
        self._sessions: dict[str, tuple[str, str]] = {}  # a session cookie: its city and email
        other = requests.Session.request

        def request(session: requests.Session, method: str, url: str, **k: Any) -> requests.Response:
            host = urlsplit(url).hostname or ""
            city = next((c for c in self.cities if evaka.host_of(c) == host), None)
            if city is None and evaka.city_of(host) not in evaka.CITIES:
                return other(session, method, url, **k)
            return self.answer(session, city or evaka.city_of(host), method, url, **k)
        mp.setattr(requests.Session, "request", request)

    def city(self, city: str) -> EvakaCity:
        name = evaka.city_of(city) or city
        return self.cities.setdefault(name, EvakaCity(name))

    def set_up(self) -> None:
        for signin in (self.sources.config.get("evaka") or {}).get("signins") or []:
            city = evaka.city_of(signin["city"]) or signin["city"]
            self.city(city).accounts.setdefault(signin["username"].lower(), EVAKA_PASSWORD)
            self.sources.secrets.setdefault(evaka.secret_name(city, signin["username"]), EVAKA_PASSWORD)

    def answer(self, session: requests.Session, city: str, method: str, url: str, **k: Any) -> requests.Response:
        parts = urlsplit(requests.Request(method, url, params=k.get("params")).prepare().url)
        host = parts.hostname or ""
        assert parts.path.startswith("/api/citizen/"), f"the citizen API is under /api/citizen/: {url}"
        path = parts.path.removeprefix("/api/citizen/")
        devices = {c.name: c.value for c in session.cookies
                   if c.name.startswith(evaka.DEVICE_COOKIE) and c.domain == host}
        self.requests.append({"method": method, "city": city, "path": path, "body": k.get("json"),
                              "query": {key: v for key, [v] in parse_qs(parts.query).items()},
                              "headers": dict(session.headers), "devices": devices})
        instead = self.answers.get((city, path))
        if isinstance(instead, BaseException):
            raise instead
        if instead is not None:
            return _response(*instead)
        portal = self.cities.get(city)
        if portal is None:
            return _response(404, "Not Found")
        if method != "GET" and session.headers.get("x-evaka-csrf") != "1":
            return _response(403, "Forbidden")  # apigw's InvalidAntiCsrfToken
        if (method, path) == ("POST", evaka.SIGN_IN):
            body = k.get("json") or {}
            username = str(body.get("username") or "").lower()  # as apigw takes it
            if not body.get("password") or portal.accounts.get(username) != body.get("password"):
                return _response(*evaka.WRONG_CREDENTIALS)
            cookie = tokens.token_hex(8)
            self._sessions[cookie] = (city, username)
            session.cookies.set(evaka.SESSION_COOKIE, cookie, domain=host)
            device = hashlib.sha256(username.encode()).hexdigest()
            session.cookies.set(evaka.DEVICE_COOKIE + device, f"s:{device}.signature", domain=host,
                                path="/", secure=True)
            return _response(200, "OK")
        signed_in = self._sessions.get(session.cookies.get(evaka.SESSION_COOKIE, domain=host) or "")
        if signed_in is None or signed_in[0] != city:
            return _response(401, "Unauthorized")
        query = {key: v for key, [v] in parse_qs(parts.query).items()}
        if (method, path) == ("GET", "children"):
            return _response(200, json.dumps(portal.children, ensure_ascii=False))
        if (method, path) == ("GET", "messages/received"):
            return _response(200, json.dumps(portal.received(int(query["page"])), ensure_ascii=False))
        if (method, path) == ("GET", "calendar-events"):
            first, last = date.fromisoformat(query["start"]), date.fromisoformat(query["end"])
            return _response(200, json.dumps(
                [{k: v for k, v in e.items() if k != "city"} for e in self.tonight()
                 if e["city"] == city and date.fromisoformat(e["period"]["start"]) <= last
                 and date.fromisoformat(e["period"]["end"]) >= first], ensure_ascii=False))
        if (method, path) == ("GET", "reservations"):
            first, last = date.fromisoformat(query["from"]), date.fromisoformat(query["to"])
            monday = portal.first_reservable_monday()
            days = [portal.day(first + timedelta(days=n)) for n in range((last - first).days + 1)]
            children = [{"id": c["id"], "firstName": c["firstName"], "lastName": c["lastName"],
                         "preferredName": c["preferredName"], "duplicateOf": None, "imageId": None,
                         "monthSummaries": [], "upcomingPlacementStartDate": None,
                         "upcomingPlacementType": None, "upcomingPlacementUnitName": None}
                        for c in portal.children]
            return _response(200, json.dumps({
                "children": children, "days": days,
                "reservableRange": {"start": monday.isoformat(),
                                    "end": date(monday.year + (monday.month >= 7), 8, 31).isoformat()}}))
        return _response(404, "Not Found")


# ── Social Schools: its sign-in at login.socialschools.eu and its API at api.socialschools.eu

SOCIALSCHOOLS_PASSWORD = "socialschools-password"
_LOGIN_PAGE = """<!DOCTYPE html><html><head><title>Social Schools</title></head><body>
<div class="login-page">{error}
<form method="post" action="/Account/Login?ReturnUrl={return_url_in_action}">
<input type="email" name="Username" value="" autofocus>
<input type="password" name="Password">
<input type="hidden" name="ReturnUrl" value="{return_url}">
<button type="submit" name="button" value="login">Inloggen</button>
<input name="__RequestVerificationToken" type="hidden" value="{token}">
</form>
<a href="/Account/ExternalLogin?scheme=Google&amp;returnUrl={return_url_in_action}">Google</a>
<a href="/Account/ExternalLogin?scheme=Microsoft&amp;returnUrl={return_url_in_action}">Microsoft</a>
<a href="/Account/ExternalLogin?scheme=Apple&amp;returnUrl={return_url_in_action}">Apple</a>
</div></body></html>"""


class SocialSchoolsSchool:
    """One school on Social Schools: its name, and the children it lists to the parent under their
    parent role there (conftest's socialschools_child), with their groups."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.children: list[dict[str, Any]] = []


class SocialSchools(SourceFake):
    """Social Schools: its identity provider at login.socialschools.eu and its API at
    api.socialschools.eu/api/v1/, as the iOS app meets them.

    The sign-in is OpenID Connect with PKCE: GET connect/authorize, for the iOS app's client only,
    redirects to the login form at Account/Login, whose page carries a request verification token
    and the ReturnUrl; POSTing the form with an email and the password `accounts` has for it, and
    that token, redirects to the ReturnUrl, connect/authorize/callback, which redirects to the app's
    own address with a one-time code; a wrong password shows the form again. `ways` says how an
    account signs in instead of with its password: "two-step" asks for a code on another page,
    "external" sends the browser on to Google. POST connect/token takes the code with its verifier
    and gives an access token, and no refresh token unless offline_access was asked for.

    With the token, the API's GET useraccounts/current lists the parent's role at each of `schools`
    (by id) with its `children`, and a role of another kind; GET communityposts, for the school in
    the Schoolid header, a page of tonight's posts there (conftest's socialschools_post), the
    latest first; GET events that school's events of tonight (conftest's socialschools_event)
    between its from and until. Tonight's items carry the `school` they are at, which the fake
    leaves out. `answers` maps a host's path to what it answers there instead: an HTTP status and
    the text, or an exception the request raises. `requests` records each request: its method,
    host, path, query, form, and its headers but the token. Any other address goes to requests as
    before."""
    name = "socialschools"

    def __init__(self, sources: FakeSources, mp: pytest.MonkeyPatch) -> None:
        super().__init__(sources)
        self.accounts: dict[str, str] = {}
        self.ways: dict[str, str] = {}
        self.schools: dict[int, SocialSchoolsSchool] = {}
        self.answers: dict[str, tuple[int, str] | BaseException] = {}
        self.requests: list[dict[str, Any]] = []
        self.tokens: dict[str, str] = {}  # an access token: its email
        self.refresh_tokens: list[str] = []  # each one it gave, which the Source never keeps
        self._codes: dict[str, tuple[str, str, str]] = {}  # a code: its email, challenge and scope
        self._signed_in: dict[str, str] = {}  # the identity provider's cookie: its email
        self._forms: set[str] = set()  # the request verification tokens of the forms it showed
        other = requests.Session.request

        def request(session: requests.Session, method: str, url: str, **k: Any) -> requests.Response:
            host = urlsplit(url).hostname or ""
            if host not in (socialschools.LOGIN_HOST, socialschools.API_HOST):
                return other(session, method, url, **k)
            assert k.get("allow_redirects") is False, "every redirect is followed by the Source itself"
            return self.answer(session, method, url, **k)
        mp.setattr(requests.Session, "request", request)

    def school(self, school_id: int, name: str = "Basisschool De Kilo") -> SocialSchoolsSchool:
        return self.schools.setdefault(school_id, SocialSchoolsSchool(name))

    def set_up(self) -> None:
        for signin in (self.sources.config.get("socialschools") or {}).get("signins") or []:
            self.accounts.setdefault(signin["username"].lower(), SOCIALSCHOOLS_PASSWORD)
            self.sources.secrets.setdefault(socialschools.secret_name(signin["username"]),
                                            SOCIALSCHOOLS_PASSWORD)

    def answer(self, session: requests.Session, method: str, url: str, **k: Any) -> requests.Response:
        parts = urlsplit(requests.Request(method, url, params=k.get("params")).prepare().url)
        host, path = parts.hostname or "", parts.path
        query = {key: v for key, [v] in parse_qs(parts.query).items()}
        headers = {**session.headers, **(k.get("headers") or {})}
        self.requests.append({"method": method, "host": host, "path": path, "query": query,
                              "form": k.get("data"), "headers": {h: v for h, v in headers.items()
                                                                 if h != "Authorization"}})
        instead = self.answers.get(path if host == socialschools.LOGIN_HOST else path.removeprefix("/api/v1/"))
        if isinstance(instead, BaseException):
            raise instead
        if instead is not None:
            return _response(*instead)
        if host == socialschools.LOGIN_HOST:
            return self._identity(session, method, path, query, k.get("data") or {})
        return self._api(method, path.removeprefix("/api/v1/"), query, headers)

    # ── The identity provider

    def _identity(self, session: requests.Session, method: str, path: str, query: dict[str, str],
                  form: dict[str, str]) -> requests.Response:
        host = socialschools.LOGIN_HOST
        if (method, path) == ("GET", socialschools.AUTHORIZE):
            if query.get("client_id") != socialschools.CLIENT_ID or query.get("redirect_uri") != \
                    socialschools.REDIRECT_URI or query.get("code_challenge_method") != "S256":
                return _response(400, "<html>invalid_request</html>")
            back = socialschools.CALLBACK + "?" + parts_of(query)
            return _redirect(f"{socialschools.LOGIN_FORM}?ReturnUrl={quote(back, safe='')}")
        if (method, path) == ("GET", socialschools.LOGIN_FORM):
            return self._login_page(session, query["ReturnUrl"])
        if (method, path) == ("POST", socialschools.LOGIN_FORM):
            token = form.get("__RequestVerificationToken")
            if token not in self._forms or session.cookies.get(".AspNetCore.Antiforgery", domain=host) != token:
                return _response(400, "<html>Bad Request</html>")
            email = str(form.get("Username") or "").strip().lower()
            way = self.ways.get(email)
            if way == "external":
                return _redirect("https://accounts.google.com/o/oauth2/v2/auth?client_id=socialschools")
            if not form.get("Password") or self.accounts.get(email) != form.get("Password"):
                return self._login_page(session, form["ReturnUrl"], error="Ongeldige gebruikersnaam of wachtwoord")
            if way == "two-step":
                return _redirect("/Account/LoginWith2fa?ReturnUrl=" + quote(form["ReturnUrl"], safe=""))
            cookie = tokens.token_hex(8)
            self._signed_in[cookie] = email
            session.cookies.set("idsrv", cookie, domain=host)
            return _redirect(form["ReturnUrl"])
        if (method, path) == ("GET", "/Account/LoginWith2fa"):
            return _response(200, "<html><body><form method='post'><input name='TwoFactorCode'></form></body></html>")
        if (method, path) == ("GET", socialschools.CALLBACK):
            email = self._signed_in.get(session.cookies.get("idsrv", domain=host) or "")
            if email is None:
                return _redirect(f"{socialschools.LOGIN_FORM}?ReturnUrl=" + quote(path + "?" + parts_of(query), safe=""))
            code = tokens.token_hex(16)
            self._codes[code] = (email, query["code_challenge"], query["scope"])
            return _redirect(f"{query['redirect_uri']}?" + urlencode({
                "code": code, "scope": query["scope"], "state": query["state"],
                "session_state": tokens.token_hex(8), "iss": socialschools.ISSUER}))
        if (method, path) == ("POST", socialschools.TOKEN):
            got = self._codes.pop(form.get("code") or "", None)
            verifier = (form.get("code_verifier") or "").encode()
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier).digest()).rstrip(b"=").decode()
            if got is None or got[1] != challenge or form.get("client_id") != socialschools.CLIENT_ID \
                    or form.get("redirect_uri") != socialschools.REDIRECT_URI \
                    or form.get("grant_type") != "authorization_code":
                return _response(400, json.dumps({"error": "invalid_grant"}))
            access = tokens.token_urlsafe(24)
            self.tokens[access] = got[0]
            answer = {"access_token": access, "expires_in": 3600, "token_type": "Bearer", "scope": got[2]}
            if "offline_access" in got[2].split():
                answer["refresh_token"] = tokens.token_urlsafe(24)
                self.refresh_tokens.append(answer["refresh_token"])
            return _response(200, json.dumps(answer))
        return _response(404, "<html>Not found</html>")

    def _login_page(self, session: requests.Session, return_url: str, error: str = "") -> requests.Response:
        token = tokens.token_urlsafe(16)
        self._forms.add(token)
        session.cookies.set(".AspNetCore.Antiforgery", token, domain=socialschools.LOGIN_HOST)
        page = _LOGIN_PAGE.format(error=f'<div class="alert">{error}</div>' if error else "",
                                  return_url=html.escape(return_url),
                                  return_url_in_action=html.escape(quote(return_url, safe="")), token=token)
        return _response(200, page)

    # ── The API

    def _api(self, method: str, path: str, query: dict[str, str], headers: dict[str, Any]) -> requests.Response:
        email = self.tokens.get(str(headers.get("Authorization", "")).removeprefix("Bearer "))
        if email is None:
            return _response(401, "")
        if method != "GET":
            return _response(405, "")
        if path == "useraccounts/current":
            roles = [{"id": 500 + school_id, "type": 1, "school": {"id": school_id, "displayName": school.name},
                      "connections": school.children} for school_id, school in self.schools.items()]
            roles.append({"id": 599, "type": 2, "school": {"id": 99, "displayName": "Werkgever"}})
            return _response(200, json.dumps({"userProfileId": 4242, "displayName": "Ouder Testpersoon",
                                              "email": email, "roles": roles}, ensure_ascii=False))
        school = int(headers.get("Schoolid") or 0)
        if school not in self.schools or headers.get("Roletypeid") != "1":
            return _response(403, "")
        here = [{k: v for k, v in item.items() if k != "school"} for item in self.tonight()
                if item["school"] == school]
        if path == "communityposts":
            posts = sorted((p for p in here if "created" in p), key=lambda p: p["created"], reverse=True)
            offset, limit = int(query["offset"]), int(query["limit"])
            return _response(200, json.dumps({"values": posts[offset:offset + limit], "totalCount": len(posts),
                                              "timeStamp": "2026-09-27T15:00:00Z"}, ensure_ascii=False))
        if path == "events":
            first, last = datetime.fromisoformat(query["from"]), datetime.fromisoformat(query["until"])
            return _response(200, json.dumps(
                [e for e in here if "start" in e and datetime.fromisoformat(e["start"].replace("Z", "+00:00")) <= last
                 and datetime.fromisoformat((e.get("end") or e["start"]).replace("Z", "+00:00")) >= first],
                ensure_ascii=False))
        return _response(404, "")


def parts_of(query: dict[str, str]) -> str:
    return urlencode(query)


def _redirect(location: str) -> requests.Response:
    r = _response(302, "")
    r.headers["Location"] = location
    return r


# ── Konnect: each provider's parent portal over HTTP

KONNECT_PASSWORD = "konnect-password"
# What the portal's own app may see, as `parent/authorizations` lists it for a parent of a BSO.
KONNECT_AUTHORIZATIONS = ["WEB_PHOTOS_RECENT", "WEB_JOURNAL_PARENT_VIEW", "WEB_PARENT_LOGBOOK",
                          "WEB_HTML_NEWS_VIEW", "WEB_PARENT_BOOKING_OVERVIEW",
                          "WEB_ONLY_HTML_NEWSLETTER_ARCHIVE"]


class KonnectPortal:
    """One provider's parent portal: its parents' passwords by username, those whose password it
    wants changed first (`must_reset`), the children it lists to each of them, whole, what the
    account may see (`authorizations`), and the Messages page's three lists: the `conversations`
    (conftest's konnect_conversation), the `news` (konnect_news) and the `newsletters`
    (konnect_newsletter), each listed when its time is in the range asked for."""

    def __init__(self) -> None:
        self.accounts: dict[str, str] = {}
        self.must_reset: set[str] = set()
        self.children: list[dict[str, Any]] = []
        self.authorizations: list[str] = list(KONNECT_AUTHORIZATIONS)
        self.conversations: list[dict[str, Any]] = []
        self.news: list[dict[str, Any]] = []
        self.newsletters: list[dict[str, Any]] = []


def _amsterdam_day(millis: int) -> date:
    return datetime.fromtimestamp(millis / 1000, konnect.PORTAL_ZONE).date()


class Konnect(SourceFake):
    """Each provider's parent portal at <portal>.ouderportaal.nl (`portals`, by the part before
    .ouderportaal.nl), as its JSON answers. A parent signs in with PUT auth-api/login, a JSON body
    of the username, the password, which the portal's `accounts` must have, and `childMinder:
    false`, and gets a token; a wrong one is answered 401. Every read under restservices-parent/
    needs the token as a Bearer, else 401, and a browser's User-Agent, else 403, as some of the
    portal's addresses check it. `children/`, `parent/authorizations`, `htmlnews/view` and
    `newsletter` answer in the portal's `{result, payload, messages}` envelope, `logbook/overview`
    with a bare list, as the portal does, and `calendar/<from>/until/<to>` with the Agenda's days.

    Tonight's items are planned times as the Agenda gives them (conftest's konnect_planned), each
    with the `portal` and the child's id, which the fake puts each in its day. `answers` maps a
    portal and a path to what the portal answers there instead: an HTTP status and the text, or an
    exception the request raises. `requests` records each request: its method, portal, path,
    query, JSON body and headers. Any other address goes to requests as before."""
    name = "konnect"

    def __init__(self, sources: FakeSources, mp: pytest.MonkeyPatch) -> None:
        super().__init__(sources)
        self.portals: dict[str, KonnectPortal] = {}
        self.answers: dict[tuple[str, str], tuple[int, str] | BaseException] = {}
        self.requests: list[dict[str, Any]] = []
        self._tokens: dict[str, tuple[str, str]] = {}  # a token: its portal and username
        other = requests.Session.request

        def request(session: requests.Session, method: str, url: str, **k: Any) -> requests.Response:
            host = urlsplit(url).hostname or ""
            if not host.endswith("." + konnect.DOMAIN):
                return other(session, method, url, **k)
            return self.answer(session, method, url, **k)
        mp.setattr(requests.Session, "request", request)

    def portal(self, portal: str) -> KonnectPortal:
        return self.portals.setdefault(portal, KonnectPortal())

    def set_up(self) -> None:
        for signin in (self.sources.config.get("konnect") or {}).get("signins") or []:
            portal = konnect.portal_of(signin["portal"]) or signin["portal"]
            self.portal(portal).accounts.setdefault(signin["username"], KONNECT_PASSWORD)
            self.sources.secrets.setdefault(konnect.secret_name(portal, signin["username"]), KONNECT_PASSWORD)

    def paths(self) -> list[str]:
        """The paths asked for, each once, in the order first asked."""
        return list(dict.fromkeys(r["path"] for r in self.requests))

    def answer(self, session: requests.Session, method: str, url: str, **k: Any) -> requests.Response:
        parts = urlsplit(requests.Request(method, url, params=k.get("params")).prepare().url)
        portal_name = (parts.hostname or "").removesuffix("." + konnect.DOMAIN)
        path = parts.path.lstrip("/")
        query = {key: v for key, [v] in parse_qs(parts.query).items()}
        headers = dict(session.headers)
        self.requests.append({"method": method, "portal": portal_name, "path": path, "query": query,
                              "body": k.get("json"), "headers": headers})
        instead = self.answers.get((portal_name, path))
        if isinstance(instead, BaseException):
            raise instead
        if instead is not None:
            return _response(*instead)
        portal = self.portals.get(portal_name)
        if portal is None:
            return _response(404, "<html>Not found</html>")
        if (method, path) == ("PUT", "auth-api/login"):
            body = k.get("json") or {}
            username = body.get("username")
            if body.get("childMinder") is not False or not body.get("password") \
                    or portal.accounts.get(username) != body.get("password"):
                return _response(401, json.dumps({"message": "bad credentials"}))
            token = tokens.token_hex(8)
            self._tokens[token] = (portal_name, username)
            return _response(200, json.dumps({
                "authToken": token, "expiration": int((datetime.now(timezone.utc).timestamp() + 900) * 1000),
                "refreshToken": None, "mustResetPassword": username in portal.must_reset}))
        if not path.startswith(konnect.DATA):
            return _response(404, "<html>Not found</html>")
        signed_in = self._tokens.get(str(headers.get("Authorization", "")).removeprefix("Bearer "))
        if signed_in is None or signed_in[0] != portal_name or signed_in[1] in portal.must_reset:
            return _response(401, json.dumps({}))
        if "Mozilla/" not in str(headers.get("User-Agent", "")):
            return _response(403, "<html>Forbidden</html>")
        path = path.removeprefix(konnect.DATA)

        def enveloped(payload: Any) -> requests.Response:
            return _response(200, json.dumps({"result": True, "payload": payload, "messages": None},
                                             ensure_ascii=False))

        def listed(items: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
            first, last = date.fromisoformat(query["from"]), date.fromisoformat(query["to"])
            return [i for i in items if first <= _amsterdam_day(i[field]) <= last]
        if (method, path) == ("GET", "children/"):
            return enveloped({"activeChildren": portal.children, "inactiveChildren": []})
        if (method, path) == ("GET", "parent/authorizations"):
            return enveloped({"systemFunctions": portal.authorizations, "hasActiveChildren": True})
        if (method, path) == ("GET", "logbook/overview"):
            return _response(200, json.dumps(listed(portal.conversations, "date"), ensure_ascii=False))
        if (method, path) == ("GET", "htmlnews/view"):
            return enveloped({"unreadNewsCount": 0, "newsItems": listed(portal.news, "publishDate")})
        if (method, path) == ("GET", "newsletter"):
            return enveloped({"moreItems": False, "newsletterItems": listed(portal.newsletters, "sendDate")})
        if method == "GET" and (m := re.fullmatch(r"calendar/(\d{8})/until/(\d{8})", path)):
            first, last = (datetime.strptime(d, "%Y%m%d").date() for d in m.groups())
            days: dict[date, dict[str, Any]] = {}
            for p in self.tonight():
                if p["portal"] != portal_name or not first <= _amsterdam_day(p["startTime"]) <= last:
                    continue
                on = _amsterdam_day(p["startTime"])
                day = days.setdefault(on, {"title": None, "dayOfMonth": on.day, "dayType": "schoolday",
                                           "date": p["startTime"], "children": [], "hasMore": False,
                                           "plannableActivities": {"agendaActivities": []},
                                           "parentMeetings": [], "activityPrograms": []})
                child = next(c for c in portal.children if c["id"] == p["child"])
                day["children"].append({
                    "child": {k: child[k] for k in ("id", "fullname", "firstName", "lastName")} | {"avatar": None},
                    "showLocationName": True, "toConfirmPlannedAttendancesIds": [],
                    "combinedPlanningParts": [{"planningParts": [
                        {k: v for k, v in p.items() if k not in ("portal", "child")}], "subscriptions": []}]})
            return enveloped({"days": [days[d] for d in sorted(days)]})
        return _response(404, "<html>Not found</html>")


# ── Local Files: the folder the family puts files in

class LocalFiles(SourceFake):
    """The folder Local Files reads (ADR 0044), made as `setup save` makes it when the config turns
    it on, unless `made` is False. Tonight's Messages are written, each time the collector reads
    the folder, into one `.json` file, `tonight.json`, as a program of the family's own would
    write them: each its time, chat, sender and text. A test puts other files in with `write`.
    An exception tonight is raised reading the folder."""
    name = "local_files"
    FILE = "tonight.json"

    def __init__(self, sources: FakeSources, mp: pytest.MonkeyPatch) -> None:
        super().__init__(sources)
        self.made = True
        read = local_files.read

        def reading(cfg: Any) -> local_files.Folder:
            given = self.tonight()
            if given:
                self.write(self.FILE, json.dumps([self.entry(m) for m in given], ensure_ascii=False))
            return read(cfg)
        mp.setattr(local_files, "read", reading)

    def folder(self) -> Path:
        section = self.sources.config.get("local_files") or {}
        folder = section.get("folder") or "~/ParentRecap/inbox"
        return Path(folder.replace("~", str(self.sources.home), 1))

    def set_up(self) -> None:
        if self.made and (self.sources.config.get("local_files") or {}).get("enabled"):
            self.folder().mkdir(parents=True, exist_ok=True)

    def write(self, name: str, text: str | bytes) -> Path:
        """Puts a file named `name` in the folder, as the family would."""
        path = self.folder() / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
        return path

    @staticmethod
    def entry(m: Message) -> dict[str, Any]:
        """`m` as a `.json` file has it."""
        _carries(m, "local_files", {"sender", "chat_name"})
        assert m.chat_name, f"a Local Files Message from a .json file is in a chat: {m.external_id}"
        assert m.external_id == local_files.message_id(m.chat_name, m.sender, m.body, m.timestamp), \
            f"give {m.body!r} the id local_files.message_id makes of it"
        return {"time": m.timestamp.isoformat(), "chat": m.chat_name, "sender": m.sender, "text": m.body}


# The fake of each Source, by its name in the registry.
FAKES: dict[str, Callable[[FakeSources, pytest.MonkeyPatch], SourceFake]] = {
    f.name: f for f in (Gmail, MyClub, Wilma, WhatsApp, Eepos, Evaka, SocialSchools, Konnect,
                        LocalFiles)}


class FakeSources:
    """Every Source in the registry (ADR 0022), each faked at its transport, as `fakes[name]`,
    serving `tonight[name]` to its real collector: what the family's computer in `home` gets,
    with its `secrets`, when Parent Recap reads them with `config`. `lookback_hours` records the
    lookback each collection of a Source ran with."""

    def __init__(self, home: Path, config: dict[str, Any], secrets: FakeSecrets,
                 mp: pytest.MonkeyPatch) -> None:
        self.home, self.config, self.secrets = home, config, secrets
        # source -> its Messages and events tonight | the Exception its transport raises | a
        # function returning either
        self.tonight: dict[str, Any] = {}
        self.lookback_hours: dict[str, list[int]] = {}
        missing = [s.name for s in registry.ALL if s.name not in FAKES]
        assert not missing, f"no fake in fake_sources.FAKES for {missing}"
        self.fakes = {s.name: FAKES[s.name](self, mp) for s in registry.ALL}
        for s in registry.ALL:
            if s.lookback:
                self._spy_on_lookback(mp, s)

    def set_up(self) -> None:
        """What each Source's setup leaves on the family's computer, where the test hasn't put it
        itself. The evening run does this; a test reading the Sources another way, such as the
        setup page's preview, calls it."""
        for fake in self.fakes.values():
            fake.set_up()

    def program(self, cmd: list[str], prog: str, **kwargs: Any) -> subprocess.CompletedProcess | None:
        """A Source's own program, such as the wilma CLI."""
        for fake in self.fakes.values():
            answer = fake.run(cmd, **kwargs)
            if answer is not None:
                return answer
        return None

    def _spy_on_lookback(self, mp: pytest.MonkeyPatch, source: registry.Source) -> None:
        """Records the lookback each collection of `source` runs with, then collects as its record
        does. The record is frozen, so the spy goes in its __dict__, the same record everywhere."""
        collect = source.collect

        def spy(cfg: Any, *a: Any, **k: Any) -> Collected:
            self.lookback_hours.setdefault(source.name, []).append(source.lookback(cfg).lookback_hours)
            return collect(cfg, *a, **k)
        mp.setitem(vars(source), "collect", spy)
