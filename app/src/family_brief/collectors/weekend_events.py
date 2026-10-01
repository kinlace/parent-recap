from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

from ..config import Config

log = logging.getLogger(__name__)

BASE = "https://api.hel.fi/linkedevents/v1"
MAX_PAGES = 20            # ~600 events cap
PAGE_SIZE = 30
FETCH_TIMEOUT = 20


@dataclass
class Candidate:
    ext_id: str
    title: str
    description: str
    start: datetime
    end: datetime | None
    price_eur: float | None    # None if free
    is_free: bool
    location_name: str
    locality: str              # Espoo / Helsinki / Vantaa / …
    address: str
    url: str
    keywords: list[str] = field(default_factory=list)
    audience_min_age: int | None = None
    audience_max_age: int | None = None
    data_source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ext_id": self.ext_id,
            "title": self.title,
            "description": self.description[:280],
            "start": self.start.isoformat(),
            "end": self.end.isoformat() if self.end else None,
            "price_eur": self.price_eur,
            "is_free": self.is_free,
            "location_name": self.location_name,
            "locality": self.locality,
            "address": self.address,
            "url": self.url,
            "keywords": self.keywords[:12],
            "audience": {"min_age": self.audience_min_age, "max_age": self.audience_max_age},
            "data_source": self.data_source,
        }


def _loc(v: Any, prefer: tuple[str, ...] = ("fi", "en", "sv")) -> str:
    """Linked Events localized dict → plain string. Also handles plain strings."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        for k in prefer:
            if v.get(k):
                return str(v[k]).strip()
        for val in v.values():
            if val:
                return str(val).strip()
    return ""


def _parse_price(offers: list[dict] | None) -> tuple[bool, float | None]:
    """Return (is_free, price_eur_or_None). Extract lowest numeric price if not free."""
    if not offers:
        return (False, None)
    is_free = any(o.get("is_free") for o in offers)
    if is_free:
        return (True, 0.0)
    lowest: float | None = None
    for o in offers:
        price_val = _loc(o.get("price"))
        # Grab a numeric like "5,00", "10-20", "€5"
        for m in re.finditer(r"(\d+(?:[,.]\d+)?)", price_val):
            try:
                p = float(m.group(1).replace(",", "."))
                if lowest is None or p < lowest:
                    lowest = p
            except ValueError:
                pass
    return (False, lowest)


def _weekend_range(now_local: datetime | None = None) -> tuple[date, date]:
    """Return (Saturday, Sunday) dates for the coming weekend (or this weekend if run Fri/Sat)."""
    now = now_local or datetime.now()
    # Days until Saturday (weekday 5). If today is Sat/Sun, use current weekend.
    dow = now.weekday()
    if dow == 5:      # Sat
        sat = now.date()
    elif dow == 6:    # Sun
        sat = now.date() - timedelta(days=1)
    else:
        sat = now.date() + timedelta(days=(5 - dow))
    return (sat, sat + timedelta(days=1))


def _fetch_json(url: str) -> dict:
    r = requests.get(url, timeout=FETCH_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _resolve_place(place_url: str, cache: dict[str, dict]) -> dict:
    if place_url in cache:
        return cache[place_url]
    try:
        data = _fetch_json(place_url)
    except Exception as e:
        log.warning("failed to resolve place %s: %s", place_url, e)
        data = {}
    cache[place_url] = data
    return data


def _to_dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def collect(cfg: Config) -> list[Candidate]:
    sat, sun = _weekend_range()
    log.info("weekend range: %s → %s", sat, sun)
    wanted_regions = {r.lower() for r in cfg.weekend_events.regions}
    price_cap = cfg.weekend_events.max_price_eur
    place_cache: dict[str, dict] = {}

    candidates: list[Candidate] = []
    url = (f"{BASE}/event/?start={sat.isoformat()}&end={sun.isoformat()}"
           f"&page_size={PAGE_SIZE}&format=json")
    pages = 0
    while url and pages < MAX_PAGES:
        pages += 1
        try:
            payload = _fetch_json(url)
        except Exception as e:
            log.error("linkedevents fetch failed: %s", e)
            break
        for ev in payload.get("data", []):
            is_free, price = _parse_price(ev.get("offers"))
            # Price filter — accept free, or a positive price under cap. If price cannot be parsed
            # AND it's clearly not free, we defer to LLM (keep it) — cheap events often have no price data.
            if not is_free and price is not None and price > price_cap:
                continue

            place_ref = (ev.get("location") or {}).get("@id")
            place_data = _resolve_place(place_ref, place_cache) if place_ref else {}
            locality = _loc(place_data.get("address_locality")) or ""
            if wanted_regions and locality and locality.lower() not in wanted_regions:
                continue

            start = _to_dt(ev["start_time"]) if ev.get("start_time") else None
            if not start:
                continue
            end = _to_dt(ev["end_time"]) if ev.get("end_time") else None

            # Weekend-only: require the start date to fall on Sat or Sun.
            # Kills ongoing exhibitions whose start_time was weeks ago.
            start_local_date = start.astimezone().date()
            if start_local_date not in (sat, sun):
                continue

            keywords: list[str] = []
            for kw in ev.get("keywords", []) or []:
                # keyword refs are {"@id": ".../keyword/yso:pxx/"} — extract the tail
                kid = (kw.get("@id") or "").rstrip("/").rsplit("/", 1)[-1]
                if kid:
                    keywords.append(kid)

            candidates.append(Candidate(
                ext_id=str(ev.get("id") or ""),
                title=_loc(ev.get("name")) or "(no title)",
                description=_loc(ev.get("short_description")) or _loc(ev.get("description")) or "",
                start=start,
                end=end,
                price_eur=price,
                is_free=is_free,
                location_name=_loc(place_data.get("name")) or "",
                locality=locality,
                address=_loc(place_data.get("street_address")) or "",
                url=_loc(ev.get("info_url")) or "",
                keywords=keywords,
                audience_min_age=ev.get("audience_min_age"),
                audience_max_age=ev.get("audience_max_age"),
                data_source=str(ev.get("data_source") or ""),
            ))
        url = payload.get("meta", {}).get("next")

    log.info("weekend_events: %d candidates after price+region filter (pages fetched: %d)",
             len(candidates), pages)
    return candidates
