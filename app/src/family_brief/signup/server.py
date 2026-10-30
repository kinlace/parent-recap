"""The sign-up service's HTTP side (ADR 0031): `GET /join/<code>` shows the page under a valid
Invite Link, `POST /join/<code>` signs the Household up, and anything else, a code that isn't
good any more included, gets the gateway's web server's usual 404, so the service says nothing
of which links exist.

Signing up checks the Wilma sign-in before anything is made, so a wrong password is said at once
and the link stays good. Then the Household is made through the household host: its Linux user,
its Gateway Key, the program installed under it, and `signup household` run as it with the
answers, the Wilma sign-in and the key on its stdin. A step that fails takes back what the ones
before it made, and the link stays good. Once made, the Household is listed in the store, its
link retired, and the page says when its first Brief comes.

Sign-ups go one at a time: each takes a minute or two, which ten Households can wait, and so two
never take the same evening slot or the same link.

The service logs one line per sign-up, saying how it ended, and nothing of the request: not its
address, which carries the code, nor its body, which carries the Wilma password.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import socketserver
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

from .. import failure, setup_wilma
from . import page
from .host import HostError, HouseholdHost, user_name
from .invites import LANGUAGES, HouseholdRecord, Recipient, Store

log = logging.getLogger("family_brief.signup")

PREFIX = "/join/"
NOT_FOUND = b"Not found"  # as the Caddyfile's `respond "Not found" 404`
HEALTH = "/health"  # for gateway-setup.sh's check on localhost; Caddy sends only /join/ here
MAX_BODY = 16 * 1024
TIMEZONE = "Europe/Helsinki"  # every Hosted Household's for now: they are invited in Finland
# The Households' evening jobs, one slot each in turn, so ten runs don't all start at once on a
# server the gateway shares (ADR 0029).
SLOTS = tuple(f"21:{minute:02d}" for minute in range(0, 60, 5))
INSTALL_SECONDS = 900  # install.sh downloads Python and Node and installs the packages
HOUSEHOLD_SECONDS = 600  # `signup household` installs the wilma CLI with npm
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
    release: Path  # the unpacked release whose install.sh installs the program for a Household
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._towns: dict[str, tuple[dict[str, Any], str]] | None = None

    # ---------------------------------------------------------------- the page

    def show(self, code: str, language: str | None) -> tuple[HTTPStatus, str | None]:
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

    def sign_up(self, code: str, fields: dict[str, str]) -> tuple[HTTPStatus, str | None]:
        with self._lock:
            invite = self.store.valid(code, self.now())
            if invite is None:
                return HTTPStatus.NOT_FOUND, None
            language = fields.get("lang") if fields.get("lang") in LANGUAGES else invite.language
            typed = page.Typed(fields.get("town", "").strip(), fields.get("username", "").strip(),
                               _recipients(fields, language))
            password = fields.get("password", "")

            def again(error: str, status: HTTPStatus = HTTPStatus.OK) -> tuple[HTTPStatus, str]:
                log.info("A sign-up wasn't taken: %s", error)
                return status, page.form(language, typed, list(self.towns()), error)
            picked = self._picked(typed.town)
            if picked is None:
                return again("invalid-town", HTTPStatus.BAD_REQUEST)
            if not typed.username or not password:
                return again("invalid-sign-in", HTTPStatus.BAD_REQUEST)
            if not typed.recipients or any(not EMAIL.fullmatch(r.address)
                                           for r in typed.recipients):
                return again("invalid-recipients", HTTPStatus.BAD_REQUEST)
            tenant, town = picked
            checked, students = setup_wilma.check(tenant, typed.username, password)
            if checked["result"] in ("wrong-password", "no-kids"):
                return again(checked["result"])
            if checked["result"] != "signed-in":
                log.warning("Wilma couldn't be signed in to: %s",
                            checked.get("reason") or checked["result"])
                return again("sign-in-failed")

            name = user_name()
            evening = _evening(self.store.households())
            answers = {
                "language": language, "city": town, "evening": evening,
                "recipients": [{"address": r.address, "language": r.language}
                               for r in typed.recipients],
                "kids": [{"name": k["name"],
                          **({"school": k["school"]} if k.get("school") else {}),
                          **({"class_name": k["class"]} if k.get("class") else {})}
                         for k in checked["kids"]],
                "wilma": {"tenant": {"url": tenant["url"], "name": tenant["name"]},
                          "username": typed.username, "password": password,
                          "students": students},
            }
            try:
                self._create(name, answers)
            except Exception as e:  # noqa: BLE001 - whatever it was, the family hears and it's undone
                log.error("Household %s wasn't created, and what was made is taken back: %s",
                          name, failure.raised(e, "Signing up"))
                self._take_back(name)
                return HTTPStatus.INTERNAL_SERVER_ERROR, page.failed(language)
            self.store.add_household(HouseholdRecord(name, evening, self.now().isoformat()))
            self.store.retire(code)
            day = _first_day(self.now(), evening)
            log.info("Household %s created from an Invite Link; its Brief comes at %s",
                     name, evening)
            return HTTPStatus.OK, page.done(language, day, evening)

    def _create(self, name: str, answers: dict[str, Any]) -> None:
        home = self.host.create_user(name)
        key = self.host.mint_key(name)
        code, out = self.host.run_as(name, ["bash", str(self.release / "install.sh")], "",
                                     INSTALL_SECONDS)
        if code != 0:
            raise HostError(f"install.sh ended with {code}: {out.strip()[-300:]}")
        program = home / "ParentRecap" / "app" / ".venv" / "bin" / "parent-recap"
        code, out = self.host.run_as(name, [str(program), "signup", "household"],
                                     json.dumps({**answers, "gateway_key": key}),
                                     HOUSEHOLD_SECONDS)
        said = _last_json(out)
        if code != 0 or said.get("result") != "created":
            raise HostError(f"signup household said {said.get('result', code)}"
                            + (f": {said['reason']}" if said.get("reason") else ""))

    def _take_back(self, name: str) -> None:
        for undo in (self.host.revoke_key, self.host.remove_user):
            try:
                undo(name)
            except Exception as e:  # noqa: BLE001 - the other is still taken back
                log.error("%s couldn't be taken back for %s: %s", undo.__name__, name,
                          failure.raised(e, "Taking back"))


def _recipients(fields: dict[str, str], language: str) -> list[Recipient]:
    out = []
    for i in range(page.MAX_RECIPIENTS):
        address = fields.get(f"r{i}", "").strip()
        chosen = fields.get(f"r{i}_language")
        if address:
            out.append(Recipient(address, chosen if chosen in LANGUAGES else language))
    return out


def _evening(households: list[HouseholdRecord]) -> str:
    """The slot fewest Households' Briefs come at, the earliest of those."""
    taken = [h.evening for h in households]
    return min(SLOTS, key=lambda slot: (taken.count(slot), SLOTS.index(slot)))


def _first_day(now: datetime, evening: str) -> str:
    """Whether the first Brief comes this evening or tomorrow, in the Household's time."""
    local = now.astimezone(ZoneInfo(TIMEZONE))
    hour, minute = map(int, evening.split(":"))
    return "today" if (local.hour, local.minute) < (hour, minute) else "tomorrow"


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
        self._send(status, html.encode(), "text/html; charset=utf-8")

    def _read_body(self) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length < 0 or length > MAX_BODY:
            return None
        return self.rfile.read(length)

    def _send(self, status: HTTPStatus, body: bytes, kind: str) -> None:
        self.send_response(status)
        for name, value in {**HEADERS, "Content-Type": kind,
                            "Content-Length": str(len(body))}.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


def _fields(body: bytes) -> dict[str, str]:
    try:
        parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=40)
    except (UnicodeDecodeError, ValueError):
        return {}
    return {k: v[0] for k, v in parsed.items()}
