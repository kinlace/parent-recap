"""Finish: installing the evening job and the wake-up, checking the outcomes, and stopping the page
once they're all true, or once the family finishes for now."""
from __future__ import annotations

import threading
from http import HTTPStatus
from typing import Any

from .. import ops, setup_connect, setup_save, setup_status
from ..config import Config
from ..system import wake
from .context import Action, Context, Phase, Reply, nothing_to_give
from .health import FINISH_CHECKS, FINISH_LEAVES_OUT, health_check

# `installing` while the evening job and the wake-up are installed, which waits for the Mac
# password in macOS's own dialog, `checking` while the outcomes are checked, then `done` once
# every outcome is true, when the server stops, or `not-done`; `install-failed` when the jobs
# couldn't be loaded, and `no-finish` when this server hasn't started one.
FINISH_RESULTS = ("installing", "checking", "done", "not-done", "install-failed", "no-finish")
UNDER_WAY = ("installing", "checking")  # which nothing may cut off or start again


class Finish(Phase):
    def __init__(self, context: Context) -> None:
        super().__init__(context)
        # Finish was tried, with Turn it on or Check again: from then on, its checklist says
        # what's missing under each outcome that isn't true.
        self._tried = False
        # How setting the wake-up went at the last Turn it on, which Check again still shows, with
        # Replace it for another wake schedule.
        self._wake: dict[str, Any] = {}
        # Opened again once setup was at Finish: the family came back to change something.
        self._opened_at_finish = context.progress()["phase"] == "finish"

    def routes(self) -> dict[str, Action]:
        return {"api/finish": self.start_finish, "api/finish/outcomes": self.check_outcomes,
                "api/finish/check": self.check_finish, "api/finish/stop": self.stop_for_now}

    def start_finish(self, raw: Any) -> Reply:
        """Installs the evening job and the Mac's wake-up through the schedule step, in the
        background, then checks the outcomes: the page asks how it went with `check_finish`. The
        Mac password goes into macOS's own administrator dialog, never the page (ADR 0007). The
        Mac's other repeating wake schedule is replaced only with `replace_wake`, once the family
        agrees."""
        if not (isinstance(raw, dict) and set(raw) == {"replace_wake"}
                and isinstance(raw["replace_wake"], bool)):
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Say whether to replace the Mac's other wake schedule.",
                errors=["replace_wake: should be true or false"])
        return self._start(raw["replace_wake"])

    def check_outcomes(self, raw: Any) -> Reply:
        """Checks the outcomes without installing anything, for a Household that may have
        finished already: once every outcome is true, the page shows only the checklist. With
        `again`, it's Check again: the outcomes, the health check among them, are checked once
        more without loading the evening job or setting the wake-up again, and the checklist says
        what's still missing."""
        again = isinstance(raw, dict) and set(raw) == {"again"} and raw["again"] is True
        if raw != {} and not again:
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Give nothing, or say it's Check again.",
                errors=["again: should be true, or left out"])
        return self._start(None, again=again)

    def check_finish(self, raw: Any) -> Reply:
        """How Finish went: each outcome and whether it's true, without its reason, which can name
        an address, and how setting the wake-up went. Once every outcome is true, the server stops
        after saying so, unless the page was opened again after setup with a Source skipped: then
        it `stays`, for the way back to Connect to add it, until 30 minutes without a request."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, nothing_to_give()
        finish = self.context.runs.get("finish")
        if finish is None:
            return HTTPStatus.OK, {"result": "no-finish"}
        if finish["result"] == "done":
            if self._opened_at_finish and \
                    setup_connect.skipped(self.context.connect.progress()["sources"]):
                return HTTPStatus.OK, {**finish, "stays": True}
            self.context.closing = True
        return HTTPStatus.OK, finish

    def stop_for_now(self, raw: Any) -> Reply:
        """"Finish for now": stops the page with setup not done yet, once Finish isn't installing
        or checking, which would be cut off. The family comes back with the install line or in
        the chat."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, nothing_to_give()
        if self.context.progress()["phase"] != "finish":
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Setup isn't at Finish yet.",
                errors=["answers: should be given once setup is at Finish"])
        with self.context.working:
            finish = self.context.runs.get("finish")
            if finish is not None and finish["result"] in UNDER_WAY:
                return HTTPStatus.OK, {"result": finish["result"]}
            self.context.closing = True
        return HTTPStatus.OK, {"result": "stopped"}

    def _start(self, replace: bool | None, again: bool = False) -> Reply:
        """Installs with `replace` true or false, then checks; with None, only checks."""
        if self.context.progress()["phase"] != "finish":
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Send the first Brief to yourself first.",
                errors=["answers: should be given once setup is at Finish"])
        runs = self.context.runs
        with self.context.working:
            if runs.get("finish", {}).get("result") in UNDER_WAY:
                return HTTPStatus.OK, {"result": runs["finish"]["result"]}  # never asked twice
            started = {"result": "checking" if replace is None else "installing"}
            runs["finish"] = started
            self._tried = self._tried or again or replace is not None
            threading.Thread(target=self._finishing, args=(replace,), daemon=True).start()
        return HTTPStatus.OK, dict(started)

    def _finishing(self, replace: bool | None) -> None:
        runs = self.context.runs
        if replace is not None:
            try:
                cfg = Config.load(self.context.config)
                ops.install_jobs(cfg)
                hour, minute = cfg.schedule.daily_hour, cfg.schedule.daily_minute
                other = wake.status(hour, minute).others
                result = wake.set(hour, minute, replace)
            except Exception:  # its error can name the Mac's files: never leave the page installing
                self._wake = {}
                runs["finish"] = {"result": "install-failed"}
                return
            self._wake = {"wake": result, **({"other": other} if result == "other-schedule" else {})}
            runs["finish"] = {"result": "checking"}
        try:
            outcomes = setup_status.check(str(self.context.config))
        except Exception:  # never leave the page checking
            outcomes = []
        done = bool(outcomes) and all(o.ok for o in outcomes)
        # Only checked, after a try: the checklist still says what's missing.
        tried = {"tried": True} if replace is None and self._tried else {}
        runs["finish"] = {"result": "done" if done else "not-done", **self._wake, **tried,
                          "outcomes": [_finish_outcome(o) for o in outcomes]}


def _finish_outcome(o: setup_status.Outcome) -> dict[str, Any]:
    """An outcome as Finish shows it, without its reason, which can name an address: whether
    it's true, and for a health check that isn't, the checks that aren't OK."""
    out: dict[str, Any] = {"outcome": o.outcome, "ok": o.ok}
    checks = [c for c in o.checks if c.key not in FINISH_LEAVES_OUT]
    if checks:
        out["checks"] = [health_check(c, FINISH_CHECKS) for c in checks]
    return out
