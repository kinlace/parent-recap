"""Tonight's Brief for a Recipient who reads another language than the first Recipient (ADR 0004).

The model writes the Brief once; for each other language, one more call translates the finished
summary. A reply that doesn't carry the same Brief (the same Kids, Notices, Action Items and
calendar events, with the same due dates, times and refs) counts as a failed translation. Only
text is taken from it: the translation is a copy of the original with its Digest, Notices, Action
Items and calendar event titles and descriptions replaced, so its Sources are the original's too.
While the AI filter is on, the model gets the Brief with the night's placeholders (ADR 0013) and
the reply gets the real values back before it is checked. `per_language` fans the Brief and
Weekend Picks out to each language's Recipients."""
from __future__ import annotations

import copy
import logging
from datetime import date, timedelta
from string import Template
from typing import Callable, TypeVar

from . import ai_filter, citations, kid_placeholders, languages
from .brief import Brief, Event, KidSection, for_translation, parse
from .brief_text import BriefText, Language
from .languages import FINNISH_TERMS, is_finnish
from .config import Config
from .summarize import call_llm_filtered
from .utils.dates import today_str

log = logging.getLogger(__name__)

_T = TypeVar("_T")
_V = TypeVar("_V")


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
_PLACEHOLDERS = Template(ai_filter.INSTRUCTION)
_KID_PLACEHOLDERS = Template(kid_placeholders.INSTRUCTION)
_KEEP_FINNISH = Template(FINNISH_TERMS)
# The original's Finnish was kept for a reader who reads no Finnish; copied into a Finnish text it
# doubles a word or reads as a fragment (#287).
_INTO_FINNISH = Template(
    "The original may pair a Finnish word with its translation in brackets, either way round (such as "
    "syysloma（秋假） or 秋假（syysloma）): write that Finnish word once, with nothing in brackets after it. "
    "Write Finnish the original splits with slashes or quotes (such as koe/kokeita) as the words a Finnish "
    "parent would use in that sentence, instead of copying it")
_REPLY = [
    Template("Reply with exactly one JSON object of the same shape: no markdown fences, no explanations"),
    Template("**JSON format**: every `\"` inside a string value must be escaped as `\\\"`; don't stand in "
             "full-width or curly quotes for it. To quote something inside a text value, use $quotes instead of "
             "straight double quotes, so the JSON doesn't break"),
]


def instructions(intro: Template, translate: Template, keep_finnish: Template, keep: list[Template],
                 src: BriefText, dst: BriefText, target: Language, day: date | None = None) -> str:
    """Numbered translation instructions: what to translate, what to do with Finnish words (for a
    Finnish translation, with Finnish the original glossed or quoted), what else to keep, and how to
    reply. Weekend Picks use them too. `day` shows how the target writes a date."""
    rules = [translate, _INTO_FINNISH if is_finnish(target) else keep_finnish, *keep, *_REPLY]
    values = {"original": src.language_name, "target": dst.language_name, "quotes": dst.quotes,
              "date": dst.on(day) if day else ""}
    return (intro.substitute(values) + "\n"
            + "".join(f"{n}. {r.substitute(values)}\n" for n, r in enumerate(rules, 1)))


def system_prompt(src: BriefText, dst: BriefText, target: Language, tomorrow: date, masked: bool = False,
                  kids_masked: bool = False) -> str:
    """The translation instructions, saying how to treat placeholders when the Brief is `masked`,
    and the Kids' when `kids_masked`."""
    keep = [*_KEEP, _PLACEHOLDERS, *([_KID_PLACEHOLDERS] if kids_masked else [])] if masked else _KEEP
    return instructions(_INTRO, _TRANSLATE, _KEEP_FINNISH, keep, src, dst, target, tomorrow)


def _unprefixed(what: str, t: BriefText) -> str:
    """A re-reminded Action Item without its prefix, which _merge puts back in the target language."""
    return what.removeprefix(t.re_reminder)


def _has_text(brief: Brief) -> bool:
    return bool(brief.digest or brief.events or any(brief.entries()))


def _skeleton(brief: Brief, label: Callable[[str | None], str | None] = lambda kid: kid) -> tuple:
    """The Brief without its text: everything a translation has to leave exactly as it was.
    `label` reads each Kid, so a translated Household label can count as the original's."""
    return ([(label(kid.kid), [n.refs or [] for n in kid.notices],
              [(a.by, a.refs or []) for a in kid.action_items]) for kid in brief.kids],
            [(label(ev.kid), ev.start) for ev in brief.events])


def _check(original: Brief, reply: Brief, src: BriefText, dst: BriefText) -> None:
    """Raise unless the reply carries the same Brief as the original it translates. No model call.
    The Household label is the program's word, so a model that translated it anyway lost nothing."""
    def label(kid: str | None) -> str | None:
        return src.household if kid == dst.household else kid
    if _skeleton(reply, label) != _skeleton(original):
        raise ValueError("the translation doesn't carry the same Kids, Notices, Action Items, "
                         "calendar events, due dates or refs as the Brief")


def _text(value: str | None, what: str) -> str:
    # The reply has been parsed, so a text it lacks is None and one it has is not blank.
    if value is None:
        raise ValueError(f"the translation has no {what} text")
    return value


def _merge(original: Brief, reply: Brief, src: BriefText, dst: BriefText) -> Brief:
    """The original with the reply's text in place of its own. The Household's label and the
    assignees are the program's own words, so they are swapped for the target's, not translated."""
    out = copy.deepcopy(original)

    def relabel(entry: KidSection | Event) -> None:
        if entry.kid == src.household:
            entry.kid = dst.household

    if original.digest:
        out.digest = _text(reply.digest if reply.digest.strip() else None, "message_digest")
    # _check made sure the reply's lists line up with the original's; strict in case it didn't run.
    for kid, translated in zip(out.kids, reply.kids, strict=True):
        relabel(kid)
        for n, tn in zip(kid.notices, translated.notices, strict=True):
            n.text = _text(tn.text, "text")
        for a, ta in zip(kid.action_items, translated.action_items, strict=True):
            prefix = dst.re_reminder if a.what.startswith(src.re_reminder) else ""
            a.what = prefix + _text(ta.what, "what")
            if a.who in src.who:
                a.who = dst.who[src.who.index(a.who)]
    for ev, te in zip(out.events, reply.events, strict=True):
        relabel(ev)
        # A link the original didn't have would reach the calendar unchecked (#89).
        if ev.title:
            ev.title = citations.keep_links(_text(te.title, "title"), ev.title)
        if ev.description:
            ev.description = citations.keep_links(_text(te.description, "description"), ev.description)
    return out


def translate(cfg: Config, brief: Brief, original: Language, target: Language,
              placeholders: ai_filter.Placeholders | None = None) -> Brief:
    """`brief`, written in `original`, as a Recipient reading `target` gets it. Raises when the
    model call fails or its reply doesn't carry the same Brief; the caller then sends the original.
    `placeholders` are the run's, so the model sees each value as the Brief's call did."""
    if not _has_text(brief):  # e.g. a night with only MyClub events: nothing for the model to do
        return brief
    src, dst = languages.text(cfg, original), languages.text(cfg, target)
    log.info("Translating the Brief from %s into %s", original, target)
    tomorrow = date.fromisoformat(today_str(cfg.timezone)) + timedelta(days=1)
    # Restored before the checks, which compare links with the original's.
    payload = for_translation(brief, lambda what: _unprefixed(what, src))
    reply = parse(call_llm_filtered(cfg, "", payload,
                                    lambda masked, kids: system_prompt(src, dst, target, tomorrow, masked, kids),
                                    dst.placeholder_words(), placeholders).data)
    _check(brief, reply, src, dst)
    return _merge(brief, reply, src, dst)


def per_language(what: str, original: Language, by_language: dict[Language, list[str]],
                 translated: Callable[[Language], _T], in_language: Callable[[Language, list[str], _T], _V],
                 as_written: Callable[[list[str], str], _V], translation_failed: Callable[[Language], str]) -> list[_V]:
    """One version of `what` (the Brief, Weekend Picks) per language among the Recipients, the
    original first: `as_written(to, note)` for those reading `original`, and for each other
    language `in_language(language, to, translated(language))`. When the translation raises, its
    readers get the original after all, with `translation_failed(language)` as its note. With no
    Recipients, the original alone, to nobody."""
    versions = []
    for language, to in (by_language or {original: []}).items():
        if language == original:
            versions.append(as_written(to, ""))
            continue
        try:
            text = translated(language)
        except Exception as e:
            log.error("Translating %s into %s failed: %s (sending the original)", what, language, e)
            versions.append(as_written(to, translation_failed(language)))
            continue
        versions.append(in_language(language, to, text))
    return versions
