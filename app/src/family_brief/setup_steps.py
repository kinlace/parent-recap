"""Setup's steps that need the family, one command each, for the setup skill to run.

setup gmail — open Google's App passwords page, ask for the App Password in a macOS dialog,
              test the Gmail sign-in, store it in the Keychain
setup wilma — open the wilma CLI's sign-in in a Terminal window, wait for the family, then
              read the Kids and the city

Each prints one line of JSON for the assistant, and nothing else: `result` says what happened
and, when something went wrong, `next` says what the family does about it. A secret is never in
it (ADR 0005).
"""
from __future__ import annotations

import argparse
import imaplib
import json
import math
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import keyring.errors

from . import secret_dialog
from .config import Config

APP_PASSWORDS_URL = "https://myaccount.google.com/apppasswords"
TWO_STEP_URL = "https://myaccount.google.com/signinoptions/two-step-verification"
PAGE_UNAVAILABLE = "The page isn't available"

# The Wilma address of each city in "City presets" in docs/config.md.
WILMA_CITIES = {
    "espoo.inschool.fi": "Espoo",
    "helsinki.inschool.fi": "Helsinki",
    "yvkoulut.inschool.fi": "Helsinki",
    "vantaa.inschool.fi": "Vantaa",
    "kauniainen.inschool.fi": "Kauniainen",
}
WILMA_INSTALL = "npm install -g @wilm-ai/wilma-cli"
WILMA_POLL_SECONDS = 2
_OK = ("saved", "signed-in")


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

    pwilma = steps.add_parser("wilma", help="Open the Wilma sign-in in Terminal, wait for it, "
                              "then report the Kids and the city")
    pwilma.add_argument("--timeout", type=int, default=600,
                        help="Seconds to wait for the family to sign in (default: 600)")
    pwilma.add_argument("--no-open", action="store_true",
                        help="Don't open the sign-in again, only wait for it and read the Kids")
    pwilma.set_defaults(func=cmd_wilma)


def _report(result: str, next_: str | None = None, **extra: Any) -> int:
    out = {"result": result, **extra}
    if next_:
        out["next"] = next_
    print(json.dumps(out, ensure_ascii=False))
    return 0 if result in _OK else 1


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


# ---------------------------------------------------------------- wilma

def cmd_wilma(args: argparse.Namespace) -> int:
    """The wilma CLI signs in only on its own interactive screen, so this runs that screen in a
    Terminal window and watches for its result: the CLI saves its config once the sign-in has
    worked, and the window's script records the screen's exit status when it closes. The
    password stays between the family and the CLI; this reads only the Wilma address from the
    CLI's config."""
    from .collectors import wilma

    program = shutil.which(wilma.WILMA)
    if not program:
        return _report("not-installed", f"Install the wilma CLI in Terminal with {WILMA_INSTALL} "
                       "(it needs Node: brew install node), then run this again.")
    again = f"{_program()} setup wilma"
    config = _wilma_config_path()
    with tempfile.TemporaryDirectory(prefix="parent-recap-wilma-") as tmp:
        status = Path(tmp) / "exit-status"
        seen = _mtime(config)
        if not args.no_open:
            script = Path(tmp) / "Wilma sign-in.command"
            script.write_text(_sign_in_script(program, status))
            script.chmod(0o700)
            if subprocess.run(["open", "-a", "Terminal", str(script)],
                              capture_output=True).returncode != 0:
                return _report("no-terminal", "Terminal didn't open. The family runs wilma in "
                               f"Terminal and signs in there, then run: {again} --no-open")

        polls = max(1, math.ceil(args.timeout / WILMA_POLL_SECONDS))
        check_now = args.no_open  # already signed in, perhaps
        for poll in range(polls + 1):
            exit_status = _exit_status(status)
            config_mtime = _mtime(config)
            if check_now or config_mtime != seen or exit_status is not None:
                check_now, seen = False, config_mtime
                try:
                    kids = wilma.list_kids()
                except wilma.WilmaError:
                    kids = None
                if kids is not None:
                    address = _wilma_address(config)
                    return _report("signed-in", city=WILMA_CITIES.get(address or ""),
                                   wilma_address=address, kids=kids)
                if exit_status == 0:
                    return _report("not-signed-in", "The Wilma screen was closed before signing "
                                   f"in. Run this again when the family is ready: {again}")
                if exit_status is not None:
                    return _report("sign-in-failed", "Wilma didn't sign in. The family checks "
                                   "the city and the parent account's username and password, "
                                   f"then signs in again: {again}")
            if poll < polls:
                time.sleep(WILMA_POLL_SECONDS)
    return _report("timeout", f"The family didn't sign in to Wilma within {args.timeout} "
                   "seconds. If the Wilma window is still open, they finish there, then run: "
                   f"{again} --no-open. Otherwise run: {again}")


def _sign_in_script(program: str, status: Path) -> str:
    return (
        "#!/bin/sh\n"
        "clear\n"
        "echo 'Parent Recap: sign in to Wilma with your parent account.'\n"
        "echo 'Type your city to find it, pick it, then enter your Wilma username and password.'\n"
        "echo 'When Wilma asks what you want to view, you are done: choose Exit.'\n"
        "echo\n"
        f"{shlex.quote(program)}\n"
        f"{{ echo $? > {shlex.quote(str(status))}; }} 2>/dev/null\n"
        "echo 'You can close this window.'\n"
    )


def _wilma_config_path() -> Path:
    """Where the wilma CLI keeps its config, found the way the CLI finds it."""
    if os.environ.get("WILMAI_CONFIG_PATH"):
        return Path(os.environ["WILMAI_CONFIG_PATH"])
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "wilmai" / "config.json"


def _mtime(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _exit_status(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _wilma_address(config: Path) -> str | None:
    """The host of the Wilma the family signed in to last, such as espoo.inschool.fi."""
    try:
        data = json.loads(config.read_text())
        profile = next(p for p in data["profiles"] if p.get("id") == data.get("lastProfileId"))
        url = str(profile["tenantUrl"])
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        return None
    return urlparse(url if "//" in url else f"//{url}").hostname


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
