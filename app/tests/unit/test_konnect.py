"""The Konnect Source's pure parts (ADR 0049): a portal's address, a child matched to a Kid by
name, a message's HTML as text, the Messages page's lists as Messages and the Agenda's planned
times as events. All names and messages are made up."""
from __future__ import annotations

import pytest

from conftest import konnect_child, konnect_conversation, konnect_news, konnect_planned
from family_brief.collectors import konnect
from family_brief.config import Config, KonnectSignin


@pytest.mark.parametrize("given, portal", [
    ("mijnbso", "mijnbso"), ("MijnBSO", "mijnbso"), ("mijnbso.ouderportaal.nl", "mijnbso"),
    ("https://mijnbso.ouderportaal.nl/parent/#/messages", "mijnbso"), (" mijn-bso.ouderportaal.nl/ ", "mijn-bso"),
    ("", None), ("mijnbso.example.nl", None), ("mijn bso", None), ("-bso", None),
])
def test_a_portal_is_the_part_of_its_address_before_ouderportaal_nl(given, portal):
    assert konnect.portal_of(given) == portal


@pytest.mark.parametrize("given, website", [
    ("www.example-kinderopvang.nl", True), ("https://example-kinderopvang.nl/ouders", True),
    ("mijnbso", False), ("mijnbso.ouderportaal.nl", False), ("", False),
])
def test_an_address_elsewhere_than_ouderportaal_is_a_website(given, website):
    assert konnect.is_website(given) is website


def test_the_config_keeps_a_portal_by_its_part_before_ouderportaal_nl():
    assert KonnectSignin(portal="https://mijnbso.ouderportaal.nl/parent/", username="p").portal == "mijnbso"
    with pytest.raises(ValueError, match="not a Konnect portal"):
        KonnectSignin(portal="mijn bso", username="p")


def test_a_sign_in_s_secret_is_named_after_its_portal_and_username_never_with_them():
    name = konnect.secret_name("mijnbso", "Parent@Example.nl")

    assert name.startswith("konnect-signin-") and "example" not in name.lower()
    assert name == konnect.secret_name("mijnbso", " parent@example.nl")
    assert name != konnect.secret_name("anderebso", "parent@example.nl")


KIDS = Config(kids=[{"name": "Mia Test", "aliases": ["Mimi"]}, {"name": "Leo"}]).kids


@pytest.mark.parametrize("first, last, kid", [
    ("Mia", "Test", "Mia Test"),   # the full name
    ("Mimi", "Anders", "Mia Test"),  # an alias
    ("Leo", "Anders", "Leo"),      # the first name is the Kid's
    ("Olli", "Test", None),        # no Kid
])
def test_a_child_is_the_kid_their_name_is(first, last, kid):
    assert konnect.kid_of(konnect.Child("1", first, last), KIDS) == kid


def test_a_message_s_html_is_its_text_a_line_each():
    html = "<p>Beste ouders,</p>\n<p>\n  <br/>\n</p><p>Neem &amp; laarzen\n  mee! </p><ul><li>Jas</li></ul>"

    assert konnect.text_of(html) == "Beste ouders,\nNeem & laarzen mee!\nJas"


def test_a_conversation_about_both_children_is_for_the_household():
    both = konnect_conversation("Ouderavond", "<p>Donderdag ouderavond.</p>", "2026-09-27T16:00:00+02:00",
                                children=(konnect_child("a", "Mia", "Test"), konnect_child("b", "Leo", "Test")))

    [m] = konnect.messages_of([both], [], [], "mijnbso", {"a": "Mia Test", "b": "Leo"})

    assert m.kid_hint is None and m.body == "Donderdag ouderavond."


def test_an_item_without_its_time_is_left_out_and_one_without_its_id_is_a_change():
    news = konnect_news("Sluitingsdag", "Dicht.", "2026-09-27T09:00:00+02:00")

    assert konnect.messages_of([], [{**news, "publishDate": None}], [], "mijnbso", {}) == []
    with pytest.raises(konnect.Changed, match="news without its id"):
        konnect.messages_of([], [{**news, "id": None}], [], "mijnbso", {})


def test_a_planned_time_without_its_end_or_of_a_child_with_no_kid_is_handled():
    part = konnect_planned("a", "2026-09-28T14:00:00+02:00", "2026-09-28T18:00:00+02:00")
    day = {"children": [{"child": {"id": "a"}, "combinedPlanningParts": [{"planningParts": [
        {k: v for k, v in part.items() if k not in ("portal", "child")}, {**part, "endTime": None}]}]}]}

    [event] = konnect.events([day], "mijnbso", {})

    assert event.kid is None and event.title == "Opvang"
