"""Local Files (ADR 0044): messages from plain files the family puts in a folder it picks, for news
from any place Parent Recap doesn't read itself. Only the files right in the folder are read, and
only these two kinds:
  - `.json`: a list of messages, each {"time": ISO 8601, "chat": ..., "sender": ..., "text": ...},
    `chat` and `sender` optional. A time without an offset is in the Household's timezone. A
    message without a chat is in the chat the file's name gives
  - `.txt` or `.md`: the whole file is one message, in the chat its name gives, at the time it was
    last changed
Anything else in the folder, and a hidden file, is left alone. The folder is only read: nothing in
it is ever moved, changed or deleted.

A message is read once, like a WhatsApp group's: within the Source's lookback, which the evening
run widens to reach back to the last run that read it (run.catch_up_lookback), and not seen
before. A file that can't be read, or a message in one, is skipped with why, which `doctor`
names; the rest of the folder still goes into the Brief."""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..config import Config
from ..state import State
from .base import Collected, Message

log = logging.getLogger(__name__)

SOURCE = "local_files"
MAX_BODY_CHARS = 4000
MAX_FILE_BYTES = 5 * 1024 * 1024  # a bigger file is no message list a family made by hand
JSON = (".json",)
TEXT = (".txt", ".md")


class FolderMissing(Exception):
    """The folder the config names isn't there, or isn't a folder."""


@dataclass(frozen=True)
class Problem:
    """A file in the folder that couldn't be read, whole or in part, by its name, and why."""
    file: str
    why: str


@dataclass
class Folder:
    """What the folder's files hold: every message read, in time order, and each file that
    couldn't be read, whole or in part."""
    messages: list[Message] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)


def connected(cfg: Config) -> bool:
    """Whether the Household has it turned on. A Hosted Household never reads it."""
    return cfg.local_files.enabled and not cfg.hosted


def shown(path: Path) -> str:
    """`path` as the family knows it, with ~ for their home folder."""
    try:
        return f"~/{path.relative_to(Path.home()).as_posix()}"
    except ValueError:
        return str(path)


def message_id(chat: str, sender: str | None, text: str, time: datetime | None = None) -> str:
    """The id a message is seen by: the same message in another file, or the file written again,
    is the same message. A text file's has no time, so saving it again unchanged gives nothing new."""
    key = json.dumps([chat, sender, text, time.astimezone(timezone.utc).isoformat() if time else None],
                     ensure_ascii=False)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def read(cfg: Config) -> Folder:
    """Every message in the folder's files, and each file that couldn't be read. Raises
    FolderMissing when the folder isn't there."""
    folder = cfg.local_files.resolved_folder()
    if not folder.is_dir():
        raise FolderMissing(f"the folder {shown(folder)} "
                            f"{'is a file, not a folder' if folder.exists() else 'does not exist'}")
    out = Folder()
    for path in sorted(folder.iterdir()):
        suffix = path.suffix.lower()
        if path.name.startswith(".") or suffix not in (*JSON, *TEXT) or not path.is_file():
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                out.problems.append(Problem(path.name, f"it is larger than {MAX_FILE_BYTES // 2**20} MB"))
                continue
            raw = path.read_bytes()
            changed = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError as e:
            out.problems.append(Problem(path.name, f"it couldn't be opened: {e.strerror or e}"))
            continue
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            out.problems.append(Problem(path.name, "it isn't UTF-8 text"))
            continue
        if suffix in TEXT:
            if body := text.strip():
                out.messages.append(_message(path.stem, None, body, changed))
        else:
            _read_json(cfg, path, text, out)
    out.messages.sort(key=lambda m: m.timestamp)
    return out


def _read_json(cfg: Config, path: Path, text: str, out: Folder) -> None:
    """The messages of a `.json` file, into `out`, with a Problem for the file if any couldn't be
    read: the others still are."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        out.problems.append(Problem(path.name, f"it isn't valid JSON (line {e.lineno}: {e.msg})"))
        return
    if not isinstance(data, list):
        out.problems.append(Problem(path.name, "it isn't a list of messages"))
        return
    wrong = []
    for n, item in enumerate(data, 1):
        why = _wrong(item)
        if why:
            wrong.append(f"message {n} {why}")
            continue
        time = datetime.fromisoformat(item["time"])
        if time.tzinfo is None:
            time = time.replace(tzinfo=ZoneInfo(cfg.timezone))
        chat = (item.get("chat") or "").strip() or path.stem
        sender = (item.get("sender") or "").strip() or None
        out.messages.append(_message(chat, sender, item["text"].strip(), time.astimezone(timezone.utc),
                                     dated=True))
    if wrong:
        out.problems.append(Problem(path.name, f"{len(wrong)} of its {len(data)} messages couldn't be "
                                    f"read: {wrong[0]}"))


def _wrong(item: Any) -> str | None:
    """What's wrong with one message of a `.json` file, or None when it can be read."""
    if not isinstance(item, dict):
        return "isn't an object"
    time = item.get("time")
    if not isinstance(time, str):
        return "has no time"
    try:
        datetime.fromisoformat(time)
    except ValueError:
        return "has a time that isn't ISO 8601"
    if not isinstance(item.get("text"), str) or not item["text"].strip():
        return "has no text"
    for key in ("chat", "sender"):
        if item.get(key) is not None and not isinstance(item[key], str):
            return f"has a {key} that isn't text"
    return None


def _message(chat: str, sender: str | None, text: str, time: datetime, dated: bool = False) -> Message:
    return Message(source=SOURCE, external_id=message_id(chat, sender, text, time if dated else None),
                   timestamp=time, sender=sender, chat_name=chat, body=text[:MAX_BODY_CHARS])


def waiting(cfg: Config, state: State, messages: list[Message]) -> list[Message]:
    """The messages the next Brief gets: within the lookback and not seen before, each once."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=cfg.local_files.lookback_hours)
    new = [m for m in messages if m.timestamp >= cutoff and not state.has_seen_message(SOURCE, m.external_id)]
    return list({m.external_id: m for m in new}.values())


def collect(cfg: Config, state: State, kid_terms: list[str]) -> Collected:
    found = read(cfg)
    for problem in found.problems:  # named by doctor; the rest of the folder is read
        log.warning("Local Files skipped %s: %s", problem.file, problem.why)
    messages = waiting(cfg, state, found.messages)
    for m in messages:
        state.mark_message_seen(SOURCE, m.external_id)
    return Collected(messages=messages)
