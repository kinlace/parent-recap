"""What `doctor` checks of eVaka, which its record hooks in as `check` (ADR 0022)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ....ops import FAIL, OK, WARN
from . import collector as evaka, setup

if TYPE_CHECKING:
    from ....config import Config
    from ...record import Add


def check(cfg: Config, add: Add, config: str | None = None) -> None:
    """Each eVaka sign-in, one city each: signs in with the password in the secret store and reads
    the children, the coming events and the latest messages, as the evening run does. A child who
    is none of the Kids is a warning: their events go to the whole Household. So are messages that
    can't be read while the rest can."""
    for signin in cfg.evaka.signins:
        where, again = evaka.host_of(signin.city), setup.again(signin.city, signin.username)
        try:
            checked = setup.check(cfg, signin, evaka.password_of(signin))
        except evaka.WrongPassword as e:
            add(FAIL, "evaka", f"the sign-in as {signin.username} doesn't work: {e} (type the password "
                f"again: {again})", subject=where)
            continue
        except evaka.EvakaError as e:
            add(FAIL, "evaka", f"couldn't be read: {e}", subject=where)
            continue
        named = [f"{c.called()} ({kid})" if kid else c.called() for c, kid in checked["children"]]
        messages = checked["messages"]
        read = "messages readable" if isinstance(messages, int) else "no messages"
        detail = (f"signed in as {signin.username}, {checked['events']} events in the next "
                  f"{evaka.LOOKAHEAD_DAYS} days, {read}; children: {', '.join(named) or '(none)'}")
        if isinstance(messages, str):
            add(WARN, "evaka", f"{detail}. The messages couldn't be read: {messages}", subject=where)
        elif unmatched := [c.called() for c, kid in checked["children"] if not kid]:
            add(WARN, "evaka", f"{detail}. {', '.join(unmatched)} match no Kid in the config, so "
                "their events go to the whole Household (add the name as one of the Kid's aliases)",
                subject=where)
        else:
            add(OK, "evaka", detail, subject=where)
