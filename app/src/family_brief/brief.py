"""The Brief as the model writes it, typed: the Digest, a section per Kid with its Notices and
Action Items, and the calendar events. Beside it, the night's run notes: what the program
found out around the Brief (the model's error, what was read, the Held-back Messages, a reply
cut off, the citation counts).

`parse` reads the model's reply (the Brief's and its translation's), and `from_archive` and
`to_archive` the `summary` of an archive's raw JSON, whose keys are those the Brief had when it
was a dict, the run notes under underscored keys, so archives from before still read. Only here
are a Brief's keys spelled out, besides the schema the prompt shows the model."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Union

log = logging.getLogger(__name__)


# ── The Brief

@dataclass(kw_only=True)
class _Refs:
    # The ids of the inputs the model cites; None in a Brief archived before citations.
    refs: list[str] | None = None
    # Each ref that resolved, with the Sources it came from (citations.resolve).
    ref_sources: dict[str, list[str]] | None = None


@dataclass(kw_only=True)
class _Entry(_Refs):
    """What citations.resolve derives for a Notice or Action Item: the Sources its refs came
    from, whether any did, and whether it is a re-reminder of an item from before citations."""
    source: list[str] | None = None
    verified: bool | None = None
    legacy: bool = False


@dataclass
class Notice(_Entry):
    text: str


@dataclass
class ActionItem(_Entry):
    what: str
    by: str | None = None
    who: str | None = None


Entry = Union[Notice, ActionItem]


@dataclass
class KidSection:
    kid: str | None
    notices: list[Notice] = field(default_factory=list)
    action_items: list[ActionItem] = field(default_factory=list)


@dataclass
class Event(_Refs):
    """A calendar event the model found in tonight's Messages. citations.resolve gives it the
    `external_id` and `source` of the earliest Message it cites."""
    title: str | None = None
    start: str | None = None
    end: str | None = None
    location: str | None = None
    description: str | None = None
    kid: str | None = None
    external_id: str | None = None
    source: str | None = None


@dataclass
class Brief:
    digest: str = ""
    kids: list[KidSection] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)

    def entries(self) -> Iterator[Entry]:
        """Every Notice and Action Item, Kid by Kid."""
        for kid in self.kids:
            yield from kid.notices
            yield from kid.action_items


# ── The run notes

@dataclass
class Citations:
    """How many of the Brief's Notices and Action Items there are, and how many cite nothing
    tonight's inputs have, apart from the legacy re-reminders (citations.resolve)."""
    entries: int = 0
    unverified: int = 0
    legacy: int = 0


@dataclass
class RunNotes:
    error: str | None = None  # why the model failed, when the Brief is the rule-based fallback
    coverage: dict[str, dict] | None = None  # what each Source read, kept for later checks
    held_back: list[str] | None = None  # the external ids of the Messages the AI never saw
    incomplete: bool = False  # the model's reply looked cut off, so the Brief says so
    citations: Citations | None = None


# ── An earlier night's Brief

@dataclass
class EarlierBrief:
    """An earlier night's Kid sections that have something in them, which tonight's Brief stays
    consistent with, and the people that night's Messages came from (for the AI filter, not
    for the model)."""
    date: str
    kids: list[KidSection]
    third_parties: list[str] = field(default_factory=list)

    @classmethod
    def from_json(cls, day: dict[str, Any]) -> EarlierBrief:
        """As an eval case writes it: `{date, per_kid, third_parties}`."""
        return cls(str(day.get("date")), _kids(day.get("per_kid"), archived=True),
                   list(day.get("third_parties") or []))

    def for_prompt(self) -> dict[str, Any]:
        """As the model sees it: without what the program derived or the night's people."""
        return {"date": self.date,
                "per_kid": [{"kid": k.kid, **_kid_lists(k, derived=False)} for k in self.kids]}


# ── Reading the model's reply

_TOP_LEVEL = ("per_kid", "calendar_events", "message_digest")


def parse(reply: Any) -> Brief:
    """The model's reply in the schema's shape, so the rest of the night can trust it: lists
    where the schema has lists, text where it has text, and no entry that isn't an object or has
    nothing to say. What the program derives is left to citations.resolve."""
    return _brief(reply if isinstance(reply, dict) else {}, archived=False)


def looks_cut_off(reply: Any, repaired: bool) -> bool:
    """Whether a reply that needed json-repair lacks a top-level key, so was probably cut off."""
    reply = reply if isinstance(reply, dict) else {}
    missing = [k for k in _TOP_LEVEL if k not in reply
               and not (k == "message_digest" and "message_digest_cn" in reply)]
    if repaired and missing:
        log.warning("The model's reply looks cut off (no %s); the Brief will say so", ", ".join(missing))
    return repaired and bool(missing)


def _brief(data: dict[str, Any], archived: bool) -> Brief:
    # Archives from before the Brief had a language call the Digest message_digest_cn.
    digest = data.get("message_digest") or data.get("message_digest_cn")
    return Brief(_text(digest), _kids(data.get("per_kid"), archived),
                 [_event(ev, archived) for ev in _objects(data.get("calendar_events"), "calendar event")])


def _as_list(value: Any) -> list[Any]:
    """A list where the schema has one: a lone entry stands for a list of it, null for none."""
    if isinstance(value, list):
        return value
    return [] if value is None else [value]


def _objects(value: Any, what: str) -> list[dict[str, Any]]:
    out = []
    for entry in _as_list(value):
        if isinstance(entry, dict):
            out.append(entry)
        else:
            log.warning("Skipped a %s that isn't an object: %r", what, entry)
    return out


def _text(value: Any) -> str:
    """A text field as text: empty for null, and a list's or a dict's sections joined, a dict's
    under their keys (a Digest written as one section per Kid)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n\n".join(f"**{key}**\n{text}" for key, v in value.items() if (text := _text(v).strip()))
    if isinstance(value, list):
        parts = [text for v in value if (text := _text(v).strip())]
        return ("\n\n" if any("\n" in p for p in parts) else "\n").join(parts)
    return str(value)


def _scalar(entry: dict[str, Any], key: str) -> str | None:
    """`entry`'s `key` as text, or None when it has none (a null title reads as untitled, not
    "None")."""
    text = _text(entry.get(key))
    return text if text.strip() else None


def _refs(entry: dict[str, Any]) -> list[str] | None:
    refs = entry.get("refs")
    return [str(r) for r in refs] if isinstance(refs, list) else None


def _ref_sources(entry: dict[str, Any], archived: bool) -> dict[str, list[str]] | None:
    value = entry.get("ref_sources") if archived else None
    return {str(r): list(s or []) for r, s in value.items()} if isinstance(value, dict) else None


def _kids(value: Any, archived: bool) -> list[KidSection]:
    """per_kid as Kid sections, also when the model wrote it as an object keyed by Kid."""
    if isinstance(value, dict) and not {"kid", "notices", "action_items"} & value.keys():
        value = [{"kid": kid, **entry} if isinstance(entry, dict) else entry for kid, entry in value.items()]
    return [KidSection(_scalar(k, "kid"),
                       [Notice(**f) for f in _items(k.get("notices"), "text", (), archived)],
                       [ActionItem(**f) for f in _items(k.get("action_items"), "what", ("by", "who"), archived)])
            for k in _objects(value, "per_kid entry")]


def _items(value: Any, text_key: str, keys: tuple[str, ...], archived: bool) -> list[dict[str, Any]]:
    """Notices' or Action Items' fields, each with its text. A bare string still makes an entry,
    though it cites nothing; one with no text, such as the last of a cut-off reply, is dropped."""
    out = []
    for item in _as_list(value):
        item = item if isinstance(item, dict) else {text_key: item}
        text = _scalar(item, text_key)
        if text is None:
            log.warning("Dropped an entry with no %s: %r", text_key, item)
            continue
        fields: dict[str, Any] = {text_key: text, **{k: _scalar(item, k) for k in keys},
                                  "refs": _refs(item), "ref_sources": _ref_sources(item, archived)}
        if archived:
            source = item.get("source")
            fields.update(source=list(source) if isinstance(source, list) else None,
                          verified=item["verified"] if isinstance(item.get("verified"), bool) else None,
                          legacy=item.get("legacy") is True)
        out.append(fields)
    return out


def _event(ev: dict[str, Any], archived: bool) -> Event:
    texts = {k: _scalar(ev, k) for k in ("kid", "title", "start", "end", "location", "description")}
    derived = {"external_id": _scalar(ev, "external_id"), "source": _scalar(ev, "source")} if archived else {}
    return Event(**texts, refs=_refs(ev), ref_sources=_ref_sources(ev, archived), **derived)


# ── The Brief for its translation

def for_translation(brief: Brief, what: Callable[[str], str]) -> dict[str, Any]:
    """The Brief's text, and what has to come back as it is, in the shape `parse` reads the
    translation's reply in. Each Action Item goes as `what` gives it."""
    def event(ev: Event) -> dict[str, Any]:
        texts = {"kid": ev.kid, "title": ev.title, "start": ev.start, "description": ev.description}
        return {k: v for k, v in texts.items() if v}
    return {
        "message_digest": brief.digest,
        "per_kid": [{
            "kid": kid.kid,
            "notices": [{"text": n.text, "refs": n.refs or []} for n in kid.notices],
            "action_items": [{"what": what(a.what), "by": a.by, "refs": a.refs or []} for a in kid.action_items],
        } for kid in brief.kids],
        "calendar_events": [event(ev) for ev in brief.events],
    }


# ── The archive's `summary`

def from_archive(summary: Any) -> tuple[Brief, RunNotes]:
    """An archive's `summary` as the Brief and run notes it was written from, whichever version
    wrote it."""
    data = summary if isinstance(summary, dict) else {}
    cites = data.get("_citations")
    return _brief(data, archived=True), RunNotes(
        error=data.get("_llm_error"), coverage=data.get("_coverage"), held_back=data.get("_held_back"),
        incomplete=data.get("_incomplete") is True,
        citations=Citations(**{k: int(cites.get(k) or 0) for k in ("entries", "unverified", "legacy")})
        if isinstance(cites, dict) else None)


def to_archive(brief: Brief, notes: RunNotes) -> dict[str, Any]:
    """The Brief and the night's run notes as the archive's `summary` keeps them."""
    out: dict[str, Any] = {
        "per_kid": [{**({"kid": k.kid} if k.kid is not None else {}), **_kid_lists(k, derived=True)}
                    for k in brief.kids],
        "calendar_events": [_event_json(ev) for ev in brief.events],
        "message_digest": brief.digest,
    }
    if notes.incomplete:
        out["_incomplete"] = True
    if notes.citations is not None:
        c = notes.citations
        out["_citations"] = {"entries": c.entries, "unverified": c.unverified, "legacy": c.legacy}
    if notes.coverage is not None:
        out["_coverage"] = notes.coverage
    if notes.held_back is not None:
        out["_held_back"] = notes.held_back
    if notes.error is not None:
        out["_llm_error"] = notes.error
    return out


def _kid_lists(kid: KidSection, derived: bool) -> dict[str, Any]:
    return {"notices": [_entry_json({"text": n.text}, n, derived) for n in kid.notices],
            "action_items": [_entry_json({"what": a.what, "by": a.by, "who": a.who}, a, derived)
                             for a in kid.action_items]}


def _entry_json(texts: dict[str, Any], entry: Entry, derived: bool) -> dict[str, Any]:
    """An entry's JSON, without the fields it lacks, and with what the program derived when
    `derived`."""
    out = {**texts, "refs": entry.refs}
    if derived:
        out.update(ref_sources=entry.ref_sources, source=entry.source, verified=entry.verified,
                   legacy=entry.legacy or None)
    return {k: v for k, v in out.items() if v is not None}


def _event_json(ev: Event) -> dict[str, Any]:
    out = {"kid": ev.kid, "title": ev.title, "start": ev.start, "end": ev.end, "location": ev.location,
           "description": ev.description, "refs": ev.refs, "ref_sources": ev.ref_sources,
           "external_id": ev.external_id, "source": ev.source}
    return {k: v for k, v in out.items() if v is not None}
