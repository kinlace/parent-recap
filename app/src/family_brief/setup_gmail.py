"""Connecting Gmail in setup: the App Password's sign-in test and the Keychain, and each preset
city's starting allowlist. `setup gmail` and the setup page's own field both sign in through this
(ADR 0007), so both give the same results."""
from __future__ import annotations

import imaplib
import shlex
from typing import Any

from .setup_report import PLAIN_TERMINAL, keychain_failure, program
from .system import secrets

APP_PASSWORDS_URL = "https://myaccount.google.com/apppasswords"
TWO_STEP_URL = "https://myaccount.google.com/signinoptions/two-step-verification"
# The starting Gmail allowlist of each city in "City presets" in docs/config.md: the domain the
# city's schools and teachers write from, which also covers its subdomains, such as edu.espoo.fi.
CITY_DOMAINS = {
    "Espoo": "espoo.fi",
    "Helsinki": "hel.fi",
    "Vantaa": "vantaa.fi",
    "Kauniainen": "kauniainen.fi",
}
TURN_ON_TWO_STEP = (f"Turn on 2-Step Verification at {TWO_STEP_URL}, then create the App "
                    "Password again and run this again. A work or school Google account may not "
                    "allow App Passwords at all; then use a personal Gmail.")


def sign_in(address: str, password: str) -> tuple[str, str | None, dict[str, Any]]:
    """Tests the App Password `password` for `address` with Gmail and stores it in the Keychain,
    and returns the step's result, what to do next when it went wrong, and what else it reports:
    macOS's `code` when the Keychain refused it."""
    from .collectors import gmail

    password = password.replace(" ", "")
    if len(password) != 16 or not password.isalpha():
        # Not sent to Google: it's most likely the family's own Google password.
        return "not-an-app-password", ("That wasn't a 16-letter App Password. Copy the App "
                                       "Password Google showed (not the Google password) and run "
                                       "this again."), {}

    try:
        with imaplib.IMAP4_SSL(gmail.IMAP_HOST, gmail.IMAP_PORT, timeout=gmail.IMAP_TIMEOUT) as imap:
            imap.login(address, password)
            imap.logout()
    except imaplib.IMAP4.error:
        return "rejected", (f"Gmail didn't accept that App Password for {address}. Check the "
                            "address, create a new App Password and run this again. If it still "
                            f"fails: {TURN_ON_TWO_STEP}"), {}
    except OSError:
        return "no-connection", ("Couldn't reach Gmail. Check the Mac is online and run this "
                                 "again."), {}

    try:
        gmail.store_app_password(address, password)
    except secrets.SecretsError as e:
        result, extra = keychain_failure(e)
        if result == "keychain-not-reachable":
            return result, ("Gmail accepted it, but macOS doesn't let Parent Recap save it in the "
                            "Keychain from inside tmux or SSH, and shows no prompt there. Run "
                            f"this again {PLAIN_TERMINAL}: {program()} setup gmail --address "
                            f"{shlex.quote(address)}"), extra
        return result, ("Gmail accepted it, but macOS didn't let Parent Recap save it in the "
                        "Keychain. Run this again and click Allow if macOS asks."), extra
    return "saved", None, {}
