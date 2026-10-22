"""Deterministic scoring of one night's summary against a case's `expect` block.

Entries are matched on keywords, not exact text, because the model writes its own words in the
Brief's language (an English or Chinese Brief keeps a Finnish term only in brackets), so a spec
lists Chinese, English and Finnish alternatives, the Finnish as stems that survive inflection. A
keyword spec is a list of alternatives (any one must appear, case-insensitively); a list whose
items are themselves lists is a set of groups that must all appear. Everything is counted, not averaged, so `aggregate` can pool cases of different sizes."""
from __future__ import annotations

import re
from datetime import datetime
from statistics import mean
from typing import Any, Callable, TypeVar

from ..brief import Brief, Citations, Event
from ..summarize import _localize
from .cases import Spec


def matches(spec: Spec, text: str) -> bool:
    # Padded, so a keyword such as "klo 10 " also matches at the very end of the text.
    text = f" {text.casefold()} "
    if any(isinstance(s, list) for s in spec):
        return all(matches(s if isinstance(s, list) else [s], text) for s in spec)
    return any(str(s).casefold() in text for s in spec)


def _kid_ok(expected: str | list[str] | None, kid: object) -> bool:
    if expected is None:
        return True
    options = expected if isinstance(expected, list) else [expected]
    return str(kid).casefold() in {str(o).casefold() for o in options}


_T = TypeVar("_T")


def _pair(expected: list[dict], predicted: list[tuple[str | None, _T]], key: str, text_of: Callable[[_T], str],
          starts_then: Callable[[dict, _T], bool] = lambda exp, got: True,
          ) -> tuple[list[tuple[dict, tuple[str | None, _T]]], int]:
    """Greedily pair each expected entry with an unused predicted one (each with the Kid it sits
    under) whose text matches, trying the right Kid first. An optional entry only takes a
    predicted one that is also `starts_then`. Returns the (expected, predicted) pairs of required
    entries, and the count of predicted entries that are neither paired nor an optional entry."""
    free = list(range(len(predicted)))
    pairs: list[tuple[dict, tuple[str | None, _T]]] = []
    ordered = [e for e in expected if not e.get("optional")] + [e for e in expected if e.get("optional")]
    for exp in ordered:
        hits = [i for i in free if matches(exp[key], text_of(predicted[i][1]))
                and (not exp.get("optional") or starts_then(exp, predicted[i][1]))]
        hits.sort(key=lambda i: not _kid_ok(exp.get("kid"), predicted[i][0]))
        if not hits:
            continue
        free.remove(hits[0])
        if not exp.get("optional"):
            pairs.append((exp, predicted[hits[0]]))
    return pairs, len(free)


_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def _texts(brief: Brief) -> list[str]:
    """The Brief's texts: each Digest line, Notice and Action Item."""
    return ([line for line in brief.digest.splitlines() if line.strip()]
            + [n.text for k in brief.kids for n in k.notices] + [a.what for k in brief.kids for a in k.action_items])


def _date_counts(brief: Brief) -> dict[str, int]:
    """How many of the Brief's texts there are, and how many write a date as YYYY-MM-DD rather
    than the way the Brief's language writes one."""
    texts = _texts(brief)
    return {"texts": len(texts), "iso": sum(1 for text in texts if _ISO_DATE.search(text))}


# The AI filter's brackets, which no ordinary text has: a placeholder left in the Brief as it was.
_PLACEHOLDER = re.compile(r"[⟦⟧〚〛]")


def _placeholders_shown(brief: Brief) -> int:
    """How many of the Brief's texts and Calendar Events still show a placeholder that didn't go
    back to its value."""
    texts = _texts(brief) + [f"{ev.title or ''} {ev.description or ''} {ev.location or ''}" for ev in brief.events]
    return sum(1 for text in texts if _PLACEHOLDER.search(text))


def _local_start(ev: Event, tz: str) -> str | None:
    try:
        return _localize(datetime.fromisoformat(str(ev.start)), tz).strftime("%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def score_case(expect: dict[str, Any], brief: Brief | None, tz: str,
               lost_placeholders: int = 0, language: str | None = None,
               citations: Citations | None = None) -> dict[str, Any]:
    """Counts for one night. A `None` Brief (the model call failed) misses everything expected.
    `lost_placeholders` is how many the model changed beyond repair, so the Brief has their kind's
    words ("someone", "a link") where the night's text had the real value. A notice rule limited to
    other `languages` than the Brief's `language` is skipped. `citations` are the night's counts."""
    brief = brief or Brief()
    required = lambda items: sum(1 for e in items if not e.get("optional"))  # noqa: E731

    exp_actions = expect.get("action_items") or []
    actions = [(k.kid, a) for k in brief.kids for a in k.action_items]
    pairs, extra = _pair(exp_actions, actions, "what", lambda a: a.what)
    action_counts = {
        "expected": required(exp_actions),
        "predicted": len(pairs) + extra,
        "matched": len(pairs),
        "due_ok": sum(1 for e, (_, a) in pairs if "by" not in e or (a.by or "")[:10] == str(e["by"])),
        "kid_ok": sum(1 for e, (kid, _) in pairs if _kid_ok(e.get("kid"), kid)),
    }

    exp_events = expect.get("calendar_events") or []
    starts_then = lambda e, ev: "start" not in e or (_local_start(ev, tz) or "").startswith(str(e["start"]))  # noqa: E731
    # An event sits under no Kid's section, so none is tried first for its Kid.
    ev_pairs, ev_extra = _pair(exp_events, [(None, ev) for ev in brief.events], "title",
                               lambda ev: f"{ev.title or ''} {ev.description or ''}", starts_then)
    event_counts = {
        "expected": required(exp_events),
        "predicted": len(ev_pairs) + ev_extra,
        "matched": len(ev_pairs),
        "start_ok": sum(1 for e, (_, ev) in ev_pairs if starts_then(e, ev)),
    }

    rules = expect.get("notices") or {}
    applies = lambda r: language is None or "languages" not in r or language in r["languages"]  # noqa: E731
    must = [r for r in rules.get("must") or [] if applies(r)]
    must_not = [r for r in rules.get("must_not") or [] if applies(r)]

    # Each text the rules may look for, with the Kid it sits under. Digest lines sit under no Kid,
    # so a rule with a `kid` never matches one.
    notices = [(k.kid, n.text) for k in brief.kids for n in k.notices]
    action_texts = [(kid, a.what) for kid, a in actions]
    digest = [(None, line) for line in brief.digest.splitlines() if line.strip()]
    where = {"notices": notices, "action_items": action_texts, "digest": digest,
             "both": notices + action_texts, "anywhere": notices + action_texts + digest}
    said = lambda r: any(matches(r["text"], text) and _kid_ok(r.get("kid"), kid)  # noqa: E731
                         for kid, text in where[r.get("where", "notices")])
    notice_counts = {
        "required": len(must),
        "found": sum(1 for r in must if said(r)),
        "forbidden": len(must_not),
        "hits": sum(1 for r in must_not if said(r)),
    }

    cites = citations or Citations()
    return {
        "actions": action_counts,
        "events": event_counts,
        "notices": notice_counts,
        "dates": _date_counts(brief),
        "placeholders": {"shown": _placeholders_shown(brief), "lost": lost_placeholders},
        "citations": {"entries": cites.entries, "verified": cites.entries - cites.unverified - cites.legacy},
    }


def is_clean(s: dict[str, Any]) -> bool:
    """Nothing missed, nothing extra, every date, Kid and start time right, no forbidden text, no
    date in the text written as YYYY-MM-DD, and every placeholder back to its value."""
    a, e, n = s["actions"], s["events"], s["notices"]
    p = s.get("placeholders") or {}  # runs saved before the metric have none
    return (a["matched"] == a["expected"] == a["predicted"] == a["due_ok"] == a["kid_ok"]
            and e["matched"] == e["expected"] == e["predicted"] == e["start_ok"]
            and n["found"] == n["required"] and n["hits"] == 0 and s["dates"]["iso"] == 0
            and not p.get("shown") and not p.get("lost")
            and s.get("valid_json", True))


# A night's tokens by kind, as llm.LLMReply.usage keeps them, and their metric names.
TOKEN_KINDS = {"input": "input_tokens", "cache_write": "cache_write_tokens",
               "cached_input": "cached_tokens", "output": "output_tokens"}
NIGHTS_PER_MONTH = 30


def _mean(values: list, digits: int | None) -> float | None:
    return round(mean(values), digits) if values else None


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 3) if den else None


def aggregate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Pool the counts of many nights into rates; a rate with nothing to measure is None."""
    total = lambda part, key: sum(c[part][key] for c in cases)  # noqa: E731
    seconds = [c["seconds"] for c in cases if c.get("seconds") is not None]
    usages = [c["usage"] for c in cases if c.get("usage")]
    usd = _mean([c["usd"] for c in cases if c.get("usd") is not None], 9)
    placeholders = [c.get("placeholders") or {} for c in cases]
    return {
        "action_recall": _ratio(total("actions", "matched"), total("actions", "expected")),
        "action_precision": _ratio(total("actions", "matched"), total("actions", "predicted")),
        "due_date_ok": _ratio(total("actions", "due_ok"), total("actions", "matched")),
        "kid_ok": _ratio(total("actions", "kid_ok"), total("actions", "matched")),
        "event_recall": _ratio(total("events", "matched"), total("events", "expected")),
        "event_precision": _ratio(total("events", "matched"), total("events", "predicted")),
        "event_start_ok": _ratio(total("events", "start_ok"), total("events", "matched")),
        "notice_recall": _ratio(total("notices", "found"), total("notices", "required")),
        "forbidden_hits": total("notices", "hits"),
        "dates_as_written": _ratio(total("dates", "texts") - total("dates", "iso"), total("dates", "texts")),
        "citations_verified": _ratio(total("citations", "verified"), total("citations", "entries")),
        "valid_json": _ratio(sum(1 for c in cases if c.get("valid_json")), len(cases)),
        "cases_clean": _ratio(sum(1 for c in cases if is_clean(c)), len(cases)),
        "placeholders_left": sum(p.get("shown", 0) + p.get("lost", 0) for p in placeholders),
        "seconds_per_night": _mean(seconds, 1),
        **{name: _mean([u.get(kind, 0) for u in usages], None) for kind, name in TOKEN_KINDS.items()},
        "usd_per_night": round(usd, 5) if usd is not None else None,
        "usd_per_month": round(usd * NIGHTS_PER_MONTH, 3) if usd is not None else None,
    }
