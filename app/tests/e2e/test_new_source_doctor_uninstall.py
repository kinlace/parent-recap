"""A Source added to the registry and nowhere else (ADR 0022): `doctor` checks it and uninstall
lists and removes what setup made for it (#408)."""
from __future__ import annotations

import shutil
import sys
from functools import partial
from pathlib import Path

import pytest
from test_doctor import nothing_loaded  # noqa: F401  (a fixture)
from test_uninstall import set_up, uninstall

from family_brief import ops, sources
from family_brief import uninstall as uninstalling
from family_brief.collectors.base import Collected

TOKEN = "schoolapp-token"
NOTE = "SchoolApp keeps its own sign-in until you remove it at https://schoolapp.example/account."


def program_folder(programs: list[Path]) -> Path:
    """Where SchoolApp's own program would be cached: beside Parent Recap's."""
    return programs[0].parent / "schoolapp"


def school_app(monkeypatch: pytest.MonkeyPatch, macos_only: bool = False) -> None:
    """Registers a fifth Source, SchoolApp, with a sign-in token in the secret store and a cached
    program of its own."""
    def check(cfg, add, config) -> None:
        add(ops.OK, "schoolapp", "signed in, 3 notices this week")

    def survey(s: uninstalling.Survey, programs: list[Path]) -> None:
        folder = program_folder(programs)
        if folder.is_dir():
            s.items.append(uninstalling.Item("SchoolApp's program", "~/ParentRecap/schoolapp",
                                             partial(shutil.rmtree, folder), path=folder))

    record = sources.Source("schoolapp", {"en": "SchoolApp", "fi": "SchoolApp", "zh": "SchoolApp"},
                            lambda cfg: True, skippable=True, connect_rank=9, macos_only=macos_only,
                            collect=lambda cfg, state, kid_terms: Collected(), check=check,
                            secrets=lambda cfg: [TOKEN], survey=survey,
                            notes=lambda cfg, pythons: [NOTE])
    monkeypatch.setattr(sources, "ALL", (*sources.ALL, record))


def test_doctor_checks_a_source_added_to_the_registry(harness, nothing_loaded, monkeypatch,  # noqa: F811
                                                      capsys):
    school_app(monkeypatch)

    harness.cli("doctor", "--skip-llm")

    lines = capsys.readouterr().out.splitlines()
    line = f"{ops.OK} SchoolApp: signed in, 3 notices this week"
    assert line in lines
    # With the Sources, after Gmail's, and before the checks that aren't a Source's.
    at = lines.index(line)
    assert [i for i, l in enumerate(lines) if "Gmail:" in l][0] < at
    assert [i for i, l in enumerate(lines) if "Calendar:" in l][0] > at


def test_off_macos_doctor_leaves_out_a_macos_only_source_added_to_the_registry(
        harness, nothing_loaded, monkeypatch, capsys):  # noqa: F811
    school_app(monkeypatch, macos_only=True)
    monkeypatch.setattr(sys, "platform", "win32")

    harness.cli("doctor", "--skip-llm")

    assert "SchoolApp:" not in capsys.readouterr().out


def test_uninstall_lists_and_removes_what_setup_made_for_a_source_added_to_the_registry(
        harness, monkeypatch, capsys):
    set_up(harness, monkeypatch)
    school_app(monkeypatch)
    folder = program_folder([harness.home / "ParentRecap" / "app"])
    folder.mkdir()
    (folder / "schoolapp.js").write_text("")
    harness.secrets[TOKEN] = "secret"

    assert uninstall(harness) == 0

    out = capsys.readouterr().out
    assert TOKEN in out and "SchoolApp's program: ~/ParentRecap/schoolapp" in out and NOTE in out

    assert uninstall(harness, "--confirm", "--remove-archive") == 0

    assert not folder.exists() and TOKEN not in harness.secrets
