"""The email Action: one call for the Brief, its first one from setup and Weekend Picks, sent by
the transport the config picks (ADR 0028): the Household's Gmail over SMTP, or the maintainers'
gateway, which sends it from Parent Recap's own address."""
from __future__ import annotations

import base64
import logging
import smtplib
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from email.utils import formatdate
from typing import TYPE_CHECKING

from .. import gateway as api
from ..collectors.gmail import get_app_password  # same Keychain entry as inbound IMAP

if TYPE_CHECKING:
    from ..config import Config

log = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


Attachments = list[tuple[str, bytes, str]]


def send(cfg: Config, subject: str, body_text: str, to_addrs: list[str],
         body_html: str | None = None, attachments: Attachments | None = None) -> None:
    """Send one email to `to_addrs` by `cfg`'s sender. A send that fails raises, saying why.

    attachments: (filename, payload, mime type) tuples, e.g. ("events.ics", b"...", "text/calendar").
    """
    if not to_addrs:
        log.warning("email: no recipients configured")
        return
    if cfg.email.sender == "gateway":
        gateway(cfg.llm.gateway_url, subject, body_text, to_addrs, body_html, attachments)
    else:
        gmail(from_address(cfg), subject, body_text, to_addrs, body_html, attachments)


def from_address(cfg: Config) -> str:
    """The Gmail address the Brief is sent from when Gmail sends it."""
    return cfg.email.from_addr or cfg.gmail.username or ""


def write_to(cfg: Config) -> str | None:
    """Where a Recipient writes instead of replying, when the Brief comes from the gateway's
    address, which nobody reads: the setup parent, the first Recipient. None when the
    Household's own Gmail sends it, which a reply reaches."""
    if cfg.email.sender != "gateway" or not cfg.email.to:
        return None
    return cfg.email.to[0].address


def gateway(url: str, subject: str, body_text: str, to_addrs: list[str],
            body_html: str | None = None, attachments: Attachments | None = None) -> None:
    """Send through the gateway's Brief relay at `url` with the Gateway Key (ADR 0028). A refused
    send raises GatewayError as a failed model call does, and is never tried again here: the
    relay may have sent it, and the next run sends the night again."""
    key = api.key()
    body: dict = {"to": list(to_addrs), "subject": subject, "text": body_text}
    if body_html:
        body["html"] = body_html
    if attachments:
        body["attachments"] = [{"filename": f, "content_type": mime,
                                "data": base64.b64encode(payload).decode("ascii")}
                               for f, payload, mime in attachments]
    try:
        refused = api.read_sent(api.post_brief(url, key, body))
    except api.GatewayError as e:
        raise api.GatewayError(api.without_key(str(e), key), e.status) from None
    if refused:
        log.warning("email: the mail service refused %d of %d Recipients", refused, len(to_addrs))
    log.info("email sent through the gateway at %s: subject=%r (%d Recipients, %d chars, "
             "%d attachments)", api.host(url), subject, len(to_addrs), len(body_text),
             len(attachments or []))


def gmail(from_addr: str, subject: str, body_text: str, to_addrs: list[str],
          body_html: str | None = None, attachments: Attachments | None = None) -> None:
    """Send via Gmail SMTP using the App Password stored in the Keychain."""
    pw = get_app_password(from_addr)
    if not pw:
        raise RuntimeError(f"No Gmail App Password in Keychain for {from_addr}")

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body_text, "plain", "utf-8"))
    if body_html:
        alt.attach(MIMEText(body_html, "html", "utf-8"))

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
    else:
        msg = alt

    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    msg["Date"] = formatdate(localtime=True)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
        s.starttls()
        s.login(from_addr, pw)
        s.sendmail(from_addr, to_addrs, msg.as_string())
    log.info("email sent: subject=%r to=%s (%d chars, %d attachments)",
             subject, to_addrs, len(body_text), len(attachments or []))
