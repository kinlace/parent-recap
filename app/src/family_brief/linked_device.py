"""The Linked Device's Node program (ADR 0025), `baileys/linked_device.mjs`, which the pinned Node
runs on the Baileys tree `baileys_install` puts in Parent Recap's folder. Its modes, what it
prints and what it writes are described at the top of the program.

What it keeps lives in one folder of Parent Recap's own, next to the wilma CLI's and never in the
archive, which the family may keep or move: the auth state, the message store, the link's status
and the picked-groups file Python writes for it. The folder is 0700 and its files 0600, and the
evening run tightens them again (private_files.tighten).
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import baileys_install, own_node, private_files
from .system import jobs

if TYPE_CHECKING:
    from .config import Config

PROGRAM = Path(__file__).parent / "baileys" / "linked_device.mjs"
LOG = "linked-device.log"  # the program's log, in the log folder, every mode's

# How the program exits, besides 0 once pair or groups has printed the groups.
FAILED = 1
USAGE = 2
TIMED_OUT = 3  # pair: the phone didn't pair in time; groups: WhatsApp didn't connect in time
NEEDS_PAIRING = 4  # pair or groups: no auth state, or WhatsApp ended the link
STOPPED = 5  # follow, stopped by the OS
# follow: no auth state, or WhatsApp ended the link, which it has marked in its status file. The
# OS job doesn't start it again after this exit alone.
LINK_ENDED = jobs.LINK_ENDED

# The store keeps a record this long past the longest lookback the evening run can read with.
PRUNE_MARGIN = timedelta(days=1)


def folder() -> Path:
    """The Linked Device's folder, in Parent Recap's own next to the wilma CLI's."""
    return own_node.home() / "linked-device"


def auth_folder() -> Path:
    return folder() / "auth"


def store() -> Path:
    """The message store follow appends to, one line of JSON a message."""
    return folder() / "messages.jsonl"


def status_file() -> Path:
    return folder() / "status.json"


def picked_file() -> Path:
    """The picked groups' ids, which follow keeps the messages of."""
    return folder() / "picked-groups.json"


def log_dir(cfg: Config) -> Path:
    """The log folder, the evening jobs' and the always-running job's."""
    return cfg.archive.resolved_dir() / "logs"


def command(mode: str, cfg: Config, timeout: int | None = None) -> list[str] | None:
    """The command that runs the program in `mode` on the pinned Node and the installed Baileys
    tree, `timeout` passed on for pair and groups, or None without either."""
    node = own_node.node()
    if node is None or baileys_install.installed_version() is None:
        return None
    log_dir(cfg).mkdir(parents=True, exist_ok=True)
    out = [str(node), str(PROGRAM), mode, "--modules", str(baileys_install.folder()),
           "--auth", str(auth_folder()), "--log", str(log_dir(cfg) / LOG)]
    if mode == "follow":
        out += ["--picked", str(picked_file()), "--store", str(store()), "--status", str(status_file())]
    elif timeout is not None:
        out += ["--timeout", str(timeout)]
    return out


def install_follow(cfg: Config) -> bool:
    """Installs the always-running job that runs follow (ADR 0026), which starts it at once and
    at every sign-in, with the picked groups' file written for it. False without the pinned Node
    or the Baileys tree."""
    follow = command("follow", cfg)
    if follow is None:
        return False
    keep_picked(cfg)
    jobs.install_always(follow, log_dir(cfg))
    return True


@dataclass(frozen=True)
class Link:
    """The link as follow's status file has it, times in UTC. With no status file it isn't paired."""
    paired: bool = False
    connected_since: datetime | None = None
    last_connected: datetime | None = None
    ended_at: datetime | None = None


def link() -> Link:
    """The link's status. A file that can't be read counts as no pairing."""
    try:
        data = json.loads(status_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Link()
    if not isinstance(data, dict):
        return Link()
    return Link(paired=data.get("paired") is True, connected_since=_time(data.get("connected_since")),
                last_connected=_time(data.get("last_connected")), ended_at=_time(data.get("link_ended_at")))


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def keep_picked(cfg: Config) -> None:
    """Writes the picked groups' ids to the file follow reads, unless it already lists them, so
    follow reads it again only when the pick changed."""
    text = json.dumps({"groups": cfg.whatsapp.picked_ids()}) + "\n"
    path = picked_file()
    try:
        if path.read_text(encoding="utf-8") == text:
            return
    except OSError:
        pass
    _private_folder(folder())
    # A whole file in place of the old one: follow never reads one half written.
    partial = path.with_name(f"{path.name}.{os.getpid()}.partial")
    private_files.write(partial, text)
    os.replace(partial, path)


def _private_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def records() -> list[dict[str, Any]]:
    """The store's records. A line that isn't a record, such as one follow is still writing, is
    left out."""
    try:
        lines = store().read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    return [r for r in (_record(line) for line in lines) if r is not None]


def _record(line: str) -> dict[str, Any] | None:
    try:
        record = json.loads(line)
    except ValueError:
        return None
    if not isinstance(record, dict) or not isinstance(record.get("timestamp"), (int, float)) \
            or record.get("id") is None:
        return None
    return record


def prune(longest_lookback_hours: int) -> int:
    """Removes the store's records older than `longest_lookback_hours` and PRUNE_MARGIN, and lines
    that aren't records. Returns how many lines went.

    follow appends to the store while this runs. What it appended after the store was read is
    copied over just before the pruned store replaces it, so only a line appended in that last
    instant could be lost. A line it is still writing, with no newline yet, is kept as it is."""
    path = store()
    try:
        with path.open("rb") as f:
            read = f.read()
    except FileNotFoundError:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=longest_lookback_hours) - PRUNE_MARGIN).timestamp()
    complete, _, unfinished = read.rpartition(b"\n")
    lines = complete.split(b"\n") if complete else []
    kept = [line for line in lines if (r := _record(line.decode("utf-8", "replace"))) is not None
            and r["timestamp"] >= cutoff]
    if len(kept) == len(lines):
        return 0
    partial = path.with_name(f"{path.name}.{os.getpid()}.partial")
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with open(fd, "wb") as out:
        out.write(b"".join(line + b"\n" for line in kept) + unfinished)
        with path.open("rb") as f:
            f.seek(len(read))
            out.write(f.read())
    try:
        os.replace(partial, path)
    except OSError:  # on Windows, while follow has the store open
        partial.unlink(missing_ok=True)
        raise
    return len(lines) - len(kept)


def picked_records(cfg: Config, found: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The records of the groups still picked: a group unpicked since keeps its records until
    they're pruned, but they aren't read."""
    picked = set(cfg.whatsapp.picked_ids())
    return [r for r in found if r.get("group_id") in picked]
