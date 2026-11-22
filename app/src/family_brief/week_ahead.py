"""The Week Ahead: the "Next week" section of Sunday's evening Brief (#1037, ADR 0053).

It lists, day by day, what is already known for the coming Monday to Friday: the Action Items due
on each day and the calendar events on it, exams and changed school hours among them. Nothing here
asks the model. The items come from what Parent Recap already has: the earlier Briefs in the
archive, tonight's Brief, and the events the Sources gave tonight, which a Source gives again every
evening within its window, so a moved exam is where the Source says it is now.

The same thing is listed once. Tonight's Source events come first, then tonight's Brief, then the
earlier Briefs from the latest back, and an item that is the same as one already taken is left out:
an Action Item due the same day for the same Kid with the same words (a re-reminder's prefix
aside), or an event on the same day for the same Kid, at the same time when both have one, whose
title shares its words' stems with the other's (a model's "Historian koe" and Wilma's "Koe:
Historia – Luvut 3–5")."""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta

from .brief import Brief
from .collectors.base import CalendarEvent

# How many evenings back the archive is read for the week's items: as far ahead as a Source looks
# (eVaka's and Social Schools' four weeks), and as far as teachers add exams (ADR 0040).
NIGHTS_BACK = 28

# How many items a day shows before "and N more", so the section stays short.
PER_DAY = 5


@dataclass
class WeekItem:
    """One line of the Week Ahead: an Action Item due on `day` (`due`), or an event on it, at
    `time` ("HH:MM") when it has one. `detail` is the first line of an event's description.
    A Source's own event (`from_source`) keeps its words in every language, as the Brief's list of
    new events does; the rest is the model's text, which a translation translates."""
    day: date
    kid: str | None
    text: str
    time: str | None = None
    detail: str = ""
    due: bool = False
    from_source: bool = False


@dataclass
class EarlierNight:
    """An earlier evening's Brief, as archived, and the people that evening's Messages came from,
    for the AI filter when the week's text is translated (ADR 0013)."""
    date: str
    brief: Brief
    third_parties: list[str] = field(default_factory=list)


def week_after(evening: date) -> list[date]:
    """The Monday to Friday after a Sunday `evening`, or nothing on any other evening."""
    if evening.weekday() != 6:
        return []
    return [evening + timedelta(days=n) for n in range(1, 6)]


# A detail is cut at this length, so a line stays one line.
DETAIL_CHARS = 60


def _detail(description: str | None) -> str:
    line = next((x.strip() for x in (description or "").splitlines() if x.strip()), "")
    return line if len(line) <= DETAIL_CHARS else line[:DETAIL_CHARS].rstrip() + "…"


def _event_item(ev: CalendarEvent, kid: str | None, from_source: bool) -> WeekItem:
    return WeekItem(ev.start.date(), kid, ev.title,
                    None if ev.all_day else f"{ev.start.hour:02}:{ev.start.minute:02}",
                    _detail(ev.description), from_source=from_source)


_WORD = re.compile(r"\w+")


def _stems(text: str) -> set[str]:
    """The stems of `text`'s longer words: their first five letters, so Finnish endings
    (Historia, Historian) count as one word. Short words (koe, to, ja) say too little."""
    return {w[:5] for w in _WORD.findall(text.casefold()) if len(w) >= 5 and not w.isdigit()}


def _unprefixed(text: str, reminders: tuple[str, ...]) -> str:
    """`text` without a re-reminder's prefix, in whichever language it was written."""
    for prefix in reminders:
        if prefix and text.startswith(prefix):
            return text[len(prefix):]
    return text


def _same(a: WeekItem, b: WeekItem) -> bool:
    if a.day != b.day or a.due != b.due or (a.kid and b.kid and a.kid != b.kid):
        return False
    if a.due:
        return _plain(a.text) == _plain(b.text)
    if a.time and b.time and a.time != b.time:
        return False
    if _plain(a.text) == _plain(b.text):
        return True
    sa, sb = _stems(a.text), _stems(b.text)
    shared = sa & sb
    return bool(shared) and len(shared) >= min(2, len(sa), len(sb))


def _plain(text: str) -> str:
    return " ".join(_WORD.findall(text.casefold()))


def items(days: list[date], source_events: Iterable[CalendarEvent], tonight: Brief,
          tonight_events: Iterable[CalendarEvent], earlier: list[EarlierNight], *,
          kid_called: Callable[[str | None], str | None], household: Iterable[str],
          reminders: Iterable[str], model_events: Callable[[Brief], list[CalendarEvent]]) -> list[WeekItem]:
    """The Week Ahead for `days`, each item once, by day: a day's all-day events, then its timed
    ones by time, then what falls due on it. `source_events` are tonight's Sources' events the
    Brief lists, `tonight_events` the model's in tonight's Brief, and `model_events` reads an
    earlier Brief's events. A Kid is called as the Brief calls them now, and an entry about the
    whole Household (any language's label for it, in `household`) names no Kid. An Action Item
    re-reminded shows without the prefix (any of `reminders`) its re-reminder has."""
    if not days:
        return []
    households = {h.casefold() for h in household}
    prefixes = tuple(sorted(set(reminders), key=len, reverse=True))

    def kid_of(kid: str | None) -> str | None:
        return None if kid is None or kid.casefold() in households else kid_called(kid)

    def dues(brief: Brief) -> list[WeekItem]:
        out = []
        for section in brief.kids:
            for a in section.action_items:
                try:
                    day = date.fromisoformat(str(a.by)[:10])
                except ValueError:
                    continue
                out.append(WeekItem(day, kid_of(section.kid), _unprefixed(a.what, prefixes), due=True))
        return out

    candidates = [
        *(_event_item(e, kid_of(e.kid), True) for e in source_events),
        *(_event_item(e, kid_of(e.kid), False) for e in tonight_events),
        *dues(tonight),
    ]
    for night in sorted(earlier, key=lambda n: n.date, reverse=True):
        candidates += [_event_item(e, kid_of(e.kid), False) for e in model_events(night.brief)]
        candidates += dues(night.brief)

    week = set(days)
    taken: list[WeekItem] = []
    for item in candidates:
        if item.day in week and item.text.strip() and not any(_same(item, t) for t in taken):
            taken.append(item)
    return sorted(taken, key=lambda i: (i.day, i.due, i.time is not None, i.time or ""))


def by_day(week: list[WeekItem]) -> list[tuple[date, list[WeekItem], int]]:
    """The Week Ahead's days that have something, each with the items it shows and how many more
    it leaves out."""
    days: dict[date, list[WeekItem]] = {}
    for item in week:
        days.setdefault(item.day, []).append(item)
    return [(day, got[:PER_DAY], max(0, len(got) - PER_DAY)) for day, got in sorted(days.items())]


def people(nights: list[EarlierNight]) -> list[str]:
    """The people of the earlier evenings the week reads, for the AI filter to mask."""
    return [name for night in nights for name in night.third_parties]
