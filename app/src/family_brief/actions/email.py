from __future__ import annotations

import logging
import smtplib
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from email.utils import formatdate

from ..collectors.gmail import get_app_password  # same Keychain entry as inbound IMAP

log = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def send(subject: str, body_text: str, from_addr: str, to_addrs: list[str],
         body_html: str | None = None,
         attachments: list[tuple[str, bytes, str]] | None = None) -> None:
    """Send via Gmail SMTP using the App Password stored in Keychain.

    attachments: (filename, payload, mime type) tuples, e.g. ("events.ics", b"...", "text/calendar").
    """
    if not to_addrs:
        log.warning("email: no recipients configured")
        return
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
