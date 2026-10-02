"""Setup's steps that need the family, one command each, for the setup skill to run.

setup gmail — open Google's App passwords page, ask for the App Password in a macOS dialog,
              test the Gmail sign-in, store it in the Keychain

Each prints one line of JSON for the assistant, and nothing else: `result` says what happened
and, when something went wrong, `next` says what the family does about it. A secret is never in
it (ADR 0005).
"""
from __future__ import annotations

import argparse
import imaplib
import json
import shlex
import subprocess
import sys

import keyring.errors

from . import secret_dialog
from .config import Config

APP_PASSWORDS_URL = "https://myaccount.google.com/apppasswords"
TWO_STEP_URL = "https://myaccount.google.com/signinoptions/two-step-verification"
PAGE_UNAVAILABLE = "The page isn't available"


def register(sub) -> None:
    psetup = sub.add_parser("setup", help="Setup steps that ask the family for something")
    steps = psetup.add_subparsers(dest="step", required=True)

    pgmail = steps.add_parser("gmail", help="Ask for the Gmail App Password in a dialog, test it "
                              "and store it in the Keychain")
    pgmail.add_argument("--address", default=None,
                        help="The Gmail address (default: gmail.username in the config)")
    pgmail.add_argument("--no-open", action="store_true",
                        help="Don't open Google's App passwords page again")
    pgmail.set_defaults(func=cmd_gmail)


def _report(result: str, next_: str | None = None, **extra: str) -> int:
    out = {"result": result, **extra}
    if next_:
        out["next"] = next_
    print(json.dumps(out, ensure_ascii=False))
    return 0 if result == "saved" else 1


# ---------------------------------------------------------------- gmail

def cmd_gmail(args: argparse.Namespace) -> int:
    from .collectors import gmail

    address = (args.address or _configured_address(args.config) or "").strip().lower()
    if "@" not in address:
        return _report("no-address", "Run it again with --address and the family's Gmail "
                       "address: family-brief setup gmail --address name@gmail.com")
    if not args.no_open:
        subprocess.run(["open", APP_PASSWORDS_URL], capture_output=True)

    turn_on_two_step = (f"Turn on 2-Step Verification at {TWO_STEP_URL}, then create the App "
                        "Password again and run this again. A work or school Google account may "
                        "not allow App Passwords at all; then use a personal Gmail.")
    try:
        password = secret_dialog.ask(
            f"Paste the App Password for {address}.\n\nCreate it on Google's App passwords page, "
            f"which is open in your browser ({APP_PASSWORDS_URL}), with the name Parent Recap, "
            "then copy the 16 letters Google shows.", other=PAGE_UNAVAILABLE)
    except secret_dialog.Cancelled:
        return _report("cancelled", "The family closed the dialog. Run this again when they're ready.")
    except secret_dialog.OtherChosen:
        return _report("app-passwords-unavailable", turn_on_two_step)
    except secret_dialog.NoWayToAsk:
        return _report("no-prompt", "No dialog or Terminal prompt could be shown here. The family "
                       f"runs this in Terminal: {_program()} setup gmail --address {address}")

    password = password.replace(" ", "")
    if len(password) != 16 or not password.isalpha():
        # Not sent to Google: it's most likely the family's own Google password.
        return _report("not-an-app-password", "That wasn't a 16-letter App Password. Copy the "
                       "App Password Google showed (not the Google password) and run this again.")

    try:
        with imaplib.IMAP4_SSL(gmail.IMAP_HOST, gmail.IMAP_PORT, timeout=gmail.IMAP_TIMEOUT) as imap:
            imap.login(address, password)
            imap.logout()
    except imaplib.IMAP4.error:
        return _report("rejected", f"Gmail didn't accept that App Password for {address}. "
                       "Check the address, create a new App Password and run this again. If it "
                       f"still fails: {turn_on_two_step}")
    except OSError:
        return _report("no-connection", "Couldn't reach Gmail. Check the Mac is online and run this again.")

    try:
        gmail.store_app_password(address, password)
    except keyring.errors.KeyringError:
        return _report("keychain-failed", "Gmail accepted it, but macOS didn't let Parent Recap "
                       "save it in the Keychain. Run this again and click Allow if macOS asks.")
    return _report("saved", address=address)


def _program() -> str:
    """How to run this program again from Terminal, where it isn't on the PATH."""
    if sys.argv[0].endswith("__main__.py"):
        return f"{shlex.quote(sys.executable)} -m family_brief"
    return shlex.quote(sys.argv[0]) if "/" in sys.argv[0] else "family-brief"


def _configured_address(config: str | None) -> str | None:
    try:
        cfg = Config.load(config)
    except Exception:  # no config yet: setup can connect Gmail before writing it
        return None
    return cfg.gmail.username
