"""What `doctor` checks of Eepos, which its record hooks in as `check` (ADR 0022)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ....ops import FAIL, OK, WARN
from . import collector as eepos, setup

if TYPE_CHECKING:
    from ....config import Config
    from ...record import Add


def check(cfg: Config, add: Add, config: str | None = None) -> None:
    """Each Eepos sign-in, one school each: signs in with the password in the secret store and
    reads the students and the coming calendar, as the evening run does. A student who is none of
    the Kids is a warning: their lessons go to the whole Household."""
    for signin in cfg.eepos.signins:
        where, again = eepos.address(signin.school), setup.again(signin.school, signin.username)
        try:
            checked = setup.check(cfg, signin, eepos.password_of(signin))
        except eepos.WrongPassword as e:
            add(FAIL, "eepos", f"the sign-in as {signin.username} doesn't work: {e} (type the password "
                f"again: {again})", subject=where)
            continue
        except eepos.EeposError as e:
            add(FAIL, "eepos", f"couldn't be read: {e}", subject=where)
            continue
        named = [f"{s.called()} ({kid})" if kid else s.called() for s, kid in checked["students"]]
        detail = (f"signed in as {signin.username}, {checked['events']} events in the next "
                  f"{eepos.LOOKAHEAD_DAYS} days; students: {', '.join(named) or '(none)'}")
        if unmatched := [s.called() for s, kid in checked["students"] if not kid]:
            add(WARN, "eepos", f"{detail}. {', '.join(unmatched)} match no Kid in the config, so "
                "their lessons go to the whole Household (add the name as one of the Kid's aliases)",
                subject=where)
        else:
            add(OK, "eepos", detail, subject=where)
