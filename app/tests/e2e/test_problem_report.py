"""The Problem Report a parent sends from Parent Recap Check (ADR 0048, #925). Check runs `doctor
--offer-problem-report`: after doctor's results the window shows the rest of the report and asks
one yes/no question in the family's language. Only a yes sends; then it asks for one line, which
Return skips, and says whether it went. `doctor` run any other way asks nothing and sends nothing.
Only the program's own dated log lines go, never pip's output or a traceback.

The parent's answers are what the window reads (stdin); the gateway is the outbox's
(fake_outbox.py). Assertions are on what the window shows and what the gateway took."""
from __future__ import annotations

import io
import json
import sys

import pytest

from conftest import GATEWAY_KEY
from fake_system import WINDOWS
from family_brief import __main__ as cli
from family_brief import gateway as api
from test_doctor import nothing_loaded  # noqa: F401  the fixture
from test_gateway_sends import no_key

RUN_LOG = ("21:00:01 INFO family_brief.collectors.gmail | Gmail matched 3 messages\n"
           "Traceback (most recent call last):\n"
           '  File "x.py", line 1, in <module>\n'
           "ValueError: Dear Mia, the trip is on Friday\n"
           "21:00:04 ERROR family_brief | The Brief wasn't sent: the gateway didn't answer\n")
UPDATE_LOG = ("2026-11-15T23:54:44+02:00 install: 0.20.2: install.sh ended with exit status 0\n"
              "  installer:\n    Collecting pip==26.3\n"
              "2026-11-15T23:54:52+02:00 check: 0.20.2's doctor: no failures\n")
INSTALL_LOG = ("Mac: arm64, macOS 15.1, Python ~/ParentRecap/runtime/python3 (3.13.16)\n"
               "Collecting pip==26.3\nERROR: No matching distribution found for pydantic\n")


def logs(harness) -> None:
    """Parent Recap's own logs, as the evening run, the update and an install wrote them."""
    folder = harness.home / "ParentRecap" / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "run-stderr.log").write_text(RUN_LOG, encoding="utf-8")
    (folder / "update.log").write_text(UPDATE_LOG, encoding="utf-8")
    (folder / "install-2026-11-15-235428.log").write_text(INSTALL_LOG, encoding="utf-8")


def check(harness, capsys, monkeypatch, typed: str, *args: str) -> str:
    """Parent Recap Check: doctor with the offer, the parent typing `typed`. What the window
    showed."""
    monkeypatch.setattr("sys.stdin", io.StringIO(typed))
    harness.cli("doctor", "--skip-llm", "--offer-problem-report", *args)
    return capsys.readouterr().out


@pytest.fixture
def broken(harness, nothing_loaded):  # noqa: F811
    harness.config["summary_language"] = "en"
    logs(harness)
    return harness


def test_it_shows_the_whole_report_before_asking_and_sends_it_on_a_yes(broken, capsys, monkeypatch):
    shown = check(broken, capsys, monkeypatch, "yes\nThe Brief didn't come on Monday\n")

    [problem] = broken.problems
    sent = problem.json()
    assert problem.headers == {"x-api-key": broken.secrets[api.ACCOUNT]}
    # doctor's results, as the window showed them, come first; the rest follows them.
    assert sent["doctor"] in shown
    assert shown.index("checks:") < shown.index("Problem Report") < shown.index("Send this Problem Report")
    assert "go to the Parent Recap team, and a person on the team may read them" in shown
    assert f"Version {sent['version']}" in shown
    for lines in sent["logs"].values():
        for line in lines:
            assert line in shown
    assert sent["note"] == "The Brief didn't come on Monday"
    assert {"version", "system", "system_version"} <= set(sent)
    assert "✅ Sent." in shown


def test_only_the_programs_own_dated_lines_go(broken, capsys, monkeypatch):
    shown = check(broken, capsys, monkeypatch, "yes\n\n")

    sent = broken.problems[0].json()
    assert sent["logs"] == {
        "run": ["21:00:01 INFO family_brief.collectors.gmail | Gmail matched 3 messages",
                "21:00:04 ERROR family_brief | The Brief wasn't sent: the gateway didn't answer"],
        "update": ["2026-11-15T23:54:44+02:00 install: 0.20.2: install.sh ended with exit status 0",
                   "2026-11-15T23:54:52+02:00 check: 0.20.2's doctor: no failures"],
        "install": []}
    assert "Dear Mia" not in shown and "Dear Mia" not in broken.problems[0].raw.decode()
    assert "Collecting pip" not in broken.problems[0].raw.decode()
    # Return skips the line.
    assert "note" not in sent


@pytest.mark.parametrize("typed", ["no\n", "\n", "sure\n", "yes please\n", "", "kyllä\n"],
                         ids=["no", "empty", "other", "more than yes", "window closed", "other language"])
def test_anything_but_a_yes_sends_nothing_and_keeps_nothing(broken, capsys, monkeypatch, typed):
    shown = check(broken, capsys, monkeypatch, typed)

    assert broken.problems == []
    assert "Nothing was sent." in shown
    assert "In one line" not in shown
    outbox = broken.state_path.with_name("run-reports")
    assert not outbox.is_dir() or not list(outbox.glob("*.json"))


def test_a_window_closed_at_the_line_sends_nothing(broken, capsys, monkeypatch):
    shown = check(broken, capsys, monkeypatch, "yes\n")

    assert broken.problems == []
    assert "Nothing was sent." in shown


@pytest.mark.parametrize("language, yes, asked, sent", [
    ("fi", "kyllä", "Lähetetäänkö tämä vikailmoitus", "✅ Lähetetty."),
    ("zh", "是", "要把这份问题报告发送给 Parent Recap 团队吗", "✅ 已发送。"),
])
def test_it_asks_in_the_familys_language(broken, capsys, monkeypatch, language, yes, asked, sent):
    broken.config["summary_language"] = language

    shown = check(broken, capsys, monkeypatch, f"{yes}\n\n")

    assert asked in shown and sent in shown
    assert len(broken.problems) == 1


def test_a_check_that_crashes_still_offers_the_report(broken, capsys, monkeypatch):
    from family_brief import ops

    def crashes(*_a, **_k):
        print("✅ Config file: fine", flush=True)
        raise RuntimeError("a check's own bug")

    monkeypatch.setattr(ops, "health_checks", crashes)

    shown = check(broken, capsys, monkeypatch, "yes\n\n")

    [problem] = broken.problems
    assert problem.json()["doctor"].startswith("✅ Config file: fine\n")
    assert "doctor stopped before its last check" in problem.json()["doctor"]
    assert "✅ Sent." in shown


def test_doctor_run_any_other_way_asks_nothing_and_sends_nothing(broken, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("yes\nsomething\n"))

    broken.cli("doctor", "--skip-llm")

    shown = capsys.readouterr().out
    assert broken.problems == []
    assert "Problem Report" not in shown


def test_without_a_gateway_key_it_goes_under_the_report_id(broken, capsys, monkeypatch):
    no_key(broken)
    broken.key_answer = (503, {"error": "busy"})  # nor does doctor get one

    check(broken, capsys, monkeypatch, "yes\n\n")

    [problem] = broken.problems
    [(header, value)] = problem.headers.items()
    assert header == "x-report-id" and len(value) == 32


def test_a_key_the_gateway_no_longer_knows_goes_under_the_report_id(broken, capsys, monkeypatch):
    broken.secrets[api.ACCOUNT] = GATEWAY_KEY
    broken.outbox.revoked_keys.add(GATEWAY_KEY)
    broken.key_answer = (503, {"error": "busy"})

    shown = check(broken, capsys, monkeypatch, "yes\n\n")

    [problem] = broken.problems
    assert "x-report-id" in problem.headers
    assert "✅ Sent." in shown


def test_an_install_that_never_finished_sends_it_too(harness, capsys, monkeypatch):
    # No config, no Gateway Key: the install stopped before setup.
    no_key(harness)
    harness.key_answer = (503, {"error": "busy"})
    logs(harness)
    monkeypatch.setattr("sys.stdin", io.StringIO("yes\nThe install stopped\n"))

    _doctor_without_config(harness)

    [problem] = harness.problems
    sent = problem.json()
    assert "x-report-id" in problem.headers
    assert "Config file" in sent["doctor"]
    assert sent["note"] == "The install stopped"
    assert "✅ Sent." in capsys.readouterr().out


def _doctor_without_config(harness) -> None:
    missing = harness.home / ".family" / "config.yaml"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", ["family-brief", "-c", str(missing), "doctor", "--skip-llm",
                                 "--offer-problem-report"])
        cli.main()


@pytest.mark.parametrize("answer, said", [
    (api.GatewayError("couldn't reach the gateway"), "couldn't reach Parent Recap's server"),
    (429, "as many as it takes from this computer today"),
    (413, "the server didn't take it (HTTP 413)"),
])
def test_it_says_when_it_wasnt_sent_and_keeps_nothing(broken, capsys, monkeypatch, answer, said):
    broken.problem_answer = answer

    shown = check(broken, capsys, monkeypatch, "yes\n\n")

    assert "❌ It wasn't sent:" in shown and said in shown
    assert "Nothing was kept to send later." in shown
    outbox = broken.state_path.with_name("run-reports")
    assert not outbox.is_dir() or not list(outbox.glob("*.json"))


def test_on_windows_it_is_offered_the_same_way(broken, capsys, monkeypatch):
    broken.system = WINDOWS

    shown = check(broken, capsys, monkeypatch, "yes\n\n")

    [problem] = broken.problems
    assert json.loads(problem.raw)["system"] == "windows"
    assert "Version " in shown and "on Windows" in shown
