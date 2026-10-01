"""Deterministic scoring of one night's summary against a case's `expect` block.

Entries are matched on keywords, not exact text, because the model writes its own words in the
Brief's language (keeping only the Finnish key words in an English or Chinese Brief), so a spec
lists Chinese, English and Finnish alternatives, the Finnish as stems that survive inflection. A
keyword spec is a list of alternatives (any one must appear, case-insensitively); a list whose
items are themselves lists is a set of groups that must all appear. Everything is counted, not averaged, so `aggregate` can pool cases of different sizes."""
from __future__ import annotations

from datetime import datetime
from statistics import mean
from typing import Any

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


def _pair(expected: list[dict], predicted: list[dict], key: str, text_of) -> tuple[list, int]:
    """Greedily pair each expected entry with an unused predicted one whose text matches, trying
    the right Kid first. Returns the (expected, predicted) pairs of required entries, and the count
    of predicted entries that are neither paired nor an optional entry."""
    free = list(range(len(predicted)))
    pairs: list[tuple[dict, dict]] = []
    ordered = [e for e in expected if not e.get("optional")] + [e for e in expected if e.get("optional")]
    for exp in ordered:
        hits = [i for i in free if matches(exp[key], text_of(predicted[i]))]
        hits.sort(key=lambda i: not _kid_ok(exp.get("kid"), predicted[i].get("_kid")))
        if not hits:
            continue
        free.remove(hits[0])
        if not exp.get("optional"):
            pairs.append((exp, predicted[hits[0]]))
    return pairs, len(free)


def _entries(summary: dict, key: str) -> list[dict]:
    """A summary's Notices or Action Items across Kids, each tagged with the Kid it sits under."""
    return [{**e, "_kid": k.get("kid")} for k in summary.get("per_kid") or []
            for e in k.get(key) or [] if isinstance(e, dict)]


def _local_start(ev: dict, tz: str) -> str | None:
    try:
        return _localize(datetime.fromisoformat(str(ev.get("start"))), tz).strftime("%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def score_case(expect: dict[str, Any], summary: dict[str, Any] | None, tz: str) -> dict[str, Any]:
    """Counts for one night. A `None` summary (the model call failed) misses everything expected."""
    summary = summary or {}
    required = lambda items: sum(1 for e in items if not e.get("optional"))  # noqa: E731

    exp_actions = expect.get("action_items") or []
    actions = _entries(summary, "action_items")
    pairs, extra = _pair(exp_actions, actions, "what", lambda a: str(a.get("what", "")))
    action_counts = {
        "expected": required(exp_actions),
        "predicted": len(pairs) + extra,
        "matched": len(pairs),
        "due_ok": sum(1 for e, a in pairs if "by" not in e or str(a.get("by", ""))[:10] == str(e["by"])),
        "kid_ok": sum(1 for e, a in pairs if _kid_ok(e.get("kid"), a["_kid"])),
    }

    exp_events = expect.get("calendar_events") or []
    events = [ev for ev in summary.get("calendar_events") or [] if isinstance(ev, dict)]
    ev_pairs, ev_extra = _pair(exp_events, events, "title",
                               lambda ev: f"{ev.get('title', '')} {ev.get('description', '')}")
    event_counts = {
        "expected": required(exp_events),
        "predicted": len(ev_pairs) + ev_extra,
        "matched": len(ev_pairs),
        "start_ok": sum(1 for e, ev in ev_pairs
                        if "start" not in e or (_local_start(ev, tz) or "").startswith(str(e["start"]))),
    }

    notices = _entries(summary, "notices")
    rules = expect.get("notices") or {}
    must, must_not = rules.get("must") or [], rules.get("must_not") or []

    def found(rule: dict, entries: list[dict], text_of) -> bool:
        return any(matches(rule["text"], text_of(e)) and _kid_ok(rule.get("kid"), e["_kid"]) for e in entries)

    where = {"notices": [(notices, lambda n: str(n.get("text", "")))],
             "action_items": [(actions, lambda a: str(a.get("what", "")))]}
    where["both"] = where["notices"] + where["action_items"]
    said = lambda r: any(found(r, entries, text_of)  # noqa: E731
                         for entries, text_of in where[r.get("where", "notices")])
    notice_counts = {
        "required": len(must),
        "found": sum(1 for r in must if said(r)),
        "forbidden": len(must_not),
        "hits": sum(1 for r in must_not if said(r)),
    }

    cites = summary.get("_citations") or {}
    return {
        "actions": action_counts,
        "events": event_counts,
        "notices": notice_counts,
        "citations": {"entries": cites.get("entries", 0),
                      "verified": cites.get("entries", 0) - cites.get("unverified", 0) - cites.get("legacy", 0)},
    }


def is_clean(s: dict[str, Any]) -> bool:
    """Nothing missed, nothing extra, every date, Kid and start time right, no forbidden text."""
    a, e, n = s["actions"], s["events"], s["notices"]
    return (a["matched"] == a["expected"] == a["predicted"] == a["due_ok"] == a["kid_ok"]
            and e["matched"] == e["expected"] == e["predicted"] == e["start_ok"]
            and n["found"] == n["required"] and n["hits"] == 0 and s.get("valid_json", True))


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 3) if den else None


def aggregate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Pool the counts of many nights into rates; a rate with nothing to measure is None."""
    total = lambda part, key: sum(c[part][key] for c in cases)  # noqa: E731
    seconds = [c["seconds"] for c in cases if c.get("seconds") is not None]
    tokens = [c["tokens"] for c in cases if c.get("tokens") is not None]
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
        "citations_verified": _ratio(total("citations", "verified"), total("citations", "entries")),
        "valid_json": _ratio(sum(1 for c in cases if c.get("valid_json")), len(cases)),
        "cases_clean": _ratio(sum(1 for c in cases if is_clean(c)), len(cases)),
        "seconds_per_night": round(mean(seconds), 1) if seconds else None,
        "tokens_per_night": round(mean(tokens)) if tokens else None,
    }
