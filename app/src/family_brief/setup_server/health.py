"""The health check the check page runs once it's confirmed, and doctor's checks as the page shows
them, at Check and at Finish: by the key doctor gives each, never its words."""
from __future__ import annotations

from http import HTTPStatus
from typing import Any

from .. import ops, setup_save
from .context import Action, Context, Phase, Reply, nothing_to_give

# Check: the health check, and how it ended. Warnings alone don't hold setup up.
HEALTH_RESULTS = ("checking", "ok", "not-ok", "no-check")
# Doctor's checks as the check page names them. Any other is `other`.
HEALTH_CHECKS = {"config": "config", "recipients": "recipients", "language": "language",
                 "gmail": "gmail", "claude": "ai", "codex": "ai", "gateway": "gateway",
                 "gateway-key": "gateway", "wilma": "wilma", "whatsapp": "whatsapp",
                 "whatsapp-python": "whatsapp", "myclub": "myclub", "calendar": "calendar",
                 "feedback": "feedback", "weekend": "weekend"}
# A language's and a Kid's MyClub check carry the language or the Kid too, from their subject.
HEALTH_SUBJECTS = {"language": "language", "myclub": "kid"}
# The scheduled job's checks, which aren't run before Finish installs it; Windows adds the last two.
SCHEDULE_CHECKS = frozenset({"schedule", "last-run", "python-path", "wake", "sign-in-after-update"})
# Doctor's checks as Finish names them, with the scheduled job's. Python path is always OK, and
# Windows' Wake check says what the wake outcome does, so Finish leaves it out.
FINISH_CHECKS = {**HEALTH_CHECKS, "schedule": "schedule", "last-run": "last-run",
                 "sign-in-after-update": "sign-in-after-update"}
FINISH_LEAVES_OUT = frozenset({"wake"})
HEALTH_STATUSES = {ops.OK: "ok", ops.WARN: "warn", ops.FAIL: "fail"}


class Health(Phase):
    def routes(self) -> dict[str, Action]:
        return {"api/check/health": self.check_health}

    def check_health(self, raw: Any) -> Reply:
        """How the health check went: `checking` while it runs. Only each check's name and status
        are given, since their details can quote an address or an error."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, nothing_to_give()
        return HTTPStatus.OK, self.context.runs.get("health") or {"result": "no-check"}


def run(context: Context) -> None:
    """Runs the health check, without the scheduled job's checks, and moves setup on to the first
    Brief unless a check fails. How it went is the context's `health` run."""
    try:
        results = ops.health_checks(str(context.config), schedule=False)
        checks = [health_check(c) for c in results if c.key not in SCHEDULE_CHECKS]
        if any(c["status"] == "fail" for c in checks):
            done = {"result": "not-ok", "checks": checks}
        else:
            with context.connect.lock:
                out = setup_save.save(context.config, {"progress": {"phase": "first-brief"}})
            done = {"result": "ok", "checks": checks, "progress": out["progress"]} \
                if out["result"] == "saved" else {"result": "not-ok", "checks": checks}
    except Exception:  # never leave the page checking
        done = {"result": "not-ok", "checks": [{"check": "other", "status": "fail"}]}
    context.runs["health"] = done


def health_check(check: ops.Check, names: dict[str, str] = HEALTH_CHECKS) -> dict[str, Any]:
    """One of doctor's checks as the check page shows it, or Finish with its own `names`,
    without its details: with why it isn't OK, where doctor says, and the groups that's about,
    whose names the check page shows anyway."""
    out: dict[str, Any] = {"check": names.get(check.key, "other"),
                           "status": HEALTH_STATUSES.get(check.status, "warn")}
    if check.key in HEALTH_SUBJECTS and check.subject is not None:
        out[HEALTH_SUBJECTS[check.key]] = check.subject
    if check.reason:
        out["reason"] = check.reason
    if check.groups:
        out["groups"] = list(check.groups)
    return out
