from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import Config
from ..state import State
from .base import Message

log = logging.getLogger(__name__)

# macOS WhatsApp Desktop (stable) container
DB_DIR = Path.home() / "Library" / "Group Containers" / "group.net.whatsapp.WhatsApp.shared"
DB_FILE = DB_DIR / "ChatStorage.sqlite"

# Core Data epoch offset (2001-01-01 UTC → Unix epoch)
CORE_DATA_EPOCH = 978307200
MAX_BODY_CHARS = 4000


def _snapshot_db() -> Path:
    """Copy DB + WAL + SHM into a temp dir to read without holding the live DB's locks."""
    if not DB_FILE.exists():
        raise RuntimeError(f"WhatsApp DB not found at {DB_FILE} — is the app installed?")
    tmp = Path(tempfile.mkdtemp(prefix="wa_snapshot_"))
    for suffix in ("", "-wal", "-shm"):
        src = DB_DIR / f"ChatStorage.sqlite{suffix}"
        if src.exists():
            shutil.copy2(src, tmp / src.name)
    return tmp / "ChatStorage.sqlite"


def _media_placeholder(msgtype: int) -> str:
    # WhatsApp ZMESSAGETYPE: 0=text, 1=image, 2=video, 3=audio/voice, 4=contact,
    # 5=location, 7=url, 8=document, 11=live location, 14=sticker, 15=gif, ...
    return {
        1: "[image]", 2: "[video]", 3: "[voice]", 4: "[contact]",
        5: "[location]", 8: "[document]", 11: "[live location]", 14: "[sticker]", 15: "[GIF]",
    }.get(msgtype, "")


def _sender_name(conn: sqlite3.Connection, msg_pk: int,
                 from_jid: str | None, is_from_me: int) -> str:
    """Resolve sender display name. ZPUSHNAME on ZWAMESSAGE stores binary in newer builds,
    so we ignore it. Fall back chain: group member name → profile push name by JID → 'group member'.
    """
    if is_from_me:
        return "me"
    # Group message: sender via ZGROUPMEMBER foreign key
    row = conn.execute("""
        SELECT gm.ZCONTACTNAME, gm.ZFIRSTNAME, gm.ZMEMBERJID
        FROM ZWAMESSAGE m
        LEFT JOIN ZWAGROUPMEMBER gm ON gm.Z_PK = m.ZGROUPMEMBER
        WHERE m.Z_PK = ?
    """, (msg_pk,)).fetchone()
    member_jid = None
    if row:
        name = row[0] or row[1]
        if name:
            return str(name)
        member_jid = row[2]
    # Look up in profile push-name table by the member JID or from_jid
    for jid in (member_jid, from_jid):
        if not jid:
            continue
        pushed = conn.execute(
            "SELECT ZPUSHNAME FROM ZWAPROFILEPUSHNAME WHERE ZJID = ? LIMIT 1",
            (jid,),
        ).fetchone()
        if pushed and pushed[0]:
            return str(pushed[0])
    return "(group member)"


def collect(cfg: Config, state: State, kid_terms: list[str]) -> list[Message]:
    if not cfg.whatsapp.enabled:
        return []
    if not cfg.whatsapp.chats:
        log.info("WhatsApp: no chats configured, skipping")
        return []

    try:
        snap = _snapshot_db()
    except PermissionError as e:
        # The likely cause once setup has worked: Parent Recap's own Python changed (ADR 0011).
        log.error("WhatsApp snapshot failed: %s. Likely cause: Parent Recap's Python changed since "
                  "macOS allowed it to read WhatsApp. Allow %s in System Settings → Privacy & "
                  "Security → App Management (or Full Disk Access), and run parent-recap doctor to check",
                  e, os.path.realpath(sys.executable))
        return []
    except Exception as e:
        log.error("WhatsApp snapshot failed: %s — check Full Disk Access", e)
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=cfg.whatsapp.lookback_hours)
    cutoff_core = cutoff.timestamp() - CORE_DATA_EPOCH

    results: list[Message] = []
    chat_names = cfg.whatsapp.chat_names()
    conn = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
    try:
        placeholders = ",".join("?" for _ in chat_names)
        chat_rows = conn.execute(
            f"SELECT Z_PK, ZPARTNERNAME FROM ZWACHATSESSION WHERE ZPARTNERNAME IN ({placeholders})",
            chat_names,
        ).fetchall()
        known = {name for _, name in chat_rows}
        missing = [c for c in chat_names if c not in known]
        if missing:
            log.warning("WhatsApp chats not found in DB: %s", missing)
        for chat_pk, chat_name in chat_rows:
            cur = conn.execute("""
                SELECT Z_PK, ZTEXT, ZMESSAGEDATE, ZISFROMME, ZFROMJID, ZPUSHNAME, ZMESSAGETYPE
                FROM ZWAMESSAGE
                WHERE ZCHATSESSION = ? AND ZMESSAGEDATE >= ?
                  AND (ZMESSAGETYPE IS NULL OR ZMESSAGETYPE != 6)   -- skip system events
                ORDER BY ZMESSAGEDATE ASC
            """, (chat_pk, cutoff_core))
            for row in cur.fetchall():
                (msg_pk, text, msg_date, is_from_me, from_jid, _push_name, msg_type) = row
                ext_id = f"{chat_pk}:{msg_pk}"
                if state.has_seen_message("whatsapp", ext_id):
                    continue

                body = (text or "").strip()
                if not body:
                    body = _media_placeholder(msg_type or 0)
                if not body:
                    state.mark_message_seen("whatsapp", ext_id)
                    continue

                ts = datetime.fromtimestamp(msg_date + CORE_DATA_EPOCH, tz=timezone.utc)
                sender = _sender_name(conn, msg_pk, from_jid, is_from_me or 0)

                kid_hint = cfg.whatsapp.kid_for(chat_name)
                results.append(Message(
                    source="whatsapp",
                    external_id=ext_id,
                    timestamp=ts,
                    sender=sender,
                    chat_name=chat_name,
                    body=body[:MAX_BODY_CHARS],
                    kid_hint=kid_hint,
                    metadata={"from_me": bool(is_from_me), "msg_type": msg_type},
                ))
                state.mark_message_seen("whatsapp", ext_id)
    finally:
        conn.close()
        try:
            shutil.rmtree(snap.parent)
        except Exception:
            pass

    log.info("WhatsApp: collected %d messages across %d chats", len(results), len(chat_rows))
    return results


# doctor and discover compare against this to offer the scheduled job's Python, which often has access.
NO_ACCESS = ("macOS doesn't let this process read WhatsApp data "
             "(grant it in System Settings → Privacy & Security → App Management)")


def check_access() -> str | None:
    """None if the DB can be copied, else a human-readable reason (NO_ACCESS when macOS refuses)."""
    if not DB_FILE.exists():
        return ("No WhatsApp Desktop data found (WhatsApp for Mac isn't installed, "
                "or has never been signed in on this Mac)")
    try:
        snap = _snapshot_db()
        shutil.rmtree(snap.parent, ignore_errors=True)
        return None
    except PermissionError:
        return NO_ACCESS
    except Exception as e:
        return f"Couldn't read it: {e}"


def list_groups(days: int = 180) -> list[dict]:
    """Group chats with activity in the last N days: exact name, last message date, archived flag.
    Names are returned verbatim (trailing spaces, curly quotes, emoji) because config matching is exact."""
    snap = _snapshot_db()
    try:
        conn = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400 - CORE_DATA_EPOCH
        rows = conn.execute("""
            SELECT ZPARTNERNAME, ZLASTMESSAGEDATE, COALESCE(ZARCHIVED, 0)
            FROM ZWACHATSESSION
            WHERE ZCONTACTJID LIKE '%@g.us' AND COALESCE(ZREMOVED, 0) = 0
              AND ZPARTNERNAME IS NOT NULL AND ZLASTMESSAGEDATE >= ?
            ORDER BY ZLASTMESSAGEDATE DESC
        """, (cutoff,)).fetchall()
        conn.close()
    finally:
        shutil.rmtree(snap.parent, ignore_errors=True)
    return [{"name": n,
             "last": datetime.fromtimestamp(t + CORE_DATA_EPOCH).strftime("%Y-%m-%d"),
             "archived": bool(a)} for n, t, a in rows]
