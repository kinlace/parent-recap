"""The hosted version's sign-up (ADR 0031): `parent-recap signup serve`, the always-on service
behind the gateway's web server that turns an Invite Link into a Hosted Household;
`signup invite`, which mints an Invite Link (ops/gateway/invite.sh runs it as the service's user);
`signup household`, which the service runs as a new Household's own user; `signup status`,
which lists every Household's last run and doctor's statuses (ops/gateway/status.sh), from
`signup health` run as each; and `signup remove`, which removes a Household with everything of it
(ops/gateway/remove.sh).

The service runs as its own user, with Parent Recap installed under it for the wilma CLI that
checks a family's sign-in. It reads from its environment (ops/gateway/signup.env.example):
SIGNUP_RELEASE, the unpacked release whose install.sh installs the program for each Household,
and Bifrost's admin login, which mints the Gateway Keys. The store, the Invite Links and the list
of Households, is in `~/signup`, owner-only, unless SIGNUP_STORE says otherwise.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .. import setup_wilma
from .invites import DAYS, LANGUAGES, Recipient, Store

log = logging.getLogger("family_brief.signup")
PORT = 8082  # beside Bifrost's 8080 and the Brief relay's 8081


def register(sub) -> None:
    p = sub.add_parser("signup", help="The hosted version's sign-up: its service, Invite Links, "
                       "and a Household made under its own user (ADR 0031)")
    steps = p.add_subparsers(dest="signup", required=True)
    serve = steps.add_parser("serve", help="Serve the sign-up page on 127.0.0.1 (the systemd "
                             "unit's command)")
    serve.add_argument("--port", type=int, default=PORT)
    serve.set_defaults(func=cmd_serve)

    invite = steps.add_parser("invite", help="Mint an Invite Link and print it")
    invite.add_argument("--recipient", action="append", default=[], metavar="ADDRESS[:LANGUAGE]",
                        help=f"A Recipient the page shows prefilled; LANGUAGE is one of "
                             f"{', '.join(LANGUAGES)} (default: --language)")
    invite.add_argument("--language", choices=LANGUAGES, default="fi",
                        help="The page's language until the family picks another")
    invite.add_argument("--days", type=int, default=DAYS, help="How many days the link works")
    invite.add_argument("--url", default=None, help="The address the link starts with "
                        "(default: https://$GATEWAY_HOST)")
    invite.set_defaults(func=cmd_invite)

    from .status import cmd_health, cmd_status
    status = steps.add_parser("status", help="List every Hosted Household with its last run "
                              "and doctor's statuses, and nothing of its messages")
    status.set_defaults(func=cmd_status)

    health = steps.add_parser("health", help="Print this user's Household's last run and "
                              "doctor's statuses as one line of JSON (signup status runs it)")
    health.set_defaults(func=cmd_health)

    remove = steps.add_parser("remove", help="Remove a Hosted Household: its jobs, its Gateway "
                              "Key, its user and its home (ops/gateway/remove.sh runs it)")
    remove.add_argument("label", help="The Household's user, ph-XXXXXXXX")
    remove.set_defaults(func=cmd_remove)

    household = steps.add_parser("household", help="Make this user's Hosted Household from the "
                                 "answers on stdin (the sign-up service runs it)")
    from .household import cmd_household
    household.set_defaults(func=cmd_household)


def store() -> Store:
    return Store(Path(os.path.expanduser(os.environ.get("SIGNUP_STORE") or "~/signup")))


def cmd_invite(args: argparse.Namespace) -> int:
    recipients = []
    for given in args.recipient:
        address, _, language = given.rpartition(":") if ":" in given else (given, "", "")
        language = language or args.language
        if language not in LANGUAGES or "@" not in address:
            print(f"❌ Not a Recipient: {given}. Give it as ADDRESS or ADDRESS:LANGUAGE, with "
                  f"LANGUAGE one of {', '.join(LANGUAGES)}.", file=sys.stderr)
            return 2
        recipients.append(Recipient(address, language))
    base = args.url or (f"https://{os.environ['GATEWAY_HOST']}"
                        if os.environ.get("GATEWAY_HOST") else None)
    if base is None:
        print("❌ Give the address the link starts with: --url, or GATEWAY_HOST.", file=sys.stderr)
        return 2
    if args.days < 1:
        print("❌ --days is at least 1.", file=sys.stderr)
        return 2
    invite = store().mint(recipients, args.language, datetime.now(timezone.utc), args.days)
    from .server import PREFIX
    print(f"{base.rstrip('/')}{PREFIX}{invite.code}")
    print(f"It works once, until {invite.expires:%Y-%m-%d %H:%M} UTC.", file=sys.stderr)
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    from .host import HostError
    from .host_linux import LinuxHost
    from .remove import remove
    try:
        host = LinuxHost.from_env()
    except HostError as e:
        print(f"❌ {e}. Nothing was removed.", file=sys.stderr)
        return 1
    return remove(args.label, store(), host)


def cmd_serve(args: argparse.Namespace) -> int:
    from .host import HostError
    from .host_linux import LinuxHost
    from .server import Service, SignupServer
    release = os.environ.get("SIGNUP_RELEASE")
    if not release or not (Path(release) / "install.sh").is_file():
        log.error("SIGNUP_RELEASE isn't the folder of an unpacked release with its install.sh")
        return 78  # a setting is missing, which restarting won't fix
    try:
        host = LinuxHost.from_env()
    except HostError as e:
        log.error("%s", e)
        return 78
    # The service's own wilma CLI checks each family's sign-in.
    got = setup_wilma.install()
    if got["result"] != "installed":
        log.error("The wilma CLI didn't install, so no sign-in can be checked: %s",
                  got.get("reason") or got["result"])
        return 1
    server = SignupServer(Service(store(), host, Path(release)), args.port)
    log.info("The sign-up page is served on %s", server.url)
    server.serve_forever()
    return 0
