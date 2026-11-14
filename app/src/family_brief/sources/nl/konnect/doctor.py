"""What `doctor` checks of Konnect, which its record hooks in as `check` (ADR 0022)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ....ops import FAIL, OK, WARN
from . import collector as konnect, setup

if TYPE_CHECKING:
    from ....config import Config
    from ...record import Add


def check(cfg: Config, add: Add, config: str | None = None) -> None:
    """Each Konnect sign-in, one portal each: signs in with the password in the secret store and
    reads the children, the coming Agenda and the last day's messages, as the evening run does. A
    portal that wants a new password first says so, with what to do. A child who is none of the
    Kids is a warning: their planned times go to the whole Household. So are messages that can't
    be read while the rest can."""
    for signin in cfg.konnect.signins:
        where = konnect.address(signin.portal)
        again = setup.again(signin.portal, signin.username)
        try:
            checked = setup.check(cfg, signin, konnect.password_of(signin))
        except konnect.MustResetPassword:
            add(FAIL, "konnect", f"the sign-in as {signin.username} needs a new password: "
                f"{setup.reset_password(signin.portal)}, then type it again: {again}",
                subject=where)
            continue
        except konnect.WrongPassword as e:
            add(FAIL, "konnect", f"the sign-in as {signin.username} doesn't work: {e} (type the "
                f"password again: {again})", subject=where)
            continue
        except konnect.KonnectError as e:
            add(FAIL, "konnect", f"couldn't be read: {e}", subject=where)
            continue
        named = [f"{c.called()} ({kid})" if kid else c.called() for c, kid in checked["children"]]
        messages = checked["messages"]
        read = (f"{messages} message{'s' * (messages != 1)} in the last day" if isinstance(messages, int)
                else "no messages")
        detail = (f"signed in as {signin.username}, {checked['events']} planned times in the next "
                  f"{konnect.LOOKAHEAD_DAYS} days, {read}; children: {', '.join(named) or '(none)'}")
        if isinstance(messages, str):
            add(WARN, "konnect", f"{detail}. The messages couldn't be read: {messages}", subject=where)
        elif unmatched := [c.called() for c, kid in checked["children"] if not kid]:
            add(WARN, "konnect", f"{detail}. {', '.join(unmatched)} match no Kid in the config, so "
                "their planned times go to the whole Household (add the name as one of the Kid's "
                "aliases)", subject=where)
        else:
            add(OK, "konnect", detail, subject=where)
