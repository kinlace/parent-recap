"""Sending the Brief from the Household's own Gmail, or through the gateway again (ADR 0034): an
advanced setting, offered only when the AI isn't the gateway and Gmail is connected with an App
Password (Config.may_send_from_gmail), and never asked during setup. The setup page's Finish and
`setup sending`, which the manage skill runs, both switch through `switch`.

Turning it on first signs in to Gmail's SMTP server with the App Password, sending nothing, so a
Household is never moved to a Gmail that won't send. From then on no send-only Gateway Key is
asked for and nobody confirms anything (ADR 0033 is skipped); a key stored before stays, unused.
Turning it off goes back to the gateway, asking it for a send-only key bound to the Recipients
when none is stored, as ADR 0033 does.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import send_key, setup_save
from .actions import email as email_action
from .config import Config

GMAIL, GATEWAY = "gmail", "gateway"
# What switching can say besides `saved`: not offered to this Household, one of the email
# Action's REJECTED, NO_PASSWORD or NO_ANSWER from Gmail, or `save-failed` for the config.
NOT_OFFERED = "not-offered"


@dataclass
class Switched:
    result: str
    sending: str                          # how the Brief is sent now: GMAIL or GATEWAY
    detail: str | None = None             # why it didn't switch; can name the Gmail address
    confirm: send_key.Status | None = None  # back on the gateway: the Recipients' confirmation


def current(cfg: Config) -> str:
    """How the Brief is sent now."""
    return GMAIL if cfg.sends_from_gmail() else GATEWAY


def switch(config: Path, to: str) -> Switched:
    """Sends the Household's Brief from its own Gmail (`to` GMAIL) or through the gateway (GATEWAY)
    from the next one on, and says how it went."""
    cfg = Config.load(config)
    if to == GMAIL:
        if not cfg.may_send_from_gmail():
            why = ("the gateway writes this Household's Brief" if cfg.llm.backend == "gateway"
                   else "no Gmail is connected")
            return Switched(NOT_OFFERED, current(cfg), f"{why}, so it's sent through the gateway")
        outcome, detail = email_action.check_gmail_sends(cfg.gmail.username or "")
        if outcome != email_action.WORKS:
            return Switched(outcome, current(cfg), detail)
    out = setup_save.save(config, {"sending": to})
    if out["result"] != "saved":
        return Switched("save-failed", current(cfg), out.get("next"))
    cfg = Config.load(config)
    confirm = send_key.ensure(cfg) if to == GATEWAY and send_key.may_request(cfg) else None
    return Switched("saved", current(cfg), confirm=confirm)
