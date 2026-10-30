"""The email Action: one call for the Brief, its first one from setup and Weekend Picks, all sent
through the maintainers' gateway from Parent Recap's own address (ADR 0028, ADR 0032). The
Household's Gmail is only ever read."""
from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING

from .. import gateway as api
from .. import send_key

if TYPE_CHECKING:
    from ..config import Config

log = logging.getLogger(__name__)


Attachments = list[tuple[str, bytes, str]]


def send(cfg: Config, subject: str, body_text: str, to_addrs: list[str],
         body_html: str | None = None, attachments: Attachments | None = None) -> None:
    """Send one email to `to_addrs` through the gateway `cfg` names. A send that fails raises,
    saying why.

    attachments: (filename, payload, mime type) tuples, e.g. ("events.ics", b"...", "text/calendar").
    """
    if not to_addrs:
        log.warning("email: no recipients configured")
        return
    try:
        gateway(cfg.llm.gateway_url, subject, body_text, to_addrs, body_html, attachments)
    except api.NoKey as e:
        # A Household that sent through its own Gmail before ADR 0032 has no key: one whose AI is
        # its own Claude Code or Codex gets a send-only one on request (ADR 0033), and the
        # Brief goes out once a Recipient confirms.
        if not e.missing or not send_key.may_request(cfg):
            raise
        status = send_key.ensure(cfg)
        raise api.GatewayError(f"no Gateway Key was stored: {send_key.describe(status)}") from None


def write_to(cfg: Config) -> str | None:
    """Where a Recipient writes instead of replying, since the Brief comes from the gateway's
    address, which nobody reads: the setup parent, the first Recipient."""
    return cfg.email.to[0].address if cfg.email.to else None


def gateway(url: str, subject: str, body_text: str, to_addrs: list[str],
            body_html: str | None = None, attachments: Attachments | None = None) -> None:
    """Send through the gateway's Brief relay at `url` with the Gateway Key (ADR 0028). A refused
    send raises GatewayError as a failed model call does, and is never tried again here: the
    relay may have sent it, and the next run sends the night again. Without a Gateway Key it
    raises NoKey."""
    gateway_key = api.key()
    body: dict = {"to": list(to_addrs), "subject": subject, "text": body_text}
    if body_html:
        body["html"] = body_html
    if attachments:
        body["attachments"] = [{"filename": f, "content_type": mime,
                                "data": base64.b64encode(payload).decode("ascii")}
                               for f, payload, mime in attachments]
    try:
        refused, unconfirmed = api.read_sent(api.post_brief(url, gateway_key, body))
    except api.GatewayError as e:
        raise api.GatewayError(api.without_key(str(e), gateway_key), e.status) from None
    if refused:
        log.warning("email: the mail service refused %d of %d Recipients", refused, len(to_addrs))
    if unconfirmed:
        log.warning("email: not sent to %s, who %s not confirmed their address yet: %s",
                    ", ".join(unconfirmed), "has" if len(unconfirmed) == 1 else "have",
                    api.CONFIRM_HOW)
    log.info("email sent through the gateway at %s: subject=%r (%d Recipients, %d chars, "
             "%d attachments)", api.host(url), subject, len(to_addrs), len(body_text),
             len(attachments or []))
