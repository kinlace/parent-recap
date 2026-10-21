"""What the llm package reads from a backend's output (ADR 0023). The model's reply is the most
fragile input in the pipeline, so its parsing tiers get direct tests: strict JSON, fence stripping,
balanced-object extraction, then json-repair. Then the Claude CLI's envelope, and what says the
model is busy."""
from __future__ import annotations

import json
import subprocess

import pytest

from family_brief.llm import claude, parse, retry

REPLY = {"per_kid": [{"kid": "Mia", "notices": ["带 {雨衣}"], "action_items": []}],
         "calendar_events": [], "message_digest_cn": "**Mia**\n- 周四远足"}
TEXT = json.dumps(REPLY, ensure_ascii=False)


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    # A total failure saves the raw reply under ~/ParentRecap/logs.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows' Path.home()
    return tmp_path


@pytest.mark.parametrize("reply", [
    pytest.param(TEXT, id="clean"),
    pytest.param(f"  \n{TEXT}\n\n", id="surrounding-whitespace"),
    pytest.param(json.dumps(REPLY, ensure_ascii=False, indent=2), id="pretty-printed"),
    pytest.param(f"```json\n{TEXT}\n```", id="json-fence"),
    pytest.param(f"```\n{TEXT}\n```", id="bare-fence"),
    pytest.param(f"好的，这是今天的整理：\n```json\n{TEXT}\n```\n如需调整请告诉我。", id="fence-with-prose"),
    pytest.param(f"Here is the JSON: {TEXT} Let me know if you need more.", id="prose-no-fence"),
    pytest.param(f'Draft: {{"x": 1}}\nFinal: {TEXT}', id="largest-object-wins"),
])
def test_parses_to_the_object(reply):
    assert parse.model_json(reply) == (REPLY, False)


def test_allows_raw_control_characters_in_strings():
    # Real tab and newline inside the string, which strict JSON rejects.
    assert parse.model_json('{"message_digest_cn": "周四\t远足\n带雨衣"}') \
        == ({"message_digest_cn": "周四\t远足\n带雨衣"}, False)


@pytest.mark.parametrize("reply, expected", [
    pytest.param('{"per_kid": [], "calendar_events": [],}', {"per_kid": [], "calendar_events": []},
                 id="trailing-comma"),
    pytest.param('{"message_digest_cn": "老师说 "带雨衣" 就行", "per_kid": []}',
                 {"message_digest_cn": '老师说 "带雨衣" 就行', "per_kid": []}, id="unescaped-inner-quotes"),
    pytest.param("{'per_kid': [], 'message_digest_cn': '好'}", {"per_kid": [], "message_digest_cn": "好"},
                 id="single-quotes"),
    pytest.param('```json\n{"per_kid": [], "message_digest_cn": "被截断', {"per_kid": [], "message_digest_cn": "被截断"},
                 id="truncated-in-fence"),
    pytest.param('结果如下 {"per_kid": [{"kid": "Leo", "notices": ["a",]}]} 完',
                 {"per_kid": [{"kid": "Leo", "notices": ["a"]}]}, id="repair-with-prose"),
    pytest.param(f"好的，以下是结果：\n```json\n{TEXT[:-1]},}}\n```\n希望有帮助。", REPLY, id="every-tier"),
])
def test_repairs_near_json(reply, expected):
    # Flagged as repaired: a cut-off reply is repaired too, and may have lost what came after the cut.
    assert parse.model_json(reply) == (expected, True)


def test_reply_with_no_json_fails_and_is_saved(tmp_home):
    with pytest.raises(ValueError):
        parse.model_json("  Sorry, I can't help with that.\n")
    assert (tmp_home / "ParentRecap" / "logs" / "summarize_failed.txt").read_text() \
        == "Sorry, I can't help with that."


@pytest.mark.parametrize("reply", [
    pytest.param('[{"kid": "Mia", "notices": ["周四远足"]}]', id="list-of-kid-entries"),
    pytest.param(f"```json\n[{TEXT}]\n```", id="fenced-list-around-the-object"),
    pytest.param('"好的，已整理"', id="string"),
    pytest.param("42", id="number"),
    pytest.param("null", id="null"),
])
def test_valid_json_that_is_not_an_object_fails_and_is_saved(reply, tmp_home):
    # Digging an object out of well-formed JSON of the wrong shape would give a wrong Brief.
    with pytest.raises(ValueError):
        parse.model_json(reply)
    assert (tmp_home / "ParentRecap" / "logs" / "summarize_failed.txt").exists()


def test_claude_envelope_with_text_result():
    envelope = json.dumps({"type": "result", "is_error": False, "result": f"```json\n{TEXT}\n```"},
                          ensure_ascii=False)
    assert parse.cli_response(envelope) == (REPLY, False)


def test_claude_envelope_with_object_result():
    assert parse.cli_response(json.dumps({"result": REPLY})) == (REPLY, False)


def test_claude_envelope_that_is_not_json_fails():
    with pytest.raises(json.JSONDecodeError):
        parse.cli_response("Error: not logged in")


def test_claude_envelope_with_list_result_fails():
    with pytest.raises(ValueError):
        parse.cli_response(json.dumps({"result": [REPLY]}))


# ── What else a backend's output says

def test_claude_reply_gives_the_text_the_tokens_and_the_model_that_wrote_most():
    envelope = {"result": "{}", "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": None},
                "modelUsage": {"haiku": {"outputTokens": 1}, "opus": {"outputTokens": 4}, "sonnet": None}}

    assert claude.reply_of(json.dumps(envelope)) == \
        ("{}", {"input": 10, "cache_write": 0, "cached_input": 0, "output": 5}, "opus")


def test_claude_reply_without_usage_has_none_and_an_object_result_is_its_json():
    assert claude.reply_of(json.dumps({"result": {"a": "好"}})) == ('{"a": "好"}', None, None)


@pytest.mark.parametrize("returncode, stdout, stderr, prompt, busy", [
    pytest.param(0, "overloaded", "", None, None, id="succeeded"),
    pytest.param(1, "", "Overloaded, try again", None, "Overloaded", id="overloaded"),
    pytest.param(1, "", "error: 503 Service Unavailable", None, "error: 503", id="status"),
    pytest.param(1, "Rate limit hit; usage limit reached", "", None, None, id="wont-pass"),
    pytest.param(1, "", "prompt: we are at capacity tonight", "we are at capacity tonight", None,
                 id="the-prompt-echoed"),
    pytest.param(1, "", "Something else", None, None, id="other-failure"),
])
def test_busy_reason_says_when_waiting_may_help(returncode, stdout, stderr, prompt, busy):
    proc = subprocess.CompletedProcess([], returncode, stdout, stderr)

    assert retry.busy_reason(proc, prompt) == busy
