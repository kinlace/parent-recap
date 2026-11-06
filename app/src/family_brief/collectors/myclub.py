from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests
import yaml
from icalendar import Calendar

from ..config import Config, myclub_links
from ..state import State
from .base import CalendarEvent, Collected, retry_once_on_timeout

log = logging.getLogger(__name__)

LOOKAHEAD_DAYS = 14

def _normalize_url(url: str) -> str:
    if url.startswith("webcal://"):
        return "https://" + url[len("webcal://"):]
    return url


class FetchError(Exception):
    """A failed download of a MyClub link. It names only the host and the HTTP status, since
    the link's path and query carry the Kid's personal token (requests puts them in its errors)."""


def download(url: str) -> str:
    """The calendar at a MyClub link (webcal:// or https://), as iCal text."""
    url = _normalize_url(url)
    host = urlsplit(url).hostname or "the MyClub link"
    try:
        r = retry_once_on_timeout(lambda: requests.get(url, timeout=30),
                                  (requests.Timeout,), f"MyClub {host}")
    except Exception as e:
        raise FetchError(f"{host}: {type(e).__name__}") from None
    if r.status_code >= 400:
        raise FetchError(f"{host} answered HTTP {r.status_code}")
    return r.text


def save_link(config_path: Path, kid: str, url: str) -> None:
    """Set `url` as `kid`'s one MyClub link. The Terminal script of 0.4.1 and older calls it."""
    save_links(config_path, kid, [url])


def save_links(config_path: Path, kid: str, urls: list[str]) -> None:
    """Set `kid`'s MyClub links in the config file, leaving every other line and comment as it
    is: one link as a string, as before #810, several as a list, none by removing the key (ADR
    0041). Used by `setup myclub`, the setup page and the sign-up, so a link never goes through
    the agent chat."""
    text = config_path.read_text(encoding="utf-8")
    root = yaml.compose(text)
    kids = next((v for k, v in getattr(root, "value", []) if k.value == "kids"), None)
    entry = next((item for item in getattr(kids, "value", [])
                  if isinstance(item, yaml.MappingNode)
                  and any(k.value == "name" and v.value == kid for k, v in item.value)), None)
    if entry is None:
        names = [v.value for item in getattr(kids, "value", []) if isinstance(item, yaml.MappingNode)
                 for k, v in item.value if k.value == "name"]
        raise ValueError(f"no Kid named {kid!r} in {config_path}; the Kids there are: "
                         + (", ".join(names) or "(none)"))
    if entry.flow_style:
        raise ValueError(f"{kid!r} is written on one line in {config_path}; put each key on its own line")
    # A JSON string is a valid YAML double-quoted scalar, and a JSON list a YAML flow sequence.
    quoted = json.dumps(urls[0] if len(urls) == 1 else urls)
    key, old = next(((k, v) for k, v in entry.value if k.value == "myclub_ical_url"), (None, None))
    if old is not None and not urls:
        start = text.rfind("\n", 0, key.start_mark.index) + 1
        end = text.find("\n", old.end_mark.index)
        text = text[:start] + ("" if end == -1 else text[end + 1:])
    elif old is not None:
        text = text[:old.start_mark.index] + quoted + text[old.end_mark.index:]
    elif urls:
        name_key, name_value = next((k, v) for k, v in entry.value if k.value == "name")
        line_end = text.find("\n", name_value.end_mark.index)
        line_end = len(text) if line_end == -1 else line_end
        text = (text[:line_end] + "\n" + " " * name_key.start_mark.column
                + f"myclub_ical_url: {quoted}" + text[line_end:])
    saved = next(k for k in yaml.safe_load(text)["kids"] if k.get("name") == kid)
    if myclub_links(saved.get("myclub_ical_url")) != urls:
        raise ValueError(f"couldn't place the link in {config_path}; add it to {kid!r} by hand")
    config_path.chmod(0o600)  # before the link goes in
    config_path.write_text(text, encoding="utf-8")


def _as_utc(dt) -> datetime:
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    # date (not datetime) — treat as midnight UTC
    return datetime.combine(dt, datetime.min.time()).replace(tzinfo=timezone.utc)


def collect(cfg: Config, state: State, kid_terms: list[str]) -> Collected:
    """The upcoming events of each kid's MyClub iCal feed, to add to the calendar; MyClub has no
    Messages. The feed gives every event again each night, so none is marked seen."""
    events: list[CalendarEvent] = []
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=LOOKAHEAD_DAYS)

    for kid in cfg.kids:
        kid_events = 0
        seen: set[str] = set()  # an event in two of the Kid's feeds is theirs once (#810)
        for n, url in enumerate(kid.myclub_ical_url, 1):
            # Names the Kid, and which of their links when they have several, never the link.
            which = kid.name + (f" (link {n})" if len(kid.myclub_ical_url) > 1 else "")
            try:
                ics_text = download(url)
            except Exception as e:
                log.error("MyClub fetch failed for %s: %s", which, e)
                continue
            try:
                cal = Calendar.from_ical(ics_text)
            except Exception as e:
                log.error("MyClub iCal parse failed for %s: %s", which, e)
                continue
            kid_events += _events(cfg, kid.name, cal, now, horizon, seen, events)

        if kid.myclub_ical_url:
            log.info("MyClub: %d upcoming events for %s (next %dd)", kid_events, kid.name,
                     LOOKAHEAD_DAYS)

    return Collected(events=events)


def _events(cfg: Config, kid: str, cal: Calendar, now: datetime, horizon: datetime,
            seen: set[str], events: list[CalendarEvent]) -> int:
    """Adds to `events` the upcoming events of one of `kid`'s feeds not in `seen` already, and
    says how many."""
    kid_events = 0
    for comp in cal.walk("VEVENT"):
        uid = str(comp.get("UID", ""))
        summary = str(comp.get("SUMMARY", "")).strip() or "(Myclub event)"
        location = str(comp.get("LOCATION", "")).strip() or None
        desc = str(comp.get("DESCRIPTION", "")).strip()
        dtstart = comp.get("DTSTART")
        dtend = comp.get("DTEND")
        if not dtstart:
            continue
        start = _as_utc(dtstart.dt)
        end = _as_utc(dtend.dt) if dtend else None

        if start < now - timedelta(hours=6) or start > horizon:
            continue

        title_lc = summary.lower()
        if any(bl.lower() in title_lc for bl in cfg.myclub.blocklist):
            log.info("MyClub: skip blocklisted event: %s", summary)
            continue

        ext_id = uid or hashlib.sha1(
            f"myclub|{kid}|{summary}|{start.isoformat()}".encode("utf-8")
        ).hexdigest()[:16]

        if ext_id in seen:
            continue
        seen.add(ext_id)
        events.append(CalendarEvent(
            source="myclub",
            external_id=ext_id,
            title=f"⚽ {summary}",
            start=start,
            end=end,
            location=location,
            description=desc,
            kid=kid,
        ))
        kid_events += 1
    return kid_events
