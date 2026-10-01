#!/usr/bin/env python3
"""Store a Gmail App Password in macOS Keychain.

Prerequisites:
  1. Gmail account must have 2-Step Verification enabled
  2. Create a 16-character App Password at https://myaccount.google.com/apppasswords
     (App name: "FamilyBrief"; Device: "Mac")

Usage:
  python scripts/setup_gmail_imap.py <gmail_address>
  # Then paste the 16-char App Password when prompted (input is hidden).
"""
from __future__ import annotations

import getpass
import imaplib
import sys

sys.path.insert(0, "src")
from family_brief.collectors.gmail import IMAP_HOST, IMAP_PORT, store_app_password  # noqa


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/setup_gmail_imap.py <gmail_address>", file=sys.stderr)
        return 2
    username = sys.argv[1].strip()
    pw = getpass.getpass("Gmail App Password (16 chars, spaces allowed, hidden input): ")
    pw = pw.replace(" ", "")
    if len(pw) != 16:
        print(f"WARNING: expected 16 chars, got {len(pw)}. Continuing anyway.", file=sys.stderr)

    print(f"Testing IMAP login to {IMAP_HOST} as {username}...")
    with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) as imap:
        try:
            imap.login(username, pw)
        except imaplib.IMAP4.error as e:
            print(f"FAIL: IMAP login rejected: {e}", file=sys.stderr)
            return 1
        imap.logout()

    store_app_password(username, pw)
    print(f"OK. App Password stored in Keychain for {username}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
