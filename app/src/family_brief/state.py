from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


class State:
    """Persistent state across runs: last-run and caught-up timestamps, seen message IDs,
    created event hashes."""

    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, Any] = {
            "last_run_at": None,
            "caught_up_at": None,
            "seen_message_ids": {},
            "created_event_hashes": {},
        }
        if path.exists():
            try:
                self._data.update(json.loads(path.read_text()))
            except json.JSONDecodeError:
                pass
        self._loaded_seen = copy.deepcopy(self._data["seen_message_ids"])

    @property
    def last_run_at(self) -> datetime | None:
        v = self._data.get("last_run_at")
        return datetime.fromisoformat(v) if v else None

    def mark_run_now(self) -> None:
        self._data["last_run_at"] = datetime.now(timezone.utc).isoformat()

    def caught_up_at(self, source: str) -> datetime | None:
        """When a run last left nothing of `source` undelivered: its Brief went out, or there was
        nothing new, and that Source was read without errors."""
        v = self._caught_up_iso(source)
        return datetime.fromisoformat(v) if v else None

    def _caught_up_iso(self, source: str) -> str | None:
        # A Source without its own time was caught up with the rest (and in state from before 0.4.0).
        return self._data.get("source_caught_up_at", {}).get(source, self._data.get("caught_up_at"))

    def mark_caught_up_now(self, read: Iterable[str] = (), behind: Iterable[str] = ()) -> None:
        """Caught up now, except for the `behind` Sources, which failed or were partly read: they
        keep the time they were last caught up, so the next run's lookback reaches back to it. A
        Source that fell behind earlier stays behind until a run `read` it again."""
        read = set(read)
        pinned = {s: v for s, v in self._data.get("source_caught_up_at", {}).items() if s not in read}
        pinned.update({s: self._caught_up_iso(s) for s in behind})
        self._data["source_caught_up_at"] = pinned
        self._data["caught_up_at"] = datetime.now(timezone.utc).isoformat()

    def delivered_at(self, address: str) -> datetime | None:
        """When a Brief last went out to `address`."""
        v = self._data.get("brief_delivered_at", {}).get(address.lower())
        return datetime.fromisoformat(v) if v else None

    def mark_delivered_now(self, addresses: Iterable[str]) -> None:
        delivered = self._data.setdefault("brief_delivered_at", {})
        for address in addresses:
            delivered[address.lower()] = datetime.now(timezone.utc).isoformat()

    def forget_new_seen_messages(self, source: str | None = None) -> None:
        """Undo the seen marks made since loading, for one Source or all of them, so the next run
        collects those Messages again."""
        if source is None:
            self._data["seen_message_ids"] = copy.deepcopy(self._loaded_seen)
        else:
            self._data["seen_message_ids"][source] = copy.deepcopy(self._loaded_seen.get(source, {}))

    def has_seen_message(self, source: str, msg_id: str) -> bool:
        return msg_id in self._data["seen_message_ids"].get(source, {})

    def mark_message_seen(self, source: str, msg_id: str) -> None:
        bucket = self._data["seen_message_ids"].setdefault(source, {})
        bucket[msg_id] = datetime.now(timezone.utc).isoformat()

    def has_created_event(self, event_hash: str) -> bool:
        return event_hash in self._data["created_event_hashes"]

    def mark_event_created(self, event_hash: str, google_event_id: str) -> None:
        self._data["created_event_hashes"][event_hash] = {
            "google_event_id": google_event_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def _prune(self, keep_days: int = 45) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
        for source, bucket in list(self._data["seen_message_ids"].items()):
            self._data["seen_message_ids"][source] = {
                mid: ts for mid, ts in bucket.items()
                if datetime.fromisoformat(ts) > cutoff
            }
        self._data["created_event_hashes"] = {
            h: v for h, v in self._data["created_event_hashes"].items()
            if datetime.fromisoformat(v["created_at"]) > cutoff
        }

    def save(self) -> None:
        self._prune()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False))
