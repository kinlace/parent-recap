#!/usr/bin/env python3
"""Save a Kid's MyClub calendar subscription link in ~/.family/config.yaml.

The link carries a personal token, so it is pasted here in Terminal rather than in the agent chat.
Find it in MyClub on the web: the Kid's calendar page → "Tilaa kalenteri / Calendar subscription".

Usage:
  python scripts/setup_myclub.py "<Kid's name as in the config>" [--config PATH]
  # Then paste the webcal:// or https:// link when prompted (input is hidden).
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, "src")
from family_brief.collectors.myclub import _fetch_ics, _normalize_url, save_link  # noqa


def main() -> int:
    p = argparse.ArgumentParser(description="Save a Kid's MyClub calendar link in the config")
    p.add_argument("kid", help="The Kid's name, exactly as under kids: in the config")
    p.add_argument("--config", default="~/.family/config.yaml")
    args = p.parse_args()
    config = Path(args.config).expanduser()

    url = getpass.getpass(f"MyClub calendar link for {args.kid} (webcal://..., hidden input): ").strip()
    if not url.startswith(("webcal://", "https://")):
        print("FAIL: the link should start with webcal:// or https://", file=sys.stderr)
        return 1

    print("Testing the link...")
    try:
        n = _fetch_ics(_normalize_url(url)).count("BEGIN:VEVENT")
    except Exception as e:
        print(f"FAIL: the link doesn't open ({e}). Copy it again from MyClub.", file=sys.stderr)
        return 1

    try:
        save_link(config, args.kid, url)
    except (OSError, ValueError) as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    print(f"OK. The link works ({n} events in the calendar) and is saved for {args.kid} in {config}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
