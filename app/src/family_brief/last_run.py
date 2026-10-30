"""How the last evening run ended: when, and its exit code, in `last-run.json` beside state.json.

state.json's `last_run_at` is written only by a run that got to its end, so it can't tell a run
that failed from one that never started. This is written by every run that started, however it
ended, and holds nothing of the Household's: a time and a number. The hosted version's status
(`signup status`) reads it."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import Config


@dataclass(frozen=True)
class LastRun:
    at: datetime  # when it ended, in UTC
    exit: int

    @property
    def failed(self) -> bool:
        return self.exit != 0


def path(cfg: Config) -> Path:
    return cfg.resolved_state_path().with_name("last-run.json")


def record(cfg: Config, exit: int) -> None:
    """Notes that a run ended now with `exit`. Never fails the run it notes."""
    try:
        target = path(cfg)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.tmp")
        tmp.write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "exit": exit}),
                       encoding="utf-8")
        tmp.replace(target)
    except OSError:
        pass


def read(cfg: Config) -> LastRun | None:
    try:
        raw = json.loads(path(cfg).read_text(encoding="utf-8"))
        return LastRun(datetime.fromisoformat(raw["at"]), int(raw["exit"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None
