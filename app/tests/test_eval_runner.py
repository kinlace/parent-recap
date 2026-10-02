"""The eval runner end to end, with only the model process faked: cases are loaded from a folder,
each night is summarized through the real prompt and citation code, and a scorecard is printed,
saved and compared with the previous run. Also checks the bundled cases stay well-formed."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from family_brief.eval import __main__ as runner
from family_brief.eval.cases import BUNDLED, load_cases

HOUSEHOLD = {
    "timezone": "Europe/Helsinki",
    "kids": [{"name": "Aino", "aliases": ["小艾"]}, {"name": "Eero", "aliases": ["Eetu"]}],
    "whatsapp": {"chats": [{"name": "Salibandy Eagles", "kid": "Eero", "label": "floorball"}]},
}

CASE = {
    "covers": ["kid-attribution"],
    "now": "2026-10-04T21:00:00+03:00",
    "messages": [{"id": "wa-1", "source": "whatsapp", "at": "2026-10-04T18:00:00+03:00",
                  "chat": "Salibandy Eagles", "sender": "Coach",
                  "body": "Maksakaa kausimaksu 120 € tiistaihin mennessä."}],
    "expect": {"action_items": [{"kid": "Eero", "what": ["kausimaksu"], "by": "2026-10-06"}]},
}

GOOD = {"per_kid": [{"kid": "Eero", "notices": [],
                     "action_items": [{"what": "付 kausimaksu 120 €", "by": "2026-10-06", "refs": ["wa-1"]}]}],
        "calendar_events": [], "message_digest": "**Eero**\n- 周二前付 kausimaksu"}


@pytest.fixture
def case_dir(tmp_path: Path) -> Path:
    d = tmp_path / "cases"
    d.mkdir()
    (d / "household.yaml").write_text(yaml.safe_dump(HOUSEHOLD, allow_unicode=True))
    (d / "floorball-fee.yaml").write_text(yaml.safe_dump(CASE, allow_unicode=True))
    return d


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Replies to every `claude` call with `replies[0]` (a dict, or raw text), recording prompts."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    state = {"replies": [GOOD], "prompts": []}

    def run(cmd, *_a, **k):
        prog = Path(cmd[0]).name
        if prog == "security":
            return subprocess.CompletedProcess(cmd, 44, "", "item not found")
        reply = state["replies"][0]
        text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        if prog == "codex":
            state["prompts"].append(k["input"])
            Path(cmd[cmd.index("-o") + 1]).write_text(text)
            return subprocess.CompletedProcess(cmd, 0, "", "tokens used\n2,000\n")
        assert prog == "claude", cmd
        state["prompts"].append(k["input"])
        envelope = {"type": "result", "result": text,
                    "usage": {"input_tokens": 900, "cache_read_input_tokens": 50, "output_tokens": 50}}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(envelope), "")

    monkeypatch.setattr(subprocess, "run", run)
    return state


def test_scores_a_case_folder_and_saves_the_run(case_dir, model, tmp_path, capsys):
    out = tmp_path / "results"
    assert runner.main(["--cases", str(case_dir), "--out", str(out), "--language", "zh"]) == 0

    payload = json.loads(model["prompts"][0][model["prompts"][0].index("\n{") + 1:])
    assert payload["now"] == "2026-10-04T21:00:00+03:00"
    assert payload["date_reference"]["today"] == "2026-10-04 周日"
    assert payload["messages"][0]["kid_hint"] == "Eero"  # from the chat map, as the collector does

    card = capsys.readouterr().out
    assert "action_recall" in card and "1.0" in card
    [saved] = list(out.glob("*-claude-zh.json"))
    data = json.loads(saved.read_text())
    assert data["metrics"]["action_recall"] == 1.0
    assert data["metrics"]["citations_verified"] == 1.0
    assert data["metrics"]["tokens_per_night"] == 1000
    night = data["runs"][0]["cases"]["floorball-fee"]
    assert night["valid_json"] is True and night["summary"]["per_kid"][0]["kid"] == "Eero"


def test_compares_with_the_previous_run_and_reports_the_spread(case_dir, model, tmp_path, capsys):
    out = tmp_path / "results"
    runner.main(["--cases", str(case_dir), "--out", str(out), "--language", "zh"])
    capsys.readouterr()

    wrong = json.loads(json.dumps(GOOD))
    wrong["per_kid"][0]["action_items"][0]["by"] = "2026-10-13"
    model["replies"] = [wrong]
    runner.main(["--cases", str(case_dir), "--out", str(out), "--repeat", "2", "--language", "zh"])
    card = capsys.readouterr().out
    row = next(line for line in card.splitlines() if line.startswith("due_date_ok"))
    assert "0.0" in row and "-1.0" in row  # this run, and the change from the previous one
    data = json.loads(sorted(out.glob("*.json"))[-1].read_text())
    assert len(data["runs"]) == 2 and data["spread"]["due_date_ok"] == [0.0, 0.0]


def test_per_case_notes_name_every_run_that_was_not_clean(case_dir, model, tmp_path, capsys, monkeypatch):
    wrong = json.loads(json.dumps(GOOD))
    wrong["per_kid"][0]["action_items"][0]["by"] = "2026-10-13"
    replies = iter([wrong, GOOD])  # run 1 slips, run 2 is clean
    run_case = runner.run_case

    def next_reply(case, judge):
        model["replies"] = [next(replies)]
        return run_case(case, judge)

    monkeypatch.setattr(runner, "run_case", next_reply)
    runner.main(["--cases", str(case_dir), "--out", str(tmp_path / "r"), "--repeat", "2", "--language", "en"])
    row = next(line for line in capsys.readouterr().out.splitlines() if "floorball-fee" in line)
    assert "clean 1/2" in row and "run 1: 1 wrong due date" in row and "run 2" not in row


def test_one_command_scores_both_backends(case_dir, model, tmp_path, capsys):
    out = tmp_path / "results"
    codex = tmp_path / "codex"
    codex.write_text("")
    codex.chmod(0o755)  # find_codex only takes an executable file
    runner.main(["--cases", str(case_dir), "--out", str(out), "--backend", "claude,codex",
                 "--codex-path", str(codex), "--language", "en"])
    card = capsys.readouterr().out
    assert "eval · claude" in card and "eval · codex" in card
    saved = json.loads(next(out.glob("*-codex-en.json")).read_text())
    assert saved["metrics"]["action_recall"] == 1.0 and saved["metrics"]["tokens_per_night"] is None
    codex_card = card[card.index("eval · codex"):]
    row = next(line for line in codex_card.splitlines() if line.startswith("tokens_per_night"))
    assert "—" in row  # no Codex count, rather than a wrong one


def test_a_reply_that_is_not_json_scores_as_invalid(case_dir, model, tmp_path):
    model["replies"] = ["对不起，我今天没法整理。"]
    runner.main(["--cases", str(case_dir), "--out", str(tmp_path / "r"), "--language", "en"])
    data = json.loads(next((tmp_path / "r").glob("*.json")).read_text())
    night = data["runs"][0]["cases"]["floorball-fee"]
    assert night["valid_json"] is False and night["error"]
    assert data["metrics"]["action_recall"] == 0.0


def test_bundled_cases_cover_the_hard_parts():
    cases = load_cases(BUNDLED)
    assert len(cases) >= 20
    covered = {c for case in cases for c in case.covers}
    assert {"finnish-dates", "dst", "kid-alias", "whatsapp-group", "cross-night", "re-reminder",
            "calendar-disagrees", "noise", "empty-night", "prompt-injection", "wilma-events"} <= covered
    for case in cases:
        assert all(m.timestamp <= case.now for m in case.messages), case.name
        assert all(day["date"] < case.now.date().isoformat() for day in case.earlier_briefs), case.name


def test_every_bundled_case_runs_and_an_empty_brief_only_passes_the_quiet_nights(model, tmp_path, capsys):
    model["replies"] = [{"per_kid": [], "calendar_events": [], "message_digest": ""}]
    assert runner.main(["--out", str(tmp_path / "r")]) == 0
    print(capsys.readouterr().out)  # the scorecard, for `pytest -s`
    for saved in (tmp_path / "r").glob("*.json"):  # one per language
        data = json.loads(saved.read_text())
        clean = {name for name, night in data["runs"][0]["cases"].items() if night["clean"]}
        assert clean == {"empty-night", "noise-ads-and-chit-chat", "dst-existing-event-in-utc",
                         "re-reminder-already-done"}, data["language"]


def test_bundled_cases_score_an_english_brief(model, tmp_path):
    model["replies"] = [{
        "per_kid": [{"kid": "Aino", "notices": [{"text": "Chinese class starts at 2 pm this Saturday",
                                                 "refs": ["wa-zh-1008"]}],
                     "action_items": [{"what": "Bring last week's homework book", "by": "2026-10-10",
                                       "refs": ["wa-zh-1008"]}]}],
        "calendar_events": [{"kid": "Aino", "title": "Chinese class", "start": "2026-10-10T14:00:00",
                             "refs": ["wa-zh-1008"]}],
        "message_digest": "**Aino**\n- Chinese class at 2 pm on Saturday; bring the homework book"}]

    runner.main(["--out", str(tmp_path / "r"), "--only", "kid-alias-chinese", "--language", "en"])

    data = json.loads(next((tmp_path / "r").glob("*-en.json")).read_text())
    assert data["runs"][0]["cases"]["kid-alias-chinese"]["clean"]


def test_every_reviewed_language_is_scored_and_each_compares_with_its_own_past(case_dir, model, tmp_path, capsys):
    out = tmp_path / "results"
    out.mkdir()
    # A run saved before the Brief had a language: it scored Chinese Briefs.
    (out / "20260901-000000-000000-claude.json").write_text(json.dumps(
        {"backend": "claude", "model": None, "cases_dir": str(case_dir.resolve()), "prompt_sha": "old",
         "metrics": {"action_recall": 0.5}, "spread": {}, "runs": []}))

    assert runner.main(["--cases", str(case_dir), "--out", str(out)]) == 0

    en_prompt, zh_prompt, fi_prompt = model["prompts"]
    assert json.loads(en_prompt[en_prompt.index("\n{") + 1:])["date_reference"]["today"] == "2026-10-04 Sun"
    assert json.loads(zh_prompt[zh_prompt.index("\n{") + 1:])["date_reference"]["today"] == "2026-10-04 周日"
    assert json.loads(fi_prompt[fi_prompt.index("\n{") + 1:])["date_reference"]["today"] == "2026-10-04 su"
    card = capsys.readouterr().out
    en_card, zh_card = card[card.index("· en ·"):card.index("· zh ·")], card[card.index("· zh ·"):card.index("· fi ·")]
    assert "compared with" not in en_card
    assert "compared with 20260901-000000-000000-claude.json" in zh_card
    assert "compared with" not in card[card.index("· fi ·"):]
    assert {json.loads(p.read_text()).get("language") for p in out.glob("*.json")} == {None, "en", "zh", "fi"}


def test_a_malformed_case_names_the_file(case_dir):
    (case_dir / "broken.yaml").write_text(yaml.safe_dump(
        {**CASE, "expect": {"action_items": [{"what": "kausimaksu", "by": "tiistai"}]}}))
    with pytest.raises(ValueError, match="broken.yaml"):
        load_cases(case_dir)
