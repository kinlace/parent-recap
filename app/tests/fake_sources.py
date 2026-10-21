"""Each Source's fake, at its transport, where the real thing varies (ADR 0022): Gmail's IMAP
server, the MyClub feed over HTTP, the wilma CLI on Parent Recap's own Node, and WhatsApp for
Mac's database. The harness installs the fake of each Source in the registry, so the real
collectors run in every test of the evening run; FAKES is the list it reads.

A fake serves what `harness.sources[name]` holds when the collector reaches it: tonight's Messages
and events, an exception its transport raises, or a function returning either. A Message is
written the way its transport carries it and read back by the real collector, so a test gives it
the id the collector makes of it (Gmail's X-GM-MSGID, Wilma's `message:<id>` or `news:<id>`, with
its Wilma's tag before the id once one is signed in to, WhatsApp's `<chat>:<message>`; a MyClub
event's id is its UID) and only the fields its transport
carries. A Message older than the Source's lookback is skipped, as the collector skips it.
"""
from __future__ import annotations

import imaplib
import json
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import icalendar
import pytest
import requests

import fake_node
from family_brief import own_node
from family_brief.collectors import gmail, myclub, whatsapp
from family_brief.collectors.base import CalendarEvent, Message

if TYPE_CHECKING:
    from conftest import Harness

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

    def __init__(self, harness: Harness) -> None:
        self.harness = harness

    def set_up(self) -> None:
        """Puts in place what the Source's setup leaves, if the test hasn't: a secret, a program,
        a permission. Before each evening run, so that tests of setup, doctor and uninstall
        start without them."""

    def tonight(self) -> list[Any]:
        """What the Source has when it is read: raises the exception it holds, if it does."""
        v = self.harness.sources.get(self.name, [])
        v = v() if callable(v) else v
        if isinstance(v, BaseException):
            raise v
        return list(v)

    def run(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess | None:
        """The answer to a command this Source's transport is, or None for any other."""
        return None


# ── Gmail: the IMAP server

class Gmail(SourceFake):
    """Gmail's IMAP server, which takes the app password setup stored, or only `password` when
    it is set. Every Message on it matches any search, whose X-GM-RAW query is kept in
    `searches`, and its sender is what setup's sender scan reads. `connections` says what each
    next connection does instead of serving tonight's mail as it is: an exception connecting
    raises, or a dict from a Message's id to what fetching its source gives instead (raw bytes, or
    an exception it raises, as a dropped connection does)."""
    name = "gmail"

    def __init__(self, harness: Harness, mp: pytest.MonkeyPatch) -> None:
        super().__init__(harness)
        self.password: str | None = None
        self.connections: list[BaseException | dict[str, bytes | BaseException]] = []
        self.searches: list[str] = []
        mp.setattr(imaplib, "IMAP4_SSL", self.connect)

    def set_up(self) -> None:
        username = self.harness.config.get("gmail", {}).get("username")
        if username:
            self.harness.secrets.setdefault(gmail.keychain_account(username), APP_PASSWORD)

    def connect(self, *_a: Any, **_k: Any) -> _Imap:
        instead = self.connections.pop(0) if self.connections else {}
        if isinstance(instead, BaseException):
            raise instead
        mail = {str(i + 1).encode(): m for i, m in enumerate(self.tonight())}
        return _Imap(self, mail, instead)


class _Imap:
    """One connection to the fake Gmail, as imaplib.IMAP4_SSL's methods answer."""

    def __init__(self, server: Gmail, mail: dict[bytes, Message],
                 instead: dict[str, bytes | BaseException]) -> None:
        self.server, self.mail, self.instead = server, mail, instead

    def __enter__(self) -> _Imap:
        return self

    def __exit__(self, *_a: Any) -> None:
        pass

    def login(self, _user: str, password: str) -> tuple[str, list[bytes]]:
        if self.server.password is not None and password != self.server.password:
            raise imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Invalid credentials (Failure)")
        return "OK", [b"LOGIN completed"]

    def select(self, *_a: Any, **_k: Any) -> tuple[str, list[bytes]]:
        return "OK", [str(len(self.mail)).encode()]

    def search(self, _charset: Any, *criteria: str) -> tuple[str, list[bytes]]:
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
    the ⚽ the collector puts before it. `failures` lists what each next download does instead:
    an exception it raises, or an HTTP status the server answers with. `gets` is each link
    downloaded. Any other address goes to whatever requests.get was before."""
    name = "myclub"

    def __init__(self, harness: Harness, mp: pytest.MonkeyPatch) -> None:
        super().__init__(harness)
        self.failures: list[BaseException | int] = []
        self.gets: list[str] = []
        self._other = requests.get
        mp.setattr(myclub.requests, "get", self.get)

    def get(self, url: str, *a: Any, **k: Any) -> Any:
        # A webcal:// link is downloaded over https://.
        kid = next((kid["name"] for kid in self.harness.config.get("kids", [])
                    if kid.get("myclub_ical_url")
                    and url == re.sub(r"^webcal://", "https://", kid["myclub_ical_url"])), None)
        if kid is None:
            return self._other(url, *a, **k)
        self.gets.append(url)
        failure = self.failures.pop(0) if self.failures else None
        if isinstance(failure, BaseException):
            raise failure
        if failure is not None:
            return _Feed(failure, "")
        return _Feed(200, _ics([e for e in self.tonight() if e.kid == kid]))


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
    raises, or a function of the command's arguments giving one of those. `ran` is each command
    run. Any other command lists nothing."""
    name = "wilma"

    def __init__(self, harness: Harness, mp: pytest.MonkeyPatch) -> None:
        super().__init__(harness)
        self.answers: dict[tuple[str, ...], Any] = {}
        self.ran: list[list[str]] = []

    def set_up(self) -> None:
        if own_node.wilma() is None:
            fake_node.pinned_node(self.harness.home)
            fake_node.install_wilma(self.harness.home, "#!/bin/sh\n")

    def run(self, cmd: list[str], **_k: Any) -> subprocess.CompletedProcess | None:
        if not fake_node.is_wilma(cmd):
            return None
        args = cmd[2:-1]
        self.ran.append(args)
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


# ── WhatsApp: WhatsApp for Mac's database

# WhatsApp's Core Data store, with the tables and columns of chats, messages, group members and
# push names the Source reads or that show it skips something; real rows have many more.
WHATSAPP_SCHEMA = """
CREATE TABLE ZWACHATSESSION (
    Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZARCHIVED INTEGER, ZREMOVED INTEGER,
    ZSESSIONTYPE INTEGER, ZUNREADCOUNT INTEGER, ZLASTMESSAGE INTEGER, ZLASTMESSAGEDATE TIMESTAMP,
    ZCONTACTJID VARCHAR, ZPARTNERNAME VARCHAR);
CREATE TABLE ZWAGROUPMEMBER (
    Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZISACTIVE INTEGER, ZISADMIN INTEGER,
    ZCHATSESSION INTEGER, ZCONTACTNAME VARCHAR, ZFIRSTNAME VARCHAR, ZMEMBERJID VARCHAR);
CREATE TABLE ZWAPROFILEPUSHNAME (
    Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZJID VARCHAR, ZPUSHNAME VARCHAR);
CREATE TABLE ZWAMESSAGE (
    Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZISFROMME INTEGER,
    ZMESSAGESTATUS INTEGER, ZMESSAGETYPE INTEGER, ZCHATSESSION INTEGER, ZGROUPMEMBER INTEGER,
    ZMEDIAITEM INTEGER, ZMESSAGEDATE TIMESTAMP, ZSENTDATE TIMESTAMP, ZFROMJID VARCHAR,
    ZPUSHNAME VARCHAR, ZSTANZAID VARCHAR, ZTEXT VARCHAR, ZTOJID VARCHAR);
"""
TEXT = 0  # ZWAMESSAGE.ZMESSAGETYPE of a text message


def core_data(when: datetime) -> float:
    """`when` as WhatsApp stores dates: seconds since 2001-01-01 UTC."""
    return (when - datetime(2001, 1, 1, tzinfo=timezone.utc)).total_seconds()


class WhatsAppDb:
    """WhatsApp's ChatStorage.sqlite at `path`, made afresh. Rows get the next free Z_PK unless
    given one."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript(WHATSAPP_SCHEMA)
        self._pk = 0

    def _next(self, pk: int | None = None) -> int:
        self._pk = max(self._pk, pk or 0) if pk else self._pk + 1
        return pk or self._pk

    def chat(self, name: str, pk: int | None = None, last: datetime | None = None) -> int:
        pk = self._next(pk)
        self.conn.execute("INSERT INTO ZWACHATSESSION (Z_PK, ZSESSIONTYPE, ZCONTACTJID, ZPARTNERNAME, "
                          "ZLASTMESSAGEDATE) VALUES (?, 1, ?, ?, ?)",
                          (pk, f"1203630{pk}@g.us", name, core_data(last or datetime.now(timezone.utc))))
        return pk

    def member(self, chat: int, jid: str, contact: str | None = None, first: str | None = None) -> int:
        pk = self._next()
        self.conn.execute("INSERT INTO ZWAGROUPMEMBER (Z_PK, ZCHATSESSION, ZMEMBERJID, ZCONTACTNAME, "
                          "ZFIRSTNAME) VALUES (?, ?, ?, ?, ?)", (pk, chat, jid, contact, first))
        return pk

    def push_name(self, jid: str, name: str) -> None:
        self.conn.execute("INSERT INTO ZWAPROFILEPUSHNAME (Z_PK, ZJID, ZPUSHNAME) VALUES (?, ?, ?)",
                          (self._next(), jid, name))

    def message(self, chat: int, when: datetime, text: str | None, *, msg_type: int | None = TEXT,
                member: int | None = None, from_jid: str | None = None, from_me: bool = False,
                pk: int | None = None) -> str:
        """A message in `chat`; returns the external id the Source gives it. ZPUSHNAME holds binary
        in newer WhatsApp builds, so every row has some for the Source to ignore."""
        pk = self._next(pk)
        self.conn.execute(
            "INSERT INTO ZWAMESSAGE (Z_PK, ZCHATSESSION, ZMESSAGEDATE, ZSENTDATE, ZTEXT, ZMESSAGETYPE, "
            "ZGROUPMEMBER, ZFROMJID, ZISFROMME, ZPUSHNAME, ZSTANZAID) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pk, chat, core_data(when), core_data(when), text, msg_type, member, from_jid,
             int(from_me), b"\x0a\x07\xff\x00binary", f"3EB0{pk:016X}"))
        self.conn.commit()
        return f"{chat}:{pk}"

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()


class WhatsApp(SourceFake):
    """WhatsApp for Mac, signed in, on a Mac that gives this Python Full Disk Access. Its database
    is written from tonight's Messages each time the collector copies it: a Message's id is
    `<chat Z_PK>:<message Z_PK>`, its chat_name its chat's name, and its sender a group member's
    contact name, or "me" for one the family sent. Its kid_hint comes from the config's chat, as
    the collector gives it. An exception tonight is raised copying the database. A test that
    installs WhatsApp itself (test_setup_whatsapp.py's fake Mac) sets `installed` to False."""
    name = "whatsapp"

    def __init__(self, harness: Harness, mp: pytest.MonkeyPatch) -> None:
        super().__init__(harness)
        self.db_dir = harness.home / "Library" / "Group Containers" / "group.net.whatsapp.WhatsApp.shared"
        mp.setattr(whatsapp, "DB_DIR", self.db_dir)
        mp.setattr(whatsapp, "DB_FILE", self.db_dir / "ChatStorage.sqlite")
        self.installed = True
        copy = shutil.copy2

        def copy2(src: Any, dst: Any, **k: Any) -> Any:
            if self.installed and Path(src) == self.db_dir / "ChatStorage.sqlite":
                self._write(self.tonight())
            return copy(src, dst, **k)
        mp.setattr(shutil, "copy2", copy2)

    def set_up(self) -> None:
        if not self.installed:
            return
        tcc = whatsapp.full_disk_access_file()
        if not tcc.exists():
            tcc.parent.mkdir(parents=True, exist_ok=True)
            tcc.write_bytes(b"")
        if not whatsapp.DB_FILE.exists():
            WhatsAppDb(whatsapp.DB_FILE).close()

    def _write(self, messages: list[Message]) -> None:
        chats: dict[int, str] = {}
        rows: list[tuple[int, int, Message]] = []
        for m in messages:
            _carries(m, "whatsapp", {"sender", "chat_name", "kid_hint"})
            ids = re.fullmatch(r"(\d+):(\d+)", m.external_id)
            assert ids, f"WhatsApp's ids are <chat>:<message>: {m.external_id}"
            assert m.chat_name, f"a WhatsApp Message is in a chat: {m.external_id}"
            chat = int(ids[1])
            assert chats.setdefault(chat, m.chat_name) == m.chat_name, \
                f"chat {chat} is {chats[chat]}, not {m.chat_name}"
            rows.append((chat, int(ids[2]), m))
        db = WhatsAppDb(self.db_dir / "ChatStorage.sqlite")
        for chat, name in chats.items():
            db.chat(name, chat)
        for chat, pk, m in rows:
            from_me = m.sender == "me"
            member = None if from_me or m.sender is None else \
                db.member(chat, f"35840{chat}{pk:06d}@s.whatsapp.net", contact=m.sender)
            db.message(chat, m.timestamp, m.body, member=member, from_me=from_me, pk=pk)
        db.close()


# The fake of each Source, by its name in the registry.
FAKES: dict[str, Callable[[Harness, pytest.MonkeyPatch], SourceFake]] = {
    f.name: f for f in (Gmail, MyClub, Wilma, WhatsApp)}
