"""Updates, under Finish's "Later, if you need it": that Parent Recap installs new releases by
itself after the evening Brief (ADR 0047), the switch that turns that off, the installed version,
and Update now once a newer release is out. Where the program doesn't update itself (Windows, the
hosted version, a program not run from an installed release) the page gets the version alone."""
from __future__ import annotations

import threading
from http import HTTPStatus
from typing import Any

from pydantic import StrictBool

from .. import failure, update
from ..setup_connect import Strict, parse
from .context import Action, Context, Phase, Reply, nothing_to_give

# Update now: `updating` while the release is downloaded, verified, installed and checked, then
# how update.update ended, and `no-update` when this server hasn't started one.
NOW_RESULTS = ("updating", "updated", "up-to-date", "switched-back", "failed", "not-available",
               "no-update")


class SwitchAnswer(Strict):
    on: StrictBool


class Updates(Phase):
    def __init__(self, context: Context) -> None:
        super().__init__(context)
        self._now: dict[str, Any] | None = None  # Update now, once started
        self._thread: threading.Thread | None = None

    def routes(self) -> dict[str, Action]:
        return {"api/updates": self.switch, "api/updates/now": self.update_now,
                "api/updates/check": self.check_now}

    def state(self, progress: dict[str, Any]) -> dict[str, Any]:
        return {"updates": shown(self.context)}

    def switch(self, raw: Any) -> Reply:
        """Turns the program's own updates on or off, as `parent-recap update on|off` does."""
        answer = parse(SwitchAnswer, raw)
        context = self.context
        if update.not_applicable(str(context.config)) is not None:
            return HTTPStatus.OK, {"result": "not-available", "updates": shown(context)}
        with context.connect.lock:
            saved = update.set_enabled(context.config, answer.on)
        return HTTPStatus.OK, {"result": "saved" if saved else "save-failed",
                               "updates": shown(context)}

    def update_now(self, raw: Any) -> Reply:
        """Installs the newest release now, as `parent-recap update` does, in the background: the
        page asks how it went with `check_now`. One at a time."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, nothing_to_give()
        context = self.context
        if update.not_applicable(str(context.config)) is not None:
            return HTTPStatus.OK, {"result": "not-available"}
        with context.working:
            if self._now is None or self._now["result"] != "updating":
                self._now = {"result": "updating"}
                self._thread = threading.Thread(target=self._updating, daemon=True)
                self._thread.start()
        return HTTPStatus.OK, {"result": "updating"}

    def check_now(self, raw: Any) -> Reply:
        """How Update now went: the version that runs now, the one it tried, and for an update
        that failed or switched back, what the program says of it, for the family to copy to us.
        With it, the update state as it is now."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, nothing_to_give()
        now = self._now or {"result": "no-update"}
        if now["result"] in ("updating", "no-update"):
            return HTTPStatus.OK, now
        return HTTPStatus.OK, {**now, "updates": shown(self.context)}

    def stop(self) -> None:
        """An update under way isn't cut off halfway by the server stopping."""
        if self._thread is not None:
            self._thread.join()

    def _updating(self) -> None:
        try:
            outcome = update.update(str(self.context.config))
            done: dict[str, Any] = {"result": "not-available" if outcome.result == "not-applicable"
                                    else outcome.result}
            if outcome.version is not None:
                done["version"] = outcome.version
            if outcome.tried is not None:
                done["tried"] = outcome.tried
            if outcome.result in ("failed", "switched-back"):
                done["reason"] = failure.logged("Update now didn't update", outcome.message)
        except Exception as e:  # never leave the page updating
            done = {"result": "failed", "reason": failure.logged(
                "Update now stopped", failure.of(e, "The update"))}
        self._now = done


def shown(context: Context) -> dict[str, Any]:
    """The update state as the page shows it: the installed version, whether the program updates
    itself here (`available`), and where it does, whether that's turned on and the newer version
    this computer has seen is out, if any. `report` is doctor's line of it, which the page puts
    in what a family copies to us."""
    status = update.status(str(context.config))
    out: dict[str, Any] = {"available": status.unavailable is None, "version": status.version,
                           "report": status.line()}
    if status.unavailable is None:
        out |= {"on": status.enabled, "newer": status.newer}
    return out
