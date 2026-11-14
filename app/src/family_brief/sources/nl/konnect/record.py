"""Konnect's record in the registry of Sources (ADR 0022), which `family_brief.sources` lists.

A Dutch childcare or after-school care (BSO) on Konnect's parent portal, read through the JSON its
own web app reads with a password in the secret store (ADR 0050): the messages, news and
newsletters it sends parents, and each time a child is planned to be there, which go into the .ics
as eVaka's events do. Never its timeline, with the children's photos and health entries. Set up one
sign-in per provider from a terminal (`setup konnect`), never on the setup page, so a family
elsewhere doesn't see a step for it; the maintainers can add one on the hosted server, so a hosted
Brief doesn't call it unread."""
from __future__ import annotations

from ...record import Source, _in, _same_everywhere

_HERE = "sources.nl.konnect"

KONNECT = Source("konnect", _same_everywhere("Konnect"), lambda cfg: bool(cfg.konnect.signins),
                 skippable=True, connect_rank=11, collect=_in(f"{_HERE}.collector", "collect"),
                 counts_events=True, setup_page=False, lookback=lambda cfg: cfg.konnect,
                 check=_in(f"{_HERE}.doctor", "check"), check_items={"konnect": "Konnect ({})"},
                 secrets=_in(f"{_HERE}.collector", "secrets_of"),
                 sent_to_everyone=_in(f"{_HERE}.collector", "sent_to_everyone"),
                 held_back_link=lambda m: m.url, part="nl/konnect")
