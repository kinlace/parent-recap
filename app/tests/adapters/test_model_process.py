"""The model's program as the CLI adapters run it (llm/retry.py), a real process standing in for
`claude -p` or `codex exec`: its answer read from what it prints, a hang timed out, and a try the
computer slept through stopped and made again (#887)."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from family_brief import slept
from family_brief.llm import retry

PYTHON = [sys.executable, "-I", "-c"]


def test_the_program_is_given_the_prompt_and_its_answer_is_what_it_prints():
    ran = retry.run_with_retry([*PYTHON, "import sys; print(sys.stdin.read().upper())"], 30,
                               "the model", input="the night's messages")

    assert (ran.proc.returncode, ran.proc.stdout) == (0, "THE NIGHT'S MESSAGES\n")


def test_a_program_that_hangs_twice_is_stopped_and_fails(pauses):
    with pytest.raises(RuntimeError, match="timed out twice"):
        retry.run_with_retry([*PYTHON, "import time; time.sleep(30)"], 1, "the model", input="")


def test_a_try_the_computer_slept_through_is_stopped_and_made_again(tmp_path: Path, monkeypatch, pauses):
    # The first try leaves a mark and hangs; while it hangs, the wall clock jumps an hour ahead of
    # the time awake, as it does when a laptop is opened in the morning. The second try answers.
    mark = tmp_path / "first-try"
    program = ("import pathlib, sys, time\n"
               f"mark = pathlib.Path({str(mark)!r})\n"
               "if mark.exists(): print('the Brief'); sys.exit()\n"
               "mark.touch(); time.sleep(30)\n")
    monkeypatch.setattr(slept, "_wall", lambda: 3600.0 if mark.exists() else 0.0)
    monkeypatch.setattr(retry, "SLEEP_CHECK", 0.05)
    monkeypatch.setattr(retry, "AWAKE_AGAIN", 0.2)
    started = time.monotonic()

    ran = retry.run_with_retry([*PYTHON, program], 20, "the model", input="")

    assert ran.proc.stdout == "the Brief\n"
    assert time.monotonic() - started < 15  # the hanging try was stopped, not waited out
    assert pauses == []  # nor counted as a timeout


def test_a_stopped_program_is_no_longer_running(tmp_path: Path, monkeypatch):
    running: list[subprocess.Popen] = []
    mark = tmp_path / "started"
    monkeypatch.setattr(slept, "_wall", lambda: 3600.0 if mark.exists() else 0.0)
    monkeypatch.setattr(retry, "SLEEP_CHECK", 0.05)
    program = f"import pathlib, time; pathlib.Path({str(mark)!r}).touch(); time.sleep(30)"

    def stop() -> None:
        for process in running:
            process.kill()

    with pytest.raises(retry._Slept):
        retry._awake_call(lambda: retry._run([*PYTHON, program], 20, running, input=""), stop)

    [process] = running
    assert process.wait(10) != 0
