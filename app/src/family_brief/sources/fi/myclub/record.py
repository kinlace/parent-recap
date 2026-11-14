"""MyClub's record in the registry of Sources (ADR 0022), which `family_brief.sources` lists.

A club's calendar, read from each Kid's subscription links, one per club (ADR 0041), and checked
once for each of them. Its feed is usually subscribed to in the calendar app directly, so its
events go into the .ics only when asked."""
from __future__ import annotations

from ...record import Source, _in, _same_everywhere

_HERE = __package__.partition(".")[2]  # sources.fi.myclub, as `_in` names it

MYCLUB = Source("myclub", _same_everywhere("MyClub"),
                lambda cfg: any(k.myclub_ical_url for k in cfg.kids),
                skippable=True, connect_rank=4, collect=_in(f"{_HERE}.collector", "collect"),
                counts_events=True, sender_domain="myclub.fi",
                check=_in(f"{_HERE}.doctor", "check"), check_items={"myclub": "MyClub ({})"},
                in_ics=lambda cfg: cfg.google_calendar.ics_include_myclub,
                utc_hash_before_0_4=True, part="fi/myclub")
