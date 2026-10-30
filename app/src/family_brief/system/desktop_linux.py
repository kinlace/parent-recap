"""The Linux adapter of `desktop`: a server has no browser, text editor or window of its own, so a
page's link is printed for whoever runs the command to open on their own device, and the
languages are the locale's."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path


def open_url(url: str) -> bool:
    # On stderr: stdout may be a command's JSON for the assistant. False, since nothing opened, so
    # the caller says what to do too.
    print(f"Open this link in a browser: {url}", file=sys.stderr, flush=True)
    return False


def open_file(path: Path) -> bool:
    return False


def open_terminal(argv: list[str], env: dict[str, str], title: str) -> bool:
    return False


def forget_terminal(title: str) -> None:
    pass


def preferred_languages() -> list[str]:
    # As gettext reads them: LANGUAGE's list first, else the first locale set, such as fi_FI.UTF-8.
    listed = os.environ.get("LANGUAGE") or next(
        (os.environ[v] for v in ("LC_ALL", "LC_MESSAGES", "LANG") if os.environ.get(v)), "")
    tags = []
    for locale in listed.split(":"):
        m = re.fullmatch(r"([A-Za-z]{2,3})(?:_([A-Za-z0-9]+))?(?:[.@].*)?", locale)
        if m:
            tags.append("-".join(filter(None, m.groups())))
    return tags


def typing_at_terminal() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except ValueError:  # closed
        return False
