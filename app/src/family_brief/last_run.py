"""How the last evening run ended: when, its exit code, how long it took and its peak memory, in
`last-run.json` beside state.json.

state.json's `last_run_at` is written only by a run that got to its end, so it can't tell a run
that failed from one that never started. This is written by every run that started, however it
ended, and holds nothing of the Household's: a time and three numbers. The hosted version's status
(`signup status`) reads it, and from the duration and peak memory the maintainers check that a run
fits its unit's limits (#583)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from .system import profile


@dataclass(frozen=True)
class LastRun:
    at: datetime  # when it ended, in UTC
    exit: int
    # None in a file written before these were noted, and the peak where the platform gives
    # none (Windows).
    seconds: int | None = None
    peak_mb: int | None = None

    @property
    def failed(self) -> bool:
        return self.exit != 0


def path(cfg: Config) -> Path:
    return cfg.resolved_state_path().with_name("last-run.json")


def peak_mb(own: int, children: int, unit: int) -> int:
    """The larger of the run's and its child processes' (the wilma CLI, Node) maximum resident
    size, `ru_maxrss` in `unit` bytes as the system gives it, in MB."""
    return round(max(own, children) * unit / 2**20)


def _peak() -> int | None:
    unit = profile.current().maxrss_bytes
    if unit is None:
        return None
    import resource  # not on Windows, whose unit is None
    return peak_mb(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                   resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss, unit)


def record(cfg: Config, exit: int, seconds: float) -> None:
    """Notes that a run ended now with `exit` after `seconds`, and the peak memory it and its
    child processes used. Never fails the run it notes."""
    try:
        noted: dict[str, object] = {"at": datetime.now(timezone.utc).isoformat(), "exit": exit,
                                    "seconds": round(seconds)}
        peak = _peak()
        if peak is not None:
            noted["peak_mb"] = peak
        target = path(cfg)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.tmp")
        tmp.write_text(json.dumps(noted), encoding="utf-8")
        tmp.replace(target)
    except OSError:
        pass


def read(cfg: Config) -> LastRun | None:
    try:
        raw = json.loads(path(cfg).read_text(encoding="utf-8"))
        return LastRun(datetime.fromisoformat(raw["at"]), int(raw["exit"]),
                       number(raw.get("seconds")), number(raw.get("peak_mb")))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def number(value: object) -> int | None:
    """`value` when it is a whole number, else None: what a reader keeps of a noted number."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None
