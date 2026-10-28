"""Connect's Source list: skipping a Source or opening one from it, the sites and apps its steps
send the family to, and the way back to it from Check or Finish."""
from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus
from typing import Any, Literal

from .. import setup_connect, setup_gmail, setup_myclub, setup_save
from .. import sources as source_registry
from ..setup_connect import Nothing, Reply, Strict, parse
from ..system import desktop, desktop_macos, macos_only
from . import finish
from .context import Action, Context, Phase
from .gmail import ADDRESS

# The Sources a family can skip and add later through manage, in the page's order.
SKIPPABLE = tuple(s.name for s in source_registry.in_connect_order() if s.skippable)
# The sites the page's buttons open, by name: the page itself names none. App Passwords' also
# takes the family's Gmail address, to open in that Google account.
SITES = {"app-passwords": setup_gmail.APP_PASSWORDS_URL, "two-step": setup_gmail.TWO_STEP_URL,
         "myclub": setup_myclub.MYCLUB_URL}
# The apps they open, by name.
APPS = {"passwords": desktop_macos.open_passwords}


class SourceChoice(Strict):
    source: setup_save.SourceName
    action: Literal["open", "skip"]


class Connect(Phase):
    def routes(self) -> dict[str, Action]:
        return {"api/source": self.choose_source, "api/open": self.open_site,
                "api/connect/back": self.back_to_connect}

    def state(self, progress: dict[str, Any]) -> dict[str, Any]:
        return {"connect": {"sources": [{"name": s, "skippable": s in SKIPPABLE}
                                        for s in setup_connect.sources()]}}

    def choose_source(self, raw: Any) -> Reply:
        """Skips a Source, moving setup on to the next one to do, or opens one from the list,
        which brings a skipped one back."""
        answer = parse(SourceChoice, raw)
        if answer.action == "open":
            return self.context.connect.open(answer.source)
        if answer.source not in SKIPPABLE:
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Open a Source, or skip one that can be skipped.",
                errors=[f"source: skip one of {', '.join(SKIPPABLE)}"])
        return self.context.connect.skip(answer.source)

    def open_site(self, raw: Any) -> Reply:
        """Opens one of the sites the page sends the family to, in the default browser, or one of
        the Mac's apps. App Passwords' opens in the family's Gmail, so it needs the address."""
        apps = {name: open_app for name, open_app in APPS.items() if macos_only.offers(name)}
        site = raw.get("site") if isinstance(raw, dict) else None
        given = {"site", "address"} if site == "app-passwords" else {"site"}
        if not (isinstance(raw, dict) and set(raw) == given and site in {*SITES, *apps}):
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Open one of the page's own sites.",
                errors=[f"site: should be one of {', '.join([*SITES, *apps])}, with the "
                        "address for app-passwords"])
        if site in apps:
            opened = apps[site]()
            return HTTPStatus.OK, {"result": "opened" if opened else "not-opened"}
        url = SITES[site]
        if site == "app-passwords":
            address = raw["address"].strip().lower() if isinstance(raw["address"], str) else ""
            if not ADDRESS.match(address):
                return HTTPStatus.OK, {"result": "no-address"}
            url = setup_gmail.app_passwords_url(address)
        opened = desktop.open_url(url)
        return HTTPStatus.OK, {"result": "opened" if opened else "not-opened", "url": url}

    def back_to_connect(self, raw: Any) -> Reply:
        """Takes setup from Check or Finish back to Connect's Source list, for a Source the family
        skipped and wants after all: they open it from the list, which brings it back, and setup
        goes on from there as before. A page opened again after setup is at Finish, so this is
        also how a Source is added later. Never while a check runs."""
        parse(Nothing, raw)
        return go_back(self.context, lambda: self.context.connect.reopen(
            lambda progress: setup_connect.skipped(progress["sources"]),
            setup_save.outcome(
                "invalid-answers",
                "Only Check and Finish go back to Connect, for a Source that was skipped.",
                errors=["answers: should be {}, at Check or Finish with a Source skipped"])))


def go_back(context: Context, step: Callable[[], Reply]) -> Reply:
    """Takes setup back to an earlier phase with `step`, never while a check runs. How the health
    check and Finish went was about setup as it was, so a step back forgets it."""
    with context.working:
        runs = context.runs
        if runs.get("health", {}).get("result") == "checking" or \
                runs.get("finish", {}).get("result") in finish.UNDER_WAY:
            return HTTPStatus.CONFLICT, {"result": "busy"}
        status, out = step()
        if status == HTTPStatus.OK:
            runs.pop("health", None)
            runs.pop("finish", None)
    return status, out
