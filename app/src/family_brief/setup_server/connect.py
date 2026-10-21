"""Connect's Source list: skipping a Source or opening one from it, the sites and apps its steps
send the family to, and the way back to it from Check or Finish."""
from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus
from typing import Any, Literal

from .. import setup_ai, setup_connect, setup_gmail, setup_myclub, setup_save
from .. import sources as source_registry
from ..setup_connect import Nothing, Reply, Strict, parse
from ..system import desktop, desktop_macos, macos_only
from . import finish
from .context import Action, Phase

# The Sources a family can skip and add later through manage, in the page's order.
SKIPPABLE = tuple(s.name for s in source_registry.in_connect_order() if s.skippable)
# The sites the page's buttons open, by name: the page itself names none.
SITES = {"app-passwords": setup_gmail.APP_PASSWORDS_URL, "two-step": setup_gmail.TWO_STEP_URL,
         "myclub": setup_myclub.MYCLUB_URL, "codex-app": setup_ai.CODEX_APP_URL}
# The apps they open, by name.
APPS = {"passwords": desktop_macos.open_passwords}


class SourceChoice(Strict):
    source: setup_save.SourceName
    action: Literal["open", "skip"]


class Connect(Phase):
    def routes(self) -> dict[str, Action]:
        return {"api/source": self.choose_source, "api/open": self.open_site,
                "api/whatsapp/back": self.back_to_whatsapp,
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
        the Mac's apps."""
        apps = {name: open_app for name, open_app in APPS.items() if macos_only.offers(name)}
        if not (isinstance(raw, dict) and set(raw) == {"site"} and raw["site"] in {*SITES, *apps}):
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Open one of the page's own sites.",
                errors=[f"site: should be one of {', '.join([*SITES, *apps])}"])
        if raw["site"] in apps:
            opened = apps[raw["site"]]()
            return HTTPStatus.OK, {"result": "opened" if opened else "not-opened"}
        url = SITES[raw["site"]]
        opened = desktop.open_url(url)
        return HTTPStatus.OK, {"result": "opened" if opened else "not-opened", "url": url}

    def back_to_whatsapp(self, raw: Any) -> Reply:
        """Takes setup from Check or Finish back to WhatsApp's step in Connect, for ticked groups
        the health check didn't find on this Mac. The step reads the groups again, so a group's
        new name is there to tick in Check, and setup goes on from there as before."""
        return self._back_to_connect(
            raw, "whatsapp", lambda progress: progress["sources"]["whatsapp"] == "done",
            "Only Check and Finish go back to WhatsApp.",
            "answers: should be {}, at Check or Finish with WhatsApp done")

    def back_to_connect(self, raw: Any) -> Reply:
        """Takes setup from Check or Finish back to Connect's Source list, for a Source the family
        skipped and wants after all: they open it from the list, which brings it back, and setup
        goes on from there as before. A page opened again after setup is at Finish, so this is
        also how a Source is added later."""
        return self._back_to_connect(
            raw, None, lambda progress: setup_connect.skipped(progress["sources"]),
            "Only Check and Finish go back to Connect, for a Source that was skipped.",
            "answers: should be {}, at Check or Finish with a Source skipped")

    def _back_to_connect(self, raw: Any, source: str | None,
                         allowed: Callable[[dict[str, Any]], bool], refusal: str,
                         error: str) -> Reply:
        """Takes setup from Check or Finish back to Connect at `source`, or at its list for None,
        when `allowed` says setup's progress allows it, and never while a check runs."""
        parse(Nothing, raw)
        context = self.context
        with context.working:
            runs = context.runs
            if runs.get("health", {}).get("result") == "checking" or \
                    runs.get("finish", {}).get("result") in finish.UNDER_WAY:
                return HTTPStatus.CONFLICT, {"result": "busy"}
            status, out = context.connect.reopen(
                source, allowed, setup_save.outcome("invalid-answers", refusal, errors=[error]))
            if status == HTTPStatus.OK:
                # How the health check and Finish went was about the Sources as they were.
                runs.pop("health", None)
                runs.pop("finish", None)
        return status, out
