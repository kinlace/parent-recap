"""Runs never overlap, and `bg` says when its run didn't finish in time. (That the macOS adapter
leaves no job or output behind when `bg` is stopped is in test_jobs.py.)"""
from __future__ import annotations

import os
import subprocess
import sys
import time

from conftest import SAYS_SOMETHING, msg

from family_brief import run_lock
from family_brief.config import Config
from family_brief.system import jobs


def one_message(h) -> None:
    h.sources = {"gmail": [msg("gmail", "g-1", "2026-09-27T08:15:00+03:00",
                               "Class photos for 1A are on Wednesday morning.",
                               sender="office@kilo.example.fi", subject="Valokuvaus ke 30.9.")]}
    h.model_reply = SAYS_SOMETHING


def another_run_is_going(h):
    return run_lock.exclusive(Config.model_validate(h.config))


def test_a_run_while_another_is_going_stops_with_a_message_and_sends_nothing(harness, caplog):
    one_message(harness)

    with another_run_is_going(harness):
        code = harness.run()

    assert code == run_lock.BUSY
    assert "Another Parent Recap run is still going" in caplog.text
    assert harness.sent == [] and harness.model_calls == []
    assert not harness.state_path.exists()


def test_a_run_goes_ahead_once_the_other_has_finished(harness):
    one_message(harness)
    with another_run_is_going(harness):
        pass

    assert harness.run() == 0
    assert len(harness.sent) == 1


def test_a_preview_isnt_held_up_by_a_run_that_is_going(harness):
    one_message(harness)

    with another_run_is_going(harness):
        assert harness.run("--dry-run") == 0

    assert harness.sent == [] and not harness.state_path.exists()


def test_a_run_that_was_killed_doesnt_keep_the_next_one_out(harness, tmp_path):
    one_message(harness)
    ready = tmp_path / "locked"
    holder = subprocess.Popen([sys.executable, "-c", (
        "import pathlib, sys, time\n"
        "from family_brief import run_lock\n"
        "from family_brief.config import Config\n"
        f"cfg = Config.model_validate({harness.config!r})\n"
        "with run_lock.exclusive(cfg):\n"
        f"    pathlib.Path({str(ready)!r}).touch()\n"
        "    time.sleep(60)\n")], env={**os.environ, "HOME": str(harness.home)})
    try:
        for _ in range(100):
            if ready.exists():
                break
            time.sleep(0.1)
        assert harness.run() == run_lock.BUSY
    finally:
        holder.kill()
        holder.wait()

    assert harness.run() == 0
    assert len(harness.sent) == 1


def test_weekend_picks_while_another_run_is_going_stop_with_a_message(harness, caplog):
    harness.config["weekend_events"] = {"enabled": True}

    with another_run_is_going(harness):
        code = harness.cli("weekend-events")

    assert code == run_lock.BUSY
    assert "Another Parent Recap run is still going" in caplog.text


def test_bg_that_does_not_finish_in_time_says_so_with_timeouts_code(harness, capsys):
    harness.jobs.hangs = True

    assert harness.cli("bg", "--timeout", "0", "doctor") == jobs.TIMED_OUT

    assert harness.jobs.detached == [["-c", str(harness.home / ".family" / "config.yaml"),
                                      "doctor"]]
    assert "didn't finish within 0 seconds" in capsys.readouterr().out
