"""Each Source's fake, at its transport, where the real thing varies (ADR 0022): Gmail's IMAP
server, the MyClub feed over HTTP, the wilma CLI on Parent Recap's own Node, and the Linked
Device's message store and status file. FakeSources, the `sources` fixture, installs the fake of
each Source in the registry, so the real collectors run in every test of the evening run; FAKES is
the list it reads.

A fake serves what `sources.tonight[name]` holds when the collector reaches it: tonight's Messages
and events, an exception its transport raises, or a function returning either. A Message is
written the way its transport carries it and read back by the real collector. So a test gives it
only the fields its transport carries, and the id the collector makes of it:
  - Gmail: its X-GM-MSGID, digits only;
  - Wilma: `message:<id>` or `news:<id>`, or `message:<tag>:<id>` once a Wilma is signed in to;
  - WhatsApp: its message id;
  - MyClub: the event's UID.
A Message older than the Source's lookback is skipped, as the collector skips it.
"""
from __future__ import annotations

import imaplib
import json
import re
import subprocess
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from typing import Any, Callable

import icalendar
import pytest
import requests

import fake_node
from family_brief import linked_device, own_node, sources as registry
from family_brief.collectors import gmail, myclub
from family_brief.collectors.base import CalendarEvent, Collected, Message
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
    the ⚽ the collector puts before it. `failures` lists what each next download does instead:
    an exception it raises, or an HTTP status the server answers with. `gets` is each link
    downloaded. Any other address goes to whatever requests.get was before."""
    name = "myclub"

    def __init__(self, sources: FakeSources, mp: pytest.MonkeyPatch) -> None:
        super().__init__(sources)
        self.failures: list[BaseException | int] = []
        self.gets: list[str] = []
        self._other = requests.get
        mp.setattr(myclub.requests, "get", self.get)

    def get(self, url: str, *a: Any, **k: Any) -> Any:
        # A webcal:// link is downloaded over https://.
        kid = next((kid["name"] for kid in self.sources.config.get("kids", [])
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
    raises, or a function of the command's arguments giving one of those, which may read the CLI's
    config the command was given in place of its own (`config`). `ran` is each command run. Any
    other command lists nothing."""
    name = "wilma"

    def __init__(self, sources: FakeSources, mp: pytest.MonkeyPatch) -> None:
        super().__init__(sources)
        self.answers: dict[tuple[str, ...], Any] = {}
        self.ran: list[list[str]] = []
        self.config: str | None = None  # the running command's WILMAI_CONFIG_PATH, if any

    def set_up(self) -> None:
        if own_node.wilma() is None:
            fake_node.pinned_node(self.sources.home)
            fake_node.install_wilma(self.sources.home, "#!/bin/sh\n")

    def run(self, cmd: list[str], **_k: Any) -> subprocess.CompletedProcess | None:
        if not fake_node.is_wilma(cmd):
            return None
        args = cmd[2:-1]
        self.ran.append(args)
        self.config = (_k.get("env") or {}).get("WILMAI_CONFIG_PATH")
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


# The fake of each Source, by its name in the registry.
FAKES: dict[str, Callable[[FakeSources, pytest.MonkeyPatch], SourceFake]] = {
    f.name: f for f in (Gmail, MyClub, Wilma, WhatsApp)}


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
