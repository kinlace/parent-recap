"""The email Action: one call for the Brief, its first one from setup and Weekend Picks, sent by
the transport the config picks: through the maintainers' gateway from Parent Recap's own address
(ADR 0028, ADR 0032), the default, or, for a Household whose AI is its own and that turned it on,
from its own Gmail over SMTP with the App Password it's read with (ADR 0034)."""
from __future__ import annotations

import base64
import logging
import smtplib
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from typing import TYPE_CHECKING

from .. import gateway as api
from .. import send_key
from ..system import secrets

if TYPE_CHECKING:
    from ..config import Config

log = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465  # SSL from the first byte, so the App Password never crosses in the clear
SMTP_TIMEOUT = 30

# What Gmail's sending check finds: it takes the App Password, refuses it, has none to use (none
# stored, or the store wouldn't give it), or couldn't be reached.
WORKS, REJECTED, NO_PASSWORD, NO_ANSWER = "works", "rejected", "no-password", "no-answer"

# A refused App Password stops both what Gmail does for the Household (ADR 0034).
REJECTED_MESSAGE = ("Gmail didn't accept the App Password for {address}, so Parent Recap can "
                    "neither read that Gmail nor send the Brief from it (it was probably revoked: "
                    "create a new one and store it with parent-recap setup gmail)")


class GmailError(RuntimeError):
    """The Household's Gmail didn't send; `reason` is REJECTED, NO_PASSWORD or NO_ANSWER."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


Attachments = list[tuple[str, bytes, str]]


def send(cfg: Config, subject: str, body_text: str, to_addrs: list[str],
         body_html: str | None = None, attachments: Attachments | None = None) -> None:
    """Send one email to `to_addrs` by `cfg`'s transport: the Household's own Gmail when it sends
    from there, else the gateway `cfg` names. A send that fails raises, saying why.

    attachments: (filename, payload, mime type) tuples, e.g. ("events.ics", b"...", "text/calendar").
    """
    if not to_addrs:
        log.warning("email: no recipients configured")
        return
    if cfg.sends_from_gmail():
        gmail(cfg.gmail.username or "", subject, body_text, to_addrs, body_html, attachments)
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
    """Where a Recipient writes instead of replying, when the Brief comes from the gateway's
    address, which nobody reads: the setup parent, the first Recipient. None when the Household's
    own Gmail sends it, which a reply reaches."""
    if cfg.sends_from_gmail() or not cfg.email.to:
        return None
    return cfg.email.to[0].address


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


def gmail(from_addr: str, subject: str, body_text: str, to_addrs: list[str],
          body_html: str | None = None, attachments: Attachments | None = None) -> None:
    """Send from the Household's own Gmail `from_addr` over SMTP, signed in with the App Password
    it's read with (ADR 0034), so the Brief stays in that Gmail's Sent folder. Raises GmailError,
    saying why, when Gmail couldn't be signed in to; a send Gmail refuses raises as smtplib does."""
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body_text, "plain", "utf-8"))
    if body_html:
        alt.attach(MIMEText(body_html, "html", "utf-8"))
    msg = alt
    if attachments:
        msg = MIMEMultipart("mixed")
        msg.attach(alt)
        for filename, payload, mime in attachments:
            maintype, subtype = mime.split("/", 1)
            part = MIMEBase(maintype, subtype, name=filename)
            if mime == "text/calendar":
                part.set_param("method", "PUBLISH")
            part.set_payload(payload)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=filename)
            msg.attach(part)
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    msg["Date"] = formatdate(localtime=True)
    with _signed_in(from_addr) as smtp:
        refused = smtp.sendmail(from_addr, to_addrs, msg.as_string())
    if refused:
        log.warning("email: Gmail refused %d of %d Recipients", len(refused), len(to_addrs))
    log.info("email sent from the Household's Gmail: subject=%r (%d Recipients, %d chars, "
             "%d attachments)", subject, len(to_addrs), len(body_text), len(attachments or []))


def check_gmail(from_addr: str) -> tuple[str, str]:
    """Whether Gmail would send from `from_addr`: signs in to its SMTP server with the App
    Password and out again, sending nothing. WORKS, REJECTED, NO_PASSWORD or NO_ANSWER, with what
    was found."""
    try:
        with _signed_in(from_addr):
            pass
    except GmailError as e:
        return e.reason, str(e)
    return WORKS, f"Gmail takes the App Password for {from_addr}"


def _signed_in(from_addr: str) -> smtplib.SMTP_SSL:
    """Gmail's SMTP server, signed in as `from_addr` with its App Password, for a `with`."""
    from ..collectors.gmail import get_app_password  # the same entry the Gmail Source reads with
    try:
        password = get_app_password(from_addr)
    except secrets.SecretsError as e:
        raise GmailError(f"couldn't read the App Password for {from_addr} from the "
                         f"{secrets.STORE}: {e}", NO_PASSWORD) from None
    if not password:
        raise GmailError(f"no Gmail App Password in the {secrets.STORE} for {from_addr} (store "
                         "one with parent-recap setup gmail)", NO_PASSWORD)
    try:
        smtp = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT)
    except OSError as e:
        raise GmailError(f"couldn't reach Gmail at {SMTP_HOST}: {e}", NO_ANSWER) from None
    try:
        smtp.login(from_addr, password)
    except smtplib.SMTPAuthenticationError:
        smtp.close()
        raise GmailError(REJECTED_MESSAGE.format(address=from_addr), REJECTED) from None
    except (smtplib.SMTPException, OSError) as e:
        smtp.close()
        raise GmailError(f"Gmail at {SMTP_HOST} didn't let {from_addr} sign in: {e}",
                         NO_ANSWER) from None
    return smtp
