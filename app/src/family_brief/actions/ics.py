from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event

from ..brief_text import PRODUCT_NAME
from ..collectors.base import CalendarEvent
from .calendar import calendar_uid

# Each .ics goes on the Brief as a plain file, not text/calendar with method=PUBLISH.
MIME_TYPE = "application/ics"

# Gmail reads every .ics on a mail, whatever its type, and puts a card above the mail with "Add to
# Calendar". It shows only a file's first event, and a file with 7 or more events breaks the card
# into "Unable to load event". With one file per event, it lists each in a stack, each with its own
# button (#1034, checked in Gmail on 2026-11-21). Past MAX_FILES, the rest go in one last file, so
# a week full of events doesn't take a mail's worth of attachments.
MAX_FILES = 20
UNSAFE_IN_NAME = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]+')


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


def files(events: list[CalendarEvent], tz: str, stamp: datetime,
          translated: Callable[[CalendarEvent], CalendarEvent] | None = None
          ) -> list[tuple[str, bytes]]:
    """The Brief's .ics files: one per event, by start, named by its day and title so a mail app
    that shows only the files still says what each is. From the MAX_FILES-th on, the rest share
    the last file. Each event is as build() makes it, so its UID is the same as before."""
    events = sorted(events, key=lambda ev: _utc(ev.start, tz))
    singles, rest = events[:MAX_FILES - 1], events[MAX_FILES - 1:]
    if len(rest) == 1:
        singles, rest = events, []
    out = [(_name(ev, tz, translated), build([ev], tz, stamp, translated)) for ev in singles]
    if rest:
        day = _day(rest[0], tz)
        out.append((f"{day} +{len(rest)}.ics", build(rest, tz, stamp, translated)))
    return out


def _day(ev: CalendarEvent, tz: str) -> str:
    """The event's day in the family's timezone."""
    day = ev.start.date() if ev.all_day else _utc(ev.start, tz).astimezone(ZoneInfo(tz)).date()
    return day.isoformat()


def _name(ev: CalendarEvent, tz: str,
          translated: Callable[[CalendarEvent], CalendarEvent] | None) -> str:
    title = (translated(ev) if translated else ev).title
    title = UNSAFE_IN_NAME.sub("-", title).strip(" .-")[:60].strip()
    day = _day(ev, tz)
    return f"{day} {title}.ics" if title else f"{day}.ics"
