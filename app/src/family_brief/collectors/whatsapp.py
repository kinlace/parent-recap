"""WhatsApp, which nothing reads until the Linked Device's collector lands (ADR 0027): `collect`
returns no Message. `to_messages` is how that collector turns the records the Linked Device keeps
into Messages, one line of JSON each as `baileys/linked_device.mjs` describes them, whose text is
already a placeholder such as "[image]" for media without one."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import Config
from ..state import State
from .base import Collected, Message

MAX_BODY_CHARS = 4000


def collect(cfg: Config, state: State, kid_terms: list[str]) -> Collected:
    return Collected(messages=[])


def to_messages(cfg: Config, state: State, records: Iterable[Mapping[str, Any]]) -> list[Message]:
    """The records within the lookback as Messages, in time order, each given once: a record seen
    before, as after a reconnect hands the same message over again, is skipped, and each one given
    is marked seen. A record with no text is marked seen and not given."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=cfg.whatsapp.lookback_hours)).timestamp()
    results: list[Message] = []
    for record in sorted(records, key=lambda r: r["timestamp"]):
        ext_id = str(record["id"])
        if record["timestamp"] < cutoff or state.has_seen_message("whatsapp", ext_id):
            continue
        body = (record.get("text") or "").strip()
        state.mark_message_seen("whatsapp", ext_id)
        if not body:
            continue
        chat_name = record["group_subject"]
        results.append(Message(
            source="whatsapp",
            external_id=ext_id,
            timestamp=datetime.fromtimestamp(record["timestamp"], tz=timezone.utc),
            sender="me" if record.get("from_me") else record.get("sender") or "(group member)",
            chat_name=chat_name,
            body=body[:MAX_BODY_CHARS],
            kid_hint=cfg.whatsapp.kid_for(chat_name),
            metadata={"from_me": bool(record.get("from_me"))},
        ))
    return results
