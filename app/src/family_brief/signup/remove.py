"""Removing a Hosted Household (ADR 0031): `parent-recap signup remove <user>`, which
ops/gateway/remove.sh runs as the sign-up service's user, through the household host.

In order: its evening jobs are removed by its own program as its own user, its Gateway Key is
revoked, its user is deleted with its home, and it is taken off the store's list. The list goes
last, so a removal that stopped half-way still knows the Household and, run again, does the rest.
"""
from __future__ import annotations

import sys
from pathlib import Path

from .host import USER, HostError, HouseholdHost, program
from .invites import Store

JOBS_SECONDS = 120
OK, NONE, WARN, FAIL = "✅", "—", "⚠️", "❌"


def remove(label: str, store: Store, host: HouseholdHost) -> int:
    if not USER.fullmatch(label):
        print(f"{FAIL} {label!r} isn't a Hosted Household: its label is its user, ph- and 8 hex "
              f"digits, as `ls /home` shows. Nothing was removed.", file=sys.stderr)
        return 2
    home = host.home(label)
    listed = any(h.user == label for h in store.households())
    if home is None and not listed:
        print(f"{FAIL} No Hosted Household {label} on this server: there is no such user and the "
              f"sign-up's list doesn't have it. Nothing was removed.", file=sys.stderr)
        return 1
    try:
        _jobs(label, home, host)
        if host.revoke_key(label):
            print(f"{OK} {label}: its Gateway Key revoked")
        else:
            print(f"{NONE} {label}: no Gateway Key to revoke")
        if home is not None:
            host.remove_user(label)
            print(f"{OK} {label}: its user deleted with its home {home}")
        else:
            print(f"{NONE} {label}: no user to delete")
    except HostError as e:
        print(f"{FAIL} {label} is only partly removed: {e}. Run the command again to remove the "
              f"rest.", file=sys.stderr)
        return 1
    if store.remove_household(label):
        print(f"{OK} {label}: taken off the sign-up's list, which frees its evening slot")
    else:
        print(f"{NONE} {label}: not on the sign-up's list")
    return 0


def _jobs(label: str, home: Path | None, host: HouseholdHost) -> None:
    if home is None:
        print(f"{NONE} {label}: no user, so no jobs to stop")
        return
    try:
        code, out = host.run_as(label, [str(program(home)), "schedule", "uninstall"], "",
                                JOBS_SECONDS)
    except HostError as e:
        code, out = None, str(e)
    if code == 0:
        print(f"{OK} {label}: its evening jobs stopped and removed")
    else:
        # Deleting the user ends its systemd user manager and its timers with it.
        print(f"{WARN} {label}: its program didn't remove its jobs "
              f"({out.strip()[-200:] or code}); deleting its user below stops them")
