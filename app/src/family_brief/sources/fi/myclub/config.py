"""MyClub's part of the Household's config: its `myclub` section, which `family_brief.config`
lists among the Sources' sections, and how a Kid's `myclub_ical_url` holds their links, which the
config's Kid reads with `myclub_links`."""
from __future__ import annotations

from pydantic import BaseModel, Field


def myclub_links(value: object) -> list[str]:
    """A Kid's MyClub calendar links as the config holds them under `myclub_ical_url`: one link, as
    every config written before #810 has it, or a list of them, one per club (ADR 0041)."""
    given = value if isinstance(value, list) else [value]
    return [v for v in given if isinstance(v, str) and v.strip()]


class MyClubConfig(BaseModel):
    # Case-insensitive substrings; matching MyClub events are dropped (club-wide events unrelated to your kid).
    blocklist: list[str] = Field(default_factory=list)
