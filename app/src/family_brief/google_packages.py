"""Google's client packages, which only the Google Calendar mode uses: the `google` extra.

Setup starts every Household with the `.ics` attachment, so a default install leaves them out,
and `cryptography` with them, which has no prebuilt package for some Macs. install.sh adds the
extra when the config's calendar mode is google, which is also how manage turns it on. Only the
code that talks to Google imports them, inside the functions that need them.

install.sh calls it as `python -m family_brief.google_packages wanted`, which exits 0 when the
config (~/.family/config.yaml) asks for google mode.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

MODULES = ("googleapiclient.discovery", "google.oauth2.credentials", "google.auth.transport.requests",
           "google_auth_oauthlib.flow", "google_auth_httplib2")
CONFIG = Path("~/.family/config.yaml")


def installed() -> bool:
    try:
        for name in MODULES:
            importlib.import_module(name)
    except ImportError:
        return False
    return True


def wanted(config: Path | None = None) -> bool:
    """Whether the config's calendar mode is google. Not when it's missing or unreadable: setup
    and doctor report that, and the extra can follow once it's fixed."""
    import yaml
    try:
        data = yaml.safe_load((config or CONFIG).expanduser().read_text())
        return data["google_calendar"]["mode"] == "google"
    except (OSError, yaml.YAMLError, KeyError, TypeError):
        return False


def main(argv: list[str]) -> int:
    if argv != ["wanted"]:
        print("Usage: python -m family_brief.google_packages wanted", file=sys.stderr)
        return 2
    return 0 if wanted() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
