"""eVaka's record in the registry of Sources (ADR 0022), which `family_brief.sources` lists.

A public daycare's calendar and messages, read through eVaka's citizen API with a password in the
secret store (ADR 0039): its events, the days the unit is closed and the reservation deadline,
which go into the .ics, as Eepos's do, and the messages the daycare sends guardians (#786). A
sensitive one comes only as a stub, which is withheld and named. Set up one sign-in per city on
the setup page's step (#791), from a terminal (`setup evaka`) or at the hosted sign-up (#790), as
Eepos's."""
from __future__ import annotations

from ...record import Source, _in, _same_everywhere

_HERE = __package__.partition(".")[2]  # sources.fi.evaka, as `_in` names it

EVAKA = Source("evaka", _same_everywhere("eVaka"), lambda cfg: bool(cfg.evaka.signins),
               skippable=True, connect_rank=7, collect=_in(f"{_HERE}.collector", "collect"),
               counts_events=True, lookback=lambda cfg: cfg.evaka,
               check=_in(f"{_HERE}.doctor", "check"), check_items={"evaka": "eVaka ({})"},
               secrets=_in(f"{_HERE}.collector", "secrets_of"),
               survey=_in(f"{_HERE}.uninstall", "survey"),
               sent_to_everyone=_in(f"{_HERE}.collector", "sent_to_everyone"),
               withheld=_in(f"{_HERE}.collector", "withheld"), held_back_link=lambda m: m.url,
               part="fi/evaka")
