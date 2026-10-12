"""Each Kid as Kid A, Kid B, in the Household's Kid order (ADR 0013): for Weekend Picks' model,
which never sees a Kid's name, for the pilot feedback links, which never carry one, and for the
evening Brief's model calls while the AI filter's mask_kids switch is on (#268)."""
from __future__ import annotations

import re
from collections.abc import Callable, Collection
from typing import Any

from .ai_filter import inflected, name_stems
from .config import Kid

# For the model's instructions, wherever a payload has the Kids as their placeholders (#268).
INSTRUCTION = ("Each kid is a placeholder such as Kid A, in kid_profiles and wherever a message names them, and "
               "the program puts their names back. Call each kid by their placeholder exactly as written, in "
               "every language: never translate it or guess a name. In Finnish, a case ending goes after a "
               "kid's placeholder with a colon, as in Kid A:lle")

_LATIN = "A-Za-zÀ-ÖØ-öø-ÿ"
# Finnish case endings a Kid's name may carry in a parent's own text (Mian, Mialle).
_FINNISH_ENDINGS = ("n", "a", "ä", "ta", "tä", "na", "nä", "ksi", "lla", "llä", "lta", "ltä", "lle",
                    "ssa", "ssä", "sta", "stä", "kin")
_CLITICS = "(?:kin|kaan|kään)"
# A placeholder, with the Finnish case ending `mask` wrote after a colon (Kid A:lle), if any.
_PLACEHOLDER = re.compile(r"(?<![A-Za-z])Kid ([A-Z])(?::([A-Za-zäöÄÖ]+))?(?![A-Za-z])")


class KidPlaceholders:
    """Each Kid as Kid A, Kid B (in the Household's Kid order), and back. Every name, everyday
    name and alias of a Kid becomes their placeholder, and the Recipient sees the everyday name
    wherever the model wrote one."""

    def __init__(self, kids: list[Kid]):
        self.kids = kids[:26]
        by_term: dict[str, tuple[str, str]] = {}  # each term once, for the first Kid it names
        for i, kid in enumerate(self.kids):
            first = kid.name.split()[0] if len(kid.name.split()) > 1 else None
            for term in (kid.name, first, kid.everyday_name, *kid.aliases):
                if term and term.strip():
                    by_term.setdefault(term.strip().casefold(), (term.strip(), self.placeholder(i)))
        terms = sorted(by_term.values(), key=lambda tp: len(tp[0]), reverse=True)  # full name first
        self._placeholders: list[str] = []  # by match group: the placeholder its form stands for
        alternatives = []
        for term, placeholder in terms:
            for stem, endings in self._forms(term):
                alternatives.append(self._form_pattern(len(self._placeholders), term, stem, endings))
                self._placeholders.append(placeholder)
        self._pattern = re.compile("|".join(alternatives), re.IGNORECASE) if alternatives else None

    @staticmethod
    def placeholder(i: int) -> str:
        return f"Kid {chr(ord('A') + i)}"

    @staticmethod
    def _forms(term: str) -> list[tuple[str, str]]:
        """The stems a term stands in text with, each with the Finnish endings it takes: the term
        itself with any or none, and for a Latin name the stems its last word changes to (Pekka as
        Pekan, Elias as Eliaksen), which always take one."""
        if not re.match(f"[{_LATIN}]", term[-1]):
            return [(term, "")]
        last = term.split()[-1]
        changed = [(term[:len(term) - len(last)] + stem, endings)
                   for stem, endings in (name_stems(last) if len(last) >= 3 else [])]
        return [(term, f"(?:{'|'.join(_FINNISH_ENDINGS)})?"), *changed]

    @staticmethod
    def _form_pattern(j: int, term: str, stem: str, endings: str) -> str:
        """One form of the term on its own, not inside a longer Latin word: the stem as group j,
        then its ending and a clitic, if any."""
        latin = re.compile(f"[{_LATIN}]")
        before = f"(?<![{_LATIN}])" if latin.match(term[0]) else ""
        after = f"{endings}{_CLITICS}?(?![{_LATIN}])" if latin.match(term[-1]) else ""
        return f"{before}(?P<f{j}>{re.escape(stem)}){after}"

    def mask(self, text: str) -> str:
        """`text` with every Kid's name or alias as their placeholder, an inflected one as `Kid A:lle`."""
        if not self._pattern:
            return text
        def one(m: re.Match) -> str:
            ending = m.group()[len(m.group(m.lastgroup)):]
            return self._placeholders[int(m.lastgroup[1:])] + (f":{ending}" if ending else "")
        return self._pattern.sub(one, text)

    def mask_all(self, value: Any, keep: Collection[str] = ()) -> Any:
        """`value` with `mask` applied to every string in it, except under the `keep` keys."""
        return _walk(value, self.mask, keep)

    def restore_all(self, value: Any) -> Any:
        """`value` with `restore` applied to every string in it."""
        return _walk(value, self.restore)

    def restore(self, text: str) -> str:
        """`text` with each Kid's placeholder as the name the Brief calls them by. A Finnish case
        ending joins a name that ends in a vowel (Kid A:lle as Mialle, Pekalle) and keeps its colon
        after other consonants, where the right form would need the name's own stem."""
        def one(m: re.Match) -> str:
            i = ord(m.group(1)) - ord("A")
            if i >= len(self.kids):
                return m.group()
            return inflected(self.kids[i].called(), m.group(2) or "")
        return _PLACEHOLDER.sub(one, text)


def _walk(value: Any, text: Callable[[str], str], keep: Collection[str] = ()) -> Any:
    if isinstance(value, str):
        return text(value)
    if isinstance(value, list):
        return [_walk(v, text, keep) for v in value]
    if isinstance(value, dict):
        return {k: v if k in keep else _walk(v, text, keep) for k, v in value.items()}
    return value
