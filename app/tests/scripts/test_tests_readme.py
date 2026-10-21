"""tests/README.md stays a map a reader can scan (#428): every test file is in its layer's table,
and no section runs past 40 lines. What a test checks goes in its docstring, not there."""
from __future__ import annotations

import re
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
README = (TESTS / "README.md").read_text(encoding="utf-8")


def test_every_test_file_is_in_the_readme():
    listed = set(re.findall(r"`(test_\w+\.py)`", README))
    # One row lists the Sources' setup files together: `test_setup_gmail.py`, `_wilma`, ...
    for row in re.findall(r"^\| `test_setup_gmail\.py`.*$", README, re.M):
        listed |= {f"test_setup_{name}.py" for name in re.findall(r"`_(\w+)`", row)}
    files = {p.name for p in TESTS.glob("*/test_*.py")}
    assert files - listed == set()


def test_no_section_is_over_40_lines():
    sections = re.split(r"^(?=#+ )", README, flags=re.M)
    long = {s.splitlines()[0]: len(s.splitlines()) for s in sections if len(s.splitlines()) > 40}
    assert long == {}
