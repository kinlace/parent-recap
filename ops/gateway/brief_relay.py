"""Sends a Household's Brief from Parent Recap's own address (ADR 0028, #525), and makes send-only
Gateway Keys on request, bound to Recipients who confirmed by email (ADR 0033, #618).

The program posts the Brief to POST /v1/brief with its Gateway Key in `x-api-key`, as its model
calls do. The key must be an active Bifrost virtual key, looked up on Bifrost's admin API on
localhost, so revoking a key in Bifrost stops both. Each key may send DAILY_CAP messages a UTC
day, to at most MAX_RECIPIENTS each, so a leaked key can't use up Email Delivery's 100 a day for
everyone or become a spam relay. The message goes to OCI Email Delivery over SMTP and nothing
of it is kept: the log has the key's name, the number of recipients, the size and the outcome.

A key made on request (POST /v1/brief/key) has no model behind it, and the relay sends with it
only to the addresses bound to it that confirmed, through the link each was mailed. Which
addresses are bound to which key, and whether they confirmed, is the one thing kept
(STATE_DIRECTORY/bindings.json), with per-day counts that rate-limit the requests. Keys made by
hand aren't in that file and send as before.

Python's standard library only, so it runs on the host's own python3 with nothing to install.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import hashlib
import html as html_escape
import re
import secrets
import smtplib
import ssl
import sys
import threading
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

FROM_ADDR = "brief@parentrecap.com"
FROM = f"Parent Recap <{FROM_ADDR}>"
# Messages per Gateway Key per UTC day: the Brief, Weekend Picks and a retry. With MAX_RECIPIENTS,
# one key can send at most 40 of the tenancy's 100 a day (README "Sending the Brief").
DAILY_CAP = 4
# A key made on request's (ADR 0033): the Brief and Weekend Picks in two languages on a Friday.
# Its own constant, so the two can be tuned apart.
ON_REQUEST_DAILY_CAP = 4
MAX_RECIPIENTS = 10
MAX_ATTACHMENTS = 5
MAX_REQUEST_BYTES = 2_000_000
# The unit doesn't restart on this exit: a missing setting won't appear by trying again.
EX_CONFIG = 78
# Bifrost's virtual-key list returns at most 100 keys a page.
PAGE = 100

# Strict on purpose: no display names, no commas, nothing a header could be split on, and ASCII
# only, as Email Delivery's SMTP takes no internationalized addresses.
ADDRESS = re.compile(r"[!#-'*+./0-9=?A-Z^-~-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+", re.ASCII)
MIME_TYPE = re.compile(r"([a-z0-9][a-z0-9.+-]*)/([a-z0-9][a-z0-9.+-]*)")
FILENAME = re.compile(r"[^/\\\x00-\x1f\x7f]{1,100}")

log = logging.getLogger("brief-relay")


class Refused(Exception):
    """A request the relay answers with an error: `status` and a short reason for the program,
    and anything else the answer carries in `extra`."""

    def __init__(self, status: int, reason: str, **extra: Any) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason
        self.extra = extra


# ── The Gateway Key

@dataclass(frozen=True)
class Key:
    id: str
    name: str


class Bifrost:
    """The Gateway Keys, as Bifrost's admin API lists its virtual keys."""

    def __init__(self, base: str, user: str, password: str,
                 fetch: Callable[..., dict] | None = None) -> None:
        self.base = base.rstrip("/")
        self.auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        self.fetch = fetch or self._call

    def _call(self, path: str, body: dict | None = None) -> dict:
        """GET `path`, or POST `body` to it as JSON."""
        headers = {"Authorization": self.auth}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=data, headers=headers)
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)

    def create(self, name: str, description: str) -> tuple[Key, str]:
        """A new active send-only key: no provider behind it, so Bifrost refuses its model calls
        (README "Create a send-only Gateway Key"). Returns it and its value."""
        try:
            made = self.fetch("/api/governance/virtual-keys",
                              {"name": name, "description": description, "is_active": True})
            k = made["virtual_key"]
            value = k["value"]
            if not isinstance(value, str) or not value:
                raise ValueError
            return Key(str(k["id"]), str(k.get("name") or name)), value
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            log.warning("bifrost admin API: %s", e.__class__.__name__)
            raise Refused(503, "the gateway couldn't make a key; try again later") from None

    def key(self, value: str) -> Key | None:
        """The active key whose value this is, else None. Bifrost can't search by value, so the
        list is read page by page; a pilot has tens of keys."""
        offset = 0
        try:
            while True:
                page = self.fetch(f"/api/governance/virtual-keys?limit={PAGE}&offset={offset}")
                keys = page.get("virtual_keys") or []
                for k in keys:
                    if k.get("value") == value:
                        return Key(str(k["id"]), str(k.get("name") or k["id"])) \
                            if k.get("is_active") is True else None
                offset += len(keys)
                if not keys or offset >= int(page.get("total_count") or 0):
                    return None
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            log.warning("bifrost admin API: %s", e.__class__.__name__)
            raise Refused(503, "the gateway couldn't check the key; try again later") from None


# ── The daily cap

class DailyCap:
    """Messages sent per key on the current UTC day, kept in a file so a restart doesn't reset it."""

    def __init__(self, path: Path, limit: int,
                 today: Callable[[], str] = lambda: datetime.now(UTC).date().isoformat(),
                 on_request_limit: int | None = None):
        self.path = path
        self.limit = limit
        # A key made on request's own cap (ADR 0033), else the same.
        self.on_request_limit = limit if on_request_limit is None else on_request_limit
        self.today = today
        self.lock = threading.Lock()

    def _read(self) -> dict[str, int]:
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return state.get("sent", {}) if state.get("day") == self.today() else {}

    def _write(self, sent: dict[str, int]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"day": self.today(), "sent": sent}), encoding="utf-8")
        tmp.replace(self.path)

    def take(self, key: Key, on_request: bool = False) -> bool:
        """Counts one message for `key`, or returns False if today's are used up: the cap of a
        key made `on_request`, else the cap of one made by hand."""
        with self.lock:
            sent = self._read()
            if sent.get(key.id, 0) >= (self.on_request_limit if on_request else self.limit):
                return False
            sent[key.id] = sent.get(key.id, 0) + 1
            self._write(sent)
            return True

    def give_back(self, key: Key) -> None:
        """Uncounts a message Email Delivery didn't take, so a retry isn't held against the key."""
        with self.lock:
            sent = self._read()
            if sent.get(key.id, 0) > 0:
                sent[key.id] -= 1
                self._write(sent)


# ── The request

def _text(body: dict, field: str, required: bool = True, one_line: bool = False) -> str | None:
    value = body.get(field)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise Refused(400, f"`{field}` must be a non-empty string")
    if one_line and ("\r" in value or "\n" in value):
        raise Refused(400, f"`{field}` must be one line")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise Refused(400, f"`{field}` isn't valid text") from None
    return value


def message(body: Any, stop_url: str) -> tuple[EmailMessage, list[str]]:
    """The email for a request's JSON body, and its Recipients, or Refused(400) saying what's wrong.

    {"to": [address, ...], "subject": str, "text": str, "html": str (optional),
     "attachments": [{"filename": str, "content_type": "type/subtype", "data": base64}] (optional)}
    """
    if not isinstance(body, dict):
        raise Refused(400, "the body must be a JSON object")
    to = body.get("to")
    if not isinstance(to, list) or not to:
        raise Refused(400, "`to` must be a list of addresses")
    if len(to) > MAX_RECIPIENTS:
        raise Refused(400, f"at most {MAX_RECIPIENTS} recipients")
    if not all(isinstance(a, str) and ADDRESS.fullmatch(a) for a in to):
        raise Refused(400, "`to` has an address that isn't a plain name@domain")
    subject = _text(body, "subject", one_line=True)
    text = _text(body, "text")
    html = _text(body, "html", required=False)
    attachments = body.get("attachments") or []
    if not isinstance(attachments, list) or len(attachments) > MAX_ATTACHMENTS:
        raise Refused(400, f"`attachments` must be a list of at most {MAX_ATTACHMENTS}")

    msg = EmailMessage()
    msg["From"] = FROM
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg["Date"] = formatdate(usegmt=True)
    msg["Message-ID"] = make_msgid(domain=FROM_ADDR.split("@")[1])
    # Email Delivery adds its own tracking List-Unsubscribe unless there is an HTTPS one; the page
    # says how a Household turns the Brief off, which is in its own config, not here.
    msg["List-Unsubscribe"] = f"<{stop_url}>"
    msg.set_content(text)
    if html is not None:
        msg.add_alternative(html, subtype="html")
    for a in attachments:
        if not isinstance(a, dict):
            raise Refused(400, "each attachment must be an object")
        filename, mime, data = a.get("filename"), a.get("content_type"), a.get("data")
        if not isinstance(filename, str) or not FILENAME.fullmatch(filename):
            raise Refused(400, "an attachment's `filename` is missing or not a plain file name")
        if not isinstance(mime, str) or not (m := MIME_TYPE.fullmatch(mime.lower())):
            raise Refused(400, "an attachment's `content_type` must be type/subtype")
        try:
            payload = base64.b64decode(data, validate=True) if isinstance(data, str) else None
        except (binascii.Error, ValueError):
            payload = None
        if payload is None:
            raise Refused(400, "an attachment's `data` must be base64")
        # Calendar apps offer to add an .ics only when it says METHOD:PUBLISH, as the program's
        # own Gmail sending does.
        params = {"params": {"method": "PUBLISH"}} if mime.lower() == "text/calendar" else {}
        msg.add_attachment(payload, maintype=m[1], subtype=m[2], filename=filename, **params)
    return msg, to


# ── Keys made on request (ADR 0033)

# A key made on request may be bound to this many addresses.
MAX_BOUND = 6
# Per UTC day: keys one caller (by IP address) may ask for, keys made for everyone, confirmation
# emails one address may get, and confirmation emails sent in all. Together they keep the
# confirmation mail, the only mail sent before a Recipient says yes, to 30 of the tenancy's 100.
KEYS_PER_CALLER = 3
KEYS_PER_DAY = 20
MAILS_PER_ADDRESS = 3
MAILS_PER_DAY = 30
LANGUAGE = re.compile(r"[a-z]{2,3}(-[a-z0-9]{1,8})*", re.ASCII)
TOKEN = re.compile(r"[A-Za-z0-9_-]{20,64}", re.ASCII)


def _hashed(value: str) -> str:
    """What the day's counts keep of a caller's IP address or an email address."""
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def recipients_in(body: Any) -> list[dict[str, str]]:
    """The `recipients` of a key request or a binding, each {"address", "language"} with the
    address in lower case, the setup parent first; or Refused(400) saying what's wrong."""
    if not isinstance(body, dict):
        raise Refused(400, "the body must be a JSON object")
    given = body.get("recipients")
    if not isinstance(given, list) or not given:
        raise Refused(400, "`recipients` must be a list of {address, language}")
    if len(given) > MAX_BOUND:
        raise Refused(400, f"at most {MAX_BOUND} recipients")
    out: list[dict[str, str]] = []
    for r in given:
        address = r.get("address") if isinstance(r, dict) else None
        language = r.get("language") if isinstance(r, dict) else None
        if not isinstance(address, str) or not ADDRESS.fullmatch(address):
            raise Refused(400, "a recipient's `address` isn't a plain name@domain")
        language = language.lower() if isinstance(language, str) else ""
        if not LANGUAGE.fullmatch(language):
            raise Refused(400, "a recipient's `language` isn't a language code")
        if any(o["address"] == address.lower() for o in out):
            raise Refused(400, "the same address is given twice")
        out.append({"address": address.lower(), "language": language})
    return out


class Bindings:
    """Which addresses are bound to each key made on request, and which of them confirmed, with
    the day's counts that rate-limit asking for keys and mailing confirmations. One JSON file in
    the relay's state directory, so a restart keeps it:

    {"keys": {"<Bifrost key id>": {"name": "on-request-…", "made": "2026-10-30",
              "recipients": [{"address": "…", "language": "fi", "token": "…",
                              "confirmed": "2026-10-30T19:02:11Z" | null}]}},
     "day": "2026-10-30", "callers": {"<hash>": 1}, "addresses": {"<hash>": 1},
     "keys_made": 1, "mails": 2}
    """

    def __init__(self, path: Path,
                 today: Callable[[], str] = lambda: datetime.now(UTC).date().isoformat(),
                 now: Callable[[], str] = lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 token: Callable[[], str] = lambda: secrets.token_urlsafe(24)) -> None:
        self.path = path
        self.today, self.now, self.token = today, now, token
        self.lock = threading.Lock()

    def _read(self) -> dict:
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        if not isinstance(state, dict):
            state = {}
        state.setdefault("keys", {})
        if state.get("day") != self.today():
            state.update(day=self.today(), callers={}, addresses={}, keys_made=0, mails=0)
        return state

    def _write(self, state: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def bound(self, key: Key) -> list[dict] | None:
        """The addresses bound to `key`, or None for a key made by hand, which has none."""
        with self.lock:
            entry = self._read()["keys"].get(key.id)
        return None if entry is None else entry["recipients"]

    def reserve_key(self, caller: str, addresses: list[str]) -> None:
        """Counts a key request from `caller` that will mail each of `addresses`, or raises
        Refused(429) without counting anything when that is over a day's limit."""
        with self.lock:
            state = self._read()
            who = _hashed(caller)
            if state["callers"].get(who, 0) >= KEYS_PER_CALLER or state["keys_made"] >= KEYS_PER_DAY:
                raise Refused(429, "too many keys asked for today; try again tomorrow")
            if state["mails"] + len(addresses) > MAILS_PER_DAY or any(
                    state["addresses"].get(_hashed(a), 0) >= MAILS_PER_ADDRESS for a in addresses):
                raise Refused(429, "too many confirmation emails today; try again tomorrow")
            state["callers"][who] = state["callers"].get(who, 0) + 1
            state["keys_made"] += 1
            for a in addresses:
                state["addresses"][_hashed(a)] = state["addresses"].get(_hashed(a), 0) + 1
            state["mails"] += len(addresses)
            self._write(state)

    def take_mail(self, address: str) -> bool:
        """Counts one confirmation email to `address`, or False when that is over a day's limit."""
        with self.lock:
            state = self._read()
            h = _hashed(address)
            if state["mails"] >= MAILS_PER_DAY or state["addresses"].get(h, 0) >= MAILS_PER_ADDRESS:
                return False
            state["addresses"][h] = state["addresses"].get(h, 0) + 1
            state["mails"] += 1
            self._write(state)
            return True

    def add(self, key: Key, recipients: list[dict[str, str]]) -> list[dict]:
        """Binds a new key to `recipients`, none confirmed yet."""
        entries = [{**r, "token": self.token(), "confirmed": None} for r in recipients]
        with self.lock:
            state = self._read()
            state["keys"][key.id] = {"name": key.name, "made": self.today(), "recipients": entries}
            self._write(state)
        return entries

    def rebind(self, key: Key, recipients: list[dict[str, str]]) -> tuple[list[dict], list[dict]]:
        """Binds `key` to exactly `recipients`: an address already bound keeps whether it
        confirmed, a new one waits for its confirmation, and one left out is unbound. Returns the
        new list and the entries new to it."""
        with self.lock:
            state = self._read()
            entry = state["keys"][key.id]
            before = {r["address"]: r for r in entry["recipients"]}
            entries, new = [], []
            for r in recipients:
                kept = before.get(r["address"])
                if kept is None:
                    kept = {**r, "token": self.token(), "confirmed": None}
                    new.append(kept)
                else:
                    kept = {**kept, "language": r["language"]}
                entries.append(kept)
            entry["recipients"] = entries
            self._write(state)
        return entries, new

    def find(self, token: str) -> tuple[str, dict] | None:
        """The key's name and the bound address a confirmation link's token is for, else None."""
        with self.lock:
            for entry in self._read()["keys"].values():
                for r in entry["recipients"]:
                    if secrets.compare_digest(r["token"], token):
                        return entry["name"], r
        return None

    def confirm(self, token: str) -> tuple[str, dict] | None:
        """Marks the address of `token` confirmed, as `find` finds it."""
        with self.lock:
            state = self._read()
            for entry in state["keys"].values():
                for r in entry["recipients"]:
                    if secrets.compare_digest(r["token"], token):
                        if not r["confirmed"]:
                            r["confirmed"] = self.now()
                            self._write(state)
                        return entry["name"], r
        return None


# What a confirmation email and page say, by language; any other language gets English. {who} is
# the setup parent's address, {address} the Recipient's.
CONFIRM_TEXT: dict[str, dict[str, str]] = {
    "en": {
        "subject": "Confirm that you want the Parent Recap Brief",
        "body": "{who} set up Parent Recap to send the evening Brief to this address ({address}).\n\n"
                "To get it, open this link and press Confirm:\n{link}\n\n"
                "If you don't know about this, ignore this email. Nothing more will be sent to you.\n\n"
                "Nobody reads this address, so please don't reply.\n",
        "title": "Confirm the Parent Recap Brief",
        "ask": "Press Confirm to get the evening Brief from Parent Recap at {address}.",
        "button": "Confirm",
        "done": "Confirmed. The evening Brief now comes to {address}. You can close this page.",
        "gone": "This link doesn't work any more. Ask the person who set up Parent Recap to send "
                "a new confirmation email.",
    },
    "fi": {
        "subject": "Vahvista, että haluat Parent Recapin koosteen",
        "body": "{who} otti Parent Recapin käyttöön ja haluaa iltakoosteen tähän osoitteeseen "
                "({address}).\n\n"
                "Jos haluat koosteen, avaa tämä linkki ja paina Vahvista:\n{link}\n\n"
                "Jos et tiedä asiasta, voit jättää tämän viestin huomiotta. Sinulle ei lähetetä "
                "enempää.\n\n"
                "Tätä osoitetta ei lueta, joten älä vastaa tähän viestiin.\n",
        "title": "Vahvista Parent Recapin kooste",
        "ask": "Paina Vahvista, niin Parent Recapin iltakooste tulee osoitteeseen {address}.",
        "button": "Vahvista",
        "done": "Vahvistettu. Iltakooste tulee nyt osoitteeseen {address}. Voit sulkea tämän sivun.",
        "gone": "Tämä linkki ei enää toimi. Pyydä Parent Recapin asentanutta henkilöä lähettämään "
                "uusi vahvistusviesti.",
    },
    "zh": {
        "subject": "请确认接收 Parent Recap 日报",
        "body": "{who} 设置了 Parent Recap，想把每晚的日报发到这个地址（{address}）。\n\n"
                "如果你想收到日报，请打开下面的链接，然后点「确认」：\n{link}\n\n"
                "如果你不知道这件事，忽略这封邮件就好，我们不会再给你发任何邮件。\n\n"
                "这个地址没有人看，请不要回复。\n",
        "title": "确认接收 Parent Recap 日报",
        "ask": "点「确认」后，Parent Recap 每晚的日报就会发到 {address}。",
        "button": "确认",
        "done": "已确认。每晚的日报会发到 {address}。现在可以关掉这个页面了。",
        "gone": "这个链接已经失效了。请让设置 Parent Recap 的人重新发一封确认邮件。",
    },
}


def confirm_text(language: str) -> dict[str, str]:
    return CONFIRM_TEXT.get(language.split("-")[0], CONFIRM_TEXT["en"])


def confirmation(recipient: dict, who: str, link: str, stop_url: str) -> EmailMessage:
    """The email that asks `recipient` to confirm, in their language, from the Brief's address."""
    t = confirm_text(recipient["language"])
    msg = EmailMessage()
    msg["From"] = FROM
    msg["To"] = recipient["address"]
    msg["Subject"] = t["subject"]
    msg["Date"] = formatdate(usegmt=True)
    msg["Message-ID"] = make_msgid(domain=FROM_ADDR.split("@")[1])
    msg["List-Unsubscribe"] = f"<{stop_url}>"
    msg.set_content(t["body"].format(who=who, address=recipient["address"], link=link))
    return msg


def confirm_page(language: str, *texts: str, button: str | None = None) -> str:
    """The small page a confirmation link opens: its `texts`, and a Confirm button that posts
    back."""
    t = confirm_text(language)
    lang = language.split("-")[0] if language.split("-")[0] in CONFIRM_TEXT else "en"
    form = (f'<form method="post"><button type="submit">{html_escape.escape(button)}</button></form>'
            if button else "")
    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>{html_escape.escape(t["title"], quote=False)}</title>
<style>
  body {{ font: 16px/1.5 system-ui, sans-serif; max-width: 36rem; margin: 2rem auto; padding: 0 16px; color: #12161b; background: #fff; }}
  @media (prefers-color-scheme: dark) {{ body {{ color: #e8ebef; background: #0d1014; }} }}
  button {{ font: inherit; padding: .5rem 1.25rem; border-radius: 6px; border: 0; background: #1f6feb; color: #fff; cursor: pointer; }}
</style>
</head>
<body>
<h1>{html_escape.escape(t["title"], quote=False)}</h1>
{"".join(f"<p>{html_escape.escape(text, quote=False)}</p>" for text in texts)}
{form}
</body>
</html>
"""


# ── Email Delivery

class Mailer:
    """OCI Email Delivery over SMTP with implicit TLS (port 465), AUTH PLAIN."""

    def __init__(self, host: str, port: int, user: str, password: str,
                 smtp: Callable[..., Any] = smtplib.SMTP_SSL) -> None:
        self.host, self.port, self.user, self.password = host, port, user, password
        self.smtp = smtp

    def send(self, msg: EmailMessage, to: list[str]) -> int:
        """Hands `msg` over, returning how many Recipients Email Delivery refused (0 normally),
        or raises Refused(502) with a reason that names no address."""
        try:
            with self.smtp(self.host, self.port, timeout=30,
                           context=ssl.create_default_context()) as s:
                s.login(self.user, self.password)
                refused = s.send_message(msg, from_addr=FROM_ADDR, to_addrs=to)
        except smtplib.SMTPAuthenticationError:
            raise Refused(502, "email delivery refused the gateway's login") from None
        except smtplib.SMTPRecipientsRefused:
            raise Refused(502, "email delivery refused every recipient") from None
        except smtplib.SMTPResponseException as e:
            raise Refused(502, f"email delivery answered {e.smtp_code}") from None
        except (smtplib.SMTPException, OSError) as e:
            raise Refused(502, f"couldn't reach email delivery ({e.__class__.__name__})") from None
        return len(refused or {})


# ── The relay

class Relay:
    def __init__(self, keys: Bifrost, cap: DailyCap, mailer: Mailer, stop_url: str,
                 bindings: Bindings, confirm_url: str) -> None:
        self.keys, self.cap, self.mailer, self.stop_url = keys, cap, mailer, stop_url
        self.bindings, self.confirm_url = bindings, confirm_url

    def handle(self, key_value: str | None, raw: bytes) -> tuple[int, dict]:
        """The status and JSON answer for one POST /v1/brief. A key made on request sends only to
        its confirmed addresses: the others are left out and named in `unconfirmed`, and with
        none confirmed it gets 403."""
        key = None
        recipients = 0
        counted = False
        unconfirmed: list[str] = []
        try:
            try:
                key = self._key(key_value)
                body = self._body(raw)
                msg, to = message(body, self.stop_url)
                bound = self.bindings.bound(key)
                if bound is not None:
                    confirmed = {r["address"] for r in bound if r["confirmed"]}
                    unconfirmed = [a for a in to if a.lower() not in confirmed]
                    to = [a for a in to if a.lower() in confirmed]
                    if not to:
                        raise Refused(403, "recipient not confirmed", unconfirmed=unconfirmed)
                    if unconfirmed:
                        msg, to = message({**body, "to": to}, self.stop_url)
                recipients = len(to)
                if not self.cap.take(key, on_request=bound is not None):
                    raise Refused(429, "this key has sent all the Briefs it may send today")
                counted = True
                refused = self.mailer.send(msg, to)
            except Refused:
                raise
            except Exception as e:
                # Only the class: an exception's text can quote the message.
                raise Refused(500, f"the relay failed ({e.__class__.__name__})") from None
        except Refused as e:
            if counted:
                self.cap.give_back(key)
            self._log(key, recipients, raw, f"{e.status} {e.reason}")
            return e.status, {"error": e.reason, **e.extra}
        self._log(key, recipients, raw, "sent" + (f", {refused} refused" if refused else "")
                  + (f", {len(unconfirmed)} unconfirmed left out" if unconfirmed else ""))
        return 200, {"sent": recipients - refused, "refused": refused,
                     **({"unconfirmed": unconfirmed} if unconfirmed else {})}

    def _key(self, key_value: str | None) -> Key:
        key = self.keys.key(key_value) if key_value else None
        if key is None:
            raise Refused(401, "missing, unknown or revoked Gateway Key")
        return key

    @staticmethod
    def _body(raw: bytes) -> Any:
        if len(raw) > MAX_REQUEST_BYTES:
            raise Refused(413, f"the request is over {MAX_REQUEST_BYTES} bytes")
        try:
            return json.loads(raw)
        except (ValueError, RecursionError):
            raise Refused(400, "the body isn't JSON") from None

    # ── Keys made on request

    def request_key(self, caller: str, raw: bytes) -> tuple[int, dict]:
        """POST /v1/brief/key: makes a send-only key bound to the given Recipients, none of them
        confirmed, and mails each a confirmation link. Rate-limited by caller and by address."""
        key = None
        recipients: list[dict[str, str]] = []
        try:
            try:
                recipients = recipients_in(self._body(raw))
                self.bindings.reserve_key(caller, [r["address"] for r in recipients])
                key, value = self.keys.create(f"on-request-{secrets.token_hex(4)}",
                                              f"made on request {self.bindings.today()}")
                entries = self.bindings.add(key, recipients)
                mailed = self._mail(entries, entries, counted=True)
            except Refused:
                raise
            except Exception as e:
                raise Refused(500, f"the relay failed ({e.__class__.__name__})") from None
        except Refused as e:
            self._log_event("key-request", key, len(recipients), f"{e.status} {e.reason}")
            return e.status, {"error": e.reason, **e.extra}
        self._log_event("key-request", key, len(recipients), f"made, {len(mailed)} mailed")
        return 200, {"key": value, **self._statuses(entries, mailed)}

    def rebind(self, key_value: str | None, raw: bytes) -> tuple[int, dict]:
        """POST /v1/brief/recipients: binds the key to these Recipients, mailing a confirmation
        link to each new one, and with `resend` to each still unconfirmed. Answers whether each
        confirmed. A key made by hand isn't bound, so all of its are."""
        key = None
        recipients: list[dict[str, str]] = []
        try:
            try:
                key = self._key(key_value)
                body = self._body(raw)
                recipients = recipients_in(body)
                if self.bindings.bound(key) is None:
                    self._log_event("bind", key, len(recipients), "made by hand")
                    return 200, {"recipients": [{"address": r["address"], "confirmed": True}
                                                for r in recipients], "mailed": []}
                entries, new = self.bindings.rebind(key, recipients)
                waiting = [e for e in entries if not e["confirmed"]]
                to_mail = waiting if body.get("resend") is True else new
                mailed = self._mail(entries, to_mail)
                if to_mail and not mailed and body.get("resend") is True:
                    raise Refused(429, "too many confirmation emails today; try again tomorrow",
                                  **self._statuses(entries, mailed))
            except Refused:
                raise
            except Exception as e:
                raise Refused(500, f"the relay failed ({e.__class__.__name__})") from None
        except Refused as e:
            self._log_event("bind", key, len(recipients), f"{e.status} {e.reason}")
            return e.status, {"error": e.reason, **e.extra}
        self._log_event("bind", key, len(recipients), f"{len(new)} new, {len(mailed)} mailed")
        return 200, self._statuses(entries, mailed)

    def confirm(self, query: str, post: bool) -> tuple[int, str]:
        """GET or POST /brief/confirm?t=<token>: the page a confirmation link opens. A GET only
        asks, so a mail scanner that opens the link confirms nothing; the page's button posts."""
        token = (parse_qs(query).get("t") or [""])[0]
        found = (self.bindings.confirm if post else self.bindings.find)(token) \
            if TOKEN.fullmatch(token) else None
        if found is None:
            self._log_event("confirm", None, 0, "unknown link")
            return 404, confirm_page("en", *(CONFIRM_TEXT[lang]["gone"] for lang in ("en", "fi", "zh")))
        name, r = found
        t = confirm_text(r["language"])
        if post:
            self._log_event("confirm", Key(name, name), 1, "confirmed")
            return 200, confirm_page(r["language"], t["done"].format(address=r["address"]))
        return 200, confirm_page(r["language"], t["ask"].format(address=r["address"]),
                                 button=t["button"])

    def _mail(self, entries: list[dict], to_mail: list[dict], counted: bool = False) -> list[str]:
        """Mails each of `to_mail` its confirmation link, within the day's limits unless they
        were `counted` already; the addresses it was handed over for. The setup parent, first of
        `entries`, is who asked."""
        who = entries[0]["address"]
        mailed = []
        for r in to_mail:
            if not counted and not self.bindings.take_mail(r["address"]):
                continue
            link = f"{self.confirm_url}?t={r['token']}"
            try:
                self.mailer.send(confirmation(r, who, link, self.stop_url), [r["address"]])
            except Refused as e:
                log.info("confirmation outcome=%s %s", e.status, e.reason)
                continue
            mailed.append(r["address"])
        return mailed

    @staticmethod
    def _statuses(entries: list[dict], mailed: list[str]) -> dict:
        return {"recipients": [{"address": e["address"], "confirmed": bool(e["confirmed"])}
                               for e in entries], "mailed": mailed}

    @staticmethod
    def _log(key: Key | None, recipients: int, raw: bytes, outcome: str) -> None:
        log.info("brief key=%s recipients=%d bytes=%d outcome=%s",
                 key.name if key else "-", recipients, len(raw), outcome)

    @staticmethod
    def _log_event(event: str, key: Key | None, recipients: int, outcome: str) -> None:
        log.info("%s key=%s recipients=%d outcome=%s", event, key.name if key else "-",
                 recipients, outcome)


# ── HTTP

def handler(relay: Relay) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "brief-relay"
        sys_version = ""
        # A client that stops sending mid-request doesn't hold a thread for ever.
        timeout = 30

        def _answer(self, status: int, body: dict) -> None:
            self._send(status, json.dumps(body).encode(), "application/json")

        def _send(self, status: int, data: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _page(self, status: int, page: str) -> None:
            self._send(status, page.encode(), "text/html; charset=utf-8")

        def do_GET(self) -> None:
            path = urlsplit(self.path)
            # Only for gateway-setup.sh's check on localhost; Caddy doesn't route it.
            if self.path == "/health":
                self._answer(200, {"status": "ok"})
            elif path.path == "/brief/confirm":
                self._page(*relay.confirm(path.query, post=False))
            else:
                self._answer(404, {"error": "not found"})

        def _read(self) -> bytes | None:
            if self.headers.get("Content-Length") is None:
                self._answer(411, {"error": "Content-Length is required"})
                return None
            try:
                length = int(self.headers["Content-Length"])
            except ValueError:
                length = -1
            if length < 0:
                self._answer(400, {"error": "bad Content-Length"})
                return None
            # A body over the limit isn't read: the key is checked on what's there, then 413.
            return self.rfile.read(min(length, MAX_REQUEST_BYTES + 1))

        def _caller(self) -> str:
            # Caddy sets X-Forwarded-For to the client's address; the last one is the one it saw.
            forwarded = self.headers.get("X-Forwarded-For") or self.client_address[0]
            return forwarded.split(",")[-1].strip()

        def do_POST(self) -> None:
            path = urlsplit(self.path)
            if path.path == "/brief/confirm":
                # The page's own form: its body says nothing, the link's token is in the query.
                length = self.headers.get("Content-Length")
                if length and length.isdigit():
                    self.rfile.read(min(int(length), 1024))
                return self._page(*relay.confirm(path.query, post=True))
            routes = {"/v1/brief": lambda raw: relay.handle(self.headers.get("x-api-key"), raw),
                      "/v1/brief/key": lambda raw: relay.request_key(self._caller(), raw),
                      "/v1/brief/recipients":
                          lambda raw: relay.rebind(self.headers.get("x-api-key"), raw)}
            if self.path not in routes:
                return self._answer(404, {"error": "not found"})
            raw = self._read()
            if raw is not None:
                self._answer(*routes[self.path](raw))

        def log_message(self, *_args: Any) -> None:
            # The relay logs each request itself, without the client's address.
            pass

    return Handler


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    env = os.environ
    state = Path(env.get("STATE_DIRECTORY", "/var/lib/brief-relay"))
    if sys.argv[1:] == ["keys"]:
        return print_keys(state / "bindings.json")
    missing = [v for v in ("BRIEF_SMTP_USERNAME", "BRIEF_SMTP_PASSWORD", "BIFROST_ADMIN_USERNAME",
                           "BIFROST_ADMIN_PASSWORD", "GATEWAY_HOST") if not env.get(v)]
    if missing:
        print(f"brief-relay: {', '.join(missing)} not set (see ops/gateway/README.md)",
              file=sys.stderr)
        sys.exit(EX_CONFIG)
    relay = Relay(
        keys=Bifrost(env.get("BIFROST_URL", "http://127.0.0.1:8080"),
                     env["BIFROST_ADMIN_USERNAME"], env["BIFROST_ADMIN_PASSWORD"]),
        cap=DailyCap(state / "sent.json", limit=DAILY_CAP, on_request_limit=ON_REQUEST_DAILY_CAP),
        mailer=Mailer(env.get("BRIEF_SMTP_HOST", "smtp.email.eu-stockholm-1.oci.oraclecloud.com"),
                      465, env["BRIEF_SMTP_USERNAME"], env["BRIEF_SMTP_PASSWORD"]),
        stop_url=f"https://{env['GATEWAY_HOST']}/brief/stop",
        bindings=Bindings(state / "bindings.json"),
        confirm_url=f"https://{env['GATEWAY_HOST']}/brief/confirm")
    port = int(env.get("BRIEF_RELAY_PORT", "8081"))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler(relay))
    log.info("brief-relay listening on 127.0.0.1:%d", port)
    server.serve_forever()


def print_keys(path: Path) -> None:
    """`brief_relay.py keys`: each key made on request, with its addresses and whether each
    confirmed, for the maintainers (README "Runbook")."""
    try:
        keys = json.loads(path.read_text(encoding="utf-8")).get("keys") or {}
    except (OSError, ValueError):
        keys = {}
    for key_id, entry in keys.items():
        print(f"{entry['name']}  id={key_id}  made {entry['made']}")
        for r in entry["recipients"]:
            print(f"  {r['address']} ({r['language']}): "
                  f"{'confirmed ' + r['confirmed'] if r['confirmed'] else 'not confirmed'}")


if __name__ == "__main__":
    main()
