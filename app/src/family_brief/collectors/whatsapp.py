"""WhatsApp, read from the Linked Device's message store (ADR 0027): the records follow kept of the
picked groups, one line of JSON each as `baileys/linked_device.mjs` describes them, whose text is
already a placeholder such as "[image]" for media without one. Each evening run first writes the
picked groups' ids for follow, and prunes the store once it has been read."""
from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from .. import linked_device
from ..config import Config
from ..sources import NEEDS_PAIRING, Unread
from ..state import State
from .base import Collected, Message

log = logging.getLogger(__name__)

MAX_BODY_CHARS = 4000


def connected(cfg: Config) -> bool:
    """Whether the Linked Device is paired, as its status file says, with groups picked."""
    return cfg.whatsapp.enabled and bool(cfg.whatsapp.picked_ids()) and linked_device.link().paired


def unread(cfg: Config, state: State) -> Unread | None:
    """Picked groups that aren't read: the link ended, since when WhatsApp ended it, or there's no
    pairing, as for groups picked when WhatsApp for Mac was read, since WhatsApp was last read."""
    if not cfg.whatsapp.enabled or not cfg.whatsapp.chats or connected(cfg):
        return None
    return Unread(NEEDS_PAIRING, linked_device.link().ended_at or state.caught_up_at("whatsapp"))


def collect(cfg: Config, state: State, kid_terms: list[str]) -> Collected:
    from ..run import MAX_CATCH_UP_HOURS  # here: the run module imports most of the program

    linked_device.keep_picked(cfg)
    found = linked_device.picked_records(cfg, linked_device.records())
    # Never before a record the run could still read: a lookback widened after missed nights
    # reaches back at most MAX_CATCH_UP_HOURS.
    try:
        linked_device.prune(max(MAX_CATCH_UP_HOURS, cfg.whatsapp.lookback_hours))
    except OSError as e:  # tried again tomorrow
        log.warning("the Linked Device's message store couldn't be pruned: %s", e)
    return Collected(messages=to_messages(cfg, state, found))


def to_messages(cfg: Config, state: State, records: Iterable[Mapping[str, Any]]) -> list[Message]:
    """The records within the lookback as Messages, in time order, each given once: a record seen
    before, as after a reconnect hands the same message over again, is skipped, and each one given
    is marked seen. A record with no text is marked seen and not given. A Message is in its group
    as WhatsApp names it now, and its Kid is that of the group picked with its id."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=cfg.whatsapp.lookback_hours)).timestamp()
    picked = {c.id: c.name for c in cfg.whatsapp.chats if c.id}
    results: list[Message] = []
    for record in sorted(records, key=lambda r: r["timestamp"]):
        ext_id = str(record["id"])
        if record["timestamp"] < cutoff or state.has_seen_message("whatsapp", ext_id):
            continue
        body = (record.get("text") or "").strip()
        state.mark_message_seen("whatsapp", ext_id)
        if not body:
            continue
        group_id = record.get("group_id")
        chat_name = record.get("group_subject") or picked.get(group_id) or "(group)"
        results.append(Message(
            source="whatsapp",
            external_id=ext_id,
            timestamp=datetime.fromtimestamp(record["timestamp"], tz=timezone.utc),
            sender="me" if record.get("from_me") else record.get("sender") or "(group member)",
            chat_name=chat_name,
            body=body[:MAX_BODY_CHARS],
            kid_hint=cfg.whatsapp.kid_for(chat_name, group_id),
            metadata={"from_me": bool(record.get("from_me"))},
        ))
    return results
