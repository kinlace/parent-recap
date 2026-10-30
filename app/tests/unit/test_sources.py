"""The Source registry (sources, ADR 0022): each record complete, and which Sources a Household uses."""
from __future__ import annotations

import pytest

from family_brief import sources
from family_brief.brief_text import TEXT
from family_brief.config import Config
from fake_system import LINUX, MACOS, WINDOWS


@pytest.mark.parametrize("source", sources.ALL, ids=lambda s: s.name)
def test_every_source_has_a_label_in_each_reviewed_language(source):
    assert set(source.labels) == set(TEXT)
    assert all(label.strip() for label in source.labels.values())


def test_every_source_has_a_name_and_a_place_on_the_connect_list_of_its_own():
    assert len({s.name for s in sources.ALL}) == len(sources.ALL)
    assert len({s.connect_rank for s in sources.ALL}) == len(sources.ALL)


def test_a_language_without_reviewed_text_gets_the_english_label():
    assert sources.WHATSAPP.label("sv") == sources.WHATSAPP.label("en") == "WhatsApp"


def test_the_connect_list_puts_wilma_first_and_myclub_last():
    assert [s.name for s in sources.in_connect_order()] == ["wilma", "gmail", "whatsapp", "myclub"]


@pytest.mark.parametrize("profile", [MACOS, WINDOWS], ids=["macos", "windows"])
def test_a_family_s_own_computer_reads_every_source(system, profile):
    system.profile = profile

    assert sources.available() == list(sources.ALL)
    assert sources.unavailable() == []


def test_the_hosted_version_never_reads_whatsapp(system):
    system.profile = LINUX
    cfg = Config(kids=[_KID], wilma={"enabled": True})

    assert [s.name for s in sources.available()] == ["gmail", "myclub", "wilma"]
    assert sources.unavailable() == [sources.WHATSAPP]
    assert [s.name for s in sources.in_connect_order()] == ["wilma", "gmail", "myclub"]
    assert [s.name for s in sources.used(cfg)] == ["wilma"]
    assert cfg.other_sources() == ["wilma"]


def test_a_source_is_found_by_its_name_and_any_other_name_is_its_own_label():
    assert sources.named("wilma") is sources.WILMA
    assert sources.named("calendar") is None
    assert sources.label_of("myclub") == "MyClub"
    assert sources.label_of("calendar") == "calendar"


_KID = {"name": "Mia Virtanen"}


@pytest.mark.parametrize("config, used", [
    ({}, ["gmail"]),  # nothing connected yet: Gmail, which sends the Brief
    ({"gmail": {"allowlist_domains": ["espoo.fi"]}}, ["gmail"]),
    ({"wilma": {"enabled": True}}, ["wilma"]),  # Gmail with no sender ticked only sends
    ({"gmail": {"allowlist_senders": ["a@b.fi"]}, "wilma": {"enabled": True}}, ["gmail", "wilma"]),
    ({"whatsapp": {"enabled": True}}, ["gmail"]),  # no group ticked
    # Nothing reads WhatsApp until the Linked Device's collector lands (ADR 0027).
    ({"whatsapp": {"enabled": True, "chats": [{"name": "3B"}]}}, ["gmail"]),
    ({"kids": [{**_KID, "myclub_ical_url": "https://myclub.fi/x.ics"}], "wilma": {"enabled": True},
      "whatsapp": {"enabled": True, "chats": [{"name": "3B"}]}}, ["myclub", "wilma"]),
], ids=["nothing", "gmail", "wilma", "gmail-and-wilma", "whatsapp-no-groups", "whatsapp", "all-but-gmail"])
def test_the_household_uses_the_connected_sources_in_run_order(config, used):
    cfg = Config(**{"kids": [_KID], **config})

    assert [s.name for s in sources.used(cfg)] == used
    assert cfg.default_sources() == used
    assert cfg.other_sources() == [s for s in used if s != "gmail"]
