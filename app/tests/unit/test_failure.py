"""Why a setup step failed, in the words the setup page shows and a family copies to us (#483): a
program's exit code and last lines, a time limit, or an exception on one line."""
from __future__ import annotations

import logging
import subprocess

import pytest

from family_brief import failure


def test_a_program_that_fails_gives_its_exit_code_and_its_last_ten_lines_of_errors():
    errors = "".join(f"npm error line {n}\n" for n in range(1, 31))

    said = failure.exited("npm", 243, errors, "added 0 packages")

    assert said.splitlines() == ["npm ended with exit code 243:",
                                 *(f"npm error line {n}" for n in range(21, 31))]


def test_a_program_with_no_errors_gives_what_it_printed_instead():
    assert failure.exited("schtasks", 1, " \n", "ERROR: Access is denied.\n") == \
        "schtasks ended with exit code 1:\nERROR: Access is denied."


def test_a_program_that_says_nothing_gives_its_exit_code_alone():
    assert failure.exited("npm", 1, "", None) == "npm ended with exit code 1."


def test_a_program_out_of_time_says_so_in_one_line():
    assert failure.timed_out("npm", 300) == "npm didn't finish within 300 seconds."
    assert failure.timed_out("claude auth status", 20.0) == \
        "claude auth status didn't finish within 20 seconds."


def test_an_exception_gives_its_type_and_message_on_one_line():
    e = PermissionError(13, "Permission denied", "/Users/a/ParentRecap/node\nbin")

    assert failure.raised(e, "The install") == \
        "The install stopped: PermissionError: [Errno 13] Permission denied: " \
        "'/Users/a/ParentRecap/node\\nbin'"
    assert failure.raised(ValueError("not\n  a list"), "The plugin install") == \
        "The plugin install stopped: ValueError: not a list"


@pytest.mark.parametrize("e, said", [
    (subprocess.TimeoutExpired(["/opt/bin/claude", "plugin", "list"], 180),
     "claude plugin list didn't finish within 180 seconds."),
    (subprocess.CalledProcessError(2, ["claude", "plugin", "install"], "", "no network\n"),
     "claude plugin install ended with exit code 2:\nno network"),
    (RuntimeError("launchctl said no"), "Turning it on stopped: RuntimeError: launchctl said no"),
])
def test_any_exception_is_told_by_what_it_is(e, said):
    assert failure.of(e, "Turning it on") == said


def test_a_program_named_by_the_caller_is_called_that():
    e = subprocess.TimeoutExpired(["/bin/sh", "-c", "curl … | sh"], 600)

    assert failure.of(e, "The Codex install", "The Codex installer") == \
        "The Codex installer didn't finish within 600 seconds."


def test_a_reason_is_logged_as_a_warning_with_what_failed(caplog):
    caplog.set_level(logging.WARNING)

    assert failure.logged("The wilma CLI didn't install", "npm ended with exit code 1.") == \
        "npm ended with exit code 1."

    [record] = caplog.records
    assert record.levelno == logging.WARNING
    assert record.getMessage() == "The wilma CLI didn't install: npm ended with exit code 1."
