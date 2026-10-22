from __future__ import annotations

import logging

from ..system import imessage_macos

log = logging.getLogger(__name__)

MAX_CHARS = 3500  # practical split size for iMessage


def _chunk(text: str, limit: int = MAX_CHARS) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    return chunks


def send(recipients: list[str], body: str) -> None:
    """Raises unless at least one recipient got the whole body."""
    if not recipients:
        raise RuntimeError("no iMessage recipients configured")
    failed: set[str] = set()
    for chunk in _chunk(body):
        for phone in recipients:
            error = imessage_macos.send(phone, chunk)
            if error is not None:
                log.error("iMessage to %s failed: %s", phone, error)
                failed.add(phone)
            else:
                log.info("iMessage sent to %s (%d chars)", phone, len(chunk))
    if failed == set(recipients):
        raise RuntimeError("iMessage failed for every recipient")
