"""The model's reply is the most fragile input in the pipeline, so its parsing tiers get direct tests:
strict JSON, fence stripping, balanced-object extraction, then json-repair."""
from __future__ import annotations

import json

import pytest

from family_brief.summarize import _parse_cli_response, _parse_model_json

REPLY = {"per_kid": [{"kid": "Mia", "notices": ["带 {雨衣}"], "action_items": []}],
         "calendar_events": [], "message_digest_cn": "**Mia**\n- 周四远足"}
TEXT = json.dumps(REPLY, ensure_ascii=False)


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    # A total failure saves the raw reply under ~/FamilyBrief/logs.
    monkeypatch.setenv("HOME", str(tmp_path))
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
    assert _parse_model_json(reply) == (REPLY, False)


def test_allows_raw_control_characters_in_strings():
    # Real tab and newline inside the string, which strict JSON rejects.
    assert _parse_model_json('{"message_digest_cn": "周四\t远足\n带雨衣"}') \
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
])
def test_repairs_near_json(reply, expected):
    # Flagged as repaired: a cut-off reply is repaired too, and may have lost what came after the cut.
    assert _parse_model_json(reply) == (expected, True)


def test_reply_with_no_json_fails_and_is_saved(tmp_home):
    with pytest.raises(ValueError):
        _parse_model_json("  Sorry, I can't help with that.\n")
    assert (tmp_home / "FamilyBrief" / "logs" / "summarize_failed.txt").read_text() \
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
        _parse_model_json(reply)
    assert (tmp_home / "FamilyBrief" / "logs" / "summarize_failed.txt").exists()


def test_claude_envelope_with_text_result():
    envelope = json.dumps({"type": "result", "is_error": False, "result": f"```json\n{TEXT}\n```"},
                          ensure_ascii=False)
    assert _parse_cli_response(envelope) == (REPLY, False)


def test_claude_envelope_with_object_result():
    assert _parse_cli_response(json.dumps({"result": REPLY})) == (REPLY, False)


def test_claude_envelope_that_is_not_json_fails():
    with pytest.raises(json.JSONDecodeError):
        _parse_cli_response("Error: not logged in")


def test_claude_envelope_with_list_result_fails():
    with pytest.raises(ValueError):
        _parse_cli_response(json.dumps({"result": [REPLY]}))
