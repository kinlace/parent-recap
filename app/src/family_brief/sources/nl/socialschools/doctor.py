"""What `doctor` checks of Social Schools, which its record hooks in as `check` (ADR 0022)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ....ops import FAIL, OK, WARN
from . import collector as socialschools, setup

if TYPE_CHECKING:
    from ....config import Config
    from ...record import Add


def check(cfg: Config, add: Add, config: str | None = None) -> None:
    """Each Social Schools sign-in: signs in with the password in the secret store as the evening
    run does, and reads the schools, the children, the coming events and the latest announcements.
    A child who is none of the Kids is a warning: their announcements and events go to the whole
    Household. So are announcements that can't be read while the rest can. An account that signs in
    with Google, Microsoft or Apple, or asks for a further step, fails with why."""
    for signin in cfg.socialschools.signins:
        again = setup.again(signin.username)
        try:
            checked = setup.check(cfg, signin, socialschools.password_of(signin))
        except socialschools.WrongPassword as e:
            add(FAIL, "socialschools", f"the sign-in as {signin.username} doesn't work: {e}. "
                f"{setup.OTHER_SIGN_IN} (type the password again: {again})")
            continue
        except socialschools.Unsupported as e:
            add(FAIL, "socialschools", f"the sign-in as {signin.username} can't be done: {e}. "
                f"{setup.OTHER_SIGN_IN}")
            continue
        except socialschools.SocialSchoolsError as e:
            add(FAIL, "socialschools", f"couldn't be read: {e}")
            continue
        named = [f"{c.called()} ({kid})" if kid else c.called() for c, kid in checked["children"]]
        posts = checked["posts"]
        read = "announcements readable" if isinstance(posts, int) else "no announcements"
        detail = (f"signed in as {signin.username}, {len(checked['schools'])} "
                  f"school{'s' * (len(checked['schools']) != 1)}, {checked['events']} events in the next "
                  f"{socialschools.LOOKAHEAD_DAYS} days, {read}; children: {', '.join(named) or '(none)'}")
        if isinstance(posts, str):
            add(WARN, "socialschools", f"{detail}. The announcements couldn't be read: {posts}")
        elif unmatched := [c.called() for c, kid in checked["children"] if not kid]:
            add(WARN, "socialschools", f"{detail}. {', '.join(unmatched)} match no Kid in the config, so "
                "their announcements and events go to the whole Household (add the name as one of the "
                "Kid's aliases)")
        else:
            add(OK, "socialschools", detail)
