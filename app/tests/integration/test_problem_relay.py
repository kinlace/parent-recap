"""The gateway taking a Problem Report a parent chose to send from Parent Recap Check
(ops/gateway/brief_relay.py, #925): under the Household's Gateway Key, or without one under the
report id its program made; refused over the size limit, past a day's count, or with a field the
gateway doesn't take; its words never logged; and deleted after 30 days. What the program sends
(problem_report.Report) is what the gateway takes."""
from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from family_brief import problem_report
from test_report_relay import CALLER, ID, KEY, Keys, br

SECRET = "Mia Virtanen, mia@example.com"
PROBLEM = {
    "version": "0.21.0", "system": "macos", "system_version": "15.1",
    "doctor": f"✅ Config file: ~/.family/config.yaml, Brief goes to {SECRET}\n❌ Wilma: failed\n",
    "logs": {"run": ["21:00:04 ERROR family_brief | The Brief wasn't sent"], "update": [],
             "install": []},
    "note": "The Brief didn't come",
}


@pytest.fixture
def day() -> list[str]:
    return ["2026-11-14"]


def make_relay(tmp_path: Path, day: list[str], keys: Keys | None = None) -> Any:
    count = iter(range(1000))
    return br.Relay(
        keys=br.Bifrost("http://127.0.0.1:8080", "admin", "pw", fetch=keys or Keys()),
        cap=br.DailyCap(tmp_path / "sent.json", limit=4, today=lambda: day[0]),
        mailer=None, stop_url="", bindings=br.Bindings(tmp_path / "bindings.json"),
        confirm_url="",
        problems=br.Problems(tmp_path / "problems", tmp_path / "problem-counts.json",
                             today=lambda: day[0], now=lambda: f"{day[0]}T19:05:00Z",
                             new_id=lambda: f"p-{next(count):08x}"))


def post(relay: Any, body: Any = PROBLEM, key: str | None = KEY, rid: str | None = None,
         caller: str = CALLER) -> tuple[int, dict]:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return relay.problem(key, rid, caller, raw)


def kept(tmp_path: Path) -> dict[str, dict]:
    root = tmp_path / "problems"
    return {f.relative_to(root).as_posix(): json.loads(f.read_text(encoding="utf-8"))
            for f in sorted(root.rglob("*")) if f.is_file()} if root.exists() else {}


def test_a_problem_report_with_a_known_key_is_kept_under_that_key(tmp_path, day):
    assert post(make_relay(tmp_path, day)) == (200, {"taken": True})
    assert kept(tmp_path) == {"2026-11-14/p-00000000.json": {
        "id": "p-00000000", "received": "2026-11-14T19:05:00Z", "from": "key-id-1",
        "problem": PROBLEM}}


def test_without_a_key_it_is_kept_under_its_report_id(tmp_path, day):
    assert post(make_relay(tmp_path, day), key=None, rid=ID)[0] == 200
    [entry] = kept(tmp_path).values()
    assert entry["from"] == f"id-{ID}"


@pytest.mark.parametrize("key, rid", [("sk-bf-unknown", None), (None, None), (None, "not-an-id")])
def test_an_unknown_key_or_neither_a_key_nor_an_id_is_refused(tmp_path, day, key, rid):
    assert post(make_relay(tmp_path, day), key=key, rid=rid)[0] == 401
    assert kept(tmp_path) == {}


def test_one_over_the_size_limit_is_refused(tmp_path, day):
    big = {**PROBLEM, "doctor": "x" * br.MAX_PROBLEM_BYTES}
    assert post(make_relay(tmp_path, day), big)[0] == 413
    assert kept(tmp_path) == {}


@pytest.mark.parametrize("body", [
    {**PROBLEM, "home": "/Users/mia"},
    {**PROBLEM, "note": "two\nlines"},
    {**PROBLEM, "note": "x" * (br.MAX_NOTE + 1)},
    {**PROBLEM, "logs": {"wechat": ["a line"]}},
    {**PROBLEM, "logs": {"run": "not a list"}},
    {**PROBLEM, "system": "amiga"},
    {k: v for k, v in PROBLEM.items() if k != "doctor"},
    [PROBLEM],
    b"not json",
], ids=["unknown field", "note of two lines", "long note", "unknown log", "log not a list",
        "unknown system", "no doctor", "not an object", "not json"])
def test_one_with_a_field_the_gateway_doesnt_take_is_refused(tmp_path, day, body):
    assert post(make_relay(tmp_path, day), body)[0] == 400
    assert kept(tmp_path) == {}


def test_a_key_that_sends_too_many_is_refused_until_the_next_day(tmp_path, day):
    relay = make_relay(tmp_path, day)
    statuses = [post(relay)[0] for _ in range(br.PROBLEMS_PER_KEY + 1)]
    assert statuses == [200] * br.PROBLEMS_PER_KEY + [429]
    day[0] = "2026-11-15"
    assert post(relay)[0] == 200


def test_a_caller_without_a_key_that_sends_too_many_is_refused_until_the_next_day(tmp_path, day):
    relay = make_relay(tmp_path, day)
    statuses = [post(relay, key=None, rid=ID)[0] for _ in range(br.PROBLEMS_PER_CALLER + 1)]
    assert statuses == [200] * br.PROBLEMS_PER_CALLER + [429]
    assert post(relay, key=None, rid=ID, caller="198.51.100.1")[0] == 200  # another caller
    counts = (tmp_path / "problem-counts.json").read_text(encoding="utf-8")
    assert CALLER not in counts
    day[0] = "2026-11-15"
    assert post(relay, key=None, rid=ID)[0] == 200


def test_those_without_a_key_stop_at_the_days_total(tmp_path, day, monkeypatch):
    monkeypatch.setattr(br, "PROBLEMS_PER_DAY", 3)
    relay = make_relay(tmp_path, day)
    statuses = [post(relay, key=None, rid=ID, caller=f"198.51.100.{n}")[0] for n in range(4)]
    assert statuses == [200, 200, 200, 429]
    assert post(relay)[0] == 200  # a key counts on its own


def test_a_refused_one_doesnt_count(tmp_path, day):
    relay = make_relay(tmp_path, day)
    for _ in range(br.PROBLEMS_PER_KEY + 1):
        post(relay, {**PROBLEM, "home": "x"})
    assert post(relay)[0] == 200


def test_it_is_deleted_after_30_days(tmp_path, day):
    relay = make_relay(tmp_path, day)
    post(relay)
    day[0] = "2026-12-13"  # 29 days on
    assert relay.problems.prune() == []
    assert len(kept(tmp_path)) == 1
    day[0] = "2026-12-14"  # 30 days on
    assert relay.problems.prune() == ["2026-11-14"]
    assert kept(tmp_path) == {}


def test_a_new_one_deletes_those_past_their_days(tmp_path, day):
    relay = make_relay(tmp_path, day)
    post(relay)
    day[0] = "2026-12-20"
    post(relay)
    assert [p.split("/")[0] for p in kept(tmp_path)] == ["2026-12-20"]


def test_the_journal_says_one_came_and_from_whom_never_its_words(tmp_path, day, caplog):
    caplog.set_level(logging.INFO, logger="brief-relay")
    relay = make_relay(tmp_path, day)
    post(relay)
    post(relay, key=None, rid=ID)
    post(relay, {**PROBLEM, "home": SECRET})
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("problem ")]
    assert lines[0].startswith("problem key=ai-pilot-a bytes=")
    assert lines[0].endswith("outcome=taken as=p-00000000")
    assert lines[1].startswith(f"problem id={ID} bytes=")
    assert "outcome=400" in lines[2]
    for value in (SECRET, "Wilma", "The Brief didn't come", KEY, CALLER):
        assert value not in caplog.text


def test_what_the_program_sends_is_taken(tmp_path, day):
    report = problem_report.Report(
        {"version": "0.21.0", "system": "windows", "system_version": "10.0.26100"},
        "❌ Last run: failed\n", {"run": ["21:00:04 ERROR family_brief | x"], "update": [],
                                 "install": []})
    relay = make_relay(tmp_path, day)
    assert post(relay, report.body("It stopped on Monday"))[0] == 200
    assert post(relay, report.body())[0] == 200
    assert problem_report.LOGS == br.PROBLEM_LOGS
    assert problem_report.MAX_NOTE == br.MAX_NOTE


ABOUT = {"version": "0.21.0", "system": "macos", "system_version": "15.1"}
# A log line full of characters UTF-8 writes in four bytes, and a parent's line of them too.
WIDE = "21:00:04 ERROR family_brief | " + "😀" * 260
WIDE_NOTE = "😀" * problem_report.MAX_NOTE


def test_the_program_drops_the_oldest_log_lines_of_a_report_too_big(tmp_path, day):
    report = problem_report.Report(ABOUT, "❌ Last run: failed\n", {
        name: [f"{WIDE}{n}" for n in range(problem_report.LINES)] for name in problem_report.LOGS})

    body = report.fitted().body(WIDE_NOTE)

    assert len(body) <= problem_report.MAX_BYTES == br.MAX_PROBLEM_BYTES
    assert post(make_relay(tmp_path, day), body)[0] == 200
    sent = json.loads(body)
    assert sent["doctor"] == "❌ Last run: failed\n" and sent["note"] == WIDE_NOTE
    for lines in sent["logs"].values():
        assert 0 < len(lines) < problem_report.LINES
        assert lines[-1].endswith(f"{problem_report.LINES - 1}")  # the newest kept


def test_the_program_cuts_the_end_of_doctors_output_when_that_is_too_big_alone(tmp_path, day):
    report = problem_report.Report(ABOUT, "❌ 失败\n" * 20_000, {"run": [WIDE]})

    body = report.fitted().body(WIDE_NOTE)

    assert len(body) <= problem_report.MAX_BYTES
    assert post(make_relay(tmp_path, day), body)[0] == 200
    sent = json.loads(body)
    assert sent["doctor"].startswith("❌ 失败\n") and sent["doctor"].endswith("\n…")
    assert sent["logs"] == {"run": []}


def test_a_report_that_fits_is_left_whole():
    report = problem_report.Report(ABOUT, "✅ Config file\n", {"run": ["21:00:04 INFO family_brief | x"]})
    assert report.fitted() == report



def test_over_http_it_is_posted_with_its_key_or_its_id(tmp_path, day):
    server = br.ThreadingHTTPServer(("127.0.0.1", 0), br.handler(make_relay(tmp_path, day)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(headers: dict[str, str]) -> tuple[int, dict]:
        request = urllib.request.Request(base + "/v1/problem", data=json.dumps(PROBLEM).encode(),
                                         headers={"content-type": "application/json", **headers})
        try:
            with urllib.request.urlopen(request, timeout=5) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    try:
        assert call({"x-api-key": KEY}) == (200, {"taken": True})
        assert call({"x-report-id": ID}) == (200, {"taken": True})
        assert call({})[0] == 401
    finally:
        server.shutdown()
        server.server_close()
    assert [e["from"] for e in kept(tmp_path).values()] == ["key-id-1", f"id-{ID}"]
