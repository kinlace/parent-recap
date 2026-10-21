"""The in-memory adapter of the llm package (ADR 0023), which tests run the program on in place of a
real provider. It needs no harness: it fakes nothing but itself."""
from __future__ import annotations

import pytest

from family_brief import llm
from family_brief.llm.memory import Call, Memory


def test_it_answers_every_call_with_its_reply_and_records_each():
    backend: llm.Backend = Memory({"ok": True})

    first = backend.run("prompt 1", "system", timeout=60, budget=0)
    second = backend.run("prompt 2", "system", timeout=30, budget=10)

    assert first.data == second.data == {"ok": True}
    assert first.text == '{"ok": true}'
    assert backend.calls == [Call("prompt 1", "system", 60, 0), Call("prompt 2", "system", 30, 10)]


def test_a_list_gives_one_reply_per_call_and_runs_out():
    backend = Memory(['{"n": 1}', {"n": 2}])

    assert [backend.run("p", "s", 60, 0).data for _ in range(2)] == [{"n": 1}, {"n": 2}]
    with pytest.raises(AssertionError, match="more model calls than replies"):
        backend.run("p", "s", 60, 0)


def test_a_reply_is_parsed_as_a_real_backends_is():
    reply = Memory('```json\n{"digest": "Swimming",}\n```').run("p", "s", 60, 0)

    assert reply.data == {"digest": "Swimming"} and reply.repaired


def test_an_exception_is_raised_and_a_function_answers_from_the_prompt():
    backend = Memory([RuntimeError("at capacity"), lambda prompt: {"echo": prompt}])

    with pytest.raises(RuntimeError, match="at capacity"):
        backend.run("p", "s", 60, 0)
    assert backend.run("hello", "s", 60, 0).data == {"echo": "hello"}


def test_doctor_finds_what_it_is_told_to():
    finding = llm.Finding("fail", "claude", "signed out")

    assert Memory(findings=[finding]).check() == [finding]
    assert Memory().check() == []
