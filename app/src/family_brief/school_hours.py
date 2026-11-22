"""A Kid's school hours on the next school day when Wilma's timetable has them other than usual
(#1036, ADR 0054): "Mia, Fri 9 Oct: 8:15–12:00 (normally 8:15–14:00)".

Nothing here asks the model. The Wilma collector reads four weeks of the timetable in its one
`schedule` call and works out, per Kid and weekday, the usual start of the first lesson and end of
the last: the time most of the other days of that weekday share. The next school day's hours that
differ go in the timetable Message's metadata, and the Brief shows them in the program's own words.

A message beats the timetable. The model gets the usual hours beside the timetable and is asked
to give them whenever a day's hours differ, from the timetable or from a message. When its text
about the Kid already names a usual time that differs, it has said it, perhaps with a message's
hours, so the program's line is left out."""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .brief import Brief
    from .collectors.base import Message

# How many other days of a weekday must share a time for it to be that weekday's usual one. Four
# weeks of timetable give three other days, so one odd week doesn't make a usual time.
AGREEING_DAYS = 2

# The metadata key on the timetable Message that holds what to tell, which the model never gets.
KEY = "school_hours"


@dataclass(frozen=True)
class SchoolHours:
    """A Kid's school day on `day` from `start` to `end` ("HH:MM"), usually `usual_start` to
    `usual_end` on that weekday. `kid` is the Kid as the collector found them, or the Wilma
    student's own name when no one Kid is."""
    kid: str | None
    day: date
    start: str
    end: str
    usual_start: str
    usual_end: str

    def to_dict(self) -> dict[str, Any]:
        return {"kid": self.kid, "date": self.day.isoformat(), "start": self.start, "end": self.end,
                "usual_start": self.usual_start, "usual_end": self.usual_end}

    @classmethod
    def from_dict(cls, data: Any) -> SchoolHours | None:
        try:
            return cls(data.get("kid"), date.fromisoformat(data["date"]), *(
                _hm(data[k]) or "" for k in ("start", "end", "usual_start", "usual_end")))
        except (AttributeError, KeyError, TypeError, ValueError):
            return None


def _hm(value: Any) -> str | None:
    """A time the CLI gives ("8:15", "08:15:00") as "HH:MM", or None."""
    m = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})(?::\d{2})?\s*", str(value or ""))
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        return None
    return f"{int(m[1]):02}:{m[2]}"


def day_hours(lessons: Iterable[tuple[str, Any, Any]]) -> dict[date, tuple[str, str]]:
    """Each day's first start and last end from one Kid's lessons, each (date, start, end) as the
    CLI gives it. A lesson without a date or times is left out; a day without lessons has none."""
    out: dict[date, tuple[str, str]] = {}
    for day_text, start_text, end_text in lessons:
        try:
            day = date.fromisoformat(str(day_text))
        except ValueError:
            continue
        start, end = _hm(start_text), _hm(end_text) or _hm(start_text)
        if start is None or end is None:
            continue
        first, last = out.get(day, (start, end))
        out[day] = (min(first, start), max(last, end))
    return out


def _most_common(values: list[str]) -> str | None:
    counts = Counter(values).most_common(2)
    if not counts or counts[0][1] < AGREEING_DAYS or (len(counts) > 1 and counts[1][1] == counts[0][1]):
        return None
    return counts[0][0]


def usual(days: dict[date, tuple[str, str]], day: date) -> tuple[str, str] | None:
    """The usual start and end on `day`'s weekday: each the one most of the other days of that
    weekday share, by at least AGREEING_DAYS of them. None when either isn't clear."""
    others = [hours for d, hours in days.items() if d.weekday() == day.weekday() and d != day]
    start, end = _most_common([s for s, _ in others]), _most_common([e for _, e in others])
    return (start, end) if start and end else None


def changed(kid: str | None, days: dict[date, tuple[str, str]], day: date) -> SchoolHours | None:
    """`kid`'s hours on `day` when they differ from the usual ones, or None."""
    if day not in days or (normally := usual(days, day)) is None or days[day] == normally:
        return None
    return SchoolHours(kid, day, *days[day], *normally)


# A line of the Digest that heads a Kid's section: `## Mia`, or `**Mia**` with perhaps a note.
_HEADING = re.compile(r"\s*(#{2,3}\s+.+|\*\*[^*]+\*\*\s*([(（][^()（）]*[)）])?\s*)")


def _about(brief: Brief, kid: str) -> str:
    """What the model wrote about `kid`: their Notices and their part of the Digest (the whole
    Digest when it has no Kid headings)."""
    lines = brief.digest.splitlines()
    if any(_HEADING.fullmatch(line) for line in lines):
        mine, theirs = [], False
        for line in lines:
            if _HEADING.fullmatch(line):
                theirs = kid.casefold() in line.casefold()
            elif theirs:
                mine.append(line)
    else:
        mine = lines
    notices = [n.text for k in brief.kids if (k.kid or "").casefold() == kid.casefold() for n in k.notices]
    return "\n".join([*mine, *notices])


def _names(text: str, hm: str) -> bool:
    """Whether `text` names the time `hm` ("08:15"), as 8:15, 08:15 or 8.15."""
    hour, minute = hm.split(":")
    return re.search(rf"(?<![\d:.])0?{int(hour)}[:.]{minute}(?!\d)", text) is not None


def told(brief: Brief, hours: SchoolHours) -> bool:
    """Whether the model already gave `hours`' Kid's usual hours: its text about them names a
    usual time that differs from the day's."""
    text = _about(brief, hours.kid or "")
    differing = [u for u, d in ((hours.usual_start, hours.start), (hours.usual_end, hours.end)) if u != d]
    return any(_names(text, u) for u in differing)


def to_tell(messages: Iterable[Message], brief: Brief,
            kid_called: Callable[[str | None], str | None]) -> list[SchoolHours]:
    """The changed school hours tonight's timetable Message gives that the Brief should show: each
    Kid's under the name the Brief calls them by, unless the model already gave them."""
    out = []
    for m in messages:
        for data in m.metadata.get(KEY) or []:
            hours = SchoolHours.from_dict(data)
            if hours is None:
                continue
            hours = SchoolHours(kid_called(hours.kid), hours.day, hours.start, hours.end,
                                hours.usual_start, hours.usual_end)
            if not told(brief, hours):
                out.append(hours)
    return out
