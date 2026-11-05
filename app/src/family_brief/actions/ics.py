from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event

from ..brief_text import PRODUCT_NAME
from ..collectors.base import CalendarEvent
from .calendar import calendar_uid


def _utc(dt: datetime, tz: str) -> datetime:
    # Naive datetimes from the LLM are wall-clock times in the family's timezone.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz))
    return dt.astimezone(timezone.utc)


def build(events: list[CalendarEvent], tz: str, stamp: datetime,
          translated: Callable[[CalendarEvent], CalendarEvent] | None = None) -> bytes:
    """One .ics file with a VEVENT per event. Stable UIDs mean re-importing updates
    the existing entry instead of duplicating it in most calendar apps. `translated` gives an
    event in a Recipient's language; its UID stays the original's, so both parents' .ics
    imported into one shared calendar still make one entry. An event that keeps its id when it
    moves, such as a Wilma exam, has one UID whatever its day (ADR 0040). `stamp` is when the file
    was made."""
    cal = Calendar()
    cal.add("prodid", "-//FamilyBrief//family-brief//ZH")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    for ev in events:
        uid = f"{calendar_uid(ev)}@family-brief"
        moves = ev.same_when_moved
        ev = translated(ev) if translated else ev
        e = Event()
        e.add("uid", uid)
        e.add("dtstamp", stamp)
        if moves:
            # Later each time it is sent, so a calendar app takes a moved one as an update.
            e.add("sequence", int(stamp.timestamp()) // 60)
        if ev.all_day:
            # Dates, not times, so the day shows the same in any timezone; DTEND is the day after.
            e.add("dtstart", ev.start.date())
            e.add("dtend", ev.day_after_last())
        else:
            start = _utc(ev.start, tz)
            e.add("dtstart", start)
            e.add("dtend", _utc(ev.end, tz) if ev.end else start + timedelta(hours=1))
        e.add("summary", ev.title)
        if ev.location:
            e.add("location", ev.location)
        note = ev.description or ""
        if ev.kid:
            note = f"{note}\n\n[{PRODUCT_NAME} · {ev.kid}]".strip()
        if note:
            e.add("description", note)
        cal.add_component(e)
    return cal.to_ical()
