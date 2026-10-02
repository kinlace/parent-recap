from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any, Callable, TypeVar
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

T = TypeVar("T")


def retry_once_on_timeout(call: Callable[[], T], timeouts: tuple[type[BaseException], ...],
                          what: str, before_retry: Callable[[], None] | None = None) -> T:
    """`call()`, run once more if it times out: slow DNS or a network still waking up usually
    clears by then. A second timeout raises, so the Source reports it as not read. `what` names
    the call in the log line; `before_retry` undoes what the first try left half done."""
    try:
        return call()
    except timeouts as e:
        log.warning("%s timed out (%s); trying once more", what, type(e).__name__)
    if before_retry:
        before_retry()
    return call()


# How a Source's log line starts when it skips one Message it couldn't read, leaving it unseen for
# a later run. The first error a Source logs is the reason on the Brief's coverage line.
UNREADABLE_MESSAGE = "a message couldn't be read"


def unreadable(source: str, ext_id: str, error: Exception | str) -> str:
    """The error line for a skipped Message: which one and why, for the log only."""
    return f"{UNREADABLE_MESSAGE}: {source} {ext_id} ({error})"


@dataclass
class Message:
    """Normalized message from any source."""
    source: str                      # gmail | wilma | whatsapp | myclub
    external_id: str                 # unique within source
    timestamp: datetime              # UTC
    sender: str | None = None
    recipient: str | None = None
    subject: str | None = None
    body: str = ""
    chat_name: str | None = None     # WhatsApp group / email thread / Wilma folder
    kid_hint: str | None = None      # which kid this likely refers to, if known
    url: str | None = None           # link back to original
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "external_id": self.external_id,
            "timestamp": self.timestamp.isoformat(),
            "sender": self.sender,
            "recipient": self.recipient,
            "subject": self.subject,
            "body": self.body,
            "chat_name": self.chat_name,
            "kid_hint": self.kid_hint,
            "url": self.url,
            "metadata": self.metadata,
        }


@dataclass
class CalendarEvent:
    """Event discovered from a feed (e.g. MyClub iCal) to sync into Google Calendar."""
    source: str
    external_id: str
    title: str
    start: datetime
    end: datetime | None = None
    location: str | None = None
    description: str = ""
    kid: str | None = None
    # A day with no start time, such as an exam: `start` is that day's local midnight and `end`,
    # if any, the local midnight of its last day.
    all_day: bool = False

    def in_zone(self, tz: str) -> CalendarEvent:
        """The same event with its times in `tz`, as the Brief shows them and the model reads them."""
        zone = ZoneInfo(tz)
        return replace(self, start=self.start.astimezone(zone),
                       end=self.end.astimezone(zone) if self.end else None)

    def start_iso(self) -> str:
        """The start as the Brief lists it: the date alone for an all-day event."""
        return self.start.date().isoformat() if self.all_day else self.start.isoformat()

    def day_after_last(self) -> date:
        """An all-day event's end as calendars store it: the day after its last day."""
        return (self.end or self.start).date() + timedelta(days=1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "external_id": self.external_id,
            "title": self.title,
            "start": self.start_iso(),
            "end": (self.end.date().isoformat() if self.all_day else self.end.isoformat()) if self.end else None,
            "location": self.location,
            "description": self.description,
            "kid": self.kid,
        }
