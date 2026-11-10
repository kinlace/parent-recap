"""SchoolSoft capture (#876): what SchoolSoft shows a Swedish guardian, and how long its sign-in
lasts, learned on the guardian's own Mac without anyone else holding their account.

`capture` signs in the way SchoolSoft's guardian app does (OAuth with PKCE, client vApp, a
127.0.0.1 callback; the guardian does Extern inloggning and BankID in their own browser), reads
each child's news, messages, calendar, assignments, plannings and activity log, and writes one
file with the field structure and a few samples, names, personal numbers, phone numbers, email
addresses and links replaced with placeholders. Nothing is sent anywhere but SchoolSoft.

If the guardian agrees, the refresh token goes into the Keychain and `refresh`, run by a launchd
job every evening for 14 days, renews it and logs only the date, whether it worked and the
token's age. Each new refresh token is saved before anything else, since it changes on every
use, and a lock keeps two refreshes from ever running at once.

Read-only: every request is one of ENDPOINTS, all GETs but the three READ_ONLY_POSTS, and
redirects are never followed (SchoolSoft's login page ends the session it is loaded with). It
runs on the Python get.sh downloads, with the standard library and Parent Recap's AI filter only.

The sign-in flow and the endpoints follow grimen/schoolsoft-agent's notes and code (MIT,
Copyright (c) 2026 Jonas Grimfelt), which were verified on a guardian account; see
THIRD_PARTY_NOTICES.md."""
from __future__ import annotations

import argparse
import base64
import contextlib
import fcntl
import hashlib
import html
import http.server
import json
import os
import plistlib
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

try:  # in the repository's tests; on a guardian's Mac get.sh puts ai_filter.py beside this file
    from family_brief import ai_filter
except ImportError:  # pragma: no cover
    import ai_filter  # type: ignore[no-redef]

VERSION = 1
HOST = "sms.schoolsoft.se"
BASE = f"https://{HOST}"  # the only place this program sends anything
CLIENT_ID = "vApp"  # the guardian app's: eApp, the student app's, gets a student's token
DEFAULT_SCHOOL = "academediagrundskolor"
USER_AGENT = "parent-recap-schoolsoft-capture/1 (+https://github.com/kinlace/parent-recap)"
PORTS = (43117, 0)  # the callback's port: the one SchoolSoft was seen to accept, else any free one
SIGN_IN_SECONDS = 600
DAYS = 14  # how many evenings the refresh is checked
EVENING = {"Hour": 20, "Minute": 15}
FOLDER = Path.home() / "SchoolSoftCapture"
OUTPUT = "schoolsoft-capture.json"
LOG = "refresh-log.txt"
STATE = "refresh-state.json"
LOCK = ".refresh.lock"
LABEL = "com.kinlace.schoolsoft-capture"
KEYCHAIN_SERVICE = "schoolsoft-capture"
KEYCHAIN_ACCOUNT = "refresh-token"
SECURITY = "/usr/bin/security"
SAMPLES = 2  # items kept of each list
TEXT_LIMIT = 600  # characters kept of each text


# ── What it may send

@dataclass(frozen=True)
class Endpoint:
    """One request this program can make: its method, its path after /<school>, with {name} for
    a number filled in, and the names of its query parameters."""
    method: str
    path: str
    query: tuple[str, ...] = ()


ENDPOINTS = {
    "login_methods": Endpoint("GET", "/rest-api/login/methods/", ("client_id", "usertype")),
    "token_code": Endpoint("POST", "/rest-api/login/token", ("clientId", "grantType", "code", "codeVerifier")),
    "token_refresh": Endpoint("POST", "/rest-api/login/token", ("clientId", "grantType", "refreshToken")),
    "parent": Endpoint("GET", "/eva/api/v1/parent"),
    "news": Endpoint("GET", "/eva/api/v2/parent/{userId}/schools/{orgId}/news", ("studentId", "langId")),
    "inbox": Endpoint("GET", "/eva/api/v1/parent/{userId}/schools/{orgId}/messages/inbox"),
    "message": Endpoint("GET", "/eva/api/v1/parent/{userId}/schools/{orgId}/messages/{messageId}"),
    "next_event": Endpoint("GET", "/eva/api/v1/parent/{userId}/schools/{orgId}/news/calendarevent/next",
                           ("studentId",)),
    "session_cookies": Endpoint("GET", "/eva-apps/auth/login/parent"),
    "lessons_week": Endpoint("GET", "/rest-api/parent/calendar/lessons/week/{week}"),
    "lessons_agenda": Endpoint("GET", "/rest-api/parent/calendar/lessons/agenda", ("start_date", "end_date")),
    "events_agenda": Endpoint("GET", "/rest-api/parent/calendar/event/agenda", ("start_date", "end_date")),
    "assignments": Endpoint("GET", "/rest-api/parent/ps/assignments/start-page", ("week", "year")),
    "assignment": Endpoint("GET", "/rest-api/parent/ps/assignments/{assignmentId}/view"),
    "plannings": Endpoint("GET", "/rest-api/parent/ps/planning_parts/start-page"),
    "activity_log": Endpoint("POST", "/rest/blogpost/getbyloggedinuser"),
}
# The only requests that aren't GETs, and why each changes nothing in SchoolSoft.
READ_ONLY_POSTS = {
    "token_code": "swaps the sign-in's one-time code for tokens, as the guardian app does",
    "token_refresh": "renews the sign-in, as the guardian app does",
    "activity_log": "the activity log page asks for its list with this POST, a filter, not a change",
}
# The activity log's filter, as SchoolSoft's own page sends it: everything, newest first.
ACTIVITY_FILTER = {"userId": -1, "userType": -1, "week": -1, "subjects": [], "archives": [], "tags": [],
                   "freeText": "", "goalIds": [], "groupOrStudent": "", "offset": 0, "row_count": 10}


class Unreachable(Exception):
    """SchoolSoft didn't answer at all."""


@dataclass
class Response:
    status: int
    headers: Any
    body: bytes

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None

    def cookies(self) -> dict[str, str]:
        found: dict[str, str] = {}
        for line in self.headers.get_all("Set-Cookie") or [] if self.headers else []:
            name, _, rest = line.strip().partition("=")
            found.setdefault(name, rest.split(";")[0])
        return found


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_a: Any, **_k: Any) -> None:
        return None  # a 3xx comes back as an HTTPError, which _send turns into a Response


class Http:
    """Every request this program makes goes through call(), and is one of ENDPOINTS."""

    def __init__(self, hidden: Hidden, base: str = BASE, timeout: float = 30):
        self.base = base
        self.hidden = hidden
        self.timeout = timeout
        self.opener = urllib.request.build_opener(_NoRedirects)
        self.sent: list[str] = []  # the endpoints called, in order

    def call(self, name: str, school: str, *, ids: dict[str, int] | None = None,
             query: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
             body: Any = None) -> Response:
        endpoint = ENDPOINTS[name]
        if endpoint.method != "GET" and name not in READ_ONLY_POSTS:
            raise ValueError(f"{name} would change something in SchoolSoft")
        if not valid_school(school):
            raise ValueError("not a SchoolSoft address name")
        path = re.sub(r"\{(\w+)\}", lambda m: str(int((ids or {})[m.group(1)])), endpoint.path)
        query = query or {}
        if set(query) != set(endpoint.query):
            raise ValueError(f"{name} takes {endpoint.query}, not {tuple(query)}")
        url = f"{self.base}/{school}{path}" + (f"?{urllib.parse.urlencode(query)}" if query else "")
        data = json.dumps(body).encode() if body is not None else None
        self.sent.append(name)
        return self._send(endpoint.method, url, headers or {}, data)

    def _send(self, method: str, url: str, headers: dict[str, str], data: bytes | None) -> Response:
        request = urllib.request.Request(url, data=data, method=method, headers={
            "Accept": "application/json", "User-Agent": USER_AGENT,
            **({"Content-Type": "application/json;charset=UTF-8"} if data is not None else {}), **headers})
        try:
            with self.opener.open(request, timeout=self.timeout) as r:
                return Response(r.status, r.headers, r.read())
        except urllib.error.HTTPError as e:
            return Response(e.code, e.headers, e.read() if e.fp else b"")
        except (urllib.error.URLError, OSError) as e:
            raise Unreachable(self.hidden.scrub(str(getattr(e, "reason", e)))) from None


def valid_school(school: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", school))


def school_name(answer: str) -> str:
    """The name in a SchoolSoft address as the guardian types or pastes it."""
    answer = answer.strip()
    m = re.search(r"sms\.schoolsoft\.se/([^/?#\s]+)", answer)
    return (m.group(1) if m else answer.strip("/")).lower()


class Hidden:
    """The tokens, codes and cookies of this run, which never reach the terminal, a log or a file."""

    def __init__(self) -> None:
        self.values: set[str] = set()

    def add(self, *values: Any) -> None:
        self.values |= {v for v in values if isinstance(v, str) and len(v) >= 6}

    def scrub(self, text: str) -> str:
        for value in sorted(self.values, key=len, reverse=True):
            text = text.replace(value, "[hidden]")
        return text


# ── The Mac: Keychain, launchd, browser

class Keychain:
    """The refresh token, as one Keychain item that trusts /usr/bin/security, so the evening job
    reads it without macOS asking (as Parent Recap's own items do, ADR 0009). The token is typed
    into `security -i`, so it is on no command line, where `ps` would show it."""

    def __init__(self, run: Callable[..., subprocess.CompletedProcess] = subprocess.run, security: str = SECURITY):
        self.run = run
        self.security = security

    def _item(self, verb: str) -> list[str]:
        return [self.security, verb, "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT]

    def get(self) -> str | None:
        r = self.run(self._item("find-generic-password") + ["-w"], capture_output=True, text=True,
                     encoding="utf-8", timeout=10)
        return r.stdout.strip() or None if r.returncode == 0 else None

    def put(self, token: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", token):
            raise ValueError("SchoolSoft's token has characters a Keychain command can't take")
        command = (f"add-generic-password -U -s {KEYCHAIN_SERVICE} -a {KEYCHAIN_ACCOUNT} "
                   f"-w \"{token}\" -T {self.security}\n")
        self.run([self.security, "-i"], input=command, capture_output=True, text=True, encoding="utf-8",
                 timeout=60)
        if self.get() != token:
            raise OSError("the Keychain didn't keep the sign-in")

    def delete(self) -> None:
        self.run(self._item("delete-generic-password"), capture_output=True, timeout=30)


class LaunchAgent:
    """The evening check, as a launchd job of this Mac user's."""

    def __init__(self, run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
                 agents: Path = Path.home() / "Library" / "LaunchAgents"):
        self.run = run
        self.path = agents / f"{LABEL}.plist"
        self.target = f"gui/{os.getuid()}"

    def install(self, folder: Path) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(plistlib.dumps({
            "Label": LABEL,
            "ProgramArguments": [sys.executable, "-E", "-s", str(Path(__file__).resolve()), "refresh"],
            "StartCalendarInterval": EVENING,
            "StandardOutPath": str(folder / "refresh-output.txt"),
            "StandardErrorPath": str(folder / "refresh-output.txt"),
            "Umask": 0o077,
            "RunAtLoad": False,
        }))
        self.run(["launchctl", "bootout", f"{self.target}/{LABEL}"], capture_output=True)
        self.run(["launchctl", "bootstrap", self.target, str(self.path)], capture_output=True, check=True)

    def remove(self) -> None:
        """Last, when the job removes itself: launchd ends the job's process with it."""
        self.path.unlink(missing_ok=True)
        self.run(["launchctl", "bootout", f"{self.target}/{LABEL}"], capture_output=True)


def open_in_browser(url: str) -> None:
    subprocess.run(["/usr/bin/open", url], capture_output=True)


def open_file(path: Path) -> None:
    subprocess.run(["/usr/bin/open", "-e", str(path)], capture_output=True)  # TextEdit


def mac_language() -> str:
    try:
        said = subprocess.run(["/usr/bin/defaults", "read", "-g", "AppleLanguages"], capture_output=True,
                              text=True, encoding="utf-8", timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        said = ""
    first = re.search(r'"?([A-Za-z]{2})', said.strip("( \n")) or re.match(r"([a-z]{2})", os.environ.get("LANG", ""))
    return first.group(1).lower() if first and first.group(1).lower() in TEXT["intro"] else "en"


# ── What the guardian reads, in Swedish, English and Chinese

TEXT: dict[str, dict[str, str]] = {
    "intro": {
        "en": "This reads what SchoolSoft shows you about your children, so the Parent Recap team can learn "
              "how it is written. It only reads: it changes nothing in SchoolSoft and marks nothing as read. "
              "Nothing is sent to us: you get one file to look at, and you decide.",
        "sv": "Det här läser det SchoolSoft visar dig om dina barn, så att Parent Recap-teamet kan se hur det "
              "är uppbyggt. Det läser bara: inget ändras i SchoolSoft och inget markeras som läst. Inget skickas "
              "till oss: du får en fil att titta på, och du bestämmer.",
        "zh": "这个工具读取 SchoolSoft 里关于你孩子的内容，让 Parent Recap 团队了解它的格式。它只读不写："
              "不改动 SchoolSoft 里的任何东西，也不会把任何内容标成已读。什么都不会发给我们：你会得到一个文件，"
              "看过后由你决定。",
    },
    "ask_school": {
        "en": "Your school's SchoolSoft address is sms.schoolsoft.se/<name>/. Type the name, or press Enter "
              "for {default}: ",
        "sv": "Skolans SchoolSoft-adress är sms.schoolsoft.se/<namn>/. Skriv namnet, eller tryck på Enter "
              "för {default}: ",
        "zh": "学校的 SchoolSoft 地址是 sms.schoolsoft.se/<名称>/。输入名称，或直接按回车使用 {default}：",
    },
    "bad_school": {
        "en": "That isn't the name in a SchoolSoft address. Run the command again.",
        "sv": "Det är inte namnet i en SchoolSoft-adress. Kör kommandot igen.",
        "zh": "这不是 SchoolSoft 地址里的名称。请重新运行命令。",
    },
    "unknown_school": {
        "en": "SchoolSoft has no {school}. Check the address and run the command again.",
        "sv": "SchoolSoft har ingen {school}. Kontrollera adressen och kör kommandot igen.",
        "zh": "SchoolSoft 上没有 {school}。请检查地址后重新运行命令。",
    },
    "browser": {
        "en": "Your browser opens SchoolSoft now. Choose Extern inloggning, then Mobilt BankID, and confirm "
              "on your phone. If no browser opens, paste this address into one:",
        "sv": "Webbläsaren öppnar SchoolSoft nu. Välj Extern inloggning, sedan Mobilt BankID, och bekräfta "
              "i telefonen. Om ingen webbläsare öppnas, klistra in den här adressen i en:",
        "zh": "浏览器现在会打开 SchoolSoft。选择 Extern inloggning，再选 Mobilt BankID，然后在手机上确认。"
              "如果浏览器没有打开，把这个地址粘贴进去：",
    },
    "waiting": {
        "en": "Waiting for the sign-in (up to 10 minutes)…",
        "sv": "Väntar på inloggningen (högst 10 minuter)…",
        "zh": "正在等待登录（最多 10 分钟）……",
    },
    "page_done": {
        "en": "Signed in. You can close this tab and go back to Terminal.",
        "sv": "Inloggad. Du kan stänga fliken och gå tillbaka till Terminal.",
        "zh": "已登录。可以关闭这个标签页，回到终端。",
    },
    "page_wrong": {
        "en": "This sign-in isn't the one Terminal is waiting for. Run the command again.",
        "sv": "Den här inloggningen är inte den som Terminal väntar på. Kör kommandot igen.",
        "zh": "这次登录不是终端正在等待的那一次。请重新运行命令。",
    },
    "timeout": {
        "en": "No sign-in arrived within 10 minutes. Run the command again.",
        "sv": "Ingen inloggning kom inom 10 minuter. Kör kommandot igen.",
        "zh": "10 分钟内没有完成登录。请重新运行命令。",
    },
    "sign_in_failed": {
        "en": "SchoolSoft didn't finish the sign-in ({why}). Run the command again.",
        "sv": "SchoolSoft slutförde inte inloggningen ({why}). Kör kommandot igen.",
        "zh": "SchoolSoft 没有完成登录（{why}）。请重新运行命令。",
    },
    "reading": {
        "en": "Signed in. Reading…",
        "sv": "Inloggad. Läser…",
        "zh": "已登录，正在读取……",
    },
    "unreachable": {
        "en": "SchoolSoft couldn't be reached. Check the internet connection and run the command again.",
        "sv": "Det gick inte att nå SchoolSoft. Kontrollera internetanslutningen och kör kommandot igen.",
        "zh": "连不上 SchoolSoft。请检查网络后重新运行命令。",
    },
    "ask_check": {
        "en": "One more thing, if you agree: may this Mac check every evening for 14 days whether the sign-in "
              "still works? It only renews the sign-in and reads nothing. Its log has only the date, whether "
              "it worked and the sign-in's age. It stops by itself after 14 days. [y/N] ",
        "sv": "En sak till, om du vill: får den här Macen kontrollera varje kväll i 14 dagar om inloggningen "
              "fortfarande fungerar? Den förnyar bara inloggningen och läser inget. Loggen har bara datum, om "
              "det fungerade och inloggningens ålder. Den slutar av sig själv efter 14 dagar. [j/N] ",
        "zh": "还有一件事，如果你同意：这台 Mac 能否在接下来 14 天每天晚上检查一次登录是否还有效？它只续期登录，"
              "不读取任何内容。记录里只有日期、是否成功和登录已经多久。14 天后自动停止。[y/N] ",
    },
    "check_on": {
        "en": "The evening check is on. After 14 days, please send its log too: {log}",
        "sv": "Kvällskontrollen är på. Skicka gärna dess logg också efter 14 dagar: {log}",
        "zh": "晚间检查已开启。14 天后请把它的记录也发给我们：{log}",
    },
    "check_off": {
        "en": "No evening check. The sign-in wasn't saved.",
        "sv": "Ingen kvällskontroll. Inloggningen sparades inte.",
        "zh": "不做晚间检查，登录信息没有保存。",
    },
    "check_failed": {
        "en": "The evening check couldn't be turned on ({why}). The file is still fine to send.",
        "sv": "Kvällskontrollen gick inte att slå på ({why}). Filen går ändå bra att skicka.",
        "zh": "晚间检查没能开启（{why}）。文件仍然可以发送。",
    },
    "done": {
        "en": "Done. The file is {file}, and it opens now. Names, personal numbers, phone numbers, email "
              "addresses and links are replaced with placeholders such as ⟦N1⟧. Look it over, and if you're "
              "happy with it, send it to the Parent Recap team.",
        "sv": "Klart. Filen är {file} och öppnas nu. Namn, personnummer, telefonnummer, e-postadresser och "
              "länkar är utbytta mot platshållare som ⟦N1⟧. Titta igenom den, och om du är nöjd, skicka den "
              "till Parent Recap-teamet.",
        "zh": "完成。文件在 {file}，现在会打开。名字、个人身份号码、电话、邮箱和链接都已换成 ⟦N1⟧ 这样的占位符。"
              "请先看一遍，觉得没问题再发给 Parent Recap 团队。",
    },
    "remove": {
        "en": "To remove everything this stored (the sign-in, the evening check, the files), paste: {command}",
        "sv": "För att ta bort allt som sparades (inloggningen, kvällskontrollen, filerna), klistra in: {command}",
        "zh": "要删除它保存的所有内容（登录、晚间检查、文件），粘贴：{command}",
    },
}
YES = {"y", "yes", "j", "ja", "是", "好", "要"}
REMOVE = "~/SchoolSoftCapture/remove"


# ── The run's world, so the tests can fake its edges

@dataclass
class Context:
    folder: Path = FOLDER
    base: str = BASE
    language: str = "en"
    keychain: Keychain = field(default_factory=Keychain)
    agent: LaunchAgent = field(default_factory=LaunchAgent)
    browser: Callable[[str], None] = open_in_browser
    show_file: Callable[[Path], None] = open_file
    ask: Callable[[str], str] = input
    say: Callable[[str], None] = print
    now: Callable[[], datetime] = datetime.now
    sign_in_seconds: float = SIGN_IN_SECONDS
    hidden: Hidden = field(default_factory=Hidden)
    http: Http | None = None

    def __post_init__(self) -> None:
        self.http = self.http or Http(self.hidden, self.base)

    def text(self, key: str, **values: Any) -> str:
        return TEXT[key].get(self.language, TEXT[key]["en"]).format(**values)

    def tell(self, key: str, **values: Any) -> None:
        self.say(self.hidden.scrub(self.text(key, **values)))


class Stop(Exception):
    """The capture can't go on; the guardian has been told why."""


# ── The sign-in

def pkce() -> tuple[str, str]:
    """RFC 7636: a random verifier and its S256 challenge, base64url without padding."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def sign_in_address(base: str, school: str, challenge: str, state: str, redirect_uri: str) -> str:
    query = urllib.parse.urlencode({"client_id": CLIENT_ID, "response_type": "code", "code_challenge": challenge,
                                    "code_challenge_method": "S256", "state": state, "redirect_uri": redirect_uri})
    return f"{base}/{school}/react/#/login/parent?{query}"


class Callback:
    """The one-shot page on 127.0.0.1 that SchoolSoft sends the browser back to with the code."""

    def __init__(self, ctx: Context, state: str):
        self.ctx, self.state = ctx, state
        self.code: str | None = None
        self.error: str | None = None
        callback = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                url = urllib.parse.urlsplit(self.path)
                if url.path != "/callback":
                    return self._page(404, "")
                got = urllib.parse.parse_qs(url.query)
                if got.get("state") != [callback.state]:
                    return self._page(400, ctx.text("page_wrong"))
                if got.get("code"):
                    callback.code = got["code"][0]
                    ctx.hidden.add(callback.code)
                    return self._page(200, ctx.text("page_done"))
                callback.error = (got.get("error") or ["no code"])[0][:80]
                return self._page(400, ctx.text("sign_in_failed", why=html.escape(callback.error)))

            def _page(self, status: int, text: str) -> None:
                body = (f"<!doctype html><meta charset=utf-8><title>SchoolSoft</title>"
                        f"<p style='font:18px system-ui;margin:3em'>{text}</p>").encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_a: Any) -> None:
                pass  # the request line has the code in it

        for port in PORTS:
            try:
                self.server = http.server.HTTPServer(("127.0.0.1", port), Handler)
                break
            except OSError:
                continue
        self.server.timeout = 0.5
        self.redirect_uri = f"http://127.0.0.1:{self.server.server_port}/callback"

    def wait(self, seconds: float) -> str:
        deadline = time.monotonic() + seconds
        try:
            while self.code is None and self.error is None and time.monotonic() < deadline:
                self.server.handle_request()
        finally:
            self.server.server_close()
        if self.code:
            return self.code
        if self.error:
            self.ctx.tell("sign_in_failed", why=self.error)
        else:
            self.ctx.tell("timeout")
        raise Stop


def jwt_claims(token: str) -> dict[str, Any]:
    """The access token's claims that name no one, for the file: who it was made for and how long
    it lasts, never its subject."""
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError):
        return {}
    kept = {k: claims[k] for k in ("user_type", "login_method", "client_id", "aud", "iss") if k in claims}
    if isinstance(claims.get("exp"), (int, float)) and isinstance(claims.get("iat"), (int, float)):
        kept["lifetime_seconds"] = int(claims["exp"] - claims["iat"])
    return kept


def tokens(r: Response, hidden: Hidden) -> tuple[str, str | None]:
    """The access and refresh tokens of a token response, hidden from then on."""
    data = r.json()
    data = data if isinstance(data, dict) else {}
    access, refresh = data.get("access_token"), data.get("refresh_token")
    hidden.add(access, refresh)
    if r.status != 200 or not isinstance(access, str) or not access:
        said = data.get("userMessage") if isinstance(data.get("userMessage"), str) else ""
        raise PermissionError(f"status {r.status}" + (f": {said[:80]}" if said else ""))
    return access, refresh if isinstance(refresh, str) and refresh else None


def sign_in(ctx: Context, school: str) -> tuple[str, str | None, dict[str, Any]]:
    verifier, challenge = pkce()
    state = secrets.token_hex(12)
    ctx.hidden.add(verifier)
    callback = Callback(ctx, state)
    address = sign_in_address(ctx.base, school, challenge, state, callback.redirect_uri)
    ctx.tell("browser")
    ctx.say(address)
    ctx.browser(address)
    ctx.tell("waiting")
    code = callback.wait(ctx.sign_in_seconds)
    r = ctx.http.call("token_code", school, query={"clientId": CLIENT_ID, "grantType": "code", "code": code,
                                                   "codeVerifier": verifier})
    try:
        access, refresh = tokens(r, ctx.hidden)
    except PermissionError as e:
        ctx.tell("sign_in_failed", why=ctx.hidden.scrub(str(e)))
        raise Stop from None
    data = r.json()
    about = {"callback_arrived": True, "token_response_fields": sorted(data) if isinstance(data, dict) else [],
             "access_token_claims": jwt_claims(access), "refresh_token_given": refresh is not None}
    return access, refresh, about


# ── Reading

@dataclass
class Read:
    """One request's answer, before it is redacted."""
    what: str
    request: str
    status: int
    data: Any
    child: str | None = None
    school: str | None = None
    note: str | None = None


def request_template(name: str) -> str:
    e = ENDPOINTS[name]
    return f"{e.method} {e.path}" + ("?" + "&".join(f"{q}=" for q in e.query) if e.query else "")


def weeks(today: date) -> tuple[date, list[tuple[int, int]]]:
    """This week's Monday, and the ISO (year, week) of this week and the next."""
    monday = today - timedelta(days=today.weekday())
    return monday, [monday.isocalendar()[:2], (monday + timedelta(days=7)).isocalendar()[:2]]


def read_everything(ctx: Context, school: str, access: str) -> list[Read]:
    http = ctx.http
    reads: list[Read] = []
    bearer = {"Authorization": f"Bearer {access}"}

    def get(what: str, name: str, child: str | None = None, school_label: str | None = None, **kw: Any) -> Any:
        r = http.call(name, school, **kw)
        data = r.json()
        reads.append(Read(what, request_template(name), r.status, data if r.status == 200 else _error(data),
                          child, school_label))
        return data if r.status == 200 else None

    parent = get("guardian and children", "parent", headers=bearer)
    if not isinstance(parent, dict):
        return reads
    user_id = parent.get("userId")
    children = [c for c in parent.get("children") or [] if isinstance(c, dict)]
    schools: dict[Any, str] = {}
    inbox_read: set[Any] = set()
    monday, (this_week, next_week) = weeks(ctx.now().date())
    for i, child in enumerate(children):
        kid = f"Kid {chr(ord('A') + i)}"
        student = child.get("studentId")
        for s in child.get("schools") or []:
            org = s.get("orgId") if isinstance(s, dict) else None
            if not _number(user_id) or not _number(student) or not _number(org):
                reads.append(Read("child's school", "", 0, None, kid, None, "no ids to read with"))
                continue
            label = schools.setdefault(org, f"School {len(schools) + 1}")
            ids = {"userId": user_id, "orgId": org}
            get("news", "news", kid, label, ids=ids, query={"studentId": student, "langId": 1}, headers=bearer)
            get("next calendar event", "next_event", kid, label, ids=ids, query={"studentId": student},
                headers=bearer)
            if org not in inbox_read:  # the guardian's inbox, the same for each child at a school
                inbox_read.add(org)
                inbox = get("message inbox", "inbox", None, label, ids=ids, headers=bearer)
                opened = _already_read(inbox, "isRead")
                if opened is None:
                    reads.append(Read("one message", request_template("message"), 0, None, None, label,
                                      "not opened: no message was already read, and opening one could mark it read"))
                else:
                    get("one message", "message", None, label, ids={**ids, "messageId": opened}, headers=bearer)
            cookie = _cookies(ctx, school, access, user_id, org, student, reads, kid, label)
            if not cookie:
                continue
            web = {"Cookie": cookie}
            for which, (_year, week) in (("this", this_week), ("next", next_week)):
                get(f"lessons, {which} week", "lessons_week", kid, label, ids={"week": week}, headers=web)
            span = {"start_date": monday.isoformat(), "end_date": (monday + timedelta(days=13)).isoformat()}
            get("lessons agenda, two weeks", "lessons_agenda", kid, label, query=span, headers=web)
            get("school events agenda, two weeks", "events_agenda", kid, label, query=span, headers=web)
            assignments = get("assignments, this week", "assignments", kid, label,
                              query={"week": this_week[1], "year": this_week[0]}, headers=web)
            opened = _already_read(assignments, "read")
            if opened is None:
                reads.append(Read("one assignment", request_template("assignment"), 0, None, kid, label,
                                  "not opened: no assignment was already read, and opening one could mark it read"))
            else:
                get("one assignment", "assignment", kid, label, ids={"assignmentId": opened}, headers=web)
            get("plannings", "plannings", kid, label, headers=web)
            get("activity log", "activity_log", kid, label, headers=web, body=ACTIVITY_FILTER)
    return reads


def _number(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) or isinstance(value, str) and value.isdigit()


def _error(data: Any) -> Any:
    """What an error answer says, for the file: its message fields only."""
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if k in ("userMessage", "message", "error", "status", "code")}
    return None


def _already_read(items: Any, flag: str) -> Any:
    """The id of the first item already marked read: only such an item is opened, so opening it
    can't mark anything read."""
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and item.get(flag) is True and _number(item.get("id")):
            return item["id"]
    return None


def _cookies(ctx: Context, school: str, access: str, user_id: Any, org: Any, student: Any,
             reads: list[Read], kid: str, label: str) -> str | None:
    """The web session's cookies for one child, swapped for the access token as the app does. They
    come with SchoolSoft's redirect, which isn't followed."""
    r = ctx.http.call("session_cookies", school, headers={
        "token": access, "userId": str(user_id), "orgId": str(org), "childInFocus": str(student),
        "userOS": "android", "language": "sw", "redirecturl": f"{BASE}/{school}/react/#/parent/start"})
    got = r.cookies()
    ok = bool(got.get("JSESSIONID") and got.get("hash"))
    reads.append(Read("web session for the child", request_template("session_cookies"), r.status,
                      {"cookies_set": sorted(got)}, kid, label, None if ok else "no session cookies"))
    if not ok:
        return None
    ctx.hidden.add(*got.values())
    return f"JSESSIONID={got['JSESSIONID']}; hash={got['hash']}; usertype={got.get('usertype', '1')}"


# ── Redaction

# Keys whose values name a person.
PERSON_KEYS = frozenset(k.casefold() for k in (
    "firstName", "lastName", "fullName", "displayName", "author", "authorName", "teacher", "teachers", "sender",
    "senderName", "creator", "createdBy", "creatorName", "updatedBy", "recipientsNamesString", "recipients",
    "recipientNames", "username", "userName", "studentName", "parentName", "guardianName", "contactPerson",
    "responsible", "signature"))
# Keys whose values are kept out whole: contact details, addresses, pictures, identities.
HIDDEN_KEYS = frozenset(k.casefold() for k in (
    "picture", "photo", "image", "avatar", "guid", "uuid", "sub", "token", "hash", "password",
    "socialSecurityNumber", "personalNumber", "personnummer", "personNumber", "ssn", "civicNumber",
    "address", "streetAddress", "street", "zipCode", "zip", "postalCode", "postAddress", "city", "careOf",
    "email", "mail", "emailAddress", "phone", "phoneNumber", "mobile", "mobilePhone", "telephone", "cellPhone"))
# A Swedish personal or coordination number: (YY)YYMMDD, then - or + or nothing, then four digits.
PERSONNUMMER = re.compile(r"(?<![0-9])(?:19|20)?[0-9]{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12][0-9]|3[01]|[6-8][0-9]|9[01])"
                          r"[-+ ]?[0-9]{4}(?![0-9])")
_ATTRIBUTE_LINK = re.compile(r"""\b(href|src|action)\s*=\s*(["'])[^"']*\2""", re.IGNORECASE)
_UPPER = "A-ZÀ-ÖØ-Þ"
_LOWER = "a-zß-öø-ÿ"
# A capitalised word: in Swedish only names and places are, inside a sentence.
_CAPITALISED = re.compile(rf"(?<![{_UPPER}{_LOWER}0-9⟦_])[{_UPPER}][{_LOWER}]+(?:-[{_UPPER}]?[{_LOWER}]+)?"
                          rf"(?![{_UPPER}{_LOWER}0-9])")
# What may stand right before the first word of a sentence, a heading or a field: the start, an
# end of sentence, an HTML tag or SchoolSoft's bracket markup ([b]), a quote, a bracket or a dash.
_SENTENCE_END = re.compile(r"(?:^|[.!?:;•\n\r\t]|<[^<>]*>|\[[^\[\]]*\]|&nbsp;|[\"'(\[–—-])\s*$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$")


class Redactor:
    """What may leave the guardian's Mac: each person named anywhere in the answers as a
    placeholder, wherever their name stands, and contact details, links, personal numbers and
    other capitalised words inside sentences too. Ids are renumbered, so samples still refer to
    each other without the real ones."""

    def __init__(self) -> None:
        self.placeholders = ai_filter.Placeholders()
        self.words: set[str] = set()  # every word of every name met, for the Swedish genitive and the check
        self._ids: dict[Any, int] = {}
        self._others: dict[tuple[str, str], str] = {}

    def learn(self, value: Any) -> None:
        """Takes the people named in `value`'s person fields, before anything is redacted."""
        names: list[str] = []

        def walk(v: Any, key: str = "") -> None:
            if isinstance(v, dict):
                first, last = v.get("firstName"), v.get("lastName")
                if isinstance(first, str) or isinstance(last, str):
                    names.append(" ".join(p for p in (first, last) if isinstance(p, str)))
                for k, x in v.items():
                    walk(x, k)
            elif isinstance(v, list):
                for x in v:
                    walk(x, key)
            elif isinstance(v, str) and key.casefold() in PERSON_KEYS:
                names.extend(part for part in re.split(r"[,;/]| och | and ", v) if part.strip())
        walk(value)
        names = [n.strip() for n in names if n and n.strip()]
        self.words |= {w for n in names for w in re.findall(rf"[{_UPPER}{_LOWER}'’-]{{2,}}", n)}
        self.placeholders.add_people(names)

    def redact(self, value: Any, key: str = "") -> Any:
        folded = key.casefold()
        if folded in HIDDEN_KEYS and value not in (None, "", [], {}):
            return f"⟦{key}⟧"
        if isinstance(value, dict):
            return {self.text(k) if re.search(r"[\s@./:]", k) else k: self.redact(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [self.redact(v, key) for v in value[:SAMPLES + 1]]
        if value is None or isinstance(value, bool):
            return value
        if folded == "id" or key.endswith(("Id", "ID", "_id")):
            return self._id(value)
        if isinstance(value, (int, float)):
            return value
        if not isinstance(value, str):
            return f"⟦{type(value).__name__}⟧"
        if folded in PERSON_KEYS:
            masked = self.text(value)
            return masked if not re.search(rf"[{_UPPER}{_LOWER}]{{2,}}", re.sub(r"⟦[^⟧]*⟧", "", masked)) else "⟦N⟧"
        return self.text(value)

    def text(self, text: str) -> str:
        text = PERSONNUMMER.sub(lambda m: self._other("S", m.group()), text)
        text = _ATTRIBUTE_LINK.sub(lambda m: f"{m.group(1)}={m.group(2)}⟦link⟧{m.group(2)}", text)
        text = self.placeholders.mask_text(text)
        for word in sorted(self.words, key=len, reverse=True):  # the Swedish genitive: Annas, Svenssons
            text = re.sub(rf"(?<![{_UPPER}{_LOWER}])({re.escape(word)})s(?![{_UPPER}{_LOWER}])",
                          lambda m: self.placeholders.mask_text(m.group(1)) + ":s", text)
        text = self._capitalised(text)
        return text if len(text) <= TEXT_LIMIT else f"{text[:TEXT_LIMIT]}… [{len(text) - TEXT_LIMIT} more characters]"

    def _capitalised(self, text: str) -> str:
        out, end = [], 0
        for m in _CAPITALISED.finditer(text):
            if _SENTENCE_END.search(text[max(0, m.start() - 80):m.start()]):
                continue  # the first word of a sentence, a heading or a field
            out += [text[end:m.start()], self._other("W", m.group())]
            end = m.end()
        return "".join([*out, text[end:]])

    def _other(self, kind: str, value: str) -> str:
        if (kind, value) not in self._others:
            self._others[kind, value] = f"⟦{kind}{sum(k == kind for k, _ in self._others) + 1}⟧"
        return self._others[kind, value]

    def _id(self, value: Any) -> Any:
        """A new number for an id that could say who someone is. Small numbers stay: they number
        days, languages and the like, not people."""
        small = isinstance(value, int) and abs(value) < 1000 or isinstance(value, str) and re.fullmatch(r"-?\d{1,3}", value)
        if small or value == "" or not isinstance(value, (int, str)):
            return value
        number = self._ids.setdefault(value, len(self._ids) + 1)
        return number if isinstance(value, int) else str(number)

    def leftovers(self, text: str) -> list[str]:
        """The words of a name met that still stand in `text`."""
        return sorted(w for w in self.words if len(w) >= 3
                      and re.search(rf"(?<![{_UPPER}{_LOWER}]){re.escape(w)}(?![{_UPPER}{_LOWER}])", text))


def shape(value: Any) -> Any:
    """The fields of `value` and the kind of each, every item of a list merged into one."""
    if isinstance(value, dict):
        return {k: shape(v) for k, v in value.items()}
    if isinstance(value, list):
        merged: Any = None
        for item in value:
            merged = _merge(merged, shape(item)) if merged is not None else shape(item)
        return [merged] if merged is not None else []
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if value is None:
        return "null"
    if isinstance(value, str):
        if _DATE.match(value):
            return "date and time" if len(value) > 10 else "date"
        return "html" if re.search(r"<[a-zA-Z][^<>]*>", value) else "text"
    return type(value).__name__


def _merge(a: Any, b: Any) -> Any:
    if isinstance(a, dict) and isinstance(b, dict):
        return {k: _merge(a[k], b[k]) if k in a and k in b else a.get(k, b.get(k)) for k in {**a, **b}}
    if isinstance(a, list) and isinstance(b, list):
        return [_merge(a[0], b[0])] if a and b else a or b
    if a == b:
        return a
    kinds = set(str(a).split(" | ")) | set(str(b).split(" | ")) if isinstance(a, str) and isinstance(b, str) else None
    return " | ".join(sorted(kinds)) if kinds else a


def report(ctx: Context, school: str, sign_in_about: dict[str, Any], reads: list[Read]) -> dict[str, Any]:
    redactor = Redactor()
    for r in reads:
        redactor.learn(r.data)
    entries = []
    for r in reads:
        entry: dict[str, Any] = {"what": r.what}
        if r.child:
            entry["child"] = r.child
        if r.school:
            entry["school"] = r.school
        entry |= {"request": r.request, "status": r.status}
        if r.note:
            entry["note"] = r.note
        if r.status == 200 and r.data is not None or r.status not in (0, 200):
            entry["count"] = len(r.data) if isinstance(r.data, list) else None
            entry["fields"] = shape(r.data)
            entry["samples"] = redactor.redact(r.data[:SAMPLES] if isinstance(r.data, list) else r.data)
        entries.append(entry)
    # Last, the answers once more: nothing hidden, and no word of a name met, may be left in them.
    written = ctx.hidden.scrub(json.dumps(entries, ensure_ascii=False))
    for word in redactor.leftovers(written):
        written = re.sub(rf"(?<![{_UPPER}{_LOWER}]){re.escape(word)}(?![{_UPPER}{_LOWER}])", "⟦N⟧", written)
    return {
        "about": ctx.text("done", file=str(ctx.folder / OUTPUT)),
        "tool": f"schoolsoft-capture {VERSION}",
        "captured_on": ctx.now().date().isoformat(),
        "school": school,
        "sign_in": json.loads(ctx.hidden.scrub(json.dumps(sign_in_about, ensure_ascii=False))),
        "answers": json.loads(written),
    }


# ── The commands

def capture(ctx: Context, school: str | None) -> int:
    ctx.tell("intro")
    if school is None:
        school = school_name(ctx.ask(ctx.text("ask_school", default=DEFAULT_SCHOOL)) or DEFAULT_SCHOOL)
    if not valid_school(school):
        ctx.tell("bad_school")
        return 2
    try:
        methods = ctx.http.call("login_methods", school, query={"client_id": CLIENT_ID, "usertype": "parent"})
        if methods.status != 200 or not isinstance(methods.json(), list):
            ctx.tell("unknown_school", school=school)
            return 2
        access, refresh, about = sign_in(ctx, school)
        about["login_methods"] = methods.json()
        ctx.tell("reading")
        reads = read_everything(ctx, school, access)
    except Unreachable:
        ctx.tell("unreachable")
        return 1
    except Stop:
        return 1
    ctx.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    out = ctx.folder / OUTPUT
    out.write_text(json.dumps(report(ctx, school, about, reads), ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    out.chmod(0o600)
    if refresh:
        offer_evening_check(ctx, school, refresh)
    ctx.tell("done", file=str(out))
    ctx.tell("remove", command=REMOVE)
    ctx.show_file(out)
    return 0


def offer_evening_check(ctx: Context, school: str, refresh: str) -> None:
    if ctx.ask(ctx.text("ask_check")).strip().casefold() not in YES:
        ctx.tell("check_off")
        return
    try:
        with refresh_lock(ctx.folder) as got:
            if not got:
                raise OSError("another check is running")
            ctx.keychain.put(refresh)
            now = ctx.now().isoformat(timespec="seconds")
            save_state(ctx.folder, {"school": school, "signed_in": now, "token_issued": now, "last_worked_day": 0})
        ctx.agent.install(ctx.folder)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        ctx.keychain.delete()
        (ctx.folder / STATE).unlink(missing_ok=True)
        ctx.tell("check_failed", why=ctx.hidden.scrub(str(e))[:120])
        return
    log(ctx, f"signed in, {DAYS} evening checks start")
    ctx.tell("check_on", log=str(ctx.folder / LOG))


@contextlib.contextmanager
def refresh_lock(folder: Path) -> Iterator[bool]:
    """Whether this process holds the one lock every use of the refresh token takes, so two
    refreshes never run at once: the second would send a token the first has just used up."""
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(folder / LOCK, "a", encoding="utf-8") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def load_state(folder: Path) -> dict[str, Any] | None:
    try:
        state = json.loads((folder / STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) else None


def save_state(folder: Path, state: dict[str, Any]) -> None:
    new = folder / f"{STATE}.new"
    new.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    new.chmod(0o600)
    new.replace(folder / STATE)


def log(ctx: Context, line: str) -> None:
    """One line of the refresh log: the date and what happened, never a token."""
    with open(ctx.folder / LOG, "a", encoding="utf-8") as f:
        f.write(ctx.hidden.scrub(f"{ctx.now().date().isoformat()}  {line}") + "\n")
    os.chmod(ctx.folder / LOG, 0o600)


def refresh(ctx: Context) -> int:
    """The evening check: renews the stored sign-in once and logs whether that worked."""
    with refresh_lock(ctx.folder) as got:
        if not got:
            log(ctx, "skipped: another check was running")
            return 0
        state = load_state(ctx.folder)
        token = ctx.keychain.get() if state else None
        if not state or not token or not valid_school(str(state.get("school", ""))):
            log(ctx, "stopped: no sign-in is stored")
            finish(ctx)
            return 1
        ctx.hidden.add(token)
        now = ctx.now()
        signed_in = datetime.fromisoformat(state["signed_in"])
        issued = datetime.fromisoformat(state["token_issued"])
        day = (now.date() - signed_in.date()).days
        age = f"token age {(now - issued).total_seconds() / 3600:.1f} h, day {day}"
        try:
            r = ctx.http.call("token_refresh", state["school"], query={
                "clientId": CLIENT_ID, "grantType": "refresh_token", "refreshToken": token})
        except Unreachable:
            log(ctx, f"not checked: SchoolSoft couldn't be reached ({age})")
            return 1
        if r.status >= 500:
            log(ctx, f"not checked: SchoolSoft answered {r.status} ({age})")
            return 1
        try:
            _access, new = tokens(r, ctx.hidden)
            if not new:
                raise PermissionError("no new refresh token")
        except PermissionError as e:
            log(ctx, f"failed: {e} ({age})")
            log(ctx, f"RESULT: the sign-in worked until day {state.get('last_worked_day', 0)} "
                     f"and failed on day {day}")
            finish(ctx)
            return 0
        try:
            ctx.keychain.put(new)  # first: the old token is used up now
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            log(ctx, f"worked, but the new sign-in couldn't be saved ({e}) ({age})")
            log(ctx, f"RESULT: the sign-in worked on day {day}; the check stopped there, not SchoolSoft")
            finish(ctx)
            return 1
        save_state(ctx.folder, {**state, "token_issued": now.isoformat(timespec="seconds"), "last_worked_day": day})
        log(ctx, f"worked ({age})")
        if day >= DAYS:
            log(ctx, f"RESULT: the sign-in still worked on day {day}; the checks are done")
            finish(ctx)
        return 0


def finish(ctx: Context) -> None:
    """The checks are over: the token and the job go; the log stays for the guardian to send."""
    ctx.keychain.delete()
    (ctx.folder / STATE).unlink(missing_ok=True)
    ctx.agent.remove()


def main(argv: list[str] | None = None, ctx: Context | None = None) -> int:
    parser = argparse.ArgumentParser(prog="schoolsoft-capture", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("capture", help="sign in, read, write the file")
    one.add_argument("--school", help="the name in sms.schoolsoft.se/<name>/")
    one.add_argument("--language", choices=sorted(TEXT["intro"]))
    sub.add_parser("refresh", help="the evening check")
    args = parser.parse_args(argv)
    if ctx is None:
        ctx = Context(language=getattr(args, "language", None) or mac_language())
    elif getattr(args, "language", None):
        ctx.language = args.language
    try:
        if args.command == "refresh":
            return refresh(ctx)
        return capture(ctx, school_name(args.school) if args.school else None)
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # never a traceback: its text could hold a token
        message = ctx.hidden.scrub(f"{type(e).__name__}: {e}")[:300]
        if args.command == "refresh":
            log(ctx, f"error: {message}")
        else:
            ctx.say(f"❌ {message}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
