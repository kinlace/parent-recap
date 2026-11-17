"""`admin problems` (ops/gateway/admin, #925): the Problem Reports parents sent from Parent Recap
Check, one line each, without their words, by the Gateway Key's name or the report id; and with an
id, that one whole, to be read on the server. The reports are kept by the relay's own `Problems`,
under tmp_path, and Bifrost's admin API is faked as in test_admin."""
from __future__ import annotations

import sys

import pytest
from test_admin import AI, SEND, FakeBifrost, Server, admin

relay = sys.modules["brief_relay"]

REPORT_ID = "0123456789abcdef0123456789abcdef"
SECRET = "mia@example.com"


def problem(**fields) -> dict:
    return {"version": "0.21.0", "system": "macos", "system_version": "15.1",
            "doctor": f"✅ Config file: Brief goes to {SECRET}\n❌ Wilma: failed\n❌ Last run: x\n",
            "logs": {"run": ["21:00:04 ERROR family_brief | The Brief wasn't sent \x1b[31m"],
                     "update": [], "install": []},
            **fields}


def keep(server: Server, who: str, received: str, p: dict, pid: str) -> None:
    state = server.paths.relay_state
    relay.Problems(state / "problems", state / "problem-counts.json", today=lambda: received[:10],
                   now=lambda: received, new_id=lambda: pid).keep(who, p)


def problems(server: Server, *args: str) -> int:
    try:
        return admin.main(["problems", *args], paths=server.paths, is_root=lambda: True,
                          fetch=server.bifrost)
    except SystemExit as e:
        if isinstance(e.code, str):
            print(e.code, file=sys.stderr)
            return 1
        return e.code or 0


@pytest.fixture
def server(tmp_path) -> Server:
    s = Server(tmp_path)
    s.bifrost = FakeBifrost([AI, SEND])
    keep(s, "key-id-1", "2026-11-14T19:06:00Z", problem(note=f"No Brief, write to {SECRET}"),
         "p-0000000a")
    keep(s, f"id-{REPORT_ID}", "2026-11-15T08:00:00Z",
         problem(system="windows", system_version="10.0.26100"), "p-0000000b")
    return s


def test_problems_lists_each_without_its_words(server, capsys):
    assert problems(server) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[1:] == [
        "p-0000000a  2026-11-14 21:06  ai-pilot-a  0.21.0  macos 15.1  2 checks failed  "
        "a line from the parent",
        f"p-0000000b  2026-11-15 10:00  id={REPORT_ID}  0.21.0  windows 10.0.26100  "
        "2 checks failed  no line from the parent",
    ]
    assert SECRET not in out and "Wilma" not in out


def test_an_id_shows_that_one_whole_with_nothing_a_terminal_acts_on(server, capsys):
    assert problems(server, "p-0000000a") == 0
    out = capsys.readouterr().out
    assert out.startswith("Problem Report p-0000000a: 2026-11-14 21:06  ai-pilot-a  0.21.0")
    assert "read it here, never paste it" in out
    assert f"What went wrong, in their words: No Brief, write to {SECRET}" in out
    assert "  ❌ Wilma: failed" in out
    assert "  21:00:04 ERROR family_brief | The Brief wasn't sent" in out
    assert "\x1b" not in out
    assert "The update log: no lines" in out


def test_an_id_not_kept_is_refused(server, capsys):
    assert problems(server, "p-ffffffff") == 1
    assert "no Problem Report p-ffffffff kept" in capsys.readouterr().err


@pytest.mark.parametrize("args", [["../x"], ["p-0000000a", "more"]])
def test_anything_but_an_id_is_refused(server, capsys, args):
    assert problems(server, *args) == 2
    assert "usage: sudo admin problems [ID]" in capsys.readouterr().err


def test_none_kept_says_so(tmp_path, capsys):
    s = Server(tmp_path)
    s.bifrost = FakeBifrost([AI])
    assert problems(s) == 0
    assert "No Problem Report kept" in capsys.readouterr().out
