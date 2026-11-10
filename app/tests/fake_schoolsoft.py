"""A fake SchoolSoft for the SchoolSoft capture tool (tools/schoolsoft-capture, #876): an HTTP
server on 127.0.0.1 that answers the guardian app's sign-in, the Eva API and the web session's
REST as the reference client's notes describe them, with made-up children, teachers, personal
numbers, phone numbers, email addresses and links. It records every request, so a test can
check each one is a read the tool lists, and it plays the guardian's browser too: given the
sign-in address, it sends the callback with a code, as SchoolSoft does after BankID.

`tool` is the capture tool's module, imported from its folder."""
from __future__ import annotations

import base64
import hashlib
import json
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

TOOL_DIR = Path(__file__).resolve().parents[2] / "tools" / "schoolsoft-capture"
sys.path.insert(0, str(TOOL_DIR))
import schoolsoft_capture as tool  # noqa: E402

SCHOOL = "academediagrundskolor"
USER_ID, ORG_ID = 4711, 88
ASTRID, OLLE = 1201, 1202
# Everything personal the fake's answers hold, which no output may.
NAMES = ["Karin", "Lindqvist", "Astrid", "Olle", "Johan", "Bergström", "Eva", "Nyberg", "Lars", "Holm"]
PERSONNUMMER = ["20150412-1234", "201204129876", "150412+1234"]
PHONES = ["070-123 45 67", "+46 8 123 456 78"]
EMAILS = ["karin.lindqvist@example.se", "eva.nyberg@skolan.example.se"]
LINKS = ["https://skolan.example.se/info", "www.example.se/anmalan", "https://sms.schoolsoft.se/academediagrundskolor/x"]


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def jwt(now: int = 1_800_000_000) -> str:
    claims = {"sub": str(uuid.uuid4()), "aud": "https://schoolsoft.se/eva-backend", "user_type": "PARENT",
              "login_method": "SAML", "client_id": "vApp", "iat": now, "exp": now + 900}
    return f"{b64(b'{}')}.{b64(json.dumps(claims).encode())}.{b64(uuid.uuid4().bytes)}"


def parent() -> dict:
    return {"guid": "0f1e2d3c-aaaa-bbbb-cccc-111122223333", "userId": USER_ID, "firstName": "Karin",
            "lastName": "Lindqvist", "children": [
                {"studentId": ASTRID, "firstName": "Astrid", "lastName": "Lindqvist", "picture": "https://x/p.jpg",
                 "guid": "c1", "username": "astlin15", "schools": [{"orgId": ORG_ID, "name": "Kunskapsskolan Norrort",
                                                                    "className": "3B"}]},
                {"studentId": OLLE, "firstName": "Olle", "lastName": "Lindqvist", "guid": "c2",
                 "schools": [{"orgId": ORG_ID, "name": "Kunskapsskolan Norrort", "className": "1A"}]}]}


NEWS = [
    {"id": 501, "title": "Utflykt på fredag", "description": "[b]Hej![/b] På fredag åker klassen till Skansen. "
     "Frågor? Ring Eva Nyberg på 070-123 45 67 eller mejla eva.nyberg@skolan.example.se. Anmälan: "
     "www.example.se/anmalan. Astrids grupp samlas kl 8.", "category": "Klass", "author": "Eva Nyberg",
     "read": False, "hasAttachment": False, "creDate": "2026-11-09 08:30", "toDate": "2026-11-14",
     "newsConfirm": False},
    {"id": 502, "title": "Föräldramöte", "description": "<p>Välkomna! Läs mer på "
     "<a href=\"https://skolan.example.se/info\">sidan</a>. Kontakt: Lars Holm, +46 8 123 456 78.</p>",
     "category": "Skola", "author": "Lars Holm", "read": True, "hasAttachment": True,
     "creDate": "2026-11-05 12:00", "toDate": None, "newsConfirm": True},
    {"id": 503, "title": "Lucia", "description": "Luciatåget är den 13 december.", "author": "Lars Holm",
     "read": False},
]
INBOX = [
    {"id": 9001, "subject": "Om Olle", "message": "Hej Karin! Olle har glömt sin jacka. /Johan Bergström",
     "isRead": False, "sender": {"id": 77, "firstName": "Johan", "lastName": "Bergström"},
     "date": "2026-11-08 16:02", "hasFiles": False},
    {"id": 9002, "subject": "Intyg", "message": "Personnummer 20150412-1234 behövs, eller 150412+1234.",
     "isRead": True, "sender": {"id": 78, "firstName": "Eva", "lastName": "Nyberg"},
     "date": "2026-11-01 09:00", "hasFiles": True},
]
MESSAGE = {**INBOX[1], "recipients": [{"id": USER_ID, "firstName": "Karin", "lastName": "Lindqvist"}],
           "attachments": [{"id": 3, "name": "intyg.pdf", "url": "https://sms.schoolsoft.se/academediagrundskolor/x"}],
           "body": "Skicka intyget till karin.lindqvist@example.se senast fredag. Personnr 201204129876."}
LESSONS = [{"eventId": 1, "name": "Svenska", "description": "", "startDate": "2026-11-09 08:15",
            "endDate": "2026-11-09 09:00", "room": "B204", "teachingGroup": "3B", "teacher": "Johan Bergström",
            "dayId": 1, "category": "lesson", "allDay": False}]
EVENTS = [{"eventId": 2, "name": "Studiedag", "description": "Skolan är stängd. Fritids är öppet.",
           "startDate": "2026-11-17", "endDate": "2026-11-17", "allDay": True, "category": "event"}]
ASSIGNMENTS = [
    {"id": 61, "activityId": 5, "title": "Läxa: multiplikation", "subTitle": "Matematik", "read": False,
     "submissionStatus": "NOT_SUBMITTED", "resultReportStatus": None, "sortDate": "2026-11-12"},
    {"id": 62, "activityId": 6, "title": "Läsläxa", "subTitle": "Svenska", "read": True,
     "submissionStatus": None, "resultReportStatus": None, "sortDate": "2026-11-13"},
]
ASSIGNMENT = {"id": 62, "title": "Läsläxa", "description": "Läs kapitel 3 med Olle hemma.",
              "teachers": ["Johan Bergström"], "startDate": "2026-11-09", "endDate": "2026-11-13"}
PLANNINGS = [{"id": 7, "title": "Höstens tema: rymden", "subject": "NO", "read": True}]
ACTIVITY = [{"blogPost": {"id": 900, "creDate": 1_762_700_000_000, "name": "Veckobrev v.46",
                          "description": "<p>Denna vecka har vi bakat med Eva Nyberg.</p>"},
             "author": "Eva Nyberg", "recipientsNamesString": "Karin Lindqvist, Lars Holm", "numberOfComments": 0,
             "content": [{"contentBlockDTOList": [{"blockType": "text", "contentBlocks": [
                 {"content": "Bilder: <img src=\"https://skolan.example.se/bild.jpg\"> Tack Lars!"}]}]}]}]


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, str]
    headers: Any  # as sent, read without regard to case
    body: Any


@dataclass
class FakeSchoolSoft:
    requests: list[Request] = field(default_factory=list)
    codes: dict[str, str] = field(default_factory=dict)      # a sign-in code: its PKCE challenge
    refresh_tokens: set[str] = field(default_factory=set)    # the ones still valid
    access_tokens: set[str] = field(default_factory=set)
    sessions: dict[str, int] = field(default_factory=dict)   # JSESSIONID: the child in focus
    refreshes_left: int = 1000                               # after these, every refresh fails
    used_refresh_tokens: set[str] = field(default_factory=set)
    issued: list[str] = field(default_factory=list)          # every token and cookie handed out
    callbacks: list[int] = field(default_factory=list)       # the callback's answers to the browser
    browsers: list[threading.Timer] = field(default_factory=list)

    def start(self) -> None:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                fake._answer(self, "GET")

            def do_POST(self) -> None:
                fake._answer(self, "POST")

            def do_PUT(self) -> None:
                fake._answer(self, "PUT")

            def do_DELETE(self) -> None:
                fake._answer(self, "DELETE")

            def log_message(self, *_a: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    # ── The guardian's browser

    def browser(self, address: str, delay: float = 0) -> None:
        """What the guardian's browser and BankID do with the sign-in address: SchoolSoft sends
        the browser to the callback with a code. Run on a thread, as the browser is another program."""
        url = urllib.parse.urlsplit(address)
        route, _, query = url.fragment.partition("?")
        assert route == "/login/parent", route
        q = dict(urllib.parse.parse_qsl(query))
        assert q["client_id"] == "vApp" and q["code_challenge_method"] == "S256" and q["response_type"] == "code"
        code = f"code-{uuid.uuid4().hex}"
        self.codes[code] = q["code_challenge"]
        self.issued.append(code)
        callback = f"{q['redirect_uri']}?{urllib.parse.urlencode({'code': code, 'state': q['state']})}"

        def go() -> None:
            try:
                with urllib.request.urlopen(callback, timeout=10) as r:
                    self.callbacks.append(r.status)
            except urllib.error.HTTPError as e:
                self.callbacks.append(e.code)
            except OSError:
                self.callbacks.append(-1)  # nothing listened any more
        thread = threading.Timer(delay, go)
        thread.daemon = True
        self.browsers.append(thread)
        thread.start()

    def browsers_done(self) -> list[int]:
        """The callback's answers, once each browser has had its answer."""
        for thread in self.browsers:
            thread.join(10)
        return sorted(self.callbacks)

    # ── SchoolSoft

    def _answer(self, h: BaseHTTPRequestHandler, method: str) -> None:
        url = urllib.parse.urlsplit(h.path)
        query = dict(urllib.parse.parse_qsl(url.query))
        length = int(h.headers.get("Content-Length") or 0)
        raw = h.rfile.read(length) if length else b""
        body = json.loads(raw) if raw else None
        self.requests.append(Request(method, url.path, query, h.headers, body))
        prefix = f"/{SCHOOL}"
        if not url.path.startswith(prefix + "/"):
            return self._send(h, 404, {"userMessage": "Okänd skola"})
        path = url.path[len(prefix):]
        status, data, headers = self._route(method, path, query, h.headers, body)
        self._send(h, status, data, headers)

    def _route(self, method: str, path: str, q: dict, headers: Any, body: Any) -> tuple[int, Any, dict]:
        bearer = (headers.get("Authorization") or "").removeprefix("Bearer ")
        authorised = bearer in self.access_tokens
        session = self._session(headers.get("Cookie") or "")
        if method == "GET" and path == "/rest-api/login/methods/":
            return 200, [{"name": "SAML", "value": 3}], {}
        if method == "POST" and path == "/rest-api/login/token":
            return self._token(q)
        if method == "GET" and path == "/eva-apps/auth/login/parent":
            if headers.get("token") not in self.access_tokens or not headers.get("childInFocus"):
                return 303, None, {"Location": "/x?error=other"}
            jsession, hash_ = uuid.uuid4().hex, uuid.uuid4().hex
            self.sessions[jsession] = int(headers["childInFocus"])
            self.issued += [jsession, hash_]
            return 303, None, {"Location": "/react/#/parent/start", "Set-Cookie": [
                f"JSESSIONID={jsession}; Path=/; HttpOnly", f"hash={hash_}; Path=/", "usertype=2; Path=/",
                "clientType=app; Path=/"]}
        eva = {
            "/eva/api/v1/parent": parent(),
            f"/eva/api/v2/parent/{USER_ID}/schools/{ORG_ID}/news": NEWS,
            f"/eva/api/v1/parent/{USER_ID}/schools/{ORG_ID}/messages/inbox": INBOX,
            f"/eva/api/v1/parent/{USER_ID}/schools/{ORG_ID}/messages/9002": MESSAGE,
            f"/eva/api/v1/parent/{USER_ID}/schools/{ORG_ID}/news/calendarevent/next": EVENTS[0],
        }
        if method == "GET" and path in eva:
            return (200, eva[path], {}) if authorised else (401, {"userMessage": "Ej inloggad"}, {})
        web = {
            "/rest-api/parent/calendar/lessons/agenda": LESSONS,
            "/rest-api/parent/calendar/event/agenda": EVENTS,
            "/rest-api/parent/ps/assignments/start-page": ASSIGNMENTS,
            "/rest-api/parent/ps/assignments/62/view": ASSIGNMENT,
            "/rest-api/parent/ps/planning_parts/start-page": PLANNINGS,
        }
        if path.startswith("/rest-api/parent/calendar/lessons/week/"):
            web[path] = LESSONS
        if (method, path) == ("POST", "/rest/blogpost/getbyloggedinuser"):
            return (200, ACTIVITY, {}) if session else (401, None, {})
        if method == "GET" and path in web:
            return (200, web[path], {}) if session else (302, None, {"Location": "/jsp/Login.jsp"})
        return 404, {"userMessage": "Not found"}, {}

    def _session(self, cookie: str) -> int | None:
        jar = dict(part.strip().split("=", 1) for part in cookie.split(";") if "=" in part)
        return self.sessions.get(jar.get("JSESSIONID", ""))

    def _token(self, q: dict) -> tuple[int, Any, dict]:
        if q.get("grantType") == "code":
            challenge = self.codes.pop(q.get("code", ""), None)
            verifier = q.get("codeVerifier", "")
            if challenge is None or b64(hashlib.sha256(verifier.encode()).digest()) != challenge:
                return 404, {"userMessage": "Ingen aktiv inloggnings-session"}, {}
        elif q.get("grantType") == "refresh_token":
            token = q.get("refreshToken", "")
            if token not in self.refresh_tokens or self.refreshes_left <= 0:
                return 401, {"userMessage": "Sessionen har gått ut"}, {}
            self.refreshes_left -= 1
            self.refresh_tokens.discard(token)  # it rotates: the old one is used up
            self.used_refresh_tokens.add(token)
        else:
            return 400, None, {}
        access, refresh = jwt(), f"rt.{uuid.uuid4().hex}{uuid.uuid4().hex}"
        self.access_tokens.add(access)
        self.refresh_tokens.add(refresh)
        self.issued += [access, refresh]
        return 200, {"access_token": access, "refresh_token": refresh}, {}

    @staticmethod
    def _send(h: BaseHTTPRequestHandler, status: int, data: Any, headers: dict | None = None) -> None:
        body = json.dumps(data).encode() if data is not None else b""
        h.send_response(status)
        for name, value in (headers or {}).items():
            for v in value if isinstance(value, list) else [value]:
                h.send_header(name, v)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)


class FakeKeychain:
    """The Keychain item, in memory, with each change in order."""

    def __init__(self) -> None:
        self.token: str | None = None
        self.changes: list[str] = []
        self.refuse = False

    def get(self) -> str | None:
        return self.token

    def put(self, token: str) -> None:
        if self.refuse:
            raise OSError("the Keychain is locked")
        self.token = token
        self.changes.append("put")

    def delete(self) -> None:
        self.token = None
        self.changes.append("delete")


class FakeAgent:
    def __init__(self) -> None:
        self.installed = False

    def install(self, folder: Path) -> None:
        self.installed = True

    def remove(self) -> None:
        self.installed = False
