"""Connecting WhatsApp in setup: reading it through a `bg` job and listing its chats, each with a
hint on those that look like they're about a Kid. `setup whatsapp` and the setup page both read
through this.

macOS grants WhatsApp access per responsible process, so every read goes through a `bg` job, which
reads with exactly the scheduled job's permission. The process asking never reads WhatsApp itself,
so macOS never asks for Terminal, Claude Code or Codex to get access. The job reads WhatsApp only
once its Python has Full Disk Access, so an Allow on macOS's question about data from other apps,
which lasts one read on macOS 26, can't make a read pass (ADR 0012).
"""
from __future__ import annotations

import json
import math
import re
import sys
import time
from typing import Any

from . import whatsapp_python
from .config import Config, Kid
from .system import jobs

WHATSAPP_POLL_SECONDS = 5
# Each read gets at least this long, for the background job to start and copy a large database.
WHATSAPP_READ_SECONDS = 60
WHATSAPP_DAYS = 180  # the groups listed are those with messages in this many days


def wait_until_readable(config: str | None, days: int, timeout: float, *,
                        no_open: bool = False) -> dict[str, Any]:
    """Reads WhatsApp through `bg` jobs until one can, for up to `timeout` seconds: after the first
    read without Full Disk Access it shows the scheduled job's Python in Finder and opens Full Disk
    Access, unless `no_open`, and waits for the family to allow it. Returns the last read, as
    read_through_bg does, with that `python`; for `no-permission` with what didn't open as
    `failed`, the commands the family runs instead."""
    from . import ops

    python = ops.full_disk_access_python()
    deadline = time.time() + max(0, timeout)
    opened = no_open
    while True:
        read = read_through_bg(config, days, max(WHATSAPP_READ_SECONDS, deadline - time.time()))
        if read["result"] != "no-permission":
            return {**read, "python": python}
        if not opened:
            opened = True
            _, failed = ops.show_python_for_full_disk_access()
            if failed:
                return {**read, "python": python, "failed": failed}
        if time.time() + WHATSAPP_POLL_SECONDS > deadline:
            return {**read, "python": python}
        time.sleep(WHATSAPP_POLL_SECONDS)


def read_through_bg(config: str | None, days: int, timeout: float) -> dict[str, Any]:
    """Reads WhatsApp once through a `bg` job, with the scheduled job's permission. `result` is
    `readable`, with the chats, each with its Kid hint; `no-permission`, without Full Disk Access;
    `waiting`, when the read didn't finish in time; `not-installed`; or `unreadable` or
    `bg-failed`, with the error."""
    try:
        read = _read_in_bg_job(config, days, timeout)
    except RuntimeError as e:  # the system wouldn't start the job
        return {"result": "bg-failed", "error": str(e)[:200]}
    if read is None:
        return {"result": "waiting"}
    permission = read.get("permission")
    if permission == "readable":
        return {"result": "readable",
                "chats": _with_hints(read.get("chats") or [], _configured_kids(config))}
    if permission == "not-installed":
        return {"result": "not-installed"}
    if permission != "none":
        return {"result": "unreadable", "error": str(read.get("error"))[:200]}
    return {"result": "no-permission"}


def _read_in_bg_job(config: str | None, days: int, timeout: float) -> dict[str, Any] | None:
    """What the bg job read, or None if it didn't finish in time."""
    code, out = jobs.run_detached(["setup", "whatsapp", "--read", "--days", str(days)],
                                  math.ceil(timeout), config=config, echo=False)
    for line in reversed(out.splitlines()):
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                break
    if code == jobs.TIMED_OUT:
        return None
    return {"permission": "error", "error": f"exit code {code}: {out.strip()[-200:]}"}


def read_in_job(days: int) -> dict[str, Any]:
    """Inside the bg job: whether WhatsApp could be read as `permission`, and its chats."""
    from .collectors import whatsapp

    # Full Disk Access is checked before anything of WhatsApp's is touched, even to see whether
    # it's installed, so macOS never asks.
    try:
        if not whatsapp.has_full_disk_access():
            return {"permission": "none"}
    except OSError as e:
        return {"permission": "error", "error": f"Couldn't check for Full Disk Access: {e}"}
    if not whatsapp.DB_FILE.exists():
        return {"permission": "not-installed"}
    error = whatsapp.check_access()
    if error is None:
        try:
            chats = whatsapp.list_groups(days=days)
        except Exception as e:
            error = str(e)
        else:
            # This bg job reads with the evening job's Python: the one with Full Disk Access.
            whatsapp_python.record(sys.executable)
            return {"permission": "readable", "chats": chats}
    return {"permission": "error", "error": error}


def _configured_kids(config: str | None) -> list[Kid]:
    try:
        return Config.load(config).kids
    except Exception:  # no config yet: the chats are listed without hints
        return []


def _with_hints(chats: list[dict[str, Any]], kids: list[Kid]) -> list[dict[str, Any]]:
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
