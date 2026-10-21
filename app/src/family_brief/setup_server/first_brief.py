"""First Brief: the real Brief made through a bg job, which reads WhatsApp with the scheduled job's
permission, without sending it, shown in the page's frame and sent to the setup parent only."""
from __future__ import annotations

import json
import threading
from http import HTTPStatus
from typing import Any

from .. import run_lock, setup_save
from ..actions import email as email_action
from ..state import State
from ..system import desktop, jobs
from .context import Action, Context, Page, Phase, Reply, Served, nothing_to_give

# Making it: `making` while it's made, with the Source it's reading or `writing`, then `made` or
# `make-failed`, and `no-brief` when this server hasn't made one.
BRIEF_RESULTS = ("making", "made", "make-failed", "no-brief")
BRIEF_LOOKBACK_HOURS = 72  # a single day often has nothing new
# Sending it to the setup parent: `busy` while an evening run is going, which could send it too.
SEND_RESULTS = ("sent", "send-failed", "busy")
# The Brief in the page's frame: its own inline styles and nothing else, sandboxed without
# scripts, and its links open nothing (a sandbox without popups drops a new window).
BRIEF_CSP = ("default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; "
             "frame-ancestors 'self'; sandbox")


class FirstBrief(Phase):
    def __init__(self, context: Context) -> None:
        super().__init__(context)
        self._brief: dict[str, Any] | None = None  # the first Brief, once it's being made

    def routes(self) -> dict[str, Action]:
        return {"api/brief": self.make_brief, "api/brief/check": self.check_brief,
                "api/brief/send": self.send_brief, "api/brief/done": self.brief_done,
                "api/brief/feedback": self.open_feedback}

    def pages(self) -> dict[str, Page]:
        return {"brief.html": self.brief_page}

    def state(self, progress: dict[str, Any]) -> dict[str, Any]:
        return {"brief": {"sent": self._brief_sent()}}

    def make_brief(self, raw: Any) -> Reply:
        """Makes the real Brief from the Household's Sources over the last three days, as `run
        --preview` does, without sending it, in the background: the page asks how far it is with
        `check_brief`. Making it again replaces the one made before."""
        if raw != {} or self.context.progress()["phase"] != "first-brief":
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Confirm the check page first.",
                errors=["answers: should be {}, once setup is at First Brief"])
        with self.context.working:
            if self._brief is None or self._brief["result"] != "making":
                self._brief = {"result": "making", "sources": [], "step": None}
                threading.Thread(target=self._make_brief, daemon=True).start()
        return HTTPStatus.OK, {"result": "making"}

    def check_brief(self, raw: Any) -> Reply:
        """How far making the Brief is: `making`, with the Sources and the one it's on, or
        `writing`; once `made`, its email HTML and whether the Household is a pilot one, with
        the feedback button. The feedback link stays here: the page opens it through the server."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, nothing_to_give()
        brief = self._brief
        if brief is None:
            return HTTPStatus.OK, {"result": "no-brief"}
        if brief["result"] == "made":
            return HTTPStatus.OK, {"result": "made", "html": brief["html"],
                                   "feedback": brief["feedback"] is not None}
        return HTTPStatus.OK, brief

    def brief_page(self) -> Served | None:
        """The Brief made, for the page's frame, with its links kept from opening in it."""
        brief = self._brief
        if brief is None or brief["result"] != "made":
            return None
        html = brief["html"].replace("<html>", '<html><head><base target="_blank"></head>', 1)
        return html.encode(), "text/html; charset=utf-8", {"Content-Security-Policy": BRIEF_CSP}

    def _make_brief(self) -> None:
        try:
            code, out = jobs.run_detached(
                ["run", "--preview", "--lookback-hours", str(BRIEF_LOOKBACK_HOURS)],
                self.context.timeouts.brief, config=str(self.context.config), echo=False,
                output=self._brief_progress)
            made = next((line for line in reversed(_preview_lines(out))
                         if line["preview"] == "made"), None)
            done = {"result": "made", **{k: made[k] for k in ("subject", "text", "html", "feedback")}} \
                if code == 0 and made else {"result": "make-failed"}
        except Exception:  # its error can name an address: never leave the page making it
            done = {"result": "make-failed"}
        self._brief = done

    def _brief_progress(self, out: str) -> None:
        sources, step = [], None
        for line in _preview_lines(out):
            sources = line.get("sources", sources)
            step = line.get("source") or ("writing" if line["preview"] == "writing" else step)
        self._brief = {"result": "making", "sources": sources, "step": step}

    def send_brief(self, raw: Any) -> Reply:
        """"Send it to me": sends the Brief made to the setup parent, the first Recipient, and
        never to anyone else: the others' first Brief is the first evening one. It's recorded as
        delivered to them, which the outcome check counts as the first Brief, and nothing else:
        the evening Brief still reads the same Messages."""
        brief = self._brief
        cfg = self.context.model()
        parent = cfg.email.to[0].address if cfg.email.enabled and cfg.email.to else None
        if raw != {} or brief is None or brief["result"] != "made" or parent is None:
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Make the Brief first.",
                errors=["answers: should be {}, once the Brief is made"])
        try:
            with run_lock.exclusive(cfg):
                try:
                    email_action.send(subject=brief["subject"], body_text=brief["text"],
                                      body_html=brief["html"],
                                      from_addr=cfg.email.from_addr or cfg.gmail.username or "",
                                      to_addrs=[parent])
                except Exception:  # its error can name the address
                    return HTTPStatus.OK, {"result": "send-failed"}
                state = State(cfg.resolved_state_path())
                state.mark_delivered_now([parent])
                state.save()
        except run_lock.Busy:
            return HTTPStatus.OK, {"result": "busy"}
        return HTTPStatus.OK, {"result": "sent", "to": parent}

    def brief_done(self, raw: Any) -> Reply:
        """Moves setup on to Finish once the first Brief has reached the setup parent."""
        with self.context.connect.lock:
            phase = self.context.progress()["phase"]
            if raw != {} or phase != "first-brief" or not self._brief_sent():
                return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                    "invalid-answers", "Send the first Brief to yourself first.",
                    errors=["answers: should be {}, once the first Brief is sent"])
            out = setup_save.save(self.context.config, {"progress": {"phase": "finish"}})
        if out["result"] != "saved":
            return HTTPStatus.CONFLICT, out
        return HTTPStatus.OK, out

    def open_feedback(self, raw: Any) -> Reply:
        """"Something's wrong, tell us", for a pilot Household: opens the Brief's own Digest
        feedback link, pre-filled with the Brief made, in the default browser."""
        brief = self._brief
        link = brief.get("feedback") if brief and brief["result"] == "made" else None
        if raw != {} or not link:
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Only a pilot Household's Brief has the feedback link.",
                errors=["answers: should be {}, once a pilot Household's Brief is made"])
        return HTTPStatus.OK, {"result": "opened" if desktop.open_url(link) else "not-opened"}

    def _brief_sent(self) -> bool:
        """Whether a Brief has reached the setup parent, the first Recipient."""
        cfg = self.context.model()
        if not cfg.email.to:
            return False
        return State(cfg.resolved_state_path()).delivered_at(cfg.email.to[0].address) is not None


def _preview_lines(out: str) -> list[dict[str, Any]]:
    """The JSON lines `run --preview` printed, in order, without its log lines."""
    lines = []
    for line in out.splitlines():
        if line.startswith('{"preview"'):
            try:
                lines.append(json.loads(line))
            except ValueError:
                continue  # cut off while it was being written
    return lines
