"""Eepos's record in the registry of Sources (ADR 0022), which `family_brief.sources` lists.

A music or art school's calendar, read through its portal's own JSON API with a password in the
secret store (ADR 0038). Its lessons and holidays are events, which nothing else puts in a
calendar, so they go into the .ics. Set up one sign-in per school on the setup page's step (#791),
from a terminal (`setup eepos`) or at the hosted sign-up (#769). A Hosted Household can have it, so
its Brief doesn't call it unread there."""
from __future__ import annotations

from ...record import Source, _in, _same_everywhere

_HERE = __package__.partition(".")[2]  # sources.fi.eepos, as `_in` names it

EEPOS = Source("eepos", _same_everywhere("Eepos"), lambda cfg: bool(cfg.eepos.signins),
               skippable=True, connect_rank=6, collect=_in(f"{_HERE}.collector", "collect"),
               counts_events=True, check=_in(f"{_HERE}.doctor", "check"),
               check_items={"eepos": "Eepos ({})"}, secrets=_in(f"{_HERE}.collector", "secrets_of"),
               part="fi/eepos")
