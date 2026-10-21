"""Setup's steps that need the family, one command each, for the setup skill to run.

setup gmail — open Google's App passwords page, ask for the App Password in a macOS dialog,
              test the Gmail sign-in, store it in the Keychain
setup wilma — open the wilma CLI's sign-in in a Terminal window with a guide in the family's
              language, end it once signed in, then read the Kids and the city
setup claude — open Claude's sign-in for the nightly token in a Terminal window, ask for the
               token in a macOS dialog, make one test call, store it in the Keychain
setup gateway — ask for the Gateway Key in a macOS dialog, make one test call through the gateway,
                store it in the Keychain
setup whatsapp — read WhatsApp through a `bg` job; until the scheduled job's Python has Full
                 Disk Access, show it in Finder, open Full Disk Access and wait for it, then list
                 the chats with a hint on those that look like they're about a Kid
setup myclub — open MyClub, ask for a Kid's calendar link in a macOS dialog, download it once,
               save it in the owner-only config
setup save — save the Household's answers into the config, and setup's progress (in setup_save.py)
setup status — whether setup is done (in setup_status.py)
setup page — serve the setup page on this Mac and open it in the browser (in setup_server.py)

This module is the chat's side only: the arguments, the dialogs and what each command prints. What
a step does, which the setup page does too, is in its own module: setup_gmail, setup_wilma,
setup_ai, setup_whatsapp and setup_myclub.

Each prints one line of JSON for the assistant, and nothing else: `result` says what happened
and, when something went wrong, `next` says what the family does about it. A secret is never in
it (ADR 0005).
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path

from . import secret_dialog, setup_ai, setup_gmail, setup_myclub, setup_whatsapp, setup_wilma
from .config import Config, LLMConfig
from .setup_ai import CLAUDE_WINDOW, setup_token_window
from .setup_report import KEYCHAIN_WARNING, PLAIN_TERMINAL, program, report
from .system import desktop, jobs, secrets

# Still found here by the tests, and by the setup page for its WhatsApp read, whose tests replace
# it here, until they use the Sources' own modules (#423).
from .setup_gmail import CITY_DOMAINS
from .setup_whatsapp import read_through_bg as read_whatsapp_through_bg
from .setup_wilma import WILMA_CITIES, WILMA_WINDOW

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

    pwilma = steps.add_parser("wilma", help="Open the Wilma sign-in in Terminal, wait for it, "
                              "then report the Kids and the city")
    pwilma.add_argument("--timeout", type=int, default=600,
                        help="Seconds to wait for the family to sign in (default: 600)")
    pwilma.add_argument("--no-open", action="store_true",
                        help="Don't open the sign-in again, only wait for it and read the Kids")
    pwilma.add_argument("--language", default="en",
                        help="The family's language code, for the window's guide (default: en)")
    pwilma.add_argument("--screen", action="store_true", help=argparse.SUPPRESS)  # in the window
    # With --screen: where the window records how the screen ended, for the step waiting on it.
    pwilma.add_argument("--status", type=Path, default=None, help=argparse.SUPPRESS)
    pwilma.set_defaults(func=cmd_wilma)

    pclaude = steps.add_parser("claude", help="Open Claude's sign-in for the nightly token in "
                               "Terminal, ask for the token in a dialog, test it and store it in "
                               "the Keychain")
    pclaude.add_argument("--no-open", action="store_true",
                         help="Don't open Claude's sign-in again, only ask for the token")
    pclaude.set_defaults(func=cmd_claude)

    pgateway = steps.add_parser("gateway", help="Ask for the Gateway Key in a dialog, test it with "
                                "the gateway and store it in the Keychain")
    pgateway.set_defaults(func=cmd_gateway)

    pwhatsapp = steps.add_parser("whatsapp", help="Read WhatsApp with the scheduled job's Python, "
                                 "opening Full Disk Access and waiting for it when needed, then "
                                 "report the chats")
    pwhatsapp.add_argument("--timeout", type=int, default=600,
                           help="Seconds to wait for Full Disk Access (default: 600)")
    pwhatsapp.add_argument("--days", type=int, default=setup_whatsapp.WHATSAPP_DAYS,
                           help="List chats with messages in this many days "
                           f"(default: {setup_whatsapp.WHATSAPP_DAYS})")
    pwhatsapp.add_argument("--no-open", action="store_true",
                           help="Don't open Finder and Full Disk Access again, only wait and read")
    pwhatsapp.add_argument("--read", action="store_true", help=argparse.SUPPRESS)  # inside the bg job
    pwhatsapp.set_defaults(func=cmd_whatsapp)

    pmyclub = steps.add_parser("myclub", help="Ask for a Kid's MyClub calendar link in a dialog, "
                               "download it once and save it in the config")
    pmyclub.add_argument("--kid", default=None, help="The Kid's name, exactly as under kids: in "
                         "the config")
    pmyclub.add_argument("--no-open", action="store_true",
                         help="Don't open MyClub's page again, only ask for the link")
    pmyclub.set_defaults(func=cmd_myclub)

    from . import setup_save, setup_server, setup_status
    setup_save.register(steps)
    setup_status.register(steps)
    setup_server.register(steps)


def _warn_if_unreachable() -> str:
    """At a Keychain step's start, says once that saving the secret in the Keychain will fail
    here, and returns the same warning to put on top of the dialog, or "" where it won't fail."""
    if secrets.reachable():
        return ""
    print(KEYCHAIN_WARNING, file=sys.stderr, flush=True)
    return f"{KEYCHAIN_WARNING}\n\n"


# ---------------------------------------------------------------- gmail

def cmd_gmail(args: argparse.Namespace) -> int:
    address = (args.address or _configured_address(args.config) or "").strip().lower()
    if "@" not in address:
        return report("no-address", "Run it again with --address and the family's Gmail "
                      "address: parent-recap setup gmail --address name@gmail.com")
    warning = _warn_if_unreachable()
    if not args.no_open:
        desktop.open_url(setup_gmail.APP_PASSWORDS_URL)

    try:
        password = secret_dialog.ask(
            f"{warning}Paste the App Password for {address}.\n\nCreate it on Google's App "
            f"passwords page, which is open in your browser ({setup_gmail.APP_PASSWORDS_URL}), "
            "with the name Parent Recap, then copy the 16 letters Google shows.",
            other=PAGE_UNAVAILABLE)
    except secret_dialog.Cancelled:
        return report("cancelled", "The family closed the dialog. Run this again when they're ready.")
    except secret_dialog.OtherChosen:
        return report("app-passwords-unavailable", setup_gmail.TURN_ON_TWO_STEP)
    except secret_dialog.NoWayToAsk:
        return report("no-prompt", "No dialog or Terminal prompt could be shown here. The family "
                      f"runs this in Terminal: {program()} setup gmail --address {address}")

    result, next_, extra = setup_gmail.sign_in(address, password)
    return report(result, next_, **({"address": address} if result == "saved" else extra))


def _configured_address(config: str | None) -> str | None:
    try:
        cfg = Config.load(config)
    except Exception:  # no config yet: setup can connect Gmail before writing it
        return None
    return cfg.gmail.username


# ---------------------------------------------------------------- wilma

def cmd_wilma(args: argparse.Namespace) -> int:
    if args.screen:
        return setup_wilma.sign_in_screen(args.language, args.status)
    out = setup_wilma.sign_in_in_terminal(args.language, args.timeout, no_open=args.no_open)
    return report(out.pop("result"), out.pop("next", None), **out)


# ---------------------------------------------------------------- claude

def cmd_claude(args: argparse.Namespace) -> int:
    """`claude setup-token` signs in on Ink's interactive screen, which needs a real terminal
    (without one it stops with "Raw mode is not supported"), and it shows the token only on that
    screen, wrapped to the window. So this opens it in a Terminal window and asks for the token in
    the secret dialog."""
    from . import llm

    backend = llm.named("claude")
    claude = backend.program()
    if not claude:
        return report("not-installed", f"Install Claude Code's claude command in Terminal with "
                      f"{backend.install_line()}, then run this again.")
    again = f"{program()} setup claude"
    warning = _warn_if_unreachable()
    if not args.no_open and not desktop.open_terminal(setup_token_window(claude), {},
                                                      CLAUDE_WINDOW):
        return report("no-terminal", "Terminal didn't open. The family runs claude "
                      f"setup-token in Terminal and signs in there, then run: {again} --no-open")
    try:
        typed = secret_dialog.ask(
            f"{warning}Paste the Claude token for the nightly Brief.\n\nClick Authorize on "
            "Claude's page in your browser. The Terminal window that opened then shows a "
            "token starting with sk-ant-oat01-. Copy all of it and paste it here.")
    except secret_dialog.Cancelled:
        return report("cancelled", "The family closed the dialog. Run this again when "
                      f"they're ready: {again}")
    except secret_dialog.NoWayToAsk:
        return report("no-prompt", "No dialog or Terminal prompt could be shown here. The "
                      "family runs claude setup-token in Terminal, then this in Terminal: "
                      f"{again} --no-open")

    result, extra = setup_ai.claude_token_sign_in(typed)
    if result == "not-a-token":
        return report("not-a-token", "That wasn't a Claude token. Copy the whole token Terminal "
                      f"showed, starting with sk-ant-oat01-, and run this again: {again} --no-open")
    if result == "test-call-failed":
        return report("test-call-failed", "Claude didn't accept the token, so it wasn't kept. "
                      "Check the plan is Claude Pro or Max, then run this again to make a new "
                      f"token: {again}", **extra)
    if result == "keychain-not-reachable":
        return report(result, "The test call worked, but macOS doesn't let Parent Recap save "
                      "the token in the Keychain from inside tmux or SSH, and shows no prompt "
                      f"there. Run this again {PLAIN_TERMINAL}: {again} --no-open", **extra)
    if result == "keychain-failed":
        return report(result, "The test call worked, but macOS didn't let Parent Recap save the "
                      "token in the Keychain. Run this again and click Allow if macOS asks: "
                      f"{again} --no-open", **extra)
    return report("saved", test_call="ok")


# ---------------------------------------------------------------- gateway

def cmd_gateway(args: argparse.Namespace) -> int:
    """The maintainers send an invited Household its Gateway Key (ADR 0014). The family pastes it
    in the secret dialog, and it goes from there to one test call and the Keychain only."""
    again = f"{program()} setup gateway"
    warning = _warn_if_unreachable()
    try:
        typed = secret_dialog.ask(
            f"{warning}Paste the Gateway Key for the nightly Brief.\n\nIt's the key starting with "
            "sk- that the Parent Recap maintainers sent you. Copy all of it and paste it here.")
    except secret_dialog.Cancelled:
        return report("cancelled", "The family closed the dialog. Run this again when they're "
                      f"ready: {again}")
    except secret_dialog.NoWayToAsk:
        return report("no-prompt", "No dialog or Terminal prompt could be shown here. The family "
                      f"runs this in Terminal: {again}")

    result, extra = setup_ai.gateway_sign_in(_gateway_url(args.config), typed)
    next_ = {
        "not-a-key": "That wasn't a Gateway Key. Copy the whole key the maintainers sent, starting "
                     f"with sk-, and run this again: {again}",
        "rejected": "The gateway doesn't know that Gateway Key, or it was revoked, so it wasn't "
                    "kept. Check it's the whole key the maintainers sent, or ask them for a new "
                    f"one, then run this again: {again}",
        "no-connection": "Couldn't reach the gateway, so the key wasn't kept. Check the Mac is "
                         f"online and run this again: {again}",
        "test-call-failed": "The gateway's test call failed, so the key wasn't kept. Run this "
                            f"again later: {again}",
        "keychain-not-reachable": "The test call worked, but macOS doesn't let Parent Recap save "
                                  "the key in the Keychain from inside tmux or SSH, and shows no "
                                  f"prompt there. Run this again {PLAIN_TERMINAL}: {again}",
        "keychain-failed": "The test call worked, but macOS didn't let Parent Recap save the key "
                           f"in the Keychain. Run this again and click Allow if macOS asks: {again}",
    }.get(result)
    if result == "saved":
        return report("saved", test_call="ok")
    return report(result, next_, **extra)


def _gateway_url(config: str | None) -> str:
    try:
        return Config.load(config).llm.gateway_url
    except Exception:  # no config yet: setup can store the key before writing it
        return LLMConfig().gateway_url


# ---------------------------------------------------------------- whatsapp

def cmd_whatsapp(args: argparse.Namespace) -> int:
    """Every read goes through a `bg` job, with the scheduled job's permission (ADR 0012); with
    --read this is that job."""
    if args.read:
        if not os.environ.get(jobs.BG_ENV):
            print(f"Run it through bg: {program()} setup whatsapp, which does that itself.")
            return 2
        found = setup_whatsapp.read_in_job(args.days)
        print(json.dumps(found, ensure_ascii=False))
        return 0 if found["permission"] == "readable" else 1

    read = setup_whatsapp.wait_until_readable(args.config, args.days, args.timeout,
                                              no_open=args.no_open)
    python = read["python"]
    again = f"{program()} setup whatsapp --no-open"
    grant = (f"In Finder the Python file {os.path.basename(python)} is selected. The family drags "
             "it into the list in System Settings → Privacy & Security → Full Disk Access and "
             f"turns its switch on, then run: {again}")
    if read["result"] == "bg-failed":
        return report("bg-failed", "The background job that reads WhatsApp didn't start. "
                      f"Run this again; if it fails again, run: {program()} bg doctor",
                      python=python, error=read["error"])
    if read["result"] == "waiting":
        return report("waiting", "Reading WhatsApp didn't finish in time. Check that WhatsApp "
                      f"for Mac opens and shows the chats, then run: {again}", python=python)
    if read["result"] == "readable":
        return report("readable", python=python, chats=read["chats"])
    if read["result"] == "not-installed":
        return report("not-installed", "WhatsApp for Mac isn't on this Mac, or has never been "
                      "signed in. The family installs it from the App Store (not the older "
                      "version from WhatsApp's website), links it to their phone and lets "
                      f"the chats sync, then run: {program()} setup whatsapp")
    if read["result"] == "unreadable":
        return report("unreadable", "Reading WhatsApp failed (see error). Run this again; if "
                      "it fails again, check that WhatsApp for Mac opens and shows the chats, "
                      f"and run: {program()} bg doctor", python=python, error=read["error"])
    if read.get("failed"):
        return report("no-permission", "Finder or System Settings didn't open from here. The "
                      "family runs these in Terminal: "
                      f"{'; '.join(shlex.join(c) for c in read['failed'])}. {grant}",
                      python=python)
    return report("no-permission", "The scheduled job's Python doesn't have Full Disk Access "
                  f"yet. {grant}", python=python)


# ---------------------------------------------------------------- myclub

def cmd_myclub(args: argparse.Namespace) -> int:
    """The link carries the Kid's personal token, so it's handled like the other secrets: the
    result and any error name only MyClub's server and the HTTP status, never the link."""
    path = Path(os.path.expanduser(os.path.expandvars(args.config or "~/.family/config.yaml")))
    try:
        kids = [k.name for k in Config.load(path).kids]
    except FileNotFoundError:
        return report("no-config", "The config isn't written yet. Write it with the Kids first, "
                      "then run this again.")
    except Exception:  # not its error: a config check quotes values, which can be other links
        return report("bad-config", f"The config at {path} can't be read. Run {program()} "
                      "doctor, fix what it names, then run this again.")
    if args.kid not in kids:
        return report("no-kid" if args.kid is None else "no-such-kid", "Run it again with --kid "
                      f"and one of the Kids' names: {program()} setup myclub --kid NAME",
                      kids=kids)
    again = f"{program()} setup myclub --kid {shlex.quote(args.kid)}"
    if not args.no_open:
        desktop.open_url(setup_myclub.MYCLUB_URL)

    try:
        url = secret_dialog.ask(
            f"Paste {args.kid}'s MyClub calendar link.\n\nSign in to MyClub in your browser "
            f"({setup_myclub.MYCLUB_URL}), open {args.kid}'s calendar, choose Calendar subscription "
            f"(Tilaa kalenteri), choose Valitut jäsenet with only {args.kid} ticked, save, and "
            "copy the link starting with webcal://.")
    except secret_dialog.Cancelled:
        return report("cancelled", "The family closed the dialog. Run this again when they're "
                      f"ready: {again}")
    except secret_dialog.NoWayToAsk:
        return report("no-prompt", "No dialog or Terminal prompt could be shown here. The family "
                      f"runs this in Terminal: {again} --no-open")

    result, extra = setup_myclub.save_link(path, args.kid, url)
    next_ = {
        "not-a-myclub-link": "That wasn't a MyClub calendar link. Copy the link from Calendar "
                             "subscription (Tilaa kalenteri) on the Kid's MyClub calendar, starting "
                             f"with webcal://, and run this again: {again} --no-open",
        "link-failed": "The link didn't open, so it wasn't saved. Copy it again from MyClub and "
                       f"run this again: {again} --no-open",
        "not-a-calendar": "The link opened a page, not a calendar, so it wasn't saved. Copy the "
                          "link from Calendar subscription (Tilaa kalenteri) on the Kid's MyClub "
                          f"calendar, not the browser's address bar: {again} --no-open",
        "save-failed": "The link works, but it couldn't be saved in the config (see error).",
        "same-link": f"That link is already saved for {extra.get('other_kid')}, so it wasn't saved "
                     f"again. Each Kid needs their own link: in MyClub's Calendar subscription, "
                     f"choose Valitut jäsenet, tick only {args.kid}, save, and copy that link: "
                     f"{again} --no-open",
    }.get(result)
    return report(result, next_, **({"kid": args.kid} if result == "saved" else {}), **extra)
