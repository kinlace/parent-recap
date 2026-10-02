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

from ..config import Config
from ..state import State
from .base import CalendarEvent, Message, retry_once_on_timeout

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
    """Set `kid`'s myclub_ical_url in the config file, leaving every other line and comment as
    it is. Used by `setup myclub` and the parent's own Terminal, so the link never goes through
    the agent chat."""
    text = config_path.read_text()
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
    quoted = json.dumps(url)  # a JSON string is a valid YAML double-quoted scalar
    old = next((v for k, v in entry.value if k.value == "myclub_ical_url"), None)
    if old is not None:
        text = text[:old.start_mark.index] + quoted + text[old.end_mark.index:]
    else:
        name_key, name_value = next((k, v) for k, v in entry.value if k.value == "name")
        line_end = text.find("\n", name_value.end_mark.index)
        line_end = len(text) if line_end == -1 else line_end
        text = (text[:line_end] + "\n" + " " * name_key.start_mark.column
                + f"myclub_ical_url: {quoted}" + text[line_end:])
    saved = next(k for k in yaml.safe_load(text)["kids"] if k.get("name") == kid)
    if saved.get("myclub_ical_url") != url:
        raise ValueError(f"couldn't place the link in {config_path}; add it to {kid!r} by hand")
    config_path.chmod(0o600)  # before the link goes in
    config_path.write_text(text)


def _as_utc(dt) -> datetime:
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    # date (not datetime) — treat as midnight UTC
    return datetime.combine(dt, datetime.min.time()).replace(tzinfo=timezone.utc)


def collect_events(cfg: Config, state: State) -> tuple[list[CalendarEvent], list[Message]]:
    """Fetch upcoming events from each kid's MyClub iCal feed.

    Returns:
      (events to create in Google Calendar, informational messages to include in summary)
    """
    events: list[CalendarEvent] = []
    messages: list[Message] = []
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=LOOKAHEAD_DAYS)

    for kid in cfg.kids:
        if not kid.myclub_ical_url:
            continue
        try:
            ics_text = download(kid.myclub_ical_url)
        except Exception as e:
            log.error("MyClub fetch failed for %s: %s", kid.name, e)
            continue
        try:
            cal = Calendar.from_ical(ics_text)
        except Exception as e:
            log.error("MyClub iCal parse failed for %s: %s", kid.name, e)
            continue

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
                f"myclub|{kid.name}|{summary}|{start.isoformat()}".encode("utf-8")
            ).hexdigest()[:16]

            events.append(CalendarEvent(
                source="myclub",
                external_id=ext_id,
                title=f"⚽ {summary}",
                start=start,
                end=end,
                location=location,
                description=desc,
                kid=kid.name,
            ))
            kid_events += 1

        log.info("MyClub: %d upcoming events for %s (next %dd)", kid_events, kid.name, LOOKAHEAD_DAYS)

    return events, messages


def collect(cfg: Config, state: State, kid_terms: list[str]) -> list[Message]:
    """Collector protocol — MyClub surfaces events (side channel), no chat messages."""
    return []
