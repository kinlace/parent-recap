"""Messages that look sensitive are held back from the AI (ADR 0013).

The words are in sensitive_words.yaml: one file with lists in Finnish, Swedish, English and Chinese
that a native speaker can review, which also says how an entry matches and what the lists aim at.
The check runs on the Mac and never goes through the AI. sensitive() says whether a text looks
sensitive and in which category, match() also says which entry matched and where, and hold_back()
splits a night's messages into those that may go to the AI and those held back, which the Brief
lists itself.

A message sent to everyone is never held back, whatever words it has: the word lists can't tell a
notice to the whole school from a message about one child, but the Sources can (sent_to_everyone()).
match_message() holds that rule, so the evening run and ai-filter-report both follow it.

The module knows nothing of the Brief, so other tools can check text with it too."""
from __future__ import annotations

import functools
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from . import ai_filter
from .collectors.base import Message

log = logging.getLogger(__name__)

WORDS = Path(__file__).with_name("sensitive_words.yaml")
# Words about what happened count wherever they stand.
ANYWHERE = ("health", "support", "bullying", "welfare")
# The staff and services a newsletter lists with their phone numbers, email addresses or links count
# only on a line without one.
STAFF = "staff"
CATEGORIES = (*ANYWHERE, STAFF)
_NOT = "not"


@dataclass(frozen=True)
class Match:
    """Where a text looks sensitive: its category, the entry of sensitive_words.yaml that matched,
    and where the matched text starts and ends in that text. For a message, `field` says which of
    the texts fields() gives it is in."""
    category: str
    entry: str
    start: int
    end: int
    field: str | None = None


@dataclass(frozen=True)
class _List:
    """One pattern for a list, and its entries in the order of the pattern's groups: each entry is a
    group of its own, so a match's lastindex says which one matched."""
    pattern: re.Pattern[str]
    entries: list[str]


def _pattern(entries: Iterable[str]) -> _List | None:
    """One pattern for the list's `entries`, or None for an empty list. An entry is a stem that
    counts anywhere in a word, or stems in a row, each starting a word of its own after the one
    before. A word may list its forms with |, and one ending in . must end there."""
    def word(text: str) -> str:
        whole = text.endswith(".")
        forms = "(?:" + "|".join(map(re.escape, text.removesuffix(".").split("|"))) + ")"
        return forms + (r"(?!\w)" if whole else "")

    def entry(text: str) -> str:
        return r"\w*\s+".join(map(word, text.split()))
    entries = [e for e in entries if str(e).strip()]
    if not entries:
        return None
    return _List(re.compile("|".join(f"({entry(e)})" for e in entries), re.IGNORECASE), entries)


@functools.cache
def _lists() -> tuple[dict[str, _List], _List | None]:
    """Each category's pattern over every language, and the pattern of what is taken out first."""
    data = yaml.safe_load(WORDS.read_text(encoding="utf-8"))
    entries: dict[str, list[str]] = {key: [] for key in (*CATEGORIES, _NOT)}
    for language, lists in data.items():
        for key, words in (lists or {}).items():
            if key not in entries:
                raise ValueError(f"{WORDS.name}: {language} has a list {key!r}, which isn't one of "
                                 f"{', '.join(entries)}")
            entries[key] += [str(w) for w in words or []]
    patterns = {c: p for c in CATEGORIES if (p := _pattern(entries[c])) is not None}
    return patterns, _pattern(entries[_NOT])


def _contact_line(line: str) -> bool:
    """Whether `line` has a phone number, an email address or a link, as the AI filter finds them."""
    return ai_filter.mask(line)[0] != line


def _without_contacts(text: str) -> str:
    """`text`'s lines without those with a phone number, an email address or a link."""
    return "\n".join(line for line in text.splitlines() if not _contact_line(line))


def match(text: str | None) -> Match | None:
    """Where `text` looks sensitive, or None when it has no sensitive words: the first category in
    the order health, support, bullying, welfare and staff with a match, its first match in `text`
    and the entry that made it."""
    if not text:
        return None
    patterns, skip = _lists()
    checked = skip.pattern.sub(" ", text) if skip is not None else text
    for c in ANYWHERE:
        if c in patterns and (m := patterns[c].pattern.search(checked)):
            return _found(c, patterns[c], m, _where_taken_out(skip, text))
    if STAFF in patterns and (m := patterns[STAFF].pattern.search(_without_contacts(checked))):
        where = _where_taken_out(skip, text)
        return _found(STAFF, patterns[STAFF], m, [where[i] for i in _where_without_contacts(checked)])
    return None


# Where each character of the text a list was checked against stands in the text before: worked out
# only for a match, so the check itself stays as fast as it was.
def _where_taken_out(skip: _List | None, text: str) -> list[int]:
    """For each character of skip.pattern.sub(" ", text), where it stands in `text`."""
    where, last = [], 0
    for m in skip.pattern.finditer(text) if skip is not None else ():
        where += [*range(last, m.start()), m.start()]
        last = m.end()
    return where + list(range(last, len(text)))


def _where_without_contacts(text: str) -> list[int]:
    """For each character of _without_contacts(text), where it stands in `text`."""
    where, at, first = [], 0, True
    for line, whole in zip(text.splitlines(), text.splitlines(keepends=True)):
        if not _contact_line(line):
            if not first:
                where.append(at)  # the newline that joins it to the line before
            where += range(at, at + len(line))
            first = False
        at += len(whole)
    return where


def _found(category: str, found: _List, m: re.Match[str], where: list[int]) -> Match:
    """The Match of `m`, a match of `found` in a text whose characters stand at `where` in the
    original. A match starts and ends with an entry's own letters, never with what was taken out."""
    return Match(category, found.entries[m.lastindex - 1], where[m.start()], where[m.end() - 1] + 1)


def sensitive(text: str | None) -> str | None:
    """The category of `text`'s sensitive words (health, support, bullying, welfare or staff, the
    first in that order), or None when it has none."""
    found = match(text)
    return found.category if found else None


_ADDRESS = re.compile(r"<[^<>]*>")

# ── Sent to everyone (ADR 0013). Wilma's notification emails, by their subject in Finnish, English
# and Swedish. One can gather several kinds of new items, each under its own heading: announcements
# (Tiedotteet), which go to everyone, and messages, lesson notes, exams and exam grades, which go to
# the Household. A heading is a line with the kind alone, maybe after New and with a count or a
# colon: "Uudet tiedotteet (2):". The announcements run to the next heading or the end. Any other
# kind's heading ends them, so the list of those can be long: a word too many only checks more.
_WILMA_EMAIL = re.compile(r"\s*(?:viesti wilmasta|message from wilma|meddelande från wilma)\b", re.IGNORECASE)
_ANNOUNCEMENTS = r"tiedotte\w*|announcements|news|bulletins|nyheter"
_OTHER_KINDS = (r"viest\w*|messages?|meddelanden?|tuntimerkin\w*|lesson\s+notes|koearvosan\w*|exam\s+grades"
                r"|koke\w*|exams?|läks\w*|homework")
_HEADING = re.compile(rf"[^\w\n]*(?:(?:uudet|uusia|new|nya)\s+)?(?:(?P<announcements>{_ANNOUNCEMENTS})"
                      rf"|{_OTHER_KINDS})[^\w\n]*(?:\d+[^\w\n]*)?", re.IGNORECASE)


def _wilma_email(message: Message) -> bool:
    """Whether `message` is one of Wilma's notification emails, by its subject."""
    return message.source == "gmail" and bool(_WILMA_EMAIL.match(message.subject or ""))


def _without_announcements(body: str) -> str:
    """A Wilma notification email's `body` without its announcements, each from its heading to the
    next heading. With no announcements heading, the whole body."""
    kept, announcements = [], False
    for line in body.splitlines():
        if heading := _HEADING.fullmatch(line):
            announcements = heading["announcements"] is not None
        if not announcements:
            kept.append(line)
    return "\n".join(kept)


def sent_to_everyone(message: Message) -> bool:
    """Whether `message` went to everyone, so it is never held back: an announcement the Wilma Source
    reads from the news list, or an email the Gmail Source found a mailing list's headers on. A
    Wilma message, even one to every guardian of a class, is checked: the wilma CLI Parent Recap
    installs doesn't say who it went to. So is a post in a WhatsApp group, where incidents get
    discussed. Wilma's notification emails are checked outside their announcements (fields())."""
    if message.source == "wilma":
        return message.metadata.get("wilma_kind") == "news"
    return message.source == "gmail" and bool(message.metadata.get("mailing_list")) and \
        not _wilma_email(message)


def fields(message: Message) -> dict[str, str]:
    """The texts of a message the check reads: its subject, its body and its sender. The sender's
    name counts, as in Koulupsykologi Laine <laine@school.fi>, and its address doesn't. Of a Wilma
    notification email's body, only the part outside its announcements counts."""
    body = message.body or ""
    return {"subject": message.subject or "",
            "body": _without_announcements(body) if _wilma_email(message) else body,
            "sender": _ADDRESS.sub(" ", message.sender or "")}


def match_message(message: Message) -> Match | None:
    """Where a message looks sensitive, in the first of its subject, body and sender with sensitive
    words, or None, also for a message sent to everyone. The Match's start and end are in that text
    as fields() gives it."""
    if sent_to_everyone(message):
        return None
    return next((replace(found, field=name) for name, text in fields(message).items()
                 if (found := match(text))), None)


def category(message: Message) -> str | None:
    """The category of a message's sensitive words, in its subject, body or sender, or None."""
    found = match_message(message)
    return found.category if found else None


def hold_back(messages: Iterable[Message]) -> tuple[list[Message], list[Message]]:
    """`messages` split into those that may go to the AI and those held back, each in its order."""
    to_the_ai: list[Message] = []
    held: list[Message] = []
    for m in messages:
        found = category(m)
        if found is None:
            to_the_ai.append(m)
        else:
            held.append(m)
            log.info("Held back %s message %s from the AI: it looked sensitive (%s)",
                     m.source, m.external_id, found)
    return to_the_ai, held
