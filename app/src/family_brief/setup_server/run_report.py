"""The Run Report's switch, under Finish's "Later, if you need it" (ADR 0048, #927): whether the
family's computer sends the maintainers' gateway its Run Report after each evening run, which the
page shows with one sentence on what the report is and a link to what it holds in SECURITY.md.
Left unset, it is on for a Household that sends its Brief through the gateway and off for one that
sends it from its own Gmail (ADR 0034), which the page then asks; once the family set it, it stays
as set whichever way the Brief is sent (Config.sends_run_report). A Hosted Household sends none,
and its page has no switch."""
from __future__ import annotations

from http import HTTPStatus
from typing import Any

from pydantic import StrictBool

from .. import setup_save
from ..config import Config
from ..setup_connect import Strict, parse
from .context import Action, Phase, Reply


class SwitchAnswer(Strict):
    on: StrictBool


class RunReport(Phase):
    def routes(self) -> dict[str, Action]:
        return {"api/run-report": self.switch}

    def state(self, progress: dict[str, Any]) -> dict[str, Any]:
        return {"run_report": shown(self.context.model())}

    def switch(self, raw: Any) -> Reply:
        """Turns the Run Report on or off, as the manage skill's `setup save` does."""
        answer = parse(SwitchAnswer, raw)
        context = self.context
        if context.model().hosted:
            return HTTPStatus.OK, {"result": "not-available", "run_report": shown(context.model())}
        with context.connect.lock:
            saved = setup_save.save(context.config, {"run_report": answer.on})["result"] == "saved"
        return HTTPStatus.OK, {"result": "saved" if saved else "save-failed",
                               "run_report": shown(context.model())}


def shown(cfg: Config) -> dict[str, Any]:
    """The switch as the page shows it: whether there is one (`available`), not for a Hosted
    Household; whether the report is sent (`on`); and whether the page asks the family (`ask`),
    for a Household that sends its Brief from its own Gmail and hasn't said yet."""
    if cfg.hosted:
        return {"available": False}
    return {"available": True, "on": cfg.sends_run_report(),
            "ask": cfg.run_report.enabled is None and cfg.sends_from_gmail()}
