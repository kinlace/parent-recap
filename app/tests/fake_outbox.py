"""Where the Brief goes out: the email it is sent as by the gateway's Brief relay (ADR 0028, ADR
0032), with the send-only keys it makes on request and the Recipients who confirmed (ADR 0033),
iMessage, and the Google Calendar API. Each records what it is given, or fails as the test says."""
from __future__ import annotations

import base64
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pytest
import requests

from fake_model import _response
from family_brief import gateway as api
from family_brief.actions import calendar as calendar_action


@dataclass
class SentEmail:
    subject: str
    text: str
    html: str | None
    to: list[str]
    attachments: list[tuple[str, bytes, str]]

    def attachment(self, suffix: str) -> tuple[str, bytes, str] | None:
        return next((a for a in self.attachments if a[0].endswith(suffix)), None)


@dataclass
class FakeCalendarService:
    """Stands in for googleapiclient's Calendar v3 service: events().list/insert(...).execute()."""
    existing: list[dict] = field(default_factory=list)
    inserted: list[dict] = field(default_factory=list)
    error: Exception | None = None
    fail_after: int = 0  # inserts that succeed before `error` is raised

    def events(self) -> "FakeCalendarService":
        return self

    def list(self, **kw: Any) -> "_Call":
        if "privateExtendedProperty" in kw:
            h = kw["privateExtendedProperty"].split("=", 1)[1]
            return _Call(lambda: {"items": [e for e in self.inserted
                                            if e["extendedProperties"]["private"]["family_brief_hash"] == h]})
        return _Call(lambda: {"items": self.existing})

    def insert(self, calendarId: str, body: dict, sendUpdates: str) -> "_Call":
        def run() -> dict:
            if self.error and len(self.inserted) >= self.fail_after:
                raise self.error
            ev = {**body, "id": f"gev{len(self.inserted) + 1}",
                  "htmlLink": f"https://calendar.google.com/event?eid=gev{len(self.inserted) + 1}"}
            self.inserted.append(ev)
            return ev
        return _Call(run)


@dataclass
class _Call:
    fn: Callable[[], dict]

    def execute(self) -> dict:
        return self.fn()


class FakeOutbox:
    """The email sent (`sent`), the iMessages (`imessages`: recipient and text) and the Google
    Calendar (`calendar`), which the family authorizes in `home` (authorize_google_calendar).
    The gateway's relay records the Gateway Key each request had (`relay_keys`) and answers
    `relay_answer`, a status and JSON body, after taking the email when that is 200 (doctor's
    empty request then gets the 400 a known key gets).

    It also makes send-only keys on request (ADR 0033) as ops/gateway/brief_relay.py does: each
    request's Recipients are in `key_requests`, the keys made in `made_keys` with the addresses
    bound to each, and the confirmation emails in `confirmations` (address, language). An
    address is confirmed by `confirm(address)`; until then a made key sends only to the others,
    and to none 403. `key_answer`, when set, is the status and body the key request gets
    instead, or an exception it raises. Any other key, such as the harness's, was made by hand:
    it isn't bound and sends to anyone."""

    def __init__(self, home: Path, mp: pytest.MonkeyPatch) -> None:
        self.sent: list[SentEmail] = []
        self.email_error: Exception | None = None
        self.relay_keys: list[str] = []
        self.relay_answer: tuple[int, dict[str, Any]] = (200, {"sent": 1, "refused": 0})
        self.imessages: list[tuple[str, str]] = []  # (recipient, text) actually sent
        self.imessage_error: str | None = None      # stderr of a failing osascript
        self.calendar = FakeCalendarService()
        self.key_requests: list[list[dict[str, str]]] = []
        self.made_keys: dict[str, list[dict[str, str]]] = {}
        self.confirmed: set[str] = set()
        self.confirmations: list[tuple[str, str]] = []
        self.binds: list[dict[str, Any]] = []
        self.key_answer: tuple[int, dict[str, Any]] | Exception | None = None
        mp.setattr(api, "post_brief", self._relay)
        real_post = api._post
        mp.setattr(api, "_post", lambda url, path, body, timeout, headers:
                   self._key_desk(path, body, headers) if path in ("/v1/brief/key", "/v1/brief/recipients")
                   else real_post(url, path, body, timeout, headers))
        mp.setattr(calendar_action, "TOKEN_PATH", str(home / ".family" / "calendar_token.json"))
        mp.setattr(calendar_action, "_build_service", lambda: self.calendar)

    def _relay(self, url: str, key: str, body: dict[str, Any], timeout: float = 0) -> requests.Response:
        if self.email_error:
            raise self.email_error
        self.relay_keys.append(key)
        status, answer = self.relay_answer
        if status == 200 and not body:  # doctor's check: the relay takes the key, then the empty request is bad
            return _response(400, {"error": "`to` must be a list of addresses"})
        if status == 200 and key in self.made_keys:
            bound = {r["address"] for r in self.made_keys[key]} & self.confirmed
            unconfirmed = [a for a in body["to"] if a.lower() not in bound]
            to = [a for a in body["to"] if a.lower() in bound]
            if not to:
                return _response(403, {"error": "recipient not confirmed", "unconfirmed": unconfirmed})
            if unconfirmed:
                body = {**body, "to": to}
                answer = {"sent": len(to), "refused": 0, "unconfirmed": unconfirmed}
        if status == 200:
            self.sent.append(SentEmail(body["subject"], body["text"], body.get("html"), list(body["to"]),
                                       [(a["filename"], base64.b64decode(a["data"]), a["content_type"])
                                        for a in body.get("attachments", [])]))
        return _response(status, answer)

    def confirm(self, *addresses: str) -> None:
        """The Recipients press the link in their confirmation email."""
        self.confirmed.update(addresses)

    def _key_desk(self, path: str, body: dict[str, Any], headers: dict[str, str]) -> requests.Response:
        if self.email_error:
            raise self.email_error
        recipients = body["recipients"]
        if path == "/v1/brief/key":
            self.key_requests.append(recipients)
            if isinstance(self.key_answer, Exception):
                raise self.key_answer
            if self.key_answer is not None:
                return _response(*self.key_answer)
            key = f"sk-bf-made-on-request-{len(self.made_keys)}"
            self.made_keys[key] = recipients
            mailed = [r["address"] for r in recipients]
            self.confirmations += [(r["address"], r["language"]) for r in recipients]
            return _response(200, {"key": key, **self._statuses(recipients, mailed)})
        key = headers.get("x-api-key", "")
        self.binds.append({"key": key, **body})
        if key not in self.made_keys:  # made by hand: not bound
            return _response(200, {"recipients": [{"address": r["address"], "confirmed": True}
                                                  for r in recipients], "mailed": []})
        before = {r["address"] for r in self.made_keys[key]}
        self.made_keys[key] = recipients
        waiting = [r for r in recipients if r["address"] not in self.confirmed]
        mail = waiting if body.get("resend") else [r for r in recipients if r["address"] not in before]
        self.confirmations += [(r["address"], r["language"]) for r in mail]
        return _response(200, self._statuses(recipients, [r["address"] for r in mail]))

    def _statuses(self, recipients: list[dict[str, str]], mailed: list[str]) -> dict[str, Any]:
        return {"recipients": [{"address": r["address"], "confirmed": r["address"] in self.confirmed}
                               for r in recipients], "mailed": mailed}

    def authorize_google_calendar(self) -> None:
        token = Path(calendar_action.TOKEN_PATH)
        token.parent.mkdir(parents=True, exist_ok=True)
        token.write_text("{}")

    def osascript(self, cmd: list[str], prog: str, **_k: Any) -> subprocess.CompletedProcess | None:
        """iMessage through Messages.app, which has no interface of its own (ADR 0020)."""
        if prog != "osascript":
            return None
        if self.imessage_error is not None:
            return subprocess.CompletedProcess(cmd, 1, "", self.imessage_error)
        script = cmd[cmd.index("-e") + 1]
        to = script.split('to buddy "', 1)[1].split('"', 1)[0]
        self.imessages.append((to, script.split('send "', 1)[1].rsplit('" to buddy', 1)[0]))
        return subprocess.CompletedProcess(cmd, 0, "", "")
