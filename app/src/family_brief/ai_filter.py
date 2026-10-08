"""What reaches the AI: the one step every model-bound payload passes through (ADR 0013).

mask() takes any JSON-like payload, such as a list of messages or a prompt's whole payload, and
returns a copy in which phone numbers, email addresses (also the address in `Name <address>`) and
links are opaque placeholders such as ⟦P1⟧, ⟦E1⟧ and ⟦L1⟧, together with the Placeholders that
map them back. INSTRUCTION tells the model to copy them unchanged, and restore() puts the real
values back in its reply. A placeholder the model changed so far that it can't be read any more
becomes the caller's general words for its kind ("a phone number"), so the text around it stays.

Placeholders are kept in memory only, for one run. A run passes the same Placeholders to each of
its calls, so a value has the same placeholder in every payload. Values under the `keep` keys are
sent as they are: ids, which the model cites and the program checks (ADR 0001). Keys in `drop` are
left out wherever they are, for identifiers the model doesn't need.

The module knows nothing of the Brief or the Sources, so other tools can filter with it too."""
from __future__ import annotations

import logging
import re
from collections.abc import Collection, Mapping
from typing import Any, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

# A placeholder's letter and the kind of value it stands for, the key of restore()'s words.
KINDS = {"P": "phone", "E": "email", "L": "link"}

# Fields that hold ids, which the model cites by value.
IDS = frozenset({"id", "external_id", "refs"})

# For the model's instructions, wherever a payload has placeholders.
INSTRUCTION = ("Phone numbers, email addresses and links are placeholders such as ⟦P1⟧, ⟦E1⟧ and ⟦L1⟧, "
               "and the program puts the real ones back. Copy each one you use exactly as written, with its "
               "brackets: never translate, reformat or renumber it, and never make one up")

_EMAIL = r"[\w.%+-]+@[\w-]+(?:\.[\w-]+)+"
# A web link, as a calendar app would make it clickable. Without a scheme only common top-level
# domains count, so a missing space after a full stop or a file name is not a link. Citations
# checks links with the same domains, so every link it knows is masked too.
TLDS = "com|net|org|info|biz|fi|se|eu|io|co|me|app|dev|xyz|site|online|link|page|top|click|ly|to|gl|ru|cn|uk|de|us"
_LINK = (r"(?:[a-z][a-z0-9+.-]*://|www\.)[^\s<>\"'()\[\]⟦⟧]+"
         rf"|\b(?:[a-z0-9-]+\.)+(?:{TLDS})\b(?:/[^\s<>\"'()\[\]⟦⟧]*)?")
# International (+358 40 123 4567, +44 (0)20 7946 0958), Finnish (040 123 4567, (09) 816 2000)
# and Chinese mobile (138 0013 8000) numbers, never part of a longer number or word.
_PHONE = (r"(?<![\w+])(?:"
          r"\+[0-9]{1,3}[ -]?(?:\(0\)[ -]?)?\(?[0-9]{1,4}\)?(?:[ -]?[0-9]{2,4}){1,4}"
          r"|\(?0[0-9]{1,3}\)?[ -]?[0-9]{2,4}(?:[ -]?[0-9]{2,4}){1,3}"
          r"|1[3-9][0-9][ -]?[0-9]{4}[ -]?[0-9]{4}"
          r")(?!\w)")
_CONTACT = re.compile(rf"(?P<E>{_EMAIL})|(?P<L>{_LINK})|(?P<P>{_PHONE})", re.IGNORECASE)
_PHONE_DIGITS = range(7, 16)

# A placeholder as the model may give it back: in other brackets, another case, spaced out, with
# full-width digits or missing one bracket.
_DIGITS = r"([0-9０-９]+)"
_SEEN = re.compile(rf"⟦\s*([PEL])\s*{_DIGITS}(?:\s*[⟧〛】])?"
                   rf"|[〚【]\s*([PEL])\s*{_DIGITS}\s*[⟧〛】]"
                   rf"|(?<![A-Za-z0-9])([PEL])\s*{_DIGITS}\s*[⟧〛]", re.IGNORECASE)
_ASCII_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


class Placeholders:
    """One run's placeholders and the values they stand for, numbered per kind in the order the
    values are first met."""

    def __init__(self) -> None:
        self._tokens: dict[tuple[str, str], str] = {}
        self._values: dict[str, str] = {}
        self._counts = dict.fromkeys(KINDS, 0)

    def _token(self, kind: str, value: str) -> str:
        if (kind, value) not in self._tokens:
            self._counts[kind] += 1
            token = f"⟦{kind}{self._counts[kind]}⟧"
            self._tokens[kind, value] = token
            self._values[token] = value
        return self._tokens[kind, value]

    def mask_text(self, text: str) -> str:
        def replace(m: re.Match[str]) -> str:
            kind = m.lastgroup or ""
            found = m.group()
            if kind == "L":  # a full stop or comma after a link ends the sentence, not the link
                value = found.rstrip(".,;:!?")
                return self._token(kind, value) + found[len(value):]
            if kind == "P" and sum(c.isdigit() for c in found) not in _PHONE_DIGITS:
                return found
            return self._token(kind, found)
        return _CONTACT.sub(replace, text)

    def restore_text(self, text: str, words: Mapping[str, str]) -> str:
        def replace(m: re.Match[str]) -> str:
            letter, digits = next((m.group(i), m.group(i + 1)) for i in (1, 3, 5) if m.group(i))
            kind = letter.upper()
            token = f"⟦{kind}{int(digits.translate(_ASCII_DIGITS))}⟧"
            if token in self._values:
                return self._values[token]
            log.warning("The model changed a placeholder so it can't be put back (%r); "
                        "the Brief says what kind of value it was", m.group())
            return words[KINDS[kind]]
        return _SEEN.sub(replace, text)


def mask(payload: T, placeholders: Placeholders | None = None, *,
         keep: Collection[str] = IDS, drop: Collection[str] = ()) -> tuple[T, Placeholders]:
    """A copy of `payload` with its contact details as placeholders, and the Placeholders that
    hold them: `placeholders` when given, so a run's calls share one numbering."""
    placeholders = placeholders if placeholders is not None else Placeholders()

    def walk(value: Any) -> Any:
        if isinstance(value, str):
            return placeholders.mask_text(value)
        if isinstance(value, Mapping):
            return {k: (_copy(v) if k in keep else walk(v)) for k, v in value.items() if k not in drop}
        if isinstance(value, (list, tuple)):
            return [walk(v) for v in value]
        return value
    return walk(payload), placeholders


def restore(reply: T, placeholders: Placeholders, words: Mapping[str, str]) -> T:
    """A copy of the model's `reply` with the real values in place of their placeholders. One
    that can't be read any more becomes `words` for its kind: {"phone": ..., "email": ..., "link": ...}."""
    def walk(value: Any) -> Any:
        if isinstance(value, str):
            return placeholders.restore_text(value, words)
        if isinstance(value, Mapping):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [walk(v) for v in value]
        return value
    return walk(reply)


def _copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _copy(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_copy(v) for v in value]
    return value
