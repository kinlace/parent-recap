"""What `doctor` checks of MyClub, which its record hooks in as `check` (ADR 0022)."""
from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

from ....ops import FAIL, OK
from . import collector as myclub

if TYPE_CHECKING:
    from ....config import Config
    from ...record import Add


def check(cfg: Config, add: Add, config: str | None = None) -> None:
    """Each Kid's subscription links, since each Kid can have clubs of their own (#810)."""
    for kid in cfg.kids:
        links = kid.myclub_ical_url
        for i, url in enumerate(links, 1):
            _check(kid.name, url, add, f"link {i} of {len(links)}" if len(links) > 1 else "link")


def _check(kid: str, url: str, add: Add, which: str = "link") -> None:
    try:
        text = myclub.download(url)
        n = text.count("BEGIN:VEVENT")
        add(OK, "myclub", f"subscription {which} works, {n} events in the calendar", subject=kid)
    except Exception as e:
        fix = ("remove it on the setup page's MyClub step and add a new one" if which != "link"
               else f"save a new one with parent-recap setup myclub --kid {shlex.quote(kid)}")
        add(FAIL, "myclub", f"subscription {which} doesn't open: {e} ({fix})", subject=kid)
