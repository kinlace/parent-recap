"""Messages that look sensitive are held back from the AI (ADR 0013).

The words are in sensitive_words.yaml: one file with lists in Finnish, Swedish, English and Chinese
that a native speaker can review, which also says how an entry matches and what the lists aim at.
The check runs on the Mac and never goes through the AI. sensitive() says whether a text looks
sensitive and in which category, and hold_back() splits a night's messages into those that may go
to the AI and those held back, which the Brief lists itself.

The module knows nothing of the Brief, so other tools can check text with it too."""
from __future__ import annotations

import functools
import logging
import re
from collections.abc import Iterable
from pathlib import Path

import yaml

from .collectors.base import Message

log = logging.getLogger(__name__)

WORDS = Path(__file__).with_name("sensitive_words.yaml")
CATEGORIES = ("health", "support", "bullying", "welfare")
_NOT = "not"


def _pattern(entries: Iterable[str]) -> re.Pattern[str] | None:
    """One pattern for the list's `entries`, or None for an empty list. An entry is a stem that
    counts anywhere in a word, or stems in a row, each starting a word of its own after the one
    before, and a word may list its forms with |."""
    def entry(text: str) -> str:
        return r"\w*\s+".join("(?:" + "|".join(map(re.escape, word.split("|"))) + ")"
                              for word in text.split())
    alternatives = [entry(e) for e in entries if str(e).strip()]
    return re.compile("|".join(alternatives), re.IGNORECASE) if alternatives else None


@functools.cache
def _lists() -> tuple[dict[str, re.Pattern[str]], re.Pattern[str] | None]:
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


def sensitive(text: str | None) -> str | None:
    """The category of `text`'s sensitive words (health, support, bullying or welfare, the first
    in that order), or None when it has none."""
    if not text:
        return None
    patterns, skip = _lists()
    if skip is not None:
        text = skip.sub(" ", text)
    return next((category for category, pattern in patterns.items() if pattern.search(text)), None)


def category(message: Message) -> str | None:
    """The category of a message's sensitive words, in its subject, body or sender, or None."""
    return next((c for c in map(sensitive, (message.subject, message.body, message.sender)) if c), None)


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
