"""Pre-filled links to the pilot feedback Form (see ops/feedback-form).

One Form serves the whole pilot; each link pre-fills the verdict, the item's text, its
program-derived Source, the backend, the date, the Household and the Kid, so a parent only
has to press submit. The links carry the item as the AI saw it, the Kids as Kid A and Kid B
and a pseudonym for the Household, so the team never gets anyone's name (ADR 0013)."""
from __future__ import annotations

import copy
import hashlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import yaml
from pydantic import ValidationError

from . import ai_filter
from .config import Config, FeedbackConfig
from .kid_placeholders import KidPlaceholders

# The pilot Form's `feedback` block, as ops/feedback-form's log prints it, shipped with the
# program: every pilot Household gets the same one, with its own household_label added on opt-in.
# Until the team ships it, setup doesn't offer pilot feedback.
PILOT_FORM = Path(__file__).parent / "pilot_feedback.yaml"


# Must match the Form's choices in ops/feedback-form/create_feedback_form.gs exactly. One Form
# serves Households of every language, so these stay English while the link labels follow
# each Recipient's language (see brief_text.py).
SAVED = "⭐ Glad this was here"
WRONG = "❌ This is wrong"
DIGEST_WRONG = "❌ The Digest has a mistake"

# Mail clients and link scanners get unreliable past ~2000 characters, and Chinese text grows
# ninefold when percent-encoded, so a whole Digest would not fit.
MAX_URL_LENGTH = 2000


def pilot_form() -> dict[str, Any] | None:
    """The shipped pilot Form's `prefill_base_url` and `fields`, or None when this version ships
    none, or one that wouldn't make links."""
    try:
        data = yaml.safe_load(PILOT_FORM.read_text())
        block = data["feedback"]
        form = {"prefill_base_url": block["prefill_base_url"], "fields": block["fields"]}
        if FeedbackConfig.model_validate({"enabled": True, **form}).active():
            return form
    except (OSError, yaml.YAMLError, TypeError, KeyError, ValidationError):
        pass
    return None


def _quote(text: str) -> str:
    return quote(text, safe="")


def _fit(text: str, budget: int) -> str:
    """The longest prefix of `text` whose encoded form fits `budget`, marked with … when cut."""
    if len(_quote(text)) <= budget:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(_quote(text[:mid] + "…")) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + "…"


class Links:
    """One evening's pre-filled links to the pilot Form, which carry no one's name (ADR 0013): an
    item's text has other people's names, phone numbers, email addresses and links as the
    placeholders in `people` (the run's, so as the AI saw them), each Kid is Kid A, Kid B in the
    Household's Kid order, and the Household is its pseudonym."""

    def __init__(self, cfg: Config, date: str, people: ai_filter.Placeholders):
        assert cfg.feedback.active(), "only made when cfg.feedback.active()"
        self._cfg, self._date = cfg, date
        self._people = copy.deepcopy(people)  # so masking here leaves the run's own as they were
        self._kids = KidPlaceholders(cfg.kids)

    def link(self, verdict: str, text: str, *, kid: str = "", source: Iterable[str] = ()) -> str:
        """A pre-filled Form URL. `source` is the program-derived one, empty when unverified."""
        fb = self._cfg.feedback
        f = fb.fields
        assert f is not None
        answers = {"usp": "pp_url", f.verdict: verdict, f.source: ",".join(source), f.backend: self._cfg.llm.backend,
                   f.date: self._date, f.household: pseudonym(fb.household_label), f.kid: self._kids.mask(kid)}
        url = fb.prefill_base_url + "?" + urlencode(answers, quote_via=quote) + f"&{f.item_text}="
        masked = self._kids.mask(self._people.mask_text(text))
        return url + _quote(_fit(masked, MAX_URL_LENGTH - len(url)))


def pseudonym(label: str) -> str:
    """The name the Form knows a Household by: a short hash of its `household_label`, made on the
    Mac, so the team can group a Household's feedback across evenings without learning the label."""
    label = label.strip().casefold()
    return f"Household {hashlib.sha256(label.encode()).hexdigest()[:6]}" if label else ""
