from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event

from ..collectors.base import CalendarEvent
from .calendar import event_hash


def _utc(dt: datetime, tz: str) -> datetime:
    # Naive datetimes from the LLM are wall-clock times in the family's timezone.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz))
    return dt.astimezone(timezone.utc)


def build(events: list[CalendarEvent], tz: str,
          translated: Callable[[CalendarEvent], CalendarEvent] | None = None) -> bytes:
    """One .ics file with a VEVENT per event. Stable UIDs mean re-importing updates
    the existing entry instead of duplicating it in most calendar apps. `translated` gives an
    event in a Recipient's language; its UID stays the original's, so both parents' .ics
    imported into one shared calendar still make one entry."""
    cal = Calendar()
    cal.add("prodid", "-//FamilyBrief//family-brief//ZH")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    stamp = datetime.now(timezone.utc)
    for ev in events:
        uid = f"{event_hash(ev)}@family-brief"
        ev = translated(ev) if translated else ev
        start = _utc(ev.start, tz)
        end = _utc(ev.end, tz) if ev.end else start + timedelta(hours=1)
        e = Event()
        e.add("uid", uid)
        e.add("dtstamp", stamp)
        e.add("dtstart", start)
        e.add("dtend", end)
        e.add("summary", ev.title)
        if ev.location:
            e.add("location", ev.location)
        note = ev.description or ""
        if ev.kid:
            note = f"{note}\n\n[FamilyBrief · {ev.kid}]".strip()
        if note:
            e.add("description", note)
        cal.add_component(e)
    return cal.to_ical()
