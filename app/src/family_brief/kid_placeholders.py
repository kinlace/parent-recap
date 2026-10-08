"""Each Kid as Kid A, Kid B, in the Household's Kid order (ADR 0013): for Weekend Picks' model,
which never sees a Kid's name, and for the pilot feedback links, which never carry one."""
from __future__ import annotations

import re
from typing import Any

from .config import Kid

_LATIN = "A-Za-zÀ-ÖØ-öø-ÿ"
# Finnish case endings a Kid's name may carry in a parent's own text (Mian, Mialle).
_FINNISH_ENDINGS = ("n", "a", "ä", "ta", "tä", "na", "nä", "ksi", "lla", "llä", "lta", "ltä", "lle",
                    "ssa", "ssä", "sta", "stä", "kin")
# A placeholder, with the Finnish case ending `mask` wrote after a colon (Kid A:lle), if any.
_PLACEHOLDER = re.compile(r"(?<![A-Za-z])Kid ([A-Z])(?::([A-Za-zäöÄÖ]+))?(?![A-Za-z])")
_VOWELS = "aeiouyäöåAEIOUYÄÖÅ"


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
        self._terms = sorted(by_term.values(), key=lambda tp: len(tp[0]), reverse=True)  # full name first
        self._pattern = re.compile("|".join(self._term_pattern(j, t) for j, (t, _) in enumerate(self._terms)),
                                   re.IGNORECASE) if self._terms else None

    @staticmethod
    def placeholder(i: int) -> str:
        return f"Kid {chr(ord('A') + i)}"

    @staticmethod
    def _term_pattern(j: int, term: str) -> str:
        """The term on its own, not inside a longer Latin word, with a Finnish case ending if any."""
        latin = re.compile(f"[{_LATIN}]")
        before = f"(?<![{_LATIN}])" if latin.match(term[0]) else ""
        after = f"(?:{'|'.join(_FINNISH_ENDINGS)})?(?![{_LATIN}])" if latin.match(term[-1]) else ""
        return f"{before}(?P<t{j}>{re.escape(term)}){after}"

    def mask(self, text: str) -> str:
        """`text` with every Kid's name or alias as their placeholder, an inflected one as `Kid A:lle`."""
        if not self._pattern:
            return text
        def one(m: re.Match) -> str:
            ending = m.group()[len(m.group(m.lastgroup)):]
            return self._terms[int(m.lastgroup[1:])][1] + (f":{ending}" if ending else "")
        return self._pattern.sub(one, text)

    def mask_all(self, value: Any) -> Any:
        """`value` with `mask` applied to every string in it."""
        if isinstance(value, str):
            return self.mask(value)
        if isinstance(value, list):
            return [self.mask_all(v) for v in value]
        if isinstance(value, dict):
            return {k: self.mask_all(v) for k, v in value.items()}
        return value

    def restore(self, text: str) -> str:
        """`text` with each Kid's placeholder as the name the Brief calls them by. A Finnish case
        ending joins a name that ends in a vowel (Kid A:lle as Mialle) and keeps its colon after
        a consonant, where the right form would need the name's own stem."""
        def one(m: re.Match) -> str:
            i = ord(m.group(1)) - ord("A")
            if i >= len(self.kids):
                return m.group()
            name, ending = self.kids[i].called(), m.group(2)
            if not ending:
                return name
            return name + ending if name[-1:] in _VOWELS else f"{name}:{ending}"
        return _PLACEHOLDER.sub(one, text)
