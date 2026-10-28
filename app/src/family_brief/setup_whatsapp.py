"""WhatsApp's groups as setup lists them for the check page: each with a hint on those that look
like they're about a Kid. Kept for the Linked Device's pairing step, which lists the groups the
same way (ADR 0027), and fills in the ids of groups picked by name only.
"""
from __future__ import annotations

import re
from typing import Any

from .config import Config, Kid, WhatsAppChat


def configured_kids(config: str | None) -> list[Kid]:
    try:
        return Config.load(config).kids
    except Exception:  # no config yet: the chats are listed without hints
        return []


def with_hints(chats: list[dict[str, Any]], kids: list[Kid]) -> list[dict[str, Any]]:
    """Adds a hint to each chat whose name mentions a Kid's name, class, school or club, naming
    the Kids by their name in the config, which is what whatsapp.chats[].kid takes."""
    out = []
    for chat in chats:
        matched_kids: list[str] = []
        matched: list[str] = []
        for kid in kids:
            hits = [t for t in kid.chat_hint_terms() if _mentions(chat["name"], t)]
            if hits:
                matched_kids.append(kid.name)
                matched += [t for t in hits if t not in matched]
        out.append({**chat, "hint": {"kids": matched_kids, "matched": matched}} if matched_kids
                   else chat)
    return out


def with_ids(chats: list[WhatsAppChat], found: list[dict[str, Any]]) -> list[WhatsAppChat]:
    """The picked `chats`, each one picked by name only given the id of the one group `found`
    (each with its name and id) of that name, as for groups picked before the Linked Device. A
    group not found, or whose name more than one group has, stays as it was."""
    ids: dict[str, list[str]] = {}
    for group in found:
        ids.setdefault(group["name"].strip().casefold(), []).append(group["id"])
    out = []
    for chat in chats:
        matched = ids.get(chat.name.strip().casefold(), [])
        out.append(chat.model_copy(update={"id": matched[0]})
                   if chat.id is None and len(matched) == 1 else chat)
    return out


def _mentions(text: str, term: str) -> bool:
    """Whether `term` is in `text` as a word of its own: Leo isn't in Leonardo, nor 3B in 13B.
    Chinese and Japanese aren't written with spaces, so next to their characters any term counts."""
    text, term = text.casefold(), term.strip().casefold()
    if len(term) < 2:
        return False

    def apart(c: str) -> bool:
        return not c.isalnum() or ord(c) >= 0x2E80
    for m in re.finditer(re.escape(term), text):
        before = text[m.start() - 1] if m.start() else " "
        after = text[m.end()] if m.end() < len(text) else " "
        if (apart(before) or apart(term[0])) and (apart(after) or apart(term[-1])):
            return True
    return False
