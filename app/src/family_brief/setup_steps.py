"""Setup's steps that need the family, one command each, for the setup skill to run.

setup gmail — open Google's App passwords page, ask for the App Password in a macOS dialog,
              test the Gmail sign-in, store it in the Keychain
setup wilma — open the wilma CLI's sign-in in a Terminal window with a guide in the family's
              language, end it once signed in, then read the Kids and the city
setup claude — open Claude's sign-in for the nightly token in a Terminal window, ask for the
               token in a macOS dialog, make one test call, store it in the Keychain
setup gateway — ask for the Gateway Key in a macOS dialog, make one test call through the gateway,
                store it in the Keychain
setup confirm — get a send-only Gateway Key from the gateway when there's none, bind the
                Recipients to it, and say who still has to confirm by email (ADR 0033)
setup sending — send the Brief from the Household's own Gmail, after checking Gmail takes the App
                Password, or through the gateway again (ADR 0034)
setup myclub — open MyClub, ask for a Kid's calendar link in a macOS dialog, download it once,
               save it in the owner-only config
setup eepos — have an Eepos school's guardian password typed at the secret store's own prompt in
              the terminal, sign in with it, match the students to the Kids and save the sign-in
              (for the maintainers, until the setup page has an Eepos step)
setup evaka — have a city's eVaka guardian password typed at the secret store's own prompt in the
              terminal, sign in with it, match the children to the Kids and save the sign-in
              (for the maintainers, until the setup page has an eVaka step)
setup save — save the Household's answers into the config, and setup's progress (in setup_save.py)
setup status — whether setup is done (in setup_status.py)
setup page — serve the setup page on this Mac and open it in the browser (in setup_server/)

This module is the chat's side only: the arguments, the dialogs and what each command prints. What
a step does, which the setup page does too, is in its own module: setup_gmail, setup_wilma,
setup_ai, setup_myclub, setup_eepos and setup_evaka.

Each prints one line of JSON for the assistant, and nothing else: `result` says what happened
and, when something went wrong, `next` says what the family does about it. A secret is never in
it (ADR 0005).
"""
from __future__ import annotations

import argparse
import os
import shlex
import sys
from collections.abc import Callable
from pathlib import Path

from . import secret_dialog, setup_ai, setup_gmail, setup_myclub, setup_wilma
from .config import Config, LLMConfig
from .setup_ai import CLAUDE_WINDOW, setup_token_window
from .setup_report import KEYCHAIN_WARNING, PLAIN_TERMINAL, program, report
from .system import desktop, profile, secrets, windows_only

# Still found here by the tests, until they use the Sources' own modules (#423).
from .setup_gmail import CITY_DOMAINS
from .setup_wilma import WILMA_CITIES, WILMA_WINDOW

Step = Callable[[argparse.Namespace], int]
PAGE_UNAVAILABLE = "The page isn't available"
AS_ADMINISTRATOR = ("This PowerShell window runs as administrator. Set up from it, Parent Recap "
                    "could never send a Brief: the evening job, which runs as you, couldn't open "
                    "its own files. Close this window, open PowerShell from the Start menu the "
                    "normal way (not \"Run as administrator\"), and run it again there.")


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

    pconfirm = steps.add_parser("confirm", help="Get a send-only Gateway Key when there's none, "
                                "bind the Recipients to it and say who still has to confirm their "
                                "address by email")
    pconfirm.add_argument("--resend", action="store_true",
                          help="Send the confirmation email again to each who hasn't confirmed")
    pconfirm.set_defaults(func=cmd_confirm)

    psending = steps.add_parser("sending", help="Send the Brief from the Household's own Gmail, "
                                "or through Parent Recap's gateway again (only when the AI is "
                                "Claude or ChatGPT and Gmail is connected)")
    psending.add_argument("to", choices=["gmail", "gateway"])
    psending.set_defaults(func=cmd_sending)

    pmyclub = steps.add_parser("myclub", help="Ask for a Kid's MyClub calendar link in a dialog, "
                               "download it once and save it in the config")
    pmyclub.add_argument("--kid", default=None, help="The Kid's name, exactly as under kids: in "
                         "the config")
    pmyclub.add_argument("--no-open", action="store_true",
                         help="Don't open MyClub's page again, only ask for the link")
    pmyclub.set_defaults(func=cmd_myclub)

    peepos = steps.add_parser("eepos", help="Add a guardian's sign-in at an Eepos school: the "
                              "password typed at the secret store's own prompt, checked at once, "
                              "the students matched to the Kids by name")
    peepos.add_argument("--school", required=True,
                        help="The school's Eepos address, or its part before .eepos.fi: emo for "
                             "emo.eepos.fi")
    peepos.add_argument("--username", required=True, help="The guardian's Eepos username")
    peepos.set_defaults(func=cmd_eepos)

    pevaka = steps.add_parser("evaka", help="Add a guardian's sign-in in a city's eVaka: the "
                              "password typed at the secret store's own prompt, checked at once, "
                              "the children matched to the Kids by name")
    pevaka.add_argument("--city", required=True,
                        help="The city, one of " + ", ".join(evaka_cities()) + ", or the host of "
                             "its eVaka")
    pevaka.add_argument("--username", required=True, help="The guardian's eVaka email")
    pevaka.set_defaults(func=cmd_evaka)

    from . import setup_save, setup_server, setup_status
    setup_save.register(steps)
    setup_status.register(steps)
    setup_server.register(steps)

    # Every step but `status`, which only reads, writes what the evening Brief reads.
    for name, step in steps.choices.items():
        if name != "status":
            step.set_defaults(func=_not_as_administrator(step.get_default("func")))


def _not_as_administrator(func: Step) -> Step:
    """`func`, refused in a Windows PowerShell opened with "Run as administrator", before it
    writes anything: the evening task couldn't open what it would write there (#464)."""
    def step(args: argparse.Namespace) -> int:
        if profile.current().system == "windows" and windows_only.elevated():
            return report("run-as-administrator", AS_ADMINISTRATOR)
        return func(args)
    return step


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
    url = setup_gmail.app_passwords_url(address)
    if not args.no_open:
        desktop.open_url(url)

    try:
        password = secret_dialog.ask(
            f"{warning}Paste the App Password for {address}.\n\nCreate it on Google's App "
            f"passwords page, which is open in your browser ({url}), "
            "with any name, such as Parent Recap, then copy the 16 letters right away: "
            "Google shows them only once.",
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
            f"{warning}Paste the Gateway Key for the evening Brief.\n\nIt's the key starting with "
            "sk- that the Parent Recap maintainers sent you. Copy all of it and paste it here.")
    except secret_dialog.Cancelled:
        return report("cancelled", "The family closed the dialog. Run this again when they're "
                      f"ready: {again}")
    except secret_dialog.NoWayToAsk:
        return report("no-prompt", "No dialog or Terminal prompt could be shown here. The family "
                      f"runs this in Terminal: {again}")

    url, sends_only = _gateway_use(args.config)
    result, extra = setup_ai.gateway_sign_in(url, typed, sends_only)
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


def cmd_confirm(args: argparse.Namespace) -> int:
    """A send-only Gateway Key on request (ADR 0033): gets one when none is stored, binds the
    Recipients in the config to it, and prints who still has to press the link in their
    confirmation email. Nothing secret is printed: the key goes straight to the Keychain."""
    from . import send_key
    again = f"{program()} setup confirm --resend"
    try:
        cfg = Config.load(args.config)
    except Exception:
        return report("bad-config", f"The config can't be read. Run {program()} doctor, fix what it "
                      "names, then run this again.")
    if cfg.sends_from_gmail():  # nobody confirms anything for the Household's own Gmail (ADR 0034)
        return report("not-needed", sending="gmail")
    status = send_key.ensure(cfg, resend=args.resend)
    extra = _confirmation(status)
    if status.result == send_key.CONFIRMED:
        return report("confirmed", **extra)
    if status.result == send_key.UNCONFIRMED:
        return report("unconfirmed", "Each address in `unconfirmed` presses the link in the "
                      "confirmation email from brief@parentrecap.com (it may be in Junk). The "
                      "Brief goes only to addresses that confirmed. If the email didn't come, run: "
                      f"{again}", **extra)
    if status.result == send_key.NO_KEY and not send_key.may_request(cfg):
        return report("no-key", "The gateway writes this Household's Brief, so its Gateway Key "
                      "comes from the Parent Recap team: store it with "
                      f"{program()} setup gateway", **extra)
    return report(status.result, f"{status.error} Run this again later: {program()} setup confirm",
                  **extra)


def cmd_sending(args: argparse.Namespace) -> int:
    """Sends the Brief from the Household's own Gmail, or through the gateway again (ADR 0034),
    and prints how it's sent now. Back on the gateway, it says who still has to confirm, as
    `setup confirm` does. Nothing secret is printed."""
    from . import send_key, sending
    from .actions import email as email_action
    path = Path(os.path.expanduser(os.path.expandvars(args.config or "~/.family/config.yaml")))
    try:
        Config.load(path)
    except Exception:
        return report("bad-config", f"The config can't be read. Run {program()} doctor, fix what it "
                      "names, then run this again.")
    done = sending.switch(path, args.to)
    gmail_again = f"{program()} setup sending gmail"
    next_ = {
        sending.NOT_OFFERED: "Only a Household whose AI is Claude or ChatGPT and that reads its "
                             "Gmail with an App Password can send the Brief from that Gmail.",
        email_action.REJECTED: "Gmail didn't take the App Password, which stops reading that "
                               f"Gmail too. Store a new one with {program()} setup gmail, then "
                               f"run: {gmail_again}",
        email_action.NO_PASSWORD: f"Store the Gmail App Password with {program()} setup gmail, "
                                  f"then run: {gmail_again}",
        email_action.NO_ANSWER: f"Check the computer is online, then run: {gmail_again}",
        "save-failed": f"Run {program()} doctor, fix what it names, then run this again.",
    }.get(done.result)
    extra: dict = {"sending": done.sending, **({"error": done.detail} if done.detail else {})}
    status = done.confirm
    if status is not None:
        extra.update(_confirmation(status))
        if status.result != send_key.CONFIRMED:
            next_ = (f"{send_key.describe(status)}. {program()} setup confirm says who has "
                     "confirmed.")
    return report(done.result, next_, **extra)


def _confirmation(status) -> dict:
    """Who a send_key.Status bound and who still has to confirm, as `setup confirm` and `setup
    sending` print them."""
    return {"recipients": status.recipients, "unconfirmed": status.unconfirmed,
            "mailed": status.mailed, **({"key_requested": True} if status.requested else {})}


def _gateway_use(config: str | None) -> tuple[str, bool]:
    """The gateway's address, and whether the Household uses it only to send the Brief, its AI
    being another (ADR 0028, ADR 0032)."""
    try:
        cfg = Config.load(config)
    except Exception:  # no config yet: setup can store the key before writing it
        return LLMConfig().gateway_url, False
    return cfg.llm.gateway_url, cfg.llm.backend != "gateway"


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
            f"Paste {args.kid}'s MyClub calendar link.\n\nIn MyClub's New calendar subscription "
            f"form ({setup_myclub.MYCLUB_URL}, after signing in), type any name, choose Selected "
            f"member accounts (Valitut jäsentilit) with only {args.kid} ticked, leave Indication on "
            "All, press Create, then Copy link, and copy the link starting with webcal://.")
    except secret_dialog.Cancelled:
        return report("cancelled", "The family closed the dialog. Run this again when they're "
                      f"ready: {again}")
    except secret_dialog.NoWayToAsk:
        return report("no-prompt", "No dialog or Terminal prompt could be shown here. The family "
                      f"runs this in Terminal: {again} --no-open")

    result, extra = setup_myclub.save_link(path, args.kid, url)
    next_ = {
        "not-a-myclub-link": "That wasn't a MyClub calendar link. Copy it with Copy link "
                             "(Kopioi linkki) on MyClub's Calendar subscriptions page, starting "
                             f"with webcal://, and run this again: {again} --no-open",
        "link-failed": "The link didn't open, so it wasn't saved. Copy it again from MyClub and "
                       f"run this again: {again} --no-open",
        "not-a-calendar": "The link opened a page, not a calendar, so it wasn't saved. Copy it "
                          "with Copy link (Kopioi linkki) on MyClub's Calendar subscriptions "
                          f"page, not from the browser's address bar: {again} --no-open",
        "save-failed": "The link works, but it couldn't be saved in the config (see error).",
        "same-link": f"That link is already saved for {extra.get('other_kid')}, so it wasn't saved "
                     f"again. Each Kid needs their own link: make a new calendar subscription, "
                     f"choose Valitut jäsentilit, tick only {args.kid}, and copy that link: "
                     f"{again} --no-open",
    }.get(result)
    return report(result, next_, **({"kid": args.kid} if result == "saved" else {}), **extra)


# ---------------------------------------------------------------- eepos

def cmd_eepos(args: argparse.Namespace) -> int:
    """The password is typed at the secret store's own prompt in this terminal, so it reaches no
    dialog, chat or command line, and the one line of JSON never has it."""
    from . import setup_eepos
    result, next_, extra = setup_eepos.cmd(args)
    return report(result, next_, **extra)


# ---------------------------------------------------------------- evaka

def evaka_cities() -> list[str]:
    from .collectors.evaka import CITIES
    return list(CITIES)


def cmd_evaka(args: argparse.Namespace) -> int:
    """The password is typed at the secret store's own prompt in this terminal, so it reaches no
    dialog, chat or command line, and the one line of JSON never has it."""
    from . import setup_evaka
    result, next_, extra = setup_evaka.cmd(args)
    return report(result, next_, **extra)
