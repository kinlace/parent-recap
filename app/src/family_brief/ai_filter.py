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
from collections.abc import Callable, Collection, Mapping
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

# What a contact detail is written with: ASCII letters and digits, and the Latin letters of Finnish
# and other European names. Chinese and Japanese characters, full-width punctuation (，。：（）【】)
# and spaces end it, so one written into Chinese text with no space around it stops where the text starts.
_LATIN = "A-Za-z0-9À-ÖØ-öø-ÿ"
_EMAIL = rf"(?<![{_LATIN}._%+-])[{_LATIN}._%+-]+@[{_LATIN}-]+(?:\.[{_LATIN}-]+)+"
# A web link, as a calendar app would make it clickable. Without a scheme only common top-level
# domains count, so a missing space after a full stop or a file name is not a link. Citations
# checks links with the same pattern, so every link it knows is masked too.
TLDS = "com|net|org|info|biz|fi|se|eu|io|co|me|app|dev|xyz|site|online|link|page|top|click|ly|to|gl|ru|cn|uk|de|us"
LINK_CHARS = rf"[{_LATIN}._~:/?#@!$&*+,;=%-]"
LINK = (rf"(?:[a-z][a-z0-9+.-]*://|www\.){LINK_CHARS}+"
        rf"|(?<![{_LATIN}.-])(?:[{_LATIN}-]+\.)+(?:{TLDS})(?![{_LATIN}-])(?:/{LINK_CHARS}*)?")
# International (+358 40 123 4567, +44 (0)20 7946 0958), Finnish (040 123 4567, (09) 816 2000)
# and Chinese mobile (13800138000) numbers, never part of a longer number or Latin word.
_PHONE = (r"(?<![A-Za-z0-9+])(?:"
          r"\+[0-9]{1,3}[ -]?(?:\(0\)[ -]?)?\(?[0-9]{1,4}\)?(?:[ -]?[0-9]{2,4}){1,4}"
          r"|\(?0[0-9]{1,3}\)?[ -]?[0-9]{2,4}(?:[ -]?[0-9]{2,4}){1,3}"
          r"|1[3-9][0-9][ -]?[0-9]{4}[ -]?[0-9]{4}"
          r")(?![A-Za-z0-9])")
_CONTACT = re.compile(rf"(?P<E>{_EMAIL})|(?P<L>{LINK})|(?P<P>{_PHONE})", re.IGNORECASE)
_PHONE_DIGITS = range(7, 16)

# A placeholder as the model may give it back: another case, spaced out, with full-width digits, a
# letter that only looks like P, E or L (Cyrillic, Greek or full-width), and other brackets. The
# white square brackets ⟦⟧ 〚〛 are never in ordinary text, so either one of them is enough. Others,
# such as [P1], (P1) or 【P1】, need an opening and a closing bracket, and a bare P1 is never a placeholder.
_LETTERS = str.maketrans("pelРрЕеΡρΕεＰＥＬｐｅｌ", "PELPPEEPPEEPELPEL")
_LETTER_SEEN = "[PELpelРрЕеΡρΕεＰＥＬｐｅｌ]"
_NUMBER_SEEN = r"[\s_-]*([0-9０-９]+)"
_ANY_CLOSE = "⟧〛】〕\\])）］"
_SEEN = re.compile(rf"[⟦〚]\s*({_LETTER_SEEN}){_NUMBER_SEEN}(?:\s*[{_ANY_CLOSE}])?"
                   rf"|[【〔\\[(（［]\s*({_LETTER_SEEN}){_NUMBER_SEEN}\s*[{_ANY_CLOSE}]"
                   rf"|(?<![A-Za-z0-9])({_LETTER_SEEN}){_NUMBER_SEEN}\s*[⟧〛]")
_ASCII_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
# A letter and number the night's own text has, such as a football team's P2017. In other brackets
# than ⟦⟧ the model may be quoting it, so it isn't taken for a placeholder.
_AS_WRITTEN = re.compile(r"(?<![A-Za-z0-9])([PEL])\s*([0-9]+)(?![0-9])", re.IGNORECASE)


class Placeholders:
    """One run's placeholders and the values they stand for, numbered per kind in the order the
    values are first met."""

    def __init__(self) -> None:
        self._tokens: dict[tuple[str, str], str] = {}
        self._values: dict[str, str] = {}   # token: value
        self._known: dict[str, str] = {}    # value: its first token
        self._known_pattern: re.Pattern[str] | None = None
        self._as_written: set[str] = set()   # P2017 and the like in the masked text
        self._counts = dict.fromkeys(KINDS, 0)

    def _token(self, kind: str, value: str) -> str:
        if (kind, value) not in self._tokens:
            self._counts[kind] += 1
            token = f"⟦{kind}{self._counts[kind]}⟧"
            self._tokens[kind, value] = token
            self._values[token] = value
            self._known.setdefault(value, token)
            self._known_pattern = None
        return self._tokens[kind, value]

    def _known_values(self) -> re.Pattern[str]:
        """Every value already given a placeholder, the longest first so none splits a longer one."""
        if self._known_pattern is None:
            self._known_pattern = re.compile("|".join(map(re.escape, sorted(self._known, key=len, reverse=True))))
        return self._known_pattern

    def mask_text(self, text: str) -> str:
        """`text` with its contact details as placeholders. A value this run has already seen is
        replaced as it is, wherever it stands, before the patterns look for new ones: a value put
        back into a reply may stand where the patterns wouldn't find it, such as right after a word."""
        self._as_written.update(f"{k.upper()}{int(n)}" for k, n in _AS_WRITTEN.findall(text))
        if self._known:
            text = self._known_values().sub(lambda m: self._known[m.group()], text)

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
        """`text` with the real values in place of the placeholders the model wrote, or `words` for
        the kind of one it changed beyond repair."""
        def replace(m: re.Match[str]) -> str:
            other_brackets = bool(m.group(3))
            letter, digits = next((m.group(i), m.group(i + 1)) for i in (1, 3, 5) if m.group(i))
            kind, number = letter.translate(_LETTERS), int(digits.translate(_ASCII_DIGITS))
            if other_brackets and f"{kind}{number}" in self._as_written:
                return m.group()
            token = f"⟦{kind}{number}⟧"
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
    return _walk(payload, placeholders.mask_text, keep, drop), placeholders


def restore(reply: T, placeholders: Placeholders, words: Mapping[str, str]) -> T:
    """A copy of the model's `reply` with the real values in place of their placeholders. One
    that can't be read any more becomes `words` for its kind: {"phone": ..., "email": ..., "link": ...}."""
    return _walk(reply, lambda text: placeholders.restore_text(text, words))


def _walk(value: Any, text: Callable[[str], str], keep: Collection[str] = (),
          drop: Collection[str] = ()) -> Any:
    """A copy of a JSON-like `value` with `text` applied to every string in it, except under the
    `keep` keys, which are copied as they are, and without the `drop` keys."""
    if isinstance(value, str):
        return text(value)
    if isinstance(value, Mapping):
        return {k: _walk(v, str) if k in keep else _walk(v, text, keep, drop)
                for k, v in value.items() if k not in drop}
    if isinstance(value, (list, tuple)):
        return [_walk(v, text, keep, drop) for v in value]
    return value
