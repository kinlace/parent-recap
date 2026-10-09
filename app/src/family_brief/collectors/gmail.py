from __future__ import annotations

import email
import email.policy
import imaplib
import logging
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from typing import Callable

from bs4 import BeautifulSoup

from .. import install_record
from ..config import Config
from ..state import State
from ..utils import keychain
from .base import Message, retry_once_on_timeout, unreadable

log = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
IMAP_TIMEOUT = 60  # seconds per network step; without one a stalled connection hangs the night's run
MAX_BODY_CHARS = 8000


def keychain_account(username: str) -> str:
    return f"gmail-imap-{username}"


def get_app_password(username: str) -> str | None:
    return keychain.get(keychain_account(username))


def store_app_password(username: str, password: str) -> None:
    account = keychain_account(username)
    keychain.set_(account, password)
    install_record.add("keychain", account)


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _extract_body(msg: EmailMessage) -> str:
    plain = None
    html = None
    for part in msg.walk():
        ctype = part.get_content_type()
        if part.is_multipart():
            continue
        disp = (part.get("Content-Disposition") or "").lower()
        if "attachment" in disp:
            continue
        try:
            payload = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                charset = part.get_content_charset() or "utf-8"
                try:
                    payload = payload.decode(charset, errors="replace")
                except LookupError:  # a charset Python doesn't know, e.g. unknown-8bit
                    payload = payload.decode("utf-8", errors="replace")
            elif not isinstance(payload, str):
                payload = ""
        if ctype == "text/plain" and plain is None:
            plain = payload
        elif ctype == "text/html" and html is None:
            html = payload
    body = plain or ""
    if not body and html:
        body = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
    body = re.sub(r"\n{3,}", "\n\n", body or "").strip()
    return body[:MAX_BODY_CHARS]


def _build_or_search(terms: list[str]) -> list[bytes]:
    """Build IMAP OR-tree for a set of search terms against SUBJECT and FROM and BODY.

    Gmail IMAP supports X-GM-RAW for native Gmail search syntax — simpler and more powerful.
    """
    escaped = []
    for t in terms:
        t = t.replace('"', '\\"')
        escaped.append(f'"{t}"')
    raw = " OR ".join(escaped)
    return ["X-GM-RAW", f"({raw})"]


def _has_cjk(s: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" or "\u3040" <= c <= "\u30ff" for c in s)


def _gmail_query(cfg: Config, kid_terms: list[str]) -> str:
    """Build a Gmail X-GM-RAW search string.

    Allowlist-based: only emails from trusted senders/domains are pulled.
    Self-sent notes and drafts are explicitly excluded as a defense-in-depth measure.
    """
    hours = cfg.gmail.lookback_hours
    days = max(1, hours // 24 + (1 if hours % 24 else 0))

    from_terms: list[str] = []
    for d in cfg.gmail.allowlist_domains:
        from_terms.append(f"from:{d}")
    for s in cfg.gmail.allowlist_senders:
        from_terms.append(f"from:{s}")
    if not from_terms:
        raise RuntimeError(
            "gmail.allowlist_domains or gmail.allowlist_senders is empty — "
            "refusing to scan all inbox."
        )
    from_block = "(" + " OR ".join(from_terms) + ")"

    username = cfg.gmail.username or ""
    exclusions = [
        "-category:promotions",
        "-category:social",
        "-in:drafts",
        "-in:sent",
    ]
    if username:
        exclusions.append(f"-from:{username}")

    parts = [f"newer_than:{days}d", from_block, *exclusions]
    return " ".join(parts)


def collect(cfg: Config, state: State, kid_terms: list[str]) -> list[Message]:
    username = cfg.gmail.username or cfg.kids[0].wilma_username
    if not username:
        raise RuntimeError("No Gmail username configured (set gmail.username in config.yaml)")
    password = get_app_password(username)
    if not password:
        raise RuntimeError(
            f"No Gmail App Password in Keychain for {username}. "
            "Run: parent-recap setup gmail"
        )

    query = _gmail_query(cfg, kid_terms)
    log.info("Gmail X-GM-RAW query: %s", query)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=cfg.gmail.lookback_hours)
    # A timeout gets one fresh connection, which reads again what the first one marked seen.
    return retry_once_on_timeout(
        lambda: _read_matching(state, username, password, query, cutoff), (TimeoutError,), "Gmail",
        before_retry=lambda: state.forget_new_seen_messages("gmail"))


def _read_matching(state: State, username: str, password: str, query: str,
                   cutoff: datetime) -> list[Message]:
    results: list[Message] = []
    with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=IMAP_TIMEOUT) as imap:
        imap.login(username, password)
        # Allow UTF-8 in commands (Finnish chars in query)
        imap._encoding = "utf-8"
        imap.select('"[Gmail]/All Mail"', readonly=True)
        # IMAP quoted-string: escape \ and " inside the wrapper.
        wrapped = '"' + query.replace("\\", "\\\\").replace('"', '\\"') + '"'
        typ, data = imap.search(None, "X-GM-RAW", wrapped)
        if typ != "OK":
            log.warning("Gmail search failed: %s", data)
            return []
        ids = data[0].split()
        log.info("Gmail matched %d messages", len(ids))

        for imap_id in ids:
            # Get Gmail's stable message ID (X-GM-MSGID) for dedup.
            typ, msgid_data = imap.fetch(imap_id, "(X-GM-MSGID)")
            if typ != "OK" or not msgid_data or not msgid_data[0]:
                continue
            m = re.search(rb"X-GM-MSGID\s+(\d+)", msgid_data[0])
            ext_id = m.group(1).decode() if m else imap_id.decode()
            if state.has_seen_message("gmail", ext_id):
                continue

            # A dropped connection raises out of fetch and fails the whole Source; one message
            # that won't parse is skipped and left unseen.
            typ, raw = imap.fetch(imap_id, "(RFC822)")
            if typ != "OK" or not raw or not raw[0]:
                continue
            try:
                parsed = _parse(raw[0][1], ext_id, cutoff)
            except Exception as e:
                log.error("%s", unreadable("gmail", ext_id, f"{type(e).__name__}: {e}"))
                continue
            if parsed:
                results.append(parsed)
            state.mark_message_seen("gmail", ext_id)

    return results


def _parse(raw: bytes, ext_id: str, cutoff: datetime) -> Message | None:
    """The email as a Message, or None if it is older than `cutoff`."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    subject = _decode(msg.get("Subject"))
    sender_raw = _decode(msg.get("From"))
    _, sender_email = parseaddr(sender_raw)
    date_hdr = msg.get("Date")
    try:
        ts = parsedate_to_datetime(date_hdr) if date_hdr else datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        ts = ts.astimezone(timezone.utc)
    except Exception:
        ts = datetime.now(timezone.utc)
    if ts < cutoff:
        return None
    metadata = {"sender_email": sender_email, "thread_id": msg.get("X-GM-THRID") or ""}
    if _to_a_mailing_list(msg):
        metadata["mailing_list"] = True  # sent to everyone, so never held back from the AI (held_back.py)
    return Message(
        source="gmail",
        external_id=ext_id,
        timestamp=ts,
        sender=sender_raw,
        subject=subject,
        body=_extract_body(msg),
        url=f"https://mail.google.com/mail/u/0/#search/rfc822msgid:{msg.get('Message-ID','').strip('<>')}",
        metadata=metadata,
    )


def _to_a_mailing_list(msg: EmailMessage) -> bool:
    """Whether the email went out to a list, as a city's or a school's mass email does: it has a
    List-Id or List-Unsubscribe header, or Precedence: bulk or list. The headers come with the
    message the collector reads anyway."""
    return bool(msg.get("List-Id") or msg.get("List-Unsubscribe")) or \
        str(msg.get("Precedence") or "").strip().casefold() in ("bulk", "list")


def _login(cfg: Config) -> tuple[imaplib.IMAP4_SSL, str]:
    username = cfg.gmail.username or cfg.kids[0].wilma_username
    if not username:
        raise RuntimeError("gmail.username is not set in config.yaml")
    password = get_app_password(username)
    if not password:
        raise RuntimeError(f"no Gmail App Password in Keychain for {username}")
    imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    imap.login(username, password)
    imap._encoding = "utf-8"
    imap.select('"[Gmail]/All Mail"', readonly=True)
    return imap, username


def count_matching(cfg: Config) -> int:
    """How many allowlisted messages fall in the lookback window (no bodies fetched)."""
    imap, _ = _login(cfg)
    try:
        query = _gmail_query(cfg, [])
        wrapped = '"' + query.replace("\\", "\\\\").replace('"', '\\"') + '"'
        typ, data = imap.search(None, "X-GM-RAW", wrapped)
        return len(data[0].split()) if typ == "OK" and data and data[0] else 0
    finally:
        imap.logout()


def sender_domains(cfg: Config, days: int = 60, max_messages: int = 1500,
                   progress: Callable[[int, int], None] | None = None) -> list[tuple[str, int, str]]:
    """Top sender domains over the last N days, from From: headers only. Used during onboarding
    to help pick the allowlist. Returns (domain, count, one example display name).
    `progress(done, total)` is called after each batch of headers is read."""
    imap, username = _login(cfg)
    try:
        raw = f"newer_than:{days}d -from:{username} -category:promotions -category:social -in:sent -in:drafts"
        typ, data = imap.search(None, "X-GM-RAW", f'"{raw}"')
        ids = data[0].split()[-max_messages:] if typ == "OK" and data and data[0] else []
        counts: dict[str, int] = {}
        example: dict[str, str] = {}
        step = 200
        for i in range(0, len(ids), step):
            batch = b",".join(ids[i:i + step])
            typ, rows = imap.fetch(batch, "(BODY.PEEK[HEADER.FIELDS (FROM)])")
            for row in rows or []:
                if not isinstance(row, tuple):
                    continue
                name, addr = parseaddr(_decode(row[1].decode("utf-8", "replace").split(":", 1)[-1].strip()))
                if "@" not in addr:
                    continue
                dom = addr.rsplit("@", 1)[1].lower()
                counts[dom] = counts.get(dom, 0) + 1
                example.setdefault(dom, (name or addr)[:40])
            if progress:
                progress(min(i + step, len(ids)), len(ids))
        return sorted(((d, n, example[d]) for d, n in counts.items()), key=lambda t: -t[1])
    finally:
        imap.logout()
