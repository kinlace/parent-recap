"""The Run Report: how the last evening run ended, in `last-run.json` beside state.json (ADR 0048).

state.json's `last_run_at` is written only by a run that sent its Brief or had none to send, so it
can't tell a run that failed from one that never started. The Run Report is written by every run
that started, however it ended, and holds nothing of the Household's: only the fields FIELDS names,
each a time, a number or one of a fixed set of keys. That is Parent Recap's version, the system and
its version, when the run ended and how it was started, its exit code, duration and peak memory,
each Source's result, the AI's, the Brief's outcome, and for an exception that ended the run its
type and the line of Parent Recap's own code it was raised at, never its message. A failure with no
key of its own is an unknown one (UNKNOWN), never its text.

The hosted version's status (`signup status`) reads the time, exit code, duration, peak memory and
causes, as it did before the rest was added, and from the duration and peak memory the maintainers
check that a run fits its unit's limits (#583). `doctor` says how the last run ended (`how`)."""
from __future__ import annotations

import json
import logging
import platform
import re
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Callable, get_args

from . import wilma_egress
from .config import Config, LLMConfig
from .system import profile

log = logging.getLogger(__name__)

CAUSE = re.compile(r"[a-z][a-z0-9-]{0,39}")  # a cause's key, as wilma_egress.CAUSE

# A failure the program has no key for yet. A new kind gets its key here, and in the gateway's
# list, before it can be reported (ADR 0048).
UNKNOWN = "unknown"
# A Linked Device's Source that WhatsApp ended the link of, or that was never paired.
NEEDS_PAIRING = "needs-pairing"
# The Brief that said only that no Source could be read.
NOTHING_READ = "nothing-read"
# Every cause a report can name: for what a Source couldn't read, and for a Brief not sent.
CAUSES = frozenset({wilma_egress.CAUSE, NEEDS_PAIRING, NOTHING_READ, UNKNOWN})

# How the run was started: by the scheduler at the Brief's time, by the scheduler late to catch up
# an evening the computer missed (#894), or by a person.
STARTED = ("schedule", "catch-up", "by-hand")
SYSTEMS = ("macos", "windows", "linux")
AI_RESULTS = ("ok", "busy", "timed-out", "failed")
BACKENDS = get_args(LLMConfig.model_fields["backend"].annotation)  # the config's llm.backend

_VERSION = re.compile(r"[0-9]+(\.[0-9]+)*([.+-][0-9A-Za-z.]+)?|unknown")
_SYSTEM_VERSION = re.compile(r"[0-9][0-9A-Za-z.+_-]{0,39}|unknown")
_TYPE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,99}")  # an exception's class, with its module
_WHERE = re.compile(r"[a-z_][a-z0-9_]*(/[a-z_][a-z0-9_]*)*\.py:[0-9]{1,6}")  # within family_brief
_OWN_CODE = Path(__file__).resolve().parent


@dataclass(frozen=True)
class LastRun:
    at: datetime  # when it ended, in UTC
    exit: int
    # None in a file written before these were noted, and the peak where the platform gives
    # none (Windows).
    seconds: int | None = None
    peak_mb: int | None = None
    causes: tuple[str, ...] = ()  # why it couldn't read something, by key, such as wilma-egress
    # The Run Report's other fields (FIELDS), those it holds; none in a file written before them.
    report: dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return self.exit != 0


@dataclass
class Report:
    """What the evening run tells the Run Report as it goes, besides how it ended."""
    started: str = "by-hand"
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    ai: dict[str, str] | None = None
    brief: dict[str, str] | None = None
    exception: dict[str, str] | None = None

    def read(self, source: str, count: int) -> None:
        self.sources[source] = {"read": count}

    def failed(self, source: str, cause: str) -> None:
        self.sources[source] = {"failed": cause if cause in CAUSES else UNKNOWN}

    def asked_ai(self, backend: str, result: str) -> None:
        self.ai = {"backend": backend, "result": result}

    def brief_ended(self, outcome: str, cause: str | None = None) -> None:
        self.brief = {"outcome": outcome}
        if outcome == "failed":
            self.brief["cause"] = cause if cause in CAUSES else UNKNOWN

    def ended_on(self, e: BaseException) -> None:
        """The exception that ended the run: its type and the line of Parent Recap's own code
        it went through last, never its message."""
        kind = type(e)
        name = kind.__qualname__ if kind.__module__ == "builtins" else f"{kind.__module__}.{kind.__qualname__}"
        self.exception = {"type": name if _TYPE.fullmatch(name) else UNKNOWN}
        if (where := _own_line(e)) is not None:
            self.exception["at"] = where

    def fields(self) -> dict[str, Any]:
        out: dict[str, Any] = {"started": self.started, "sources": self.sources}
        for name in ("ai", "brief", "exception"):
            if (value := getattr(self, name)) is not None:
                out[name] = value
        return out


def _own_line(e: BaseException) -> str | None:
    """The last line of Parent Recap's own code `e` went through, as `run.py:123`."""
    for frame in reversed(traceback.extract_tb(e.__traceback__)):
        try:
            within = Path(frame.filename).resolve().relative_to(_OWN_CODE)
        except ValueError:
            continue
        where = f"{within.as_posix()}:{frame.lineno}"
        return where if _WHERE.fullmatch(where) else None
    return None


def path(cfg: Config) -> Path:
    return cfg.resolved_state_path().with_name("last-run.json")


def peak_mb(own: int, children: int, unit: int) -> int:
    """The larger of the run's and its child processes' (the wilma CLI, Node) maximum resident
    size, `ru_maxrss` in `unit` bytes as the system gives it, in MB. The children's is the
    largest one's, not their sum, and Python and Node run at once: the run's unit as a whole (its
    cgroup, which MemoryMax limits) can use more."""
    return round(max(own, children) * unit / 2**20)


def _peak() -> int | None:
    unit = profile.current().maxrss_unit
    if unit is None:
        return None
    import resource  # not on Windows, whose unit is None
    return peak_mb(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                   resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss, unit)


def _version() -> str:
    try:
        return metadata.version("family-brief")
    except metadata.PackageNotFoundError:
        return UNKNOWN


def _system_version(system: str) -> str:
    """The system's version number alone: macOS's, Windows' build, or Linux's kernel."""
    if system == "macos":
        return platform.mac_ver()[0] or UNKNOWN
    return (platform.version() if system == "windows" else platform.release()) or UNKNOWN


def _about() -> dict[str, str]:
    system = profile.current().system
    return {"version": _version(), "system": system, "system_version": _system_version(system)}


def record(cfg: Config, exit: int, seconds: float, causes: list[str] | tuple[str, ...] = (),
           report: Report | None = None) -> None:
    """Notes that a run ended now with `exit` after `seconds`, the peak memory it and its child
    processes used, the `causes` it named, and what `report` says. Only fields FIELDS lists, with
    values it allows, are written. Never fails the run it notes."""
    try:
        noted: dict[str, object] = {"at": datetime.now(timezone.utc).isoformat(), "exit": exit,
                                    "seconds": round(seconds)}
        peak = _peak()
        if peak is not None:
            noted["peak_mb"] = peak
        if causes:
            noted["causes"] = list(causes)
        try:  # what signup status reads is written even when the rest can't be
            noted = only_listed({**noted, **_about(), **(report.fields() if report else {})})
        except Exception as e:  # noqa: BLE001
            log.warning("The Run Report holds only how the run ended: %s", type(e).__name__)
            noted = only_listed(noted)
        target = path(cfg)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.tmp")
        tmp.write_text(json.dumps(noted, indent=2), encoding="utf-8")
        tmp.replace(target)
    except Exception as e:  # noqa: BLE001 - the run's result stands without its report
        log.warning("Couldn't write the Run Report: %s", type(e).__name__)


def read(cfg: Config) -> LastRun | None:
    try:
        raw = json.loads(path(cfg).read_text(encoding="utf-8"))
        wrong = set(problems(raw))
        report = {k: v for k, v in raw.items()
                  if k not in wrong and k not in ("at", "exit", "seconds", "peak_mb", "causes")}
        return LastRun(datetime.fromisoformat(raw["at"]), int(raw["exit"]),
                       number(raw.get("seconds")), number(raw.get("peak_mb")),
                       causes(raw.get("causes")), report)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def causes(value: object) -> tuple[str, ...]:
    """The cause keys in `value`, in order and once each; anything else in it is left out."""
    given = value if isinstance(value, list) else []
    return tuple(dict.fromkeys(c for c in given if isinstance(c, str) and CAUSE.fullmatch(c)))


def number(value: object) -> int | None:
    """`value` when it is a whole number, else None: what a reader keeps of a noted number."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


# ── What a Run Report may hold

def _whole(value: object) -> bool:
    return number(value) is not None


def _time(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 40:
        return False
    try:
        return datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False


def _matches(pattern: re.Pattern[str]) -> Callable[[object], bool]:
    return lambda value: isinstance(value, str) and pattern.fullmatch(value) is not None


def _one_of(*keys: str | frozenset[str]) -> Callable[[object], bool]:
    allowed = {k for given in keys for k in ([given] if isinstance(given, str) else given)}
    return lambda value: isinstance(value, str) and value in allowed


def _object(required: dict[str, Callable[[object], bool]],
            optional: dict[str, Callable[[object], bool]] | None = None) -> Callable[[object], bool]:
    """An object with each of `required`'s fields, perhaps `optional`'s, and nothing else."""
    optional = optional or {}

    def check(value: object) -> bool:
        if not isinstance(value, dict) or not set(required) <= set(value) <= set(required) | set(optional):
            return False
        return all({**required, **optional}[k](v) for k, v in value.items())
    return check


def _source_result(value: object) -> bool:
    return (_object({"read": _whole})(value) or _object({"failed": _one_of(CAUSES)})(value))


def _sources(value: object) -> bool:
    from .sources import ALL  # each Source's name, which the registry fixes
    names = {s.name for s in ALL}
    return isinstance(value, dict) and all(
        name in names and _source_result(result) for name, result in value.items())


def _causes(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(c, str) and c in CAUSES for c in value)


def _brief(value: object) -> bool:
    return (_object({"outcome": _one_of("sent", "nothing-to-tell")})(value)
            or _object({"outcome": _one_of("failed"), "cause": _one_of(CAUSES)})(value))


# Every field a Run Report can hold, and what its value may be. This is the whole list (ADR 0048):
# a field that isn't here, or a value it doesn't allow, is never written.
FIELDS: dict[str, Callable[[object], bool]] = {
    "version": _matches(_VERSION),
    "system": _one_of(*SYSTEMS),
    "system_version": _matches(_SYSTEM_VERSION),
    "at": _time,
    "started": _one_of(*STARTED),
    "exit": _whole,
    "seconds": _whole,
    "peak_mb": _whole,
    "causes": _causes,
    "sources": _sources,
    "ai": _object({"backend": _one_of(*BACKENDS), "result": _one_of(*AI_RESULTS)}),
    "brief": _brief,
    "exception": _object({"type": _matches(_TYPE)}, {"at": _matches(_WHERE)}),
}


def problems(report: object) -> list[str]:
    """The fields of `report` FIELDS doesn't list or whose value it doesn't allow, by name."""
    if not isinstance(report, dict):
        return ["<not an object>"]
    return [str(k) for k, v in report.items() if k not in FIELDS or not FIELDS[k](v)]


def only_listed(report: dict[str, Any]) -> dict[str, Any]:
    """`report` without the fields `problems` names."""
    wrong = set(problems(report))
    if wrong:
        log.warning("The Run Report leaves out fields it can't hold: %s",
                    ", ".join(sorted(k for k in wrong if k in FIELDS)) or "unlisted ones")
    return {k: v for k, v in report.items() if k not in wrong}


# ── What doctor says of it

_AI_SAID = {"busy": "was busy", "timed-out": "timed out", "failed": "failed"}

def how(last: LastRun) -> str:
    """How the last run ended, in doctor's words: its Brief's outcome or the exception that ended
    it, and the Sources that failed. A note from before the Run Report says its exit code."""
    r = last.report
    if "exception" in r:
        e = r["exception"]
        said = f"stopped on {e['type']}" + (f" at {e['at']}" if "at" in e else "")
    elif "brief" in r:
        b = r["brief"]
        said = {"sent": "its Brief was sent", "nothing-to-tell": "nothing to tell, so no Brief"}.get(
            b["outcome"], "no Source could be read" if b.get("cause") == NOTHING_READ
            else f"its Brief wasn't sent ({b.get('cause', UNKNOWN)})")
    else:
        said = f"exit code {last.exit}"
    failed = [f"{name} ({result['failed']})" for name, result in r.get("sources", {}).items()
              if "failed" in result]
    if failed:
        said += f"; couldn't read {', '.join(failed)}"
    ai = r.get("ai", {}).get("result")
    if ai in _AI_SAID:
        said += f"; the AI {_AI_SAID[ai]}"
    return said
