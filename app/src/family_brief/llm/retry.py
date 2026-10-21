"""Trying a busy model again, shared by every adapter."""
from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

log = logging.getLogger(__name__)

# The pauses before each new try while the model is busy: at capacity, rate-limited, overloaded.
BUSY_PAUSES = (60, 180, 600)
# How long one model call may take, its tries and pauses together, before the night gives up on
# it and sends the Brief without a Digest. The budget is per call: the evening run makes one for
# the Brief and one per Recipient's translation, though a busy model rarely stays busy that long.
CALL_BUDGET = 30 * 60

# The errors waiting won't fix win over those it will: signed out, an expired token or key, or a
# plan's usage limit, which lasts hours, or API credit run out, which lasts until someone pays
# (OpenAI says so with a 429).
# A status number counts only as a status ("status: 429", "API Error: 529", "HTTP 503"), so
# that a token count or a time in the output isn't taken for one.
_STATUS = r"(?:status|error|http)\W{0,3}"
WONT_PASS = re.compile(r"not logged in|log ?in again|sign in again|/login|unauthori[sz]ed|forbidden|"
                       rf"{_STATUS}40[13]\b|authentication|invalid (?:api key|credentials)|expired|"
                       r"usage limit|limit reached|quota|credit.balance", re.I)
_BUSY = re.compile(r"at capacity|overloaded|rate.?limit|too many requests|"
                   rf"{_STATUS}(?:429|5(?:00|02|03|04|29))\b|"
                   r"service unavailable|bad gateway|temporarily unavailable", re.I)

# Seams for the tests, which record the pauses on a fake clock.
_sleep = time.sleep
_clock = time.monotonic


def busy_reason(proc: subprocess.CompletedProcess, prompt: str | None) -> str | None:
    """What says the model is busy in a failed call's output, or None when it isn't busy or
    waiting won't help. codex echoes the prompt on stderr, and the Household's messages in it are
    left out: they can say "at capacity" too."""
    if proc.returncode == 0:
        return None
    said = f"{proc.stdout}\n{proc.stderr}"
    if prompt:
        said = said.replace(prompt, "")
    if WONT_PASS.search(said):
        return None
    busy = _BUSY.search(said)
    return busy.group(0) if busy else None


def _minutes(seconds: float) -> str:
    return f"{seconds / 60:g} min"


@dataclass
class Ran:
    """A finished CLI call and how long it paused for a busy model before it."""
    proc: subprocess.CompletedProcess
    waited: float


def run_with_retry(cmd: list[str], timeout: int, what: str, budget: int = CALL_BUDGET,
                   **kwargs) -> Ran:
    """Run an LLM CLI, retrying as with_retry does. Both CLIs occasionally hang on start."""
    proc, waited = with_retry(
        lambda: subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout, **kwargs),
        lambda proc: busy_reason(proc, kwargs.get("input")), timeout, what, budget)
    return Ran(proc, waited)


_R = TypeVar("_R")


def with_retry(call: Callable[[], _R], busy_reason: Callable[[_R], str | None], timeout: int,
               what: str, budget: int) -> tuple[_R, float]:
    """Make one model `call`, retrying once on timeout, and after each of BUSY_PAUSES while
    `busy_reason` says the model is busy, as long as the next try could still end within `budget`
    seconds of the first. Errors waiting won't fix, such as being signed out, come back at once.
    Returns the last answer and the seconds paused for a busy model."""
    started = _clock()
    pauses = iter(BUSY_PAUSES)
    waited = 0.0
    timed_out = False
    attempt = 0
    while True:
        attempt += 1
        log.info("Calling %s (attempt %d)", what, attempt)
        try:
            answer = call()
        except (subprocess.TimeoutExpired, TimeoutError) as e:
            log.warning("%s timeout on attempt %d (%ds)", what, attempt, timeout)
            if timed_out:
                raise RuntimeError(f"{what} timed out twice after {timeout}s each") from e
            timed_out = True
            _sleep(5)
            continue
        busy = busy_reason(answer)
        if not busy:
            return answer, waited
        pause = next(pauses, None)
        if pause is None or _clock() - started + pause + timeout > budget:
            log.error("%s still busy after %d attempts (%s); giving up on it tonight", what, attempt, busy)
            return answer, waited
        log.warning("%s busy on attempt %d (%s); trying again in %s", what, attempt, busy, _minutes(pause))
        _sleep(pause)
        waited += pause


def save_diagnostics(name: str, proc: subprocess.CompletedProcess, cmd_desc: str) -> None:
    dbg = Path.home() / "ParentRecap" / "logs" / name
    dbg.parent.mkdir(parents=True, exist_ok=True)
    dbg.write_text(
        f"returncode: {proc.returncode}\n"
        f"stderr:\n{proc.stderr}\n\n"
        f"stdout:\n{proc.stdout}\n\n"
        f"cmd (truncated): {cmd_desc}\n"
        f"PATH: {os.environ.get('PATH', '')}\n"
        f"HOME: {os.environ.get('HOME', '')}\n"
        f"USER: {os.environ.get('USER', '')}\n"
    , encoding="utf-8")
    log.error("LLM CLI diagnostics saved to %s", dbg)
