"""Connecting MyClub in setup: checking a Kid's calendar link and saving it in the config.
`setup myclub` and the setup page's own field both save through this (ADR 0007), so both give the
same results. The link carries the Kid's personal token, so it's handled like the other secrets:
the result and any error name only MyClub's server and the HTTP status, never the link."""
from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

MYCLUB_URL = "https://id.myclub.fi"


def save_link(path: Path, kid: str, url: str,
              saving: AbstractContextManager[Any] = nullcontext()) -> tuple[str, dict[str, Any]]:
    """Checks the MyClub calendar link `url` by downloading it once and saves it as `kid`'s in
    the config at `path`, holding `saving` only while it writes, and returns the step's result
    with what else it reports: the events found, or an error that names only MyClub's server,
    the HTTP status or the config, never the link."""
    from .collectors import myclub

    url = "".join(url.split())
    if not _is_myclub_link(url):
        # Not downloaded: it's most likely the family's MyClub password, or another page's address.
        return "not-a-myclub-link", {}
    other = _kid_with_link(path, kid, url)
    if other is not None:
        # One link for two Kids would write every event twice, under both names (#209).
        return "same-link", {"other_kid": other}
    try:
        text = myclub.download(url)
    except myclub.FetchError as e:  # names only the server and the HTTP status
        return "link-failed", {"error": str(e)}
    if "BEGIN:VCALENDAR" not in text:
        return "not-a-calendar", {}
    try:
        with saving:
            myclub.save_link(path, kid, url)
    except (OSError, ValueError) as e:  # names the Kid and the config, never the link
        return "save-failed", {"error": str(e)[:300]}
    return "saved", {"events": text.count("BEGIN:VEVENT")}


def _kid_with_link(path: Path, kid: str, url: str) -> str | None:
    """The other Kid `url` is already saved for, if any. A link made with several members ticked
    is the same link for each of them (#207)."""
    import yaml
    from .collectors import myclub

    try:
        kids = (yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}).get("kids") or []
    except (OSError, yaml.YAMLError, AttributeError):
        return None
    same = myclub._normalize_url(url).rstrip("/")
    return next((k["name"] for k in kids
                 if isinstance(k, dict) and k.get("name") != kid
                 and isinstance(k.get("myclub_ical_url"), str)
                 and myclub._normalize_url("".join(k["myclub_ical_url"].split())).rstrip("/") == same),
                None)


def _is_myclub_link(url: str) -> bool:
    parts = urlparse(url)
    host = parts.hostname or ""
    return (parts.scheme in ("webcal", "https") and (host == "myclub.fi" or host.endswith(".myclub.fi"))
            and bool(parts.path.strip("/")))
