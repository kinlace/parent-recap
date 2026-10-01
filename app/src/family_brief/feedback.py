"""Pre-filled links to the pilot feedback Form (see ops/feedback-form).

One Form serves the whole pilot; each link pre-fills the verdict, the item's text, its
program-derived Source, the backend, the date, the Household and the Kid, so a parent only
has to press submit."""
from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import quote, urlencode

from .config import Config


# Must match the Form's choices in ops/feedback-form/create_feedback_form.gs exactly. One Form
# serves Households of every language, so these stay English while the link labels follow
# each Recipient's language (see brief_text.py).
SAVED = "⭐ Glad this was here"
WRONG = "❌ This is wrong"
DIGEST_WRONG = "❌ The Digest has a mistake"

# Mail clients and link scanners get unreliable past ~2000 characters, and Chinese text grows
# ninefold when percent-encoded, so a whole Digest would not fit.
MAX_URL_LENGTH = 2000


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


def link(cfg: Config, verdict: str, text: str, *, date: str, kid: str = "",
         source: Iterable[str] = ()) -> str:
    """A pre-filled Form URL. `source` is the program-derived one, empty when unverified."""
    fb = cfg.feedback
    assert fb.fields is not None, "only called when cfg.feedback.active()"
    f = fb.fields
    answers = {"usp": "pp_url", f.verdict: verdict, f.source: ",".join(source),
               f.backend: cfg.llm.backend, f.date: date, f.household: fb.household_label, f.kid: kid}
    url = fb.prefill_base_url + "?" + urlencode(answers, quote_via=quote) + f"&{f.item_text}="
    return url + _quote(_fit(text, MAX_URL_LENGTH - len(url)))
