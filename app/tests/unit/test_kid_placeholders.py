"""Each Kid as Kid A, Kid B and back (kid_placeholders), straight from the text in and out."""
from __future__ import annotations

import pytest

from family_brief.config import Kid
from family_brief.kid_placeholders import KidPlaceholders

KIDS = KidPlaceholders([Kid(name="Mia Virtanen", everyday_name="Mimi", aliases=["小米", " "]),
                        Kid(name="Pekka"), Kid(name="Elias"), Kid(name="Bo")])


@pytest.mark.parametrize("text, masked", [
    ("Mia Virtanen tulee", "Kid A tulee"),          # the full name, not its first name and a stray surname
    ("Mia ja Mimi", "Kid A ja Kid A"),              # the first name of a full name and the everyday name
    ("MIA", "Kid A"),
    ("小米明天去远足", "Kid A明天去远足"),             # a Chinese alias needs no word boundary
    ("Mialle", "Kid A:lle"),                        # a Finnish case ending goes after a colon
    ("Mimikin", "Kid A:kin"),
    ("Pekan kaveri", "Kid B:n kaveri"),             # Pekka's stem changes before the ending
    ("Pekkakaan ei", "Kid B:kaan ei"),
    ("Eliaksen reppu", "Kid C:n reppu"),
    ("Bolle", "Kid D:lle"),                         # a name too short for stems still takes an ending
    ("Miami", "Miami"),                             # inside a longer word
    ("Kid A", "Kid A"),
])
def test_mask_puts_each_kids_names_as_their_placeholder(text, masked):
    assert KIDS.mask(text) == masked


@pytest.mark.parametrize("text, restored", [
    ("Kid A", "Mimi"),                              # the name the Brief calls them by
    ("Kid A:lle", "Mimille"),                       # an ending joins a name ending in a vowel
    ("Kid B:lle", "Pekalle"),                       # with the stem Pekka takes
    ("Kid A:kin", "Mimikin"),
    ("Kid C:n", "Elias:n"),                         # and keeps its colon after a consonant
    ("Kid E", "Kid E"),                             # no fifth Kid
    ("Kid Ab", "Kid Ab"),                           # not a placeholder
])
def test_restore_puts_back_the_name_the_brief_uses(text, restored):
    assert KIDS.restore(text) == restored


def test_a_term_two_kids_share_stands_for_the_first():
    kids = KidPlaceholders([Kid(name="Mia"), Kid(name="mia", aliases=["Leo"])])

    assert kids.mask("mia ja Leo") == "Kid A ja Kid B"


def test_no_kids_leave_the_text_as_it_is():
    assert KidPlaceholders([]).mask("Mia") == "Mia"


def test_only_26_kids_get_a_letter():
    assert len(KidPlaceholders([Kid(name=f"Kid{i}") for i in range(30)]).kids) == 26
    assert KidPlaceholders.placeholder(25) == "Kid Z"


def test_mask_all_and_restore_all_walk_lists_and_dicts():
    masked = KIDS.mask_all({"a": ["Mia", 1, None], "b": "Pekka", "name": "Mia"}, keep=["name"])

    assert masked == {"a": ["Kid A", 1, None], "b": "Kid B", "name": "Mia"}
    assert KIDS.restore_all(["Kid A", {"x": "Kid B"}, 3]) == ["Mimi", {"x": "Pekka"}, 3]
