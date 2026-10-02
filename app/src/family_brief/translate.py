"""Tonight's Brief for a Recipient who reads another language than the first Recipient (ADR 0004).

The model writes the Brief once; for each other language, one more call translates the finished
summary. A reply that doesn't carry the same Brief (the same Kids, Notices, Action Items and
calendar events, with the same due dates, times and refs) counts as a failed translation. Only
text is taken from it: the translation is a copy of the original with its Digest, Notices, Action
Items and calendar event titles and descriptions replaced, so its Sources are the original's too."""
from __future__ import annotations

import copy
import json
import logging
from datetime import date, timedelta
from string import Template
from typing import Any, Callable

from . import citations, languages
from .brief_text import BriefText, Language
from .languages import FINNISH_WORDS, is_finnish
from .config import Config
from .summarize import call_llm, digest_of
from .utils.dates import today_str

log = logging.getLogger(__name__)


_INTRO = Template("""You translate a Household's daily Brief about their kids' school and hobbies from $original into $target. It goes to parents whose kids go to school in Finland.

You receive one JSON object: the Brief's Digest (message_digest), each kid's notices and action items, and the new calendar events.
""")

_TRANSLATE = Template("Translate every text value (message_digest, text, what, title, description) into $target")
_KEEP = [
    Template("Keep names, times, amounts and the Markdown formatting as they are, and write each date the way "
             "$target writes them in the text, such as $date"),
    Template("Keep every other value exactly as it is (kid, by, refs, start), and keep every list with the same "
             "entries in the same order: don't add, drop, merge or reorder anything"),
]
_KEEP_FINNISH = Template("Keep the key Finnish words the Brief kept as written (such as $finnish_words), so "
                         "parents can still search Wilma and talk to the teacher with them")
_REPLY = [
    Template("Reply with exactly one JSON object of the same shape: no markdown fences, no explanations"),
    Template("**JSON format**: every `\"` inside a string value must be escaped as `\\\"`; don't stand in "
             "full-width or curly quotes for it. To quote something inside a text value, use $quotes instead of "
             "straight double quotes, so the JSON doesn't break"),
]


def instructions(intro: Template, translate: Template, keep_finnish: Template, keep: list[Template],
                 src: BriefText, dst: BriefText, target: Language, day: date | None = None) -> str:
    """Numbered translation instructions: what to translate, the Finnish words to keep (none for a
    Finnish translation), what else to keep, and how to reply. Weekend Picks use them too. `day`
    shows how the target writes a date."""
    rules = [translate, *([] if is_finnish(target) else [keep_finnish]), *keep, *_REPLY]
    values = {"original": src.language_name, "target": dst.language_name, "quotes": dst.quotes,
              "finnish_words": FINNISH_WORDS, "date": dst.on(day) if day else ""}
    return (intro.substitute(values) + "\n"
            + "".join(f"{n}. {r.substitute(values)}\n" for n, r in enumerate(rules, 1)))


def system_prompt(src: BriefText, dst: BriefText, target: Language, tomorrow: date) -> str:
    return instructions(_INTRO, _TRANSLATE, _KEEP_FINNISH, _KEEP, src, dst, target, tomorrow)


def _unprefixed(what: str, t: BriefText) -> str:
    """A re-reminded Action Item without its prefix, which _merge puts back in the target language."""
    return what.removeprefix(t.re_reminder)


def _payload(summary: dict[str, Any], t: BriefText) -> dict[str, Any]:
    return {
        "message_digest": digest_of(summary),
        "per_kid": [{
            "kid": kid.get("kid"),
            "notices": [{"text": n.get("text", ""), "refs": n.get("refs", [])}
                        for n in kid.get("notices") or []],
            "action_items": [{"what": _unprefixed(str(a.get("what", "")), t), "by": a.get("by"),
                              "refs": a.get("refs", [])}
                             for a in kid.get("action_items") or []],
        } for kid in summary.get("per_kid") or []],
        "calendar_events": [{k: ev[k] for k in ("kid", "title", "start", "description") if ev.get(k)}
                            for ev in summary.get("calendar_events") or []],
    }


def _has_text(summary: dict[str, Any]) -> bool:
    return bool(digest_of(summary) or summary.get("calendar_events")
                or any(kid.get("notices") or kid.get("action_items") for kid in summary.get("per_kid") or []))


def _skeleton(brief: dict[str, Any], label: Callable[[Any], Any] = lambda kid: kid) -> dict[str, Any]:
    """The Brief without its text: everything a translation has to leave exactly as it was.
    `label` reads each Kid, so a translated Household label can count as the original's."""
    return {
        "per_kid": [{"kid": label(kid.get("kid")),
                     "notices": [n.get("refs") or [] for n in kid.get("notices") or []],
                     "action_items": [(a.get("by"), a.get("refs") or []) for a in kid.get("action_items") or []]}
                    for kid in brief.get("per_kid") or []],
        "calendar_events": [(label(ev.get("kid")), ev.get("start")) for ev in brief.get("calendar_events") or []],
    }


def _check(sent: dict[str, Any], reply: dict[str, Any], src: BriefText, dst: BriefText) -> None:
    """Raise unless the reply carries the same Brief as the payload it translates. No model call.
    The Household label is the program's word, so a model that translated it anyway lost nothing."""
    def label(kid: Any) -> Any:
        return src.household if kid == dst.household else kid
    try:
        matches = _skeleton(reply, label) == _skeleton(sent)
    except (AttributeError, TypeError):  # e.g. a list where an object belongs
        matches = False
    if not matches:
        raise ValueError("the translation doesn't carry the same Kids, Notices, Action Items, "
                         "calendar events, due dates or refs as the Brief")


def _text(obj: Any, key: str) -> str:
    value = obj.get(key) if isinstance(obj, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"the translation has no {key} text")
    return value


def _merge(original: dict[str, Any], reply: dict[str, Any], src: BriefText, dst: BriefText) -> dict[str, Any]:
    """The original with the reply's text in place of its own. The Household's label and the
    assignees are the program's own words, so they are swapped for the target's, not translated."""
    out = copy.deepcopy(original)

    def relabel(entry: dict[str, Any]) -> None:
        if entry.get("kid") == src.household:
            entry["kid"] = dst.household

    if digest_of(original):
        out["message_digest"] = _text(reply, "message_digest")
    # _check made sure the reply's lists line up with the original's; strict in case it didn't run.
    for kid, translated in zip(out.get("per_kid") or [], reply.get("per_kid") or [], strict=True):
        relabel(kid)
        for n, tn in zip(kid.get("notices") or [], translated.get("notices") or [], strict=True):
            if n.get("text"):
                n["text"] = _text(tn, "text")
        for a, ta in zip(kid.get("action_items") or [], translated.get("action_items") or [], strict=True):
            if a.get("what"):
                prefix = dst.re_reminder if str(a["what"]).startswith(src.re_reminder) else ""
                a["what"] = prefix + _text(ta, "what")
            if a.get("who") in src.who:
                a["who"] = dst.who[src.who.index(a["who"])]
    for ev, te in zip(out.get("calendar_events") or [], reply.get("calendar_events") or [], strict=True):
        relabel(ev)
        for key in ("title", "description"):
            if ev.get(key):
                # A link the original didn't have would reach the calendar unchecked (#89).
                ev[key] = citations.keep_links(_text(te, key), ev[key])
    return out


def translate(cfg: Config, summary: dict[str, Any], original: Language, target: Language) -> dict[str, Any]:
    """`summary`, written in `original`, as a Recipient reading `target` gets it. Raises when the
    model call fails or its reply doesn't carry the same Brief; the caller then sends the original."""
    if not _has_text(summary):  # e.g. a night with only MyClub events: nothing for the model to do
        return summary
    src, dst = languages.text(cfg, original), languages.text(cfg, target)
    log.info("Translating the Brief from %s into %s", original, target)
    payload = _payload(summary, src)
    tomorrow = date.fromisoformat(today_str(cfg.timezone)) + timedelta(days=1)
    reply = call_llm(cfg, json.dumps(payload, ensure_ascii=False, indent=2),
                     system_prompt(src, dst, target, tomorrow)).data
    _check(payload, reply, src, dst)
    return _merge(summary, reply, src, dst)
