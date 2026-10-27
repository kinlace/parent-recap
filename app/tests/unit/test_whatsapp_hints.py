"""The hint on each WhatsApp group that looks like it's about a Kid, as the check page shows it:
by the Kid's names, class, school and clubs, each as a word of its own."""
from __future__ import annotations

from family_brief.config import Kid
from family_brief.setup_whatsapp import with_hints

# As Wilma spells Mia's name, and as setup asks for Leo's.
KIDS = [Kid(name="Mia Virtanen", aliases=["米娅"], class_name="3B", school="Kilo School",
            activities=["football"]),
        Kid(name="Leo", aliases=["小狮"], class_name="1A", school="Kilo School",
            activities=["piano"])]


def hints(names: list[str], kids: list[Kid]) -> dict[str, dict | None]:
    found = [{"name": n, "last": "2026-09-25", "archived": False} for n in names]
    return {c["name"]: c.get("hint") for c in with_hints(found, kids)}


def test_groups_that_look_like_they_are_about_a_kid_carry_a_hint():
    assert hints(["3B parents",              # Mia's class
                  "Kilo School families",    # both Kids' school
                  "FC Kilo football U10",    # Mia's club
                  "Leo piano",               # Leo's name and club
                  "Mia's birthday 🎈",       # Mia's first name
                  "米娅 生日会",              # Mia's alias
                  "Leonardo fans",           # not Leo
                  "13B reunion",             # not 3B
                  "Neighbours"], KIDS) == {
        "3B parents": {"kids": ["Mia Virtanen"], "matched": ["3B"]},
        "Kilo School families": {"kids": ["Mia Virtanen", "Leo"], "matched": ["Kilo School"]},
        "FC Kilo football U10": {"kids": ["Mia Virtanen"], "matched": ["football"]},
        "Leo piano": {"kids": ["Leo"], "matched": ["Leo", "piano"]},
        "Mia's birthday 🎈": {"kids": ["Mia Virtanen"], "matched": ["Mia"]},
        "米娅 生日会": {"kids": ["Mia Virtanen"], "matched": ["米娅"]},
        "Leonardo fans": None,
        "13B reunion": None,
        "Neighbours": None,
    }


def test_kids_without_a_school_or_class_still_get_hints_from_their_names():
    kids = [Kid(name="Mia Virtanen"), Kid(name="Leo")]  # setup didn't ask for either

    assert hints(["Mia's birthday 🎈", "Leo piano", "3B parents"], kids) == {
        "Mia's birthday 🎈": {"kids": ["Mia Virtanen"], "matched": ["Mia"]},
        "Leo piano": {"kids": ["Leo"], "matched": ["Leo"]},
        "3B parents": None,
    }


def test_a_group_keeps_what_was_found_about_it_beside_its_hint():
    [group] = with_hints([{"name": "3B parents", "last": "2026-09-25", "archived": True}], KIDS)

    assert group == {"name": "3B parents", "last": "2026-09-25", "archived": True,
                     "hint": {"kids": ["Mia Virtanen"], "matched": ["3B"]}}


def test_without_kids_yet_groups_have_no_hints():
    assert hints(["3B parents", "Leo piano"], []) == {"3B parents": None, "Leo piano": None}
