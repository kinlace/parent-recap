"""`parent-recap demo`, end to end (#1029): the made-up Household's week goes through the evening
run's steps and its Brief is sent to the address given, in the language given. No Source is read,
no Source's secret is taken from the store, and the Household's config, state and archive stay as
they were. Assertions are on what leaves the program: the email, the model's prompt, what it
prints and the files in the family's home."""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import timedelta
from html import escape
from pathlib import Path

import pytest
import yaml

from conftest import NOW
from family_brief import __main__ as cli, gateway, sources as registry
from family_brief.brief_text import TEXT

FRIEND = "friend@example.com"
# The held-back email in demo/week.yaml, and words of its body that must never reach the model.
HELD_SENDER, HELD_SUBJECT, HELD_WORDS = "Erityisopettaja Hanna Lehto", "Ellan lukemisen tukeminen", "pedagoginen arvio"
TRIP = (NOW + timedelta(days=5)).date().isoformat()


def demo_reply(_prompt: str = "") -> dict:
    """The model's reply to the demo week: a Digest, Notices and Action Items for each Kid, a
    digest of each WhatsApp group and a trip for the calendar."""
    return {
        "per_kid": [
            {"kid": "Ella",
             "notices": [{"text": "Maths test on times tables", "refs": ["demo-wilma-exam"]}],
             "action_items": [{"what": "Answer the teacher about the Planetarium trip", "by": TRIP,
                               "who": "Either", "refs": ["demo-wilma-answer"]}]},
            {"kid": "Otto",
             "notices": [{"text": "Forest trip: rubber boots", "refs": ["demo-evaka-1"]}],
             "action_items": []},
        ],
        "calendar_events": [{"kid": "Ella", "title": "Planetarium trip", "start": f"{TRIP}T09:00:00",
                             "end": f"{TRIP}T13:00:00", "refs": ["demo-wilma-answer"]}],
        "message_digest": "**Ella**\n- Maths test on times tables\n\n**Otto**\n- Forest trip: rubber boots",
        "group_digests": [{"group": "2B vanhemmat", "text": "- A class evening is being planned"},
                          {"group": "Satakieli families", "text": "- Magazines wanted for craft week"}],
    }


@pytest.fixture
def secrets_read(harness, monkeypatch) -> list[str]:
    """The name of each secret the program reads from the store, in order."""
    read: list[str] = []
    real = harness.secrets.get

    def get(name: str, default: str | None = None) -> str | None:
        read.append(name)
        return real(name, default)
    monkeypatch.setattr(harness.secrets, "get", get)
    return read


def household_files(home: Path) -> dict[str, bytes]:
    """Every file in the Household's config folder and archive, by path."""
    return {str(p.relative_to(home)): p.read_bytes()
            for folder in (home / ".family", home / "ParentRecap") if folder.exists()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def demo(harness, capsys, *args: str) -> tuple[int, dict]:
    """Runs `parent-recap demo` with `args`, giving its exit code and the JSON line it prints."""
    code = harness.cli("demo", *args)
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


@pytest.mark.parametrize("language", ["en", "fi", "zh"])
def test_the_demo_sends_a_complete_brief_in_the_language_given(harness, capsys, language):
    harness.store_gateway_key()
    harness.model_reply = demo_reply

    code, printed = demo(harness, capsys, "--to", FRIEND, "--language", language)

    assert code == 0 and printed == {"demo": "sent", "to": FRIEND}
    [email] = harness.sent
    t = TEXT[language]
    assert email.to == [FRIEND]
    assert FRIEND not in email.text  # who set nothing up isn't named as the one to write to
    assert email.text.splitlines()[2] == t.demo  # first, under the header
    assert escape(t.demo) in email.html.split("</h2>", 1)[1].split("<h4>", 1)[0]
    assert "Maths test on times tables" in email.text  # the Digest
    for group in ("2B vanhemmat", "Satakieli families"):
        assert f"\n💬 {group}\n" in email.text
    assert f"\n{t.held_back}\n" in email.text
    assert f"• Gmail · {HELD_SENDER} · {HELD_SUBJECT} · " in email.text
    assert f"\n{t.action_items}\n• Answer the teacher about the Planetarium trip" in email.text
    assert f"\n{t.new_events_line}\n" in email.text
    [(name, ics, mime)] = email.attachments
    assert mime == "text/calendar"
    for title in ("Planetarium trip", "Salibandyharjoitukset", "Pianotunti", "Koe: Matematiikka",
                  "Satakielen perhekahvit"):
        assert title in ics.decode()
    # Each Source's news is on the coverage line, as if it had been read
    coverage = next(line for line in email.text.splitlines() if "Eepos" in line)
    for label in ("Wilma", "Gmail", "WhatsApp", "MyClub", "eVaka"):
        assert label in coverage


def test_the_held_back_message_never_reaches_the_model_and_the_kids_are_masked(harness, capsys):
    harness.store_gateway_key()
    harness.model_reply = demo_reply

    assert demo(harness, capsys, "--to", FRIEND, "--language", "en")[0] == 0

    [call] = harness.model_calls
    sent = call.prompt + call.system_prompt
    assert HELD_WORDS not in sent and HELD_SUBJECT not in sent
    assert "Ella" not in sent and "Otto" not in sent  # the AI filter's Kid placeholders
    ids = {m["external_id"] for m in harness.model_payload()["messages"]}
    assert "demo-wilma-answer" in ids and "7300000000000000103" not in ids


def test_the_demo_reads_no_source_and_leaves_the_household_as_it_was(harness, capsys, secrets_read):
    harness.set_up_sources()  # the Household's Sources and their secrets, all set up
    reached: list[str] = []
    for source in registry.ALL:
        harness.sources[source.name] = lambda name=source.name: reached.append(name) or []
    harness.state_path.parent.mkdir(parents=True, exist_ok=True)
    harness.state_path.write_text('{"last_run_at": "2026-09-26T18:00:00+00:00"}')
    harness.write_archive("2026-09-26", {"messages": []})
    harness.config["email"]["to"] = ["parent@example.com"]
    # The config file as harness.cli writes it, so writing it again changes nothing.
    (harness.home / ".family" / "config.yaml").write_text(
        yaml.safe_dump(harness.config, allow_unicode=True, sort_keys=False))
    before = household_files(harness.home)
    source_secrets = set(harness.secrets) - {gateway.ACCOUNT}
    harness.model_reply = demo_reply

    code, printed = demo(harness, capsys, "--to", FRIEND, "--language", "fi")

    assert code == 0 and printed["demo"] == "sent"
    assert reached == []
    assert source_secrets and not source_secrets & set(secrets_read)
    assert household_files(harness.home) == before  # no state, archive, Run Report or language file
    assert harness.reports == []


def test_without_a_config_the_demo_runs_with_claude(harness, capsys, monkeypatch):
    """Before setup there is no config: the demo uses Claude Code, which this computer has."""
    harness.store_gateway_key()
    harness.model_reply = demo_reply
    monkeypatch.setattr(sys, "argv", ["parent-recap", "demo", "--to", FRIEND, "--language", "en"])

    code = cli.main()

    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0 and printed == {"demo": "sent", "to": FRIEND}
    assert harness.model.served_by == ["claude"]
    assert not (harness.home / ".family" / "config.yaml").exists()


@pytest.mark.parametrize("key", [None, "bound to the Household"])
def test_a_demo_the_gateway_wont_send_is_saved_to_a_file_instead(harness, capsys, monkeypatch, tmp_path, key):
    """With no Gateway Key, as before setup, or a send-only one bound to the Household's
    Recipients, the Brief can't be mailed: it is saved as a page to open instead, and the demo
    says why."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    harness.gateway_key = None
    if key:
        harness.secrets[gateway.ACCOUNT] = "sk-bf-made-on-request-0"
        harness.outbox.made_keys["sk-bf-made-on-request-0"] = [{"address": "parent@example.com", "language": "en"}]
        harness.confirm("parent@example.com")
    harness.model_reply = demo_reply

    code, printed = demo(harness, capsys, "--to", FRIEND, "--language", "en")

    assert code == 1 and printed["demo"] == "saved"
    assert FRIEND in printed["why"] and "Gateway Key" in printed["why"]
    page = Path(printed["file"])
    assert page.parent == tmp_path and page.suffix == ".html"
    assert escape(TEXT["en"].demo) in page.read_text(encoding="utf-8")
    assert harness.sent == []


def test_a_demo_the_model_couldnt_write_is_not_sent(harness, capsys):
    harness.store_gateway_key()
    harness.model_error = "Claude Code isn't signed in"

    code, printed = demo(harness, capsys, "--to", FRIEND, "--language", "en")

    assert code == 1 and printed["demo"] == "failed"
    assert "signed in" in printed["why"]
    assert harness.sent == []


def test_the_demo_asks_for_an_address(harness, capsys):
    with pytest.raises(SystemExit):
        harness.cli("demo", "--to", "not-an-address", "--language", "en")
