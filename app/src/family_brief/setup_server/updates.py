"""Updates, under Finish's "Later, if you need it": that Parent Recap installs new releases by
itself after the evening Brief (ADR 0047), the switch that turns that off, the installed version,
and Update now once a newer release is out, with what it will ask when it is one that waits for
the family (#918). A newer release that changes nothing the Household uses (ADR 0051) is said to
be out and not installed, without Update now. On a Mac and a Windows PC alike (ADR 0052). Where the program doesn't update itself (a PC
without ssh-keygen, the hosted version, a program not run from an installed release) the page
gets the version alone.

And the update before a Source is added (ADR 0051, #961): a Household that skipped releases may
hold an old copy of a Source it never connected. A step that adds a Source with a part of its
own asks `Updates.first` before it sends anything to the Source. When the Source's part changed
since the installed version, the newest release is installed as Update now installs it, and the
step answers `updating-first` in place of adding anything: what the family typed stays on the
page, which asks `check_now` how the update goes. Once it is installed, the server stops and the
installed version starts it again at the same address (server.start_again), and the page sends
the step again, to the new version. When the update can't be done, the step says so and nothing
is added with the old code."""
from __future__ import annotations

import threading
from contextlib import ExitStack
from http import HTTPStatus
from typing import Any

from pydantic import StrictBool

from .. import failure, run_lock, update
from ..setup_connect import Strict, parse
from .context import Action, Context, Phase, Reply, nothing_to_give

# Update now: `updating` while the release is downloaded, verified, installed and checked, then
# how update.update ended (`skipped` for a newer release that changes nothing the Household uses,
# ADR 0051); `busy` when a Brief is being made right now, or another update is
# running (`parent-recap update` in Terminal or PowerShell), so nothing was started; and `no-update` when this
# server hasn't started one.
NOW_RESULTS = ("updating", "updated", "up-to-date", "skipped", "switched-back", "failed",
               "not-available", "busy", "no-update")
# What a step that adds a Source answers in place of adding it (`Updates.first`): `updating-first`
# while Parent Recap updates itself first, or while the server is about to start again in the
# newer version; `update-failed`, with the reason, when it couldn't look for a newer release; and
# `update-busy` when a Brief is being made or another update is running, so none was started.
FIRST_RESULTS = ("updating-first", "update-failed", "update-busy")


class SwitchAnswer(Strict):
    on: StrictBool


class Updates(Phase):
    def __init__(self, context: Context) -> None:
        super().__init__(context)
        self._now: dict[str, Any] | None = None  # Update now, once started
        self._thread: threading.Thread | None = None
        # The installed version this server was started from, whose code it runs until it stops.
        self._runs = update.installed_version()
        context.newest_first = self.first

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
        page asks how it went with `check_now`. One at a time: it takes the run lock here, before
        the update's look, and the update holds it until it ended (update.alone). While a run or
        another update holds it, nothing is started and the answer is `busy`."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, nothing_to_give()
        if update.not_applicable(str(self.context.config)) is not None:
            return HTTPStatus.OK, {"result": "not-available"}
        return HTTPStatus.OK, {"result": self._start()}

    def first(self, part: str | None) -> dict[str, Any] | None:
        """What a step that adds the Source whose part of a release is `part` answers in place
        of adding it (FIRST_RESULTS), or None when it goes on: the Source has no part, or its
        part didn't change since the installed version, which one request to `stable` says
        (update.needed_to_add). When it did, the newest release is installed in the background
        as Update now installs it, with that part counted as one the Household uses, and the
        server then starts again in it. A server that no longer runs the installed version,
        after Update now, an evening's update or `parent-recap update` in a terminal while the
        page was open, starts again once it has answered, without another update."""
        context = self.context
        if part is None:
            return None
        with context.working:
            if context.restart or (self._now or {}).get("result") == "updating":
                return {"result": "updating-first"}  # the page asks `check_now` how it goes
            if update.installed_version() != self._runs:
                context.restart = True  # the server stops once this is answered (server.py)
                return {"result": "updating-first"}
        try:
            wanted = update.needed_to_add(part, str(context.config))
        except update.Refused as e:
            return {"result": "update-failed", "reason": failure.logged(
                "The update before a Source is added didn't start",
                f"Parent Recap couldn't look for a newer release: {e}.")}
        if wanted is None:
            return None
        if self._start(adding=part) == "busy":
            return {"result": "update-busy"}
        return {"result": "updating-first", "version": wanted}

    def check_now(self, raw: Any) -> Reply:
        """How Update now went: the version that runs now, the one it tried, and for an update
        that failed or switched back, what the program says of it, for the family to copy to us.
        With it, the update state as it is now. A server about to start again in the newer
        version is still `updating`: the one started in its place has started no update, and
        says `no-update`."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, nothing_to_give()
        now = self._now or {"result": "no-update"}
        if self.context.restart:
            return HTTPStatus.OK, {"result": "updating"}
        if now["result"] in ("updating", "no-update"):
            return HTTPStatus.OK, now
        return HTTPStatus.OK, {**now, "updates": shown(self.context)}

    def stop(self) -> None:
        """An update under way isn't cut off halfway by the server stopping."""
        if self._thread is not None:
            self._thread.join()

    def _start(self, adding: str | None = None) -> str:
        """Starts the update in the background, holding the run lock from here: `updating`, also
        for one of this page's own still going, or `busy` while a run or another update holds
        the lock. With `adding`, the part of the Source a step is adding."""
        context = self.context
        with context.working:
            if self._now is not None and self._now["result"] == "updating":
                return "updating"  # this page's own, still going
            held = ExitStack()
            try:
                held.enter_context(update.alone(str(context.config)))
            except run_lock.Busy:
                return "busy"
            before = self._now
            self._now = {"result": "updating"}  # before it starts: it ends by saying how it went
            try:
                self._thread = threading.Thread(target=self._updating, args=(held, adding),
                                                daemon=True)
                self._thread.start()
            except BaseException:  # no update runs to let go of the lock
                self._now = before
                held.close()
                raise
        return "updating"

    def _updating(self, held: ExitStack, adding: str | None) -> None:
        """The update, holding the run lock `_start` took (`held`), which it lets go of once
        the update ended, before the page is told how. A server that updated to add a Source
        then stops, to start again in the installed version."""
        context = self.context
        with held:
            try:
                outcome = update.update(str(context.config), adding=adding)
                done: dict[str, Any] = {"result": "not-available"
                                        if outcome.result == "not-applicable" else outcome.result}
                if outcome.version is not None:
                    done["version"] = outcome.version
                if outcome.tried is not None:
                    done["tried"] = outcome.tried
                if outcome.skipped is not None:
                    done["skipped"] = outcome.skipped
                if outcome.result in ("failed", "switched-back"):
                    done["reason"] = failure.logged("Update now didn't update", outcome.message)
            except Exception as e:  # never leave the page updating
                done = {"result": "failed", "reason": failure.logged(
                    "Update now stopped", failure.of(e, "The update"))}
        if done["result"] == "updated" and adding is not None:
            context.restart = True  # before the page is told: `check_now` then says to wait
        self._now = done
        if context.restart:
            context.stopped.set()


def shown(context: Context) -> dict[str, Any]:
    """The update state as the page shows it: the installed version, whether the program updates
    itself here (`available`), and where it does, whether that's turned on, the newer version
    this computer has seen is out, if any, and the version that is `waiting` for the family to
    start it, as it needs them at the computer, with what it `asks` of them by language (#918),
    or the newer version the last look `skipped`, as it changes nothing the Household uses.
    `report` is doctor's line of it, which the page puts in what a family copies to us."""
    status = update.status(str(context.config))
    out: dict[str, Any] = {"available": status.unavailable is None, "version": status.version,
                           "report": status.line()}
    if status.unavailable is None:
        waiting = None if status.waiting is None else {"version": status.waiting,
                                                       "asks": status.asks or {}}
        out |= {"on": status.enabled, "newer": status.newer, "waiting": waiting,
                "skipped": status.skipped}
    return out
