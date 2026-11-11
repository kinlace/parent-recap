"""Trying a busy model again, shared by every adapter."""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from .. import slept

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

# How often a running call looks whether the computer slept, in seconds it is awake.
SLEEP_CHECK = 5

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


def _run(cmd: list[str], timeout: int, running: list[subprocess.Popen], *, input: str | None = None,
         **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run(cmd, input=input, timeout=timeout) with its text output captured, and the
    process added to `running`, so that a call the computer slept through can be stopped."""
    with subprocess.Popen(cmd, stdin=subprocess.PIPE if input is not None else None,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                          **kwargs) as process:
        running.append(process)
        try:
            out, err = process.communicate(input, timeout=timeout)
        except BaseException:
            process.kill()
            process.wait()
            raise
        return subprocess.CompletedProcess(process.args, process.poll(), out, err)


def run_with_retry(cmd: list[str], timeout: int, what: str, budget: int = CALL_BUDGET,
                   **kwargs) -> Ran:
    """Run an LLM CLI, retrying as with_retry does. Both CLIs occasionally hang on start. A try
    the computer slept through is stopped before it is made again."""
    running: list[subprocess.Popen] = []

    def stop() -> None:
        for process in running:
            if process.poll() is None:
                process.kill()

    proc, waited = with_retry(
        lambda: _run(cmd, timeout, running, **kwargs),
        lambda proc: busy_reason(proc, kwargs.get("input")), timeout, what, budget, stop)
    return Ran(proc, waited)


_R = TypeVar("_R")


class _Slept(Exception):
    """The computer went to sleep while a call ran."""


def _awake_call(call: Callable[[], _R], stop: Callable[[], None]) -> _R:
    """`call()`, or _Slept once the computer is seen to have slept during it (#887): a sleep
    stalls the call, whose timeout counts only the time awake, and often drops its connection.
    The call runs beside the watch and is stopped with `stop` when the computer slept; one that
    can't be stopped, the gateway's request, ends on its own timeout and its answer is dropped. A
    call that answered across a sleep keeps its answer; one that failed across one slept."""
    watch = slept.Watch()
    done = threading.Event()
    result: list[tuple[bool, object]] = []

    def work() -> None:
        try:
            result.append((True, call()))
        except BaseException as e:  # raised again below, in the caller's thread
            result.append((False, e))
        finally:
            done.set()

    threading.Thread(target=work, name="model-call", daemon=True).start()
    try:
        while not done.wait(SLEEP_CHECK):
            if watch.asleep():
                raise _Slept
    except BaseException:  # slept, or the run was stopped (Ctrl-C): no program is left running
        stop()
        raise
    answered, value = result[0]
    if answered:
        return value  # type: ignore[return-value]
    assert isinstance(value, BaseException)
    if watch.asleep():
        raise _Slept from value
    raise value


def with_retry(call: Callable[[], _R], busy_reason: Callable[[_R], str | None], timeout: int,
               what: str, budget: int, stop: Callable[[], None] = lambda: None) -> tuple[_R, float]:
    """Make one model `call`, retrying once on timeout, and after each of BUSY_PAUSES while
    `busy_reason` says the model is busy, as long as the next try could still end within `budget`
    seconds of the first. Errors waiting won't fix, such as being signed out, come back at once.
    A try the computer slept through is stopped with `stop` and made again at once: it counts
    neither as a timeout nor against the budget. Returns the last answer and the seconds paused
    for a busy model."""
    started = _clock()
    pauses = iter(BUSY_PAUSES)
    waited = 0.0
    timed_out = False
    attempt = 0
    while True:
        attempt += 1
        log.info("Calling %s (attempt %d)", what, attempt)
        tried = _clock()
        try:
            answer = _awake_call(call, stop)
        except _Slept:
            log.warning("%s: the computer slept during attempt %d; trying again, not counted", what, attempt)
            started += _clock() - tried
            continue
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
