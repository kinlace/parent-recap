"""`admin reports` (ops/gateway/admin, #924, ADR 0048): each Household that sent a Run Report, by
its Gateway Key's name or, without a key, its report id, with its last run on one line; one whose
last report came over a day ago marked and listed first; and with a key or an id, that Household's
runs of the last 30 days. The reports are kept by the relay's own `Reports`, under tmp_path, and
Bifrost's admin API is faked as in test_admin. Its output never holds a key's value or an
address."""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any

import pytest
from test_admin import AI, SEND, FakeBifrost, Server, admin

relay = sys.modules["brief_relay"]

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
REPORT_ID = "0123456789abcdef0123456789abcdef"
# A key whose holder's email is in its description: neither is printed.
EMAIL_KEY = {"id": "id-4", "name": "send-pilot-d", "value": "sk-bf-four", "is_active": True,
             "description": "Lahti family, parent@example.com, own Claude Code or Codex"}


def report(at: str, **fields: Any) -> dict:
    return {"version": "0.12.0", "system": "macos", "system_version": "15.1", "at": at,
            "started": "schedule", "exit": 0, "seconds": 300, "peak_mb": 180,
            "sources": {"gmail": {"read": 3}}, "ai": {"backend": "gateway", "result": "ok"},
            "brief": {"outcome": "sent"}, **fields}


def keep(server: Server, who: str, received: str, r: dict) -> None:
    """A report the relay took at `received` (UTC), under `who` (key-<id> or id-<id>)."""
    state = server.paths.relay_state
    reports = relay.Reports(state / "reports", state / "report-counts.json",
                            today=lambda: received[:10], now=lambda: received)
    reports.keep(who, r)


def reports(server: Server, *args: str) -> int:
    try:
        return admin.main(["reports", *args], paths=server.paths, is_root=lambda: True,
                          fetch=server.bifrost, edge=admin.Edge(now=lambda: NOW))
    except SystemExit as e:
        if isinstance(e.code, str):
            print(e.code, file=sys.stderr)
            return 1
        return e.code or 0


@pytest.fixture
def server(tmp_path) -> Server:
    s = Server(tmp_path)
    s.bifrost = FakeBifrost([AI, SEND, EMAIL_KEY])
    # ai-pilot-a: two evenings, the last one 17 hours ago, a Source failed in it.
    keep(s, "key-id-1", "2026-11-13T19:06:00Z", report("2026-11-13T19:05:00+00:00"))
    keep(s, "key-id-1", "2026-11-14T19:06:00Z", report(
        "2026-11-14T19:05:00+00:00", causes=["wilma-egress"],
        sources={"gmail": {"read": 3}, "wilma": {"failed": "wilma-egress"}}))
    # send-pilot-b: last heard of three days ago.
    keep(s, "key-id-2", "2026-11-12T19:10:00Z", report(
        "2026-11-12T19:09:00+00:00", system="windows", system_version="10.0.26100"))
    # No key: its last Brief failed and its AI was busy.
    keep(s, f"id-{REPORT_ID}", "2026-11-14T18:30:00Z", report(
        "2026-11-14T18:29:00+00:00", exit=1, ai={"backend": "claude", "result": "busy"},
        brief={"outcome": "failed", "cause": "unknown"},
        exception={"type": "TimeoutError", "at": "family_brief/run.py:120"}))
    return s


def test_reports_lists_each_household_by_its_key_s_name_or_its_id_the_silent_one_first(
        server, capsys):
    assert reports(server) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("Each Household's last Run Report (times in Europe/Helsinki)")
    assert out[1:] == [
        "⚠️ send-pilot-b  last 2026-11-12 21:09  no report for 2 days  0.12.0  "
        "windows 10.0.26100  exit 0  brief sent",
        "✅ ai-pilot-a  last 2026-11-14 21:05  0.12.0  macos 15.1  exit 0  brief sent  "
        "failed: wilma=wilma-egress",
        f"❌ id={REPORT_ID}  last 2026-11-14 20:29  0.12.0  macos 15.1  exit 1  "
        "brief failed=unknown  failed: ai=busy  exception TimeoutError at family_brief/run.py:120",
    ]


def test_a_household_silent_for_days_says_how_many(server, capsys):
    keep(server, "key-id-4", "2026-11-01T19:00:00Z", report("2026-11-01T19:00:00+00:00"))
    assert reports(server) == 0
    out = capsys.readouterr().out.splitlines()
    assert [line.split("  ")[0] for line in out[1:3]] == ["⚠️ send-pilot-b", "⚠️ send-pilot-d"]
    assert "no report for 13 days" in out[2]


@pytest.mark.parametrize("which", ["ai-pilot-a", "id-1"])
def test_a_key_s_name_or_id_shows_its_runs_one_line_each(server, capsys, which):
    assert reports(server, which) == 0
    assert capsys.readouterr().out.splitlines() == [
        "ai-pilot-a: 2 Run Reports in the last 30 days (times in Europe/Helsinki):",
        "2026-11-13 21:05  0.12.0  macos 15.1  exit 0  schedule  300 s  180 MB  ai gateway ok  "
        "read gmail=3  brief sent",
        "2026-11-14 21:05  0.12.0  macos 15.1  exit 0  schedule  300 s  180 MB  ai gateway ok  "
        "read gmail=3  brief sent  failed: wilma=wilma-egress",
    ]


def test_a_report_id_shows_that_household_s_runs(server, capsys):
    assert reports(server, REPORT_ID) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith(f"id={REPORT_ID}: 1 Run Report in the last 30 days")
    assert len(out) == 2 and "brief failed=unknown" in out[1]


def test_a_key_or_id_with_no_report_kept_is_refused(server, capsys):
    assert reports(server, "nobody") == 1
    assert "no Run Report kept for nobody" in capsys.readouterr().err


def test_no_report_kept_says_so(server, capsys):
    for d in (server.paths.relay_state / "reports").iterdir():
        for f in d.iterdir():
            f.unlink()
        d.rmdir()
    assert reports(server) == 0
    assert "No Run Report kept" in capsys.readouterr().out


def test_a_key_since_deleted_is_named_by_its_id(server, capsys):
    keep(server, "key-id-9", "2026-11-14T19:00:00Z", report("2026-11-14T19:00:00+00:00"))
    assert reports(server) == 0
    assert "✅ key-id=id-9 (no longer a key)  last" in capsys.readouterr().out
    assert reports(server, "id-9") == 0


def test_with_bifrost_down_households_are_named_by_their_key_s_id_and_why(server, capsys):
    server.bifrost.down = True
    assert reports(server) == 0
    captured = capsys.readouterr()
    assert "couldn't read the Gateway Keys" in captured.err
    assert "key-id=id-1" in captured.out and f"id={REPORT_ID}" in captured.out


def test_the_output_never_holds_a_key_s_value_or_an_address(server, capsys):
    keep(server, "key-id-4", "2026-11-14T19:00:00Z", report("2026-11-14T19:00:00+00:00"))
    # A line in the reports that isn't a report the relay takes, as if written by hand.
    day = server.paths.relay_state / "reports" / "2026-11-14"
    with (day / "key-id-4.jsonl").open("a") as f:
        f.write(json.dumps({"received": "2026-11-14T20:00:00Z",
                            "report": {**report("2026-11-14T20:00:00+00:00"),
                                       "note": "grandma@example.com"}}) + "\n")
    for args in ((), ("send-pilot-d",), ("ai-pilot-a",)):
        assert reports(server, *args) == 0
    out = capsys.readouterr().out
    assert "@" not in out and "sk-bf-" not in out and "Lahti" not in out
    assert "send-pilot-d: 1 Run Report " in out


@pytest.mark.parametrize("args", [("a", "b"), ("--all",), ("",)])
def test_reports_takes_at_most_one_key_or_id(server, capsys, args):
    assert reports(server, *args) == 2
    assert "usage: sudo admin reports [KEY-OR-ID]" in capsys.readouterr().err


def test_with_bifrost_down_no_key_is_said_to_be_gone(server, capsys):
    server.bifrost.down = True
    assert reports(server) == 0
    assert "no longer a key" not in capsys.readouterr().out


def test_two_keys_of_one_name_are_told_apart_by_their_ids(server, capsys):
    server.bifrost.keys.append({"id": "id-5", "name": "ai-pilot-a", "value": "sk-bf-five",
                                "is_active": True})
    keep(server, "key-id-5", "2026-11-14T19:00:00Z", report("2026-11-14T19:00:00+00:00"))
    assert reports(server) == 0
    out = capsys.readouterr().out
    assert "✅ ai-pilot-a (id=id-1)  last" in out and "✅ ai-pilot-a (id=id-5)  last" in out
    assert reports(server, "ai-pilot-a") == 1
    assert "give one's id" in capsys.readouterr().err
    assert reports(server, "id-5") == 0
    assert capsys.readouterr().out.startswith("ai-pilot-a (id=id-5): 1 Run Report ")


def test_a_key_of_a_shared_name_with_reports_alone_is_found_by_it(server, capsys):
    # Another key named so that never reported doesn't make the name ambiguous.
    server.bifrost.keys.append({"id": "id-5", "name": "ai-pilot-a", "value": "sk-bf-five",
                                "is_active": True})
    assert reports(server, "ai-pilot-a") == 0
    assert "2 Run Reports" in capsys.readouterr().out


def test_the_last_report_is_the_one_that_came_last_whatever_the_computer_s_clock(server, capsys):
    # A run whose computer's clock was two days ahead came before tonight's.
    keep(server, "key-id-4", "2026-11-13T19:00:00Z", report("2026-11-16T19:00:00+00:00", exit=2))
    keep(server, "key-id-4", "2026-11-14T19:00:00Z", report("2026-11-14T19:00:00+00:00"))
    assert reports(server) == 0
    assert "✅ send-pilot-d  last 2026-11-14 21:00" in capsys.readouterr().out
