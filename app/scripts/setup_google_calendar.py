#!/usr/bin/env python3
"""Authorize Parent Recap to write events into your Google Calendar (one time).

Needs a Desktop OAuth client file at ~/.family/calendar_credentials.json: the one
Parent Recap's maintainers hand to pilot households, or your own (docs/sources.md, Calendar).

The browser will say the app is unverified: click Advanced -> Go to <app name> -> Allow.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
FAMILY = Path.home() / ".family"


def main() -> int:
    creds_path = FAMILY / "calendar_credentials.json"
    token_path = FAMILY / "calendar_token.json"
    if not creds_path.exists():
        print("No Google app file at ~/.family/calendar_credentials.json. Pick one of three ways:\n"
              "  1. Pilot household: save the Google app file Parent Recap's maintainers sent you there\n"
              "  2. Create your own Google app: follow the Calendar section of docs/sources.md, "
              "then save the file there\n"
              "  3. Skip Google Calendar: set google_calendar.mode to ics in the config, and new events "
              "come as an .ics attachment on the Brief\n"
              "Run this script again once the file is saved.", file=sys.stderr)
        return 1
    try:
        # Only installed for google mode, as the `google` extra (family_brief/google_packages.py).
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Google's packages for Google Calendar aren't installed. Set google_calendar.mode to "
              "google in the config, run the plugin's install.sh again, which installs them, then "
              "run this script again.", file=sys.stderr)
        return 1
    print('The browser opens Google\'s sign-in page. If it says "Google hasn\'t verified this app", '
          'click Advanced → Go to (app name) → Allow.')
    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    token_path.write_text(creds.to_json())
    os.chmod(token_path, 0o600)
    print(f"Done. Authorization saved to {token_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
