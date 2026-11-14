"""Social Schools's record in the registry of Sources (ADR 0022), which `family_brief.sources` lists.

A school's announcements and calendar on Social Schools, which many schools in the Netherlands use,
read through the API its own apps read, signing in as its iOS app does with a password in the secret
store (ADR 0049). Its events go into the .ics, as eVaka's do. Every post goes to a group or the
whole school, so none is held back for its words. Set up from a terminal for now (`setup
socialschools`), on a family's own computer only: the hosted version doesn't offer it, and its
Brief doesn't name it, since the setup page doesn't either."""
from __future__ import annotations

from ...record import Source, _in, _same_everywhere

_HERE = __package__.partition(".")[2]  # sources.nl.socialschools, as `_in` names it

SOCIALSCHOOLS = Source("socialschools", _same_everywhere("Social Schools"),
                       lambda cfg: bool(cfg.socialschools.signins),
                       skippable=True, connect_rank=10, collect=_in(f"{_HERE}.collector", "collect"),
                       counts_events=True, systems=frozenset({"macos", "windows"}), hosted=False,
                       setup_page=False, lookback=lambda cfg: cfg.socialschools,
                       check=_in(f"{_HERE}.doctor", "check"),
                       secrets=_in(f"{_HERE}.collector", "secrets_of"),
                       sent_to_everyone=_in(f"{_HERE}.collector", "sent_to_everyone"),
                       held_back_link=lambda m: m.url, part="nl/socialschools")
