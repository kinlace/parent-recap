"""`parent-recap demo` (#1029): one Brief made from the made-up Household's week (family_brief/demo/)
through the evening run's own steps, sent to the address given in the language given. Assertions are
only on what leaves the system: the email, what the model is given, what is read from the secrets
store and the Sources, and the files in the family's home."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest
import yaml

from conftest import SAYS_SOMETHING, program
from family_brief import __main__ as cli, gateway
from family_brief.brief_text import TEXT

TO = "reviewer@example.com"

# The demo's week as the model writes it up in Finnish: every section of a Brief has something.
FINNISH_REPLY = {
    "per_kid": [
        {"kid": "Ella",
         "notices": [{"text": "Koulupäivä päättyy huomenna jo klo 12.00", "refs": ["message:4102"]},
                     {"text": "Luokkakuvaus ensi viikolla aamupäivällä", "refs": ["9101"]}],
         "action_items": [
             {"what": "Täytä metsäretken retkilupa Wilmassa", "by": "2026-09-29", "who": "Kumpi tahansa",
              "refs": ["message:4101"]},
             {"what": "Vastaa opettajalle vanhempainvartin ajasta", "by": "2026-09-29", "who": "Kumpi tahansa",
              "refs": ["message:4103"]},
             {"what": "Pakkaa heijastinliivi retkelle", "by": "2026-10-01", "who": "Kumpi tahansa",
              "refs": ["1:7003"]}]},
        {"kid": "Onni",
         "notices": [{"text": "Metsäpäivä tiistaina, aamupala jo klo 8.00", "refs": ["demo-evaka-5001"]}],
         "action_items": [{"what": "Täydennä varavaatelokero", "by": "2026-09-29", "who": "Kumpi tahansa",
                           "refs": ["demo-evaka-5001"]}]},
    ],
    "calendar_events": [
        {"kid": "Ella", "title": "Metsäretki", "start": "2026-10-01T09:00:00", "end": "2026-10-01T13:00:00",
         "description": "Mukaan eväät ja heijastinliivi", "refs": ["message:4101"]},
    ],
    "group_digests": [{"group": "2B vanhemmat", "text": "- Luokkatoimikunta ehdottaa keilaretkeä ennen joulua"}],
    "message_digest": "**Ella**\n- Huomenna lyhyt koulupäivä\n- Retkilupa ja vanhempainvartti odottavat\n\n"
                      "**Onni**\n- Metsäpäivä tiistaina",
}


def demo(h, *args: str, config: bool = True) -> int:
    """`parent-recap demo` with `args`, on a computer set up as `h.config` says, or, without
    `config`, one where setup hasn't run."""
    argv = ["parent-recap"]
    if config:
        path = h.home / ".family" / "config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(h.config, allow_unicode=True, sort_keys=False))
        argv += ["-c", str(path)]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", [*argv, "demo", *args])
        return cli.main()


def files_in(home: Path) -> dict[str, bytes]:
    return {str(p.relative_to(home)): p.read_bytes() for p in sorted(home.rglob("*")) if p.is_file()}


@pytest.fixture
def secrets_read(harness, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Each name read from the secrets store, in order."""
    read: list[str] = []
    get = harness.secrets.get

    def recorded(name: str, default: str | None = None) -> str | None:
        read.append(name)
        return get(name, default)
    monkeypatch.setattr(harness.secrets, "get", recorded)
    return read


def test_the_demo_sends_a_whole_brief_of_the_made_up_household_in_the_language_asked(harness):
    harness.store_gateway_key()
    harness.model_reply = FINNISH_REPLY

    assert demo(harness, "--to", TO, "--language", "fi") == 0

    [email] = harness.sent
    assert email.to == [TO]
    assert email.subject.startswith("🧪 ")  # not taken for the family's own Brief in an inbox
    # It says first that it's a demo, above everything else.
    assert email.text.index(TEXT["fi"].demo) < email.text.index("Ella")
    assert TEXT["fi"].demo in email.html
    # Every section of a Brief: the Digest (which the Notices reach the Brief through), the Action
    # Items, a Group Digest, the Held-back Message and the new events, with the .ics.
    assert "Huomenna lyhyt koulupäivä" in email.text
    assert TEXT["fi"].action_items in email.text and "Täydennä varavaatelokero" in email.text
    assert "💬 2B vanhemmat\n- Luokkatoimikunta ehdottaa keilaretkeä" in email.text
    assert TEXT["fi"].held_back in email.text
    assert "• Gmail · Mikko Saarinen · Ellan lukemisen tuki" in email.text
    assert TEXT["fi"].new_events_line in email.text
    for event in ("Metsäretki", "Pianotunti", "Koe: Ympäristöoppi – Vesi ja sää", "FC Kivikumpu P2018"):
        assert event in email.text
    name, ics, _ = email.attachment(".ics")
    assert b"Pianotunti" in ics and "Metsäretki".encode() in ics
    # The model wrote it in Finnish from the week's Messages, all but the one held back.
    [call] = harness.model_calls
    ids = {m["external_id"] for m in harness.model_payload()["messages"]}
    assert {"message:4101", "message:4103", "1:7004", "demo-evaka-5001"} <= ids and "9102" not in ids
    assert "Ellan lukemisen tuki" not in call.prompt and "tehostettu tuki" not in call.prompt
    assert "Finnish" in call.system_prompt
    # The demo's Brief comes from Parent Recap's own address, which nobody reads, but there is no
    # Household to write to instead.
    assert TO not in email.text.rsplit("\n\n", 1)[1]


@pytest.mark.parametrize("language", ["en", "fi", "zh"])
def test_the_demo_says_at_the_top_that_it_is_a_demo_in_each_language(harness, language):
    harness.store_gateway_key()
    harness.model_reply = SAYS_SOMETHING

    assert demo(harness, "--to", TO, "--language", language) == 0

    [email] = harness.sent
    assert email.text.index(TEXT[language].demo) < email.text.index("Something new tonight")
    assert TEXT[language].language_name in harness.model_calls[0].system_prompt


def test_the_demo_reads_no_source_and_no_secret_but_the_gateway_key_and_leaves_the_household_as_it_was(
        harness, secrets_read):
    harness.store_gateway_key()
    harness.sources = {name: AssertionError(f"{name} was read") for name in harness.source_fakes}
    harness.model_reply = FINNISH_REPLY
    demo(harness, "--to", TO, "--language", "en")  # writes the config, as setup left it
    harness.sent.clear()
    secrets_read.clear()
    before = files_in(harness.home)

    assert demo(harness, "--to", TO, "--language", "en") == 0

    assert len(harness.sent) == 1
    assert harness.lookback_hours == {}  # no Source's collector ran
    assert harness.commands == []        # nor any Source's own program, such as the wilma CLI
    assert secrets_read == [gateway.ACCOUNT]
    # Nothing written: no state, no archive, no Run Report, the config as it was.
    assert files_in(harness.home) == before
    assert set(before) == {".family/config.yaml"}
    assert harness.reports == []


def test_before_setup_the_demo_uses_the_ai_the_computer_has(harness):
    program(harness.home / ".local" / "bin", "claude")
    harness.store_gateway_key()  # a key the Parent Recap team gave, stored with `setup gateway`
    harness.model_reply = SAYS_SOMETHING

    assert demo(harness, "--to", TO, "--language", "en", config=False) == 0

    assert harness.model.served_by == ["claude"]
    [email] = harness.sent
    assert email.to == [TO] and TEXT["en"].demo in email.text
    assert files_in(harness.home) == {".local/bin/claude": b"#!/bin/sh\n"}


def test_with_no_gateway_key_to_send_it_the_demo_opens_its_brief_on_the_computer(harness, capsys, tmp_path,
                                                                                 monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))  # where the page is saved, outside the home
    program(harness.home / ".local" / "bin", "claude")
    harness.gateway_key = None
    harness.model_reply = SAYS_SOMETHING

    assert demo(harness, "--to", TO, "--language", "zh", config=False) == 0

    assert harness.sent == []
    [opened] = harness.desktop.opened
    page = Path(unquote(urlparse(opened).path))
    assert page.suffix == ".html" and not page.is_relative_to(harness.home)
    assert TEXT["zh"].demo in page.read_text(encoding="utf-8")
    assert str(page) in capsys.readouterr().out
    assert files_in(harness.home) == {".local/bin/claude": b"#!/bin/sh\n"}


def test_when_the_model_fails_the_demo_sends_nothing_and_says_why(harness, capsys):
    harness.store_gateway_key()
    harness.model_error = "the model is down"

    assert demo(harness, "--to", TO, "--language", "en") == 1

    assert harness.sent == [] and harness.desktop.opened == []
    assert "the model is down" in capsys.readouterr().out


def test_the_demo_asks_for_the_address_and_takes_only_the_reviewed_languages(harness, capsys):
    with pytest.raises(SystemExit):
        demo(harness, "--language", "en")
    with pytest.raises(SystemExit):
        demo(harness, "--to", TO, "--language", "sv")
    assert harness.sent == [] and harness.model_calls == []
