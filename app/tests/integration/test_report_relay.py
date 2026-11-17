"""The gateway taking a Run Report (ops/gateway/brief_relay.py, ADR 0048, #922): under the
Household's Gateway Key, or without one under the random id its program made and limited per
caller; refused, and nothing of it kept or logged, when it holds a field or a value outside the
list the program holds too; and deleted after 30 days."""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from family_brief import last_run
from family_brief.sources import ALL

SOURCE = Path(__file__).resolve().parents[3] / "ops" / "gateway" / "brief_relay.py"
if "brief_relay" in sys.modules:
    br = sys.modules["brief_relay"]
else:
    _spec = importlib.util.spec_from_file_location("brief_relay", SOURCE)
    br = sys.modules["brief_relay"] = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(br)

KEY = "sk-bf-test-household-one"
ID = "0123456789abcdef0123456789abcdef"
CALLER = "203.0.113.7"
REPORT = {
    "version": "0.12.0", "system": "macos", "system_version": "15.1",
    "at": "2026-11-14T19:00:00+00:00", "started": "schedule", "exit": 0, "seconds": 300,
    "peak_mb": 180, "causes": ["wilma-egress"],
    "sources": {"gmail": {"read": 3}, "wilma": {"failed": "wilma-egress"}},
    "ai": {"backend": "gateway", "result": "ok"},
    "brief": {"outcome": "sent"}, "update": "updated",
}
SECRET = "Mia Virtanen"


class Keys:
    """Bifrost's virtual-key list, one page."""

    def __init__(self, keys: list[dict] | None = None) -> None:
        self.keys = [{"id": "id-1", "name": "ai-pilot-a", "value": KEY, "is_active": True}] \
            if keys is None else keys

    def __call__(self, path: str, body: dict | None = None) -> dict:
        return {"virtual_keys": self.keys, "total_count": len(self.keys)}


@pytest.fixture
def day() -> list[str]:
    return ["2026-11-14"]


def make_relay(tmp_path: Path, day: list[str], keys: Keys | None = None) -> Any:
    return br.Relay(
        keys=br.Bifrost("http://127.0.0.1:8080", "admin", "pw", fetch=keys or Keys()),
        cap=br.DailyCap(tmp_path / "sent.json", limit=4, today=lambda: day[0]),
        mailer=None, stop_url="", bindings=br.Bindings(tmp_path / "bindings.json"),
        confirm_url="",
        reports=br.Reports(tmp_path / "reports", tmp_path / "report-counts.json",
                           today=lambda: day[0], now=lambda: f"{day[0]}T19:05:00Z"))


def post(relay: Any, body: Any = REPORT, key: str | None = KEY, rid: str | None = None,
         caller: str = CALLER) -> tuple[int, dict]:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return relay.report(key, rid, caller, raw)


def kept(tmp_path: Path) -> dict[str, list[dict]]:
    """Each file of reports kept, by its path under reports/, with its lines."""
    root = tmp_path / "reports"
    return {f.relative_to(root).as_posix():
            [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines()]
            for f in sorted(root.rglob("*")) if f.is_file()} if root.exists() else {}


# ── Under the Gateway Key

def test_a_report_with_a_known_key_is_kept_under_that_key(tmp_path, day):
    assert post(make_relay(tmp_path, day)) == (200, {"taken": True})
    assert kept(tmp_path) == {
        "2026-11-14/key-id-1.jsonl": [{"received": "2026-11-14T19:05:00Z", "report": REPORT}]}


@pytest.mark.parametrize("keys", [
    Keys([]),
    Keys([{"id": "id-1", "name": "ai-pilot-a", "value": KEY, "is_active": False}]),
])
def test_a_report_with_an_unknown_or_switched_off_key_is_refused(tmp_path, day, keys):
    status, body = post(make_relay(tmp_path, day, keys))
    assert status == 401 and body["error"]
    assert kept(tmp_path) == {}


def test_an_unknown_key_is_refused_even_with_an_id(tmp_path, day):
    assert post(make_relay(tmp_path, day, Keys([])), rid=ID)[0] == 401
    assert kept(tmp_path) == {}


def test_a_key_that_posts_too_many_reports_is_refused_until_the_next_day(tmp_path, day):
    other = {"id": "id-2", "name": "ai-pilot-b", "value": "sk-bf-two", "is_active": True}
    relay = make_relay(tmp_path, day, Keys([Keys().keys[0], other]))
    statuses = [post(relay)[0] for _ in range(br.REPORTS_PER_KEY + 1)]
    assert statuses == [200] * br.REPORTS_PER_KEY + [429]
    assert post(relay, key="sk-bf-two")[0] == 200  # another key
    day[0] = "2026-11-15"
    assert post(relay)[0] == 200


def test_a_refused_report_doesnt_count(tmp_path, day):
    relay = make_relay(tmp_path, day)
    for _ in range(br.REPORTS_PER_CALLER + 1):
        post(relay, {**REPORT, "subject": SECRET}, key=None, rid=ID)
        post(relay, {**REPORT, "subject": SECRET})
    assert post(relay, key=None, rid=ID)[0] == 200
    assert post(relay)[0] == 200


# ── Without a key

def test_a_report_without_a_key_is_kept_under_its_id(tmp_path, day):
    assert post(make_relay(tmp_path, day), key=None, rid=ID)[0] == 200
    assert list(kept(tmp_path)) == [f"2026-11-14/id-{ID}.jsonl"]


@pytest.mark.parametrize("rid", [None, "", "not-an-id", ID.upper(), ID + "0", "../" + ID[3:]])
def test_a_report_with_neither_a_key_nor_an_id_is_refused(tmp_path, day, rid):
    assert post(make_relay(tmp_path, day), key=None, rid=rid)[0] == 401
    assert kept(tmp_path) == {}


def test_a_caller_that_posts_too_many_reports_without_a_key_is_refused_until_the_next_day(
        tmp_path, day):
    relay = make_relay(tmp_path, day)
    statuses = [post(relay, key=None, rid=ID)[0] for _ in range(br.REPORTS_PER_CALLER + 1)]
    assert statuses == [200] * br.REPORTS_PER_CALLER + [429]
    assert post(relay, key=None, rid=ID, caller="198.51.100.9")[0] == 200  # another caller
    assert post(relay)[0] == 200  # with a key
    day[0] = "2026-11-15"
    assert post(relay, key=None, rid=ID)[0] == 200


def test_the_callers_count_survives_a_restart_and_keeps_no_address(tmp_path, day):
    for _ in range(br.REPORTS_PER_CALLER):
        post(make_relay(tmp_path, day), key=None, rid=ID)
    assert post(make_relay(tmp_path, day), key=None, rid=ID)[0] == 429
    assert CALLER not in (tmp_path / "report-counts.json").read_text(encoding="utf-8")


def test_reports_without_a_key_stop_at_the_days_total(tmp_path, day, monkeypatch):
    monkeypatch.setattr(br, "REPORTS_PER_DAY", 2)
    relay = make_relay(tmp_path, day)
    assert [post(relay, key=None, rid=ID, caller=f"198.51.100.{n}")[0] for n in range(3)] \
        == [200, 200, 429]


# ── Only the listed fields and keys

FREE_TEXT = ["teacher.3b@kilo.example.fi", SECRET, "/Users/anna/ParentRecap/state.json",
             "login failed: [AUTHENTICATIONFAILED]"]


@pytest.mark.parametrize("change", [
    {"error": "unknown"},                                         # a field the list doesn't name
    {"subject": SECRET},
    {"started": "nightly"},                                       # a value outside the fixed keys
    {"system": "freebsd"},
    {"sources": {"gmail": {"read": 1}, "teams": {"read": 1}}},
    {"brief": {"outcome": "sent", "cause": "unknown"}},
    {"exit": True},
    {"exit": "0"},
    *({f: t} for t in FREE_TEXT for f in ("version", "system_version", "at", "started")),
    *({"causes": [t]} for t in FREE_TEXT),
    *({"sources": {"gmail": {"failed": t}}} for t in FREE_TEXT),
    *({"sources": {t: {"read": 1}}} for t in FREE_TEXT),
    *({"ai": {"backend": "claude", "result": t}} for t in FREE_TEXT),
    *({"brief": {"outcome": "failed", "cause": t}} for t in FREE_TEXT),
    *({"exception": {"type": t}} for t in FREE_TEXT),
    *({"exception": {"type": "OSError", "at": t}} for t in FREE_TEXT),
    {"exception": {"type": "OSError", "message": SECRET}},
    {"update": "installed"},
    {"update": ["updated"]},
    {"update": {"result": "updated"}},
    {"update": None},
    *({"update": t} for t in FREE_TEXT),
])
def test_a_report_with_anything_outside_the_list_is_refused_and_nothing_of_it_is_kept_or_logged(
        tmp_path, day, caplog, change):
    caplog.set_level(logging.INFO, logger="brief-relay")
    status, body = post(make_relay(tmp_path, day), {**REPORT, **change})
    assert status == 400 and body == {"error": body["error"]}
    assert kept(tmp_path) == {}
    assert body["error"] == "the report has a field or a value the gateway doesn't take"
    assert [r.getMessage() for r in caplog.records] == [
        f"report key=ai-pilot-a bytes={len(json.dumps({**REPORT, **change}))} outcome=400 "
        f"the report has a field or a value the gateway doesn't take"]


@pytest.mark.parametrize("body", [b"not json", b"[]", b'"text"', json.dumps({}).encode(),
                                  json.dumps({"at": REPORT["at"]}).encode()])
def test_a_report_that_isnt_an_object_with_when_and_how_the_run_ended_is_refused(
        tmp_path, day, body):
    assert post(make_relay(tmp_path, day), body)[0] == 400
    assert kept(tmp_path) == {}


def test_a_report_over_the_size_limit_is_refused(tmp_path, day):
    raw = json.dumps({**REPORT, "pad": "x" * br.MAX_REPORT_BYTES}).encode()
    assert post(make_relay(tmp_path, day), raw)[0] == 413
    assert kept(tmp_path) == {}


# ── The gateway's list is the program's

def test_the_gateways_list_is_the_programs():
    assert set(br.REPORT_FIELDS) == set(last_run.FIELDS)
    assert br.REPORT_CAUSES == last_run.CAUSES
    assert br.REPORT_STARTED == last_run.STARTED
    assert br.REPORT_SYSTEMS == last_run.SYSTEMS
    assert br.REPORT_AI_RESULTS == last_run.AI_RESULTS
    assert br.REPORT_BACKENDS == tuple(last_run.BACKENDS)
    assert br.REPORT_UPDATES == last_run.UPDATES
    assert set(br.REPORT_SOURCES) == {s.name for s in ALL}
    for ours, theirs in [(br._R_VERSION, last_run._VERSION),
                         (br._R_SYSTEM_VERSION, last_run._SYSTEM_VERSION),
                         (br._R_TYPE, last_run._TYPE), (br._R_WHERE, last_run._WHERE)]:
        assert ours.pattern == theirs.pattern


def _values() -> list[object]:
    """Values of every shape a field may take, good and bad, for both lists to judge."""
    keys = [*last_run.CAUSES, *last_run.STARTED, *last_run.SYSTEMS, *last_run.AI_RESULTS,
            *last_run.BACKENDS, *last_run.UPDATES, "installed", "sent", "nothing-to-tell",
            "failed", "read", "outcome", "cause", "backend", "result", "type", "at", "message", "nightly", "teams",
            *(s.name for s in ALL), *FREE_TEXT]
    scalars: list[object] = [*keys, 0, -1, 42, True, None, 1.5, "0.12.0", "0.12.0+dev.3", "15.1",
                             "10.0.26100", "6.8.0-45-generic", "2026-11-14T19:00:00+00:00",
                             "2026-11-14T19:00:00", "OSError", "requests.ConnectionError",
                             "run.py:12", "actions/archive.py:42", "/run.py:1", [], {}]
    shapes: list[object] = [*scalars, *([k] for k in keys)]
    for a in keys:
        for b in [*keys, 3, "OSError", "run.py:12"]:
            shapes += [{a: b}, {"outcome": a, "cause": b}, {"backend": a, "result": b},
                       {"type": "OSError", a: b}, {a: {"read": 1}}, {"gmail": {a: b}}]
    return shapes


@pytest.mark.parametrize("field", sorted(last_run.FIELDS))
def test_each_field_takes_the_same_values_at_the_gateway_as_in_the_program(field):
    values = _values()
    differ = [v for v in values if br.REPORT_FIELDS[field](v) != last_run.FIELDS[field](v)]
    assert differ == []
    assert any(map(last_run.FIELDS[field], values))  # some values are taken, so it judges both ways


def test_a_report_the_program_writes_is_taken(tmp_path, day):
    report = last_run.Report(started="catch-up")
    report.read("gmail", 4)
    report.failed("wilma", "wilma-egress")
    report.failed("whatsapp", "some new failure")
    report.asked_ai("claude", "timed-out")
    report.brief_ended("failed", "nothing-read")
    try:
        raise OSError("can't open /Users/anna/state.json")
    except OSError as e:
        report.ended_on(e)
    written = last_run.only_listed({"at": "2026-11-14T19:00:00+00:00", "exit": 1, "seconds": 12,
                                    "peak_mb": 90, "causes": ["wilma-egress"],
                                    **last_run.about(), "update": "switched-back",
                                    **report.fields()})
    assert br.report_ok(written)
    assert post(make_relay(tmp_path, day), written)[0] == 200


# ── 30 days

def test_a_report_is_deleted_after_30_days(tmp_path, day):
    relay = make_relay(tmp_path, day)
    post(relay)
    day[0] = "2026-12-13"  # 29 days on
    assert relay.reports.prune() == []
    assert list(kept(tmp_path)) == ["2026-11-14/key-id-1.jsonl"]
    day[0] = "2026-12-14"  # 30 days on
    assert relay.reports.prune() == ["2026-11-14"]
    assert kept(tmp_path) == {}
    assert not (tmp_path / "reports" / "2026-11-14").exists()


def test_a_new_report_deletes_those_past_their_days(tmp_path, day):
    relay = make_relay(tmp_path, day)
    post(relay)
    day[0] = "2026-12-20"
    post(relay, key=None, rid=ID)
    assert list(kept(tmp_path)) == [f"2026-12-20/id-{ID}.jsonl"]


def test_a_prune_that_fails_doesnt_refuse_the_report(tmp_path, day, monkeypatch):
    relay = make_relay(tmp_path, day)

    def fails() -> list[str]:
        raise PermissionError

    monkeypatch.setattr(relay.reports, "prune", fails)
    assert post(relay)[0] == 200
    assert len(kept(tmp_path)) == 1


# ── The journal

def test_the_journal_says_a_report_came_and_from_which_key_or_id_never_its_body(
        tmp_path, day, caplog):
    caplog.set_level(logging.INFO, logger="brief-relay")
    relay = make_relay(tmp_path, day)
    post(relay)
    post(relay, key=None, rid=ID)
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("report ")]
    assert lines[0].startswith("report key=ai-pilot-a bytes=") and lines[0].endswith("outcome=taken")
    assert lines[1].startswith(f"report id={ID} bytes=") and lines[1].endswith("outcome=taken")
    for value in ("wilma-egress", "gateway", "15.1", KEY, CALLER):
        assert value not in caplog.text


# ── HTTP

def test_over_http_a_report_is_posted_with_its_key_or_its_id(tmp_path, day):
    server = br.ThreadingHTTPServer(("127.0.0.1", 0), br.handler(make_relay(tmp_path, day)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(headers: dict[str, str]) -> tuple[int, dict]:
        request = urllib.request.Request(base + "/v1/report", data=json.dumps(REPORT).encode(),
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
        for _ in range(br.REPORTS_PER_CALLER):
            call({"x-report-id": ID, "x-forwarded-for": "198.51.100.1, 203.0.113.9"})
        assert call({"x-report-id": ID, "x-forwarded-for": "203.0.113.9"})[0] == 429
    finally:
        server.shutdown()
        server.server_close()
    assert sorted(kept(tmp_path)) == ["2026-11-14/id-" + ID + ".jsonl", "2026-11-14/key-id-1.jsonl"]
