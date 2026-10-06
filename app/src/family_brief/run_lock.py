"""One run at a time: the Brief and Weekend Picks both read state.json and write it back, so two
at once could send the Brief twice and add the same Calendar events twice.

The lock is an flock on a file next to state.json. The system drops it when the process ends,
however it ends, so a run that was killed never leaves it stuck."""
from __future__ import annotations

import fcntl
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Callable, Iterator

from .config import Config

BUSY = 75  # EX_TEMPFAIL: the exit code of a run that found another one going

log = logging.getLogger(__name__)


class Busy(Exception):
    pass


def lock_path(cfg: Config) -> Path:
    return cfg.resolved_state_path().with_name("run.lock")


def is_busy(cfg: Config) -> bool:
    """Whether a run holds the lock right now. Never creates the lock file."""
    path = lock_path(cfg)
    if not path.exists():
        return False
    with path.open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
    return False  # closing the file let go of the lock


def _take(cfg: Config) -> IO[str] | None:
    """The open lock file, now locked, or None if another run holds it."""
    path = lock_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open("a")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        return None
    return f


@contextmanager
def exclusive(cfg: Config) -> Iterator[None]:
    """Hold the run lock for the block, or raise Busy if another run holds it."""
    f = _take(cfg)
    if f is None:
        raise Busy
    with f:
        yield


def run_alone(cfg: Config, kind: str, run: Callable[[], int]) -> int:
    """`run()` under the run lock; if another run is going, say so and return BUSY instead.
    `kind` names the run in that message ("Brief", "Weekend Picks")."""
    f = _take(cfg)
    if f is None:
        log.error("Another Parent Recap run is still going, so this %s run didn't start. Wait for "
                  "that one to finish (it can take several minutes) instead of starting it again.", kind)
        return BUSY
    with f:
        return run()
