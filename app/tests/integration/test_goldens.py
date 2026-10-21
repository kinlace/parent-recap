"""The golden files' own check (goldens.py), which `--update-goldens[=PATTERN]` switches to
rewriting, against a golden folder in the temporary directory."""
from __future__ import annotations

import pytest

from goldens import Goldens


@pytest.fixture
def folder(tmp_path):
    for name, text in {"normal_night.en.txt": "a\nb\nc\n", "normal_night.zh.txt": "甲\n",
                       "held_back.en.txt": "x\n"}.items():
        (tmp_path / name).write_text(text)
    return tmp_path


def test_without_update_a_golden_that_differs_fails_and_nothing_is_written(folder):
    goldens = Goldens(folder, update=None)

    goldens.check("normal_night.en.txt", "a\nb\nc\n")
    with pytest.raises(AssertionError, match="normal_night.en.txt differs"):
        goldens.check("normal_night.en.txt", "a\n")
    with pytest.raises(AssertionError, match="missing golden new.txt"):
        goldens.check("new.txt", "a\n")

    assert (folder / "normal_night.en.txt").read_text() == "a\nb\nc\n"
    assert not (folder / "new.txt").exists()


def test_a_pattern_rewrites_only_the_goldens_of_that_night(folder):
    goldens = Goldens(folder, update="normal_night")

    goldens.check("normal_night.en.txt", "a\nB\nc\nd\n")
    goldens.check("normal_night.zh.txt", "乙\n")
    with pytest.raises(AssertionError, match="held_back.en.txt differs"):
        goldens.check("held_back.en.txt", "y\n")

    assert (folder / "normal_night.en.txt").read_text() == "a\nB\nc\nd\n"
    assert (folder / "normal_night.zh.txt").read_text() == "乙\n"
    assert (folder / "held_back.en.txt").read_text() == "x\n"


def test_a_pattern_is_a_glob_or_a_name_up_to_a_dot():
    def matched(pattern: str) -> list[str]:
        names = ["normal_night.en.txt", "normal_night.en.ics", "normal_night_2.en.txt",
                 "codex.masked.model.txt", "held_back.fi.html"]
        return [n for n in names if Goldens(None, update=pattern).matches(n)]

    assert matched("normal_night") == ["normal_night.en.txt", "normal_night.en.ics"]
    assert matched("normal_night.en") == ["normal_night.en.txt", "normal_night.en.ics"]
    assert matched("*.ics") == ["normal_night.en.ics"]
    assert matched("*masked*") == ["codex.masked.model.txt"]
    assert matched("held_back.fi.html") == ["held_back.fi.html"]
    assert len(matched("*")) == 5  # --update-goldens with no pattern


def test_each_golden_rewritten_gets_one_line_saying_what_changed(folder):
    goldens = Goldens(folder, update="*")

    goldens.check("normal_night.en.txt", "a\nB\nc\nd\n")
    goldens.check("normal_night.zh.txt", "甲\n")  # unchanged: not rewritten, not listed
    goldens.check("new.en.txt", "1\n2\n")
    goldens.check("held_back.en.txt", "")

    assert goldens.summary() == [
        "held_back.en.txt: +0 -1 lines",
        "new.en.txt: new, 2 lines",
        "normal_night.en.txt: +2 -1 lines",
    ]


def test_a_golden_checked_twice_in_one_run_is_listed_once(folder):
    goldens = Goldens(folder, update="*")

    goldens.check("normal_night.zh.txt", "乙\n")
    goldens.check("normal_night.zh.txt", "乙\n")

    assert goldens.summary() == ["normal_night.zh.txt: +1 -1 lines"]
