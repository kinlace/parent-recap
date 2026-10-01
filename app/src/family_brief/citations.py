"""Checks the model's citations against the inputs the program actually supplied.

The model cites input ids in each Notice's, Action Item's and calendar event's `refs`; it never
names a Source. Here each entry gets a program-derived `source` (the Sources its resolving refs
came from) and a `verified` flag, so a made-up item cannot also make up where it came from.
Calendar events are held to more, since they go into the family calendar and invite the partner:
one that cites none of tonight's Messages is dropped, and it keeps only links those Messages have."""
from __future__ import annotations

import logging
import re
from typing import Any

from .brief_text import TEXT
from .collectors.base import Message

log = logging.getLogger(__name__)


# The prefixes the system prompt asks for on re-reminded Action Items, in any Brief language.
RE_REMINDER = tuple(t.re_reminder for t in TEXT.values())

# Written by the program, not the model, so kept out of what the model sees of earlier Briefs.
DERIVED = ("source", "ref_sources", "verified", "legacy")


# A web or mail link, as a calendar app would make it clickable. Without a scheme only common
# top-level domains count, so a missing space after a full stop or a file name is not a link.
_TLDS = "com|net|org|info|biz|fi|se|eu|io|co|me|app|dev|xyz|site|online|link|page|top|click|ly|to|gl|ru|cn|uk|de|us"
_LINK = re.compile(r"(?:[a-z][a-z0-9+.-]*://|www\.|mailto:)[^\s<>\"'()\[\]]+"
                   rf"|\b(?:[a-z0-9-]+\.)+(?:{_TLDS})\b(?:/[^\s<>\"'()\[\]]*)?", re.IGNORECASE)


def keep_links(text: str, allowed: str) -> str:
    """`text` without the links that don't appear in `allowed`."""
    def check(m: re.Match[str]) -> str:
        link = m.group().rstrip(".,;:!?")
        if link.casefold() in allowed.casefold():
            return m.group()
        log.warning("Dropped a link the text it must come from doesn't have: %s", link)
        return m.group()[len(link):]
    return _LINK.sub(check, text)


def _entries(day: dict[str, Any]):
    for kid in day.get("per_kid") or []:
        yield from kid.get("notices") or []
        yield from kid.get("action_items") or []


def for_prompt(earlier_briefs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Earlier Briefs as the model wrote them."""
    def strip(entry: Any) -> Any:
        return {k: v for k, v in entry.items() if k not in DERIVED} if isinstance(entry, dict) else entry
    return [{**day, "per_kid": [{**kid, "notices": [strip(n) for n in kid.get("notices") or []],
                                 "action_items": [strip(a) for a in kid.get("action_items") or []]}
                                for kid in day.get("per_kid") or []]}
            for day in earlier_briefs]


def resolve(summary: dict[str, Any], messages: list[Message], upcoming_events: list[dict],
            already_captured: list[dict], earlier_briefs: list[dict[str, Any]]) -> None:
    """Annotate every entry of a model-written summary in place, once summarize.normalise has put
    it in the schema's shape. The inputs are exactly what tonight's prompt showed the model."""
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
        for entry in _entries(day):
            ref_sources = entry.get("ref_sources") if isinstance(entry, dict) else None
            for ref, sources in (ref_sources if isinstance(ref_sources, dict) else {}).items():
                for source in sources or []:
                    supply(ref, source)

    # Briefs archived before citations existed have no refs to carry over, so their re-reminders
    # cannot verify. Counting them as unverified would inflate the rate for the first nights.
    # Deliberately loose: while any old-shape item is fed in, every unverified re-reminder counts
    # as legacy, so a made-up one is miscounted too. Matching the earlier item's text instead would
    # miscount genuine carry-overs the model rewords. Either way it ends 3 nights after upgrading.
    has_legacy = any(not isinstance(a, dict) or "refs" not in a
                     for day in earlier_briefs for kid in day.get("per_kid") or []
                     for a in kid.get("action_items") or [])

    _resolve_events(summary, messages)

    counts = {"entries": 0, "unverified": 0, "legacy": 0}
    for kid in summary["per_kid"]:
        for entry in kid["notices"] + kid["action_items"]:
            refs = entry.get("refs")
            # Stored even when empty: an entry without the key reads as pre-citations tomorrow.
            entry["refs"] = refs = refs if isinstance(refs, list) else []
            entry["ref_sources"] = {str(r): sorted(known[str(r)]) for r in refs if str(r) in known}
            sources = set().union(*entry["ref_sources"].values())
            entry["source"] = sorted(sources)
            entry["verified"] = bool(sources)
            counts["entries"] += 1
            if sources:
                continue
            if has_legacy and str(entry.get("what", "")).startswith(RE_REMINDER):
                entry["legacy"] = True
                counts["legacy"] += 1
            else:
                counts["unverified"] += 1
    summary["_citations"] = counts


def _resolve_events(summary: dict[str, Any], messages: list[Message]) -> None:
    """Keep only the calendar events that cite one of tonight's Messages. Each takes its `source`
    and `external_id` from the earliest of those, and loses any link none of them has. The
    earliest, since they make the event's identity: a rerun that also cites a later reminder
    must not write (and invite to) the event again."""
    by_id: dict[str, list[Message]] = {}
    for m in messages:
        by_id.setdefault(str(m.external_id), []).append(m)
    kept = []
    for ev in summary["calendar_events"]:
        refs = ev.get("refs")
        ev["refs"] = refs = refs if isinstance(refs, list) else []
        cited = sorted({str(r) for r in refs} & by_id.keys())
        if not cited:
            log.warning("Dropped calendar event %r at %s: no message it cites was read tonight (refs %s)",
                        ev.get("title"), ev.get("start"), refs)
            continue
        ev["ref_sources"] = {r: sorted({m.source for m in by_id[r]}) for r in cited}
        first = min((m for r in cited for m in by_id[r]), key=lambda m: (m.timestamp, m.source, str(m.external_id)))
        ev["external_id"], ev["source"] = str(first.external_id), first.source
        allowed = "\n".join(f"{m.subject or ''}\n{m.body}\n{m.url or ''}" for r in cited for m in by_id[r])
        for key in ("title", "location", "description"):
            if isinstance(ev.get(key), str):
                ev[key] = keep_links(ev[key], allowed)
        kept.append(ev)
    summary["calendar_events"] = kept
