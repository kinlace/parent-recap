"""Sends a Household's Brief from Parent Recap's own address (ADR 0028, #525).

The program posts the Brief to POST /v1/brief with its Gateway Key in `x-api-key`, as its model
calls do. The key must be an active Bifrost virtual key, looked up on Bifrost's admin API on
localhost, so revoking a key in Bifrost stops both. Each key may send `BRIEF_DAILY_CAP` messages a
UTC day, to at most MAX_RECIPIENTS each, so a leaked key can't use up Email Delivery's 100 a day
for everyone or become a spam relay. The message goes to OCI Email Delivery over SMTP and nothing
of it is kept: the log has the key's name, the number of recipients, the size and the outcome.

Python's standard library only, so it runs on the host's own python3 with nothing to install.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re
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

FROM_ADDR = "brief@parentrecap.com"
FROM = f"Parent Recap <{FROM_ADDR}>"
MAX_RECIPIENTS = 10
MAX_ATTACHMENTS = 5
MAX_REQUEST_BYTES = 2_000_000
# The unit doesn't restart on this exit: a missing setting won't appear by trying again.
EX_CONFIG = 78
# Bifrost's virtual-key list returns at most 100 keys a page.
PAGE = 100

# Strict on purpose: no display names, no commas, nothing a header could be split on.
ADDRESS = re.compile(r"[^@\s,;<>\"()\[\]\\]+@[^@\s,;<>\"()\[\]\\]+\.[^@\s,;<>\"()\[\]\\]+")
MIME_TYPE = re.compile(r"([a-z0-9][a-z0-9.+-]*)/([a-z0-9][a-z0-9.+-]*)")
FILENAME = re.compile(r"[^/\\\x00-\x1f\x7f]{1,100}")

log = logging.getLogger("brief-relay")


class Refused(Exception):
    """A request the relay answers with an error: `status` and a short reason for the program."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


# ── The Gateway Key

@dataclass(frozen=True)
class Key:
    id: str
    name: str


class Bifrost:
    """The Gateway Keys, as Bifrost's admin API lists its virtual keys."""

    def __init__(self, base: str, user: str, password: str,
                 fetch: Callable[[str], dict] | None = None) -> None:
        self.base = base.rstrip("/")
        self.auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        self.fetch = fetch or self._get

    def _get(self, path: str) -> dict:
        request = urllib.request.Request(self.base + path, headers={"Authorization": self.auth})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)

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
                 today: Callable[[], str] = lambda: datetime.now(UTC).date().isoformat()):
        self.path = path
        self.limit = limit
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

    def take(self, key: Key) -> bool:
        """Counts one message for `key`, or returns False if today's are used up."""
        with self.lock:
            sent = self._read()
            if sent.get(key.id, 0) >= self.limit:
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
    def __init__(self, keys: Bifrost, cap: DailyCap, mailer: Mailer, stop_url: str) -> None:
        self.keys, self.cap, self.mailer, self.stop_url = keys, cap, mailer, stop_url

    def handle(self, key_value: str | None, raw: bytes) -> tuple[int, dict]:
        """The status and JSON answer for one POST /v1/brief."""
        key = None
        recipients = 0
        try:
            key = self.keys.key(key_value) if key_value else None
            if key is None:
                raise Refused(401, "missing, unknown or revoked Gateway Key")
            if len(raw) > MAX_REQUEST_BYTES:
                raise Refused(413, f"the request is over {MAX_REQUEST_BYTES} bytes")
            try:
                body = json.loads(raw)
            except ValueError:
                raise Refused(400, "the body isn't JSON") from None
            msg, to = message(body, self.stop_url)
            recipients = len(to)
            if not self.cap.take(key):
                raise Refused(429, f"this key has sent its {self.cap.limit} Briefs for today")
            try:
                refused = self.mailer.send(msg, to)
            except Refused:
                self.cap.give_back(key)
                raise
        except Refused as e:
            self._log(key, recipients, raw, f"{e.status} {e.reason}")
            return e.status, {"error": e.reason}
        self._log(key, recipients, raw, "sent" + (f", {refused} refused" if refused else ""))
        return 200, {"sent": recipients - refused, "refused": refused}

    @staticmethod
    def _log(key: Key | None, recipients: int, raw: bytes, outcome: str) -> None:
        log.info("brief key=%s recipients=%d bytes=%d outcome=%s",
                 key.name if key else "-", recipients, len(raw), outcome)


# ── HTTP

def handler(relay: Relay) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "brief-relay"
        sys_version = ""

        def _answer(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            # Only for gateway-setup.sh's check on localhost; Caddy doesn't route it.
            if self.path == "/health":
                self._answer(200, {"status": "ok"})
            else:
                self._answer(404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path != "/v1/brief":
                return self._answer(404, {"error": "not found"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._answer(400, {"error": "bad Content-Length"})
            # A body over the limit isn't read: the key is checked on what's there, then 413.
            raw = self.rfile.read(min(length, MAX_REQUEST_BYTES + 1))
            self._answer(*relay.handle(self.headers.get("x-api-key"), raw))

        def log_message(self, *_args: Any) -> None:
            # The relay logs each request itself, without the client's address.
            pass

    return Handler


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    env = os.environ
    missing = [v for v in ("BRIEF_SMTP_USERNAME", "BRIEF_SMTP_PASSWORD", "BIFROST_ADMIN_USERNAME",
                           "BIFROST_ADMIN_PASSWORD", "GATEWAY_HOST") if not env.get(v)]
    if missing:
        print(f"brief-relay: {', '.join(missing)} not set (see ops/gateway/README.md)",
              file=sys.stderr)
        sys.exit(EX_CONFIG)
    relay = Relay(
        keys=Bifrost(env.get("BIFROST_URL", "http://127.0.0.1:8080"),
                     env["BIFROST_ADMIN_USERNAME"], env["BIFROST_ADMIN_PASSWORD"]),
        cap=DailyCap(Path(env.get("STATE_DIRECTORY", "/var/lib/brief-relay")) / "sent.json",
                     limit=int(env.get("BRIEF_DAILY_CAP", "4"))),
        mailer=Mailer(env.get("BRIEF_SMTP_HOST", "smtp.email.eu-stockholm-1.oci.oraclecloud.com"),
                      465, env["BRIEF_SMTP_USERNAME"], env["BRIEF_SMTP_PASSWORD"]),
        stop_url=f"https://{env['GATEWAY_HOST']}/brief/stop")
    port = int(env.get("BRIEF_RELAY_PORT", "8081"))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler(relay))
    log.info("brief-relay listening on 127.0.0.1:%d", port)
    server.serve_forever()


if __name__ == "__main__":
    main()
