from __future__ import annotations

import hashlib
import logging
import os
from datetime import timedelta, timezone
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from ..brief_text import PRODUCT_NAME
from ..collectors.base import CalendarEvent
from ..config import Config
from ..state import State

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
CREDS_PATH = os.path.expanduser("~/.family/calendar_credentials.json")
TOKEN_PATH = os.path.expanduser("~/.family/calendar_token.json")


def event_hash(ev: CalendarEvent) -> str:
    """What makes two events the same one. Not the title: an undelivered night is summarized
    again, and the model may word it differently. The kid is in it so one message can give an
    event for each of two kids."""
    start = ev.start.astimezone(timezone.utc).isoformat()
    return _hash(f"{ev.source}|{ev.external_id}|{start}|{ev.kid or ''}")


def _legacy_event_hash(ev: CalendarEvent) -> str:
    """The hash before 0.4.0, which had the title in it and MyClub starts in UTC."""
    start = ev.start.astimezone(timezone.utc) if ev.source == "myclub" else ev.start
    return _hash(f"{ev.source}|{ev.external_id}|{start.isoformat()}|{ev.title}")


def _hash(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]


def already_created(state: State, ev: CalendarEvent) -> bool:
    # The legacy check keeps an upgrade from writing (and inviting to) every upcoming event again;
    # it can go once state from before 0.4.0 has been pruned.
    return state.has_created_event(event_hash(ev)) or state.has_created_event(_legacy_event_hash(ev))


class CalendarNotConfigured(RuntimeError):
    pass


def load_credentials() -> Credentials:
    if Path(TOKEN_PATH).exists():
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        if creds.valid:
            return creds
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            Path(TOKEN_PATH).write_text(creds.to_json())
            os.chmod(TOKEN_PATH, 0o600)
            return creds
    raise CalendarNotConfigured(
        f"Google Calendar OAuth token not found at {TOKEN_PATH}. "
        "Run: python scripts/setup_google_calendar.py"
    )


def is_configured() -> bool:
    return Path(TOKEN_PATH).exists()


def _build_service():
    return build("calendar", "v3", credentials=load_credentials(), cache_discovery=False)


def get_upcoming_events(cfg: Config, days: int) -> list[dict]:
    """Return existing events in the next N days (for LLM context + conflict awareness)."""
    from datetime import datetime, timedelta, timezone
    service = _build_service()
    now = datetime.now(timezone.utc)
    resp = service.events().list(
        calendarId=cfg.google_calendar.calendar_id,
        timeMin=now.isoformat(),
        timeMax=(now + timedelta(days=days)).isoformat(),
        singleEvents=True,
        orderBy="startTime",
        maxResults=250,
    ).execute()
    return [
        {
            "id": e.get("id"),
            "summary": e.get("summary"),
            "start": e.get("start", {}).get("dateTime") or e.get("start", {}).get("date"),
            "end": e.get("end", {}).get("dateTime") or e.get("end", {}).get("date"),
            "location": e.get("location"),
        }
        for e in resp.get("items", [])
    ]


def sync_attendees(cfg: Config) -> dict[str, int]:
    """Patch all Family-Brief-tagged events in upcoming 120 days to match the attendee list.

    Adds any missing emails from cfg.google_calendar.invite_attendees. Does not remove
    anyone (safer). Returns {"scanned": N, "patched": M}.
    """
    from datetime import datetime, timedelta, timezone
    service = _build_service()
    calendar_id = cfg.google_calendar.calendar_id
    wanted = set(cfg.google_calendar.invite_attendees)
    now = datetime.now(timezone.utc)
    scanned = patched = 0
    page_token = None
    while True:
        resp = service.events().list(
            calendarId=calendar_id,
            privateExtendedProperty="family_brief=1",
            timeMin=now.isoformat(),
            timeMax=(now + timedelta(days=120)).isoformat(),
            singleEvents=True,
            pageToken=page_token,
        ).execute()
        for ev in resp.get("items", []):
            scanned += 1
            existing = {a.get("email") for a in ev.get("attendees", []) if a.get("email")}
            missing = wanted - existing
            if not missing:
                continue
            new_attendees = list(ev.get("attendees", []))
            for email in missing:
                new_attendees.append({"email": email})
            service.events().patch(
                calendarId=calendar_id,
                eventId=ev["id"],
                body={"attendees": new_attendees},
                sendUpdates=cfg.google_calendar.send_updates,
            ).execute()
            patched += 1
            log.info("Patched attendees on: %s", ev.get("summary"))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return {"scanned": scanned, "patched": patched}


def create_events(cfg: Config, state: State, events: list[CalendarEvent],
                  created: list[dict]) -> None:
    """Insert events into Google Calendar with dedup via extendedProperties + local state.

    Appends each written event to the caller's `created` as it goes rather than returning a
    list, so the caller still has the events Google took when a later insert raises.
    """
    if not events:
        return
    service = _build_service()
    calendar_id = cfg.google_calendar.calendar_id

    for ev in events:
        h = event_hash(ev)
        if already_created(state, ev):
            log.info("Skip (local state) %s", ev.title)
            continue

        # Remote dedup via extendedProperties query
        existing = service.events().list(
            calendarId=calendar_id,
            privateExtendedProperty=f"family_brief_hash={h}",
            maxResults=1,
        ).execute().get("items", [])
        if existing:
            log.info("Skip (remote dup) %s", ev.title)
            state.mark_event_created(h, existing[0]["id"])
            continue

        if ev.all_day:
            start = {"date": ev.start.date().isoformat()}
            end = {"date": ev.day_after_last().isoformat()}
        else:
            start = {"dateTime": ev.start.isoformat(), "timeZone": cfg.timezone}
            end = {"dateTime": (ev.end or ev.start + timedelta(hours=1)).isoformat(), "timeZone": cfg.timezone}
        body = {
            "summary": ev.title,
            "description": (ev.description or "") + f"\n\n[{PRODUCT_NAME} • {ev.source}"
                           + (f" • {ev.kid}" if ev.kid else "") + "]",
            "start": start,
            "end": end,
            "location": ev.location,
            "extendedProperties": {
                "private": {
                    "family_brief": "1",
                    "family_brief_hash": h,
                    "source": ev.source,
                    "external_id": ev.external_id,
                    "kid": ev.kid or "",
                }
            },
            "reminders": {"useDefault": True},
        }
        if cfg.google_calendar.invite_attendees:
            body["attendees"] = [{"email": e} for e in cfg.google_calendar.invite_attendees]
        body = {k: v for k, v in body.items() if v is not None}

        resp = service.events().insert(
            calendarId=calendar_id,
            body=body,
            sendUpdates=cfg.google_calendar.send_updates,
        ).execute()
        state.mark_event_created(h, resp["id"])
        created.append({"title": ev.title, "start": ev.start_iso(),
                        "kid": ev.kid, "google_event_id": resp["id"],
                        "htmlLink": resp.get("htmlLink")})
        log.info("Created event: %s @ %s", ev.title, ev.start_iso())
