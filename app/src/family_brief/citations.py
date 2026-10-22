"""Checks the model's citations against the inputs the program actually supplied.

The model cites input ids in each Notice's, Action Item's and calendar event's `refs`; it never
names a Source. Here each entry gets a program-derived `source` (the Sources its resolving refs
came from) and a `verified` flag, so a made-up item cannot also make up where it came from.
Calendar events are held to more, since they go into the family calendar and invite the partner:
one that cites none of tonight's Messages is dropped, and it keeps only links those Messages have."""
from __future__ import annotations

import logging
import re

from .ai_filter import LINK, LINK_CHARS
from .brief import ActionItem, Brief, Citations, EarlierBrief
from .brief_text import TEXT
from .collectors.base import Message

log = logging.getLogger(__name__)


# The prefixes the system prompt asks for on re-reminded Action Items, in any Brief language.
RE_REMINDER = tuple(t.re_reminder for t in TEXT.values())


# A web or mail link: the AI filter's, so a link it masks is the link checked here (see ai_filter).
_LINK = re.compile(rf"mailto:{LINK_CHARS}+|{LINK}", re.IGNORECASE)


def keep_links(text: str, allowed: str) -> str:
    """`text` without the links that don't appear in `allowed`."""
    def check(m: re.Match[str]) -> str:
        link = m.group().rstrip(".,;:!?")
        if link.casefold() in allowed.casefold():
            return m.group()
        log.warning("Dropped a link the text it must come from doesn't have: %s", link)
        return m.group()[len(link):]
    return _LINK.sub(check, text)


def resolve(brief: Brief, messages: list[Message], upcoming_events: list[dict],
            already_captured: list[dict], earlier_briefs: list[EarlierBrief]) -> Citations:
    """Annotate every entry of a model-written Brief in place, and count them. The inputs are
    exactly what tonight's prompt showed the model."""
    known: dict[str, set[str]] = {}

    def supply(ref: object, source: str) -> None:
        if ref:
            known.setdefault(str(ref), set()).add(source)

    for m in messages:
        supply(m.external_id, m.source)
    for ev in upcoming_events:  # existing Google Calendar events, cited by conflict Notices
        supply(ev.get("id"), "calendar")
    for ev in already_captured:  # events queued straight from a Source, e.g. MyClub
        supply(ev.get("external_id"), ev.get("source") or "calendar")
    # A re-reminder carries the earlier item's refs. Only the refs that resolved back then are
    # lent, each with its own Sources, so a carried-over ref cannot turn verified or pick up
    # the Source of another ref it sat next to.
    for day in earlier_briefs:
        for kid in day.kids:
            for entry in [*kid.notices, *kid.action_items]:
                for ref, sources in (entry.ref_sources or {}).items():
                    for source in sources:
                        supply(ref, source)

    # Briefs archived before citations existed have no refs to carry over, so their re-reminders
    # cannot verify. Counting them as unverified would inflate the rate for the first nights.
    # Deliberately loose: while any old-shape item is fed in, every unverified re-reminder counts
    # as legacy, so a made-up one is miscounted too. Matching the earlier item's text instead would
    # miscount genuine carry-overs the model rewords. Either way it ends 3 nights after upgrading.
    has_legacy = any(a.refs is None for day in earlier_briefs for kid in day.kids for a in kid.action_items)

    _resolve_events(brief, messages)

    counts = Citations()
    for entry in brief.entries():
        # Stored even when empty: an entry without refs reads as pre-citations tomorrow.
        refs = entry.refs = entry.refs or []
        entry.ref_sources = {r: sorted(known[r]) for r in refs if r in known}
        sources = set().union(*entry.ref_sources.values())
        entry.source = sorted(sources)
        entry.verified = bool(sources)
        counts.entries += 1
        if sources:
            continue
        if has_legacy and isinstance(entry, ActionItem) and entry.what.startswith(RE_REMINDER):
            entry.legacy = True
            counts.legacy += 1
        else:
            counts.unverified += 1
    return counts


def _resolve_events(brief: Brief, messages: list[Message]) -> None:
    """Keep only the calendar events that cite one of tonight's Messages. Each takes its `source`
    and `external_id` from the earliest of those, and loses any link none of them has. The
    earliest, since they make the event's identity: a rerun that also cites a later reminder
    must not write (and invite to) the event again."""
    by_id: dict[str, list[Message]] = {}
    for m in messages:
        by_id.setdefault(str(m.external_id), []).append(m)
    kept = []
    for ev in brief.events:
        refs = ev.refs = ev.refs or []
        cited = sorted(set(refs) & by_id.keys())
        if not cited:
            log.warning("Dropped calendar event %r at %s: no message it cites was read tonight (refs %s)",
                        ev.title, ev.start, refs)
            continue
        ev.ref_sources = {r: sorted({m.source for m in by_id[r]}) for r in cited}
        first = min((m for r in cited for m in by_id[r]), key=lambda m: (m.timestamp, m.source, str(m.external_id)))
        ev.external_id, ev.source = str(first.external_id), first.source
        allowed = "\n".join(f"{m.subject or ''}\n{m.body}\n{m.url or ''}" for r in cited for m in by_id[r])
        if ev.title is not None:
            ev.title = keep_links(ev.title, allowed)
        if ev.location is not None:
            ev.location = keep_links(ev.location, allowed)
        if ev.description is not None:
            ev.description = keep_links(ev.description, allowed)
        kept.append(ev)
    brief.events = kept
