"""The `links` interface against this system's own links (#991): symlinks on macOS and Linux,
junctions on Windows, which need neither an administrator nor Developer Mode. Real folders in a
temporary folder."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from family_brief.system import links


@pytest.fixture
def versions(tmp_path: Path) -> Path:
    v = tmp_path / "ParentRecap" / "versions"
    for name in ("1.0.aaa", "1.1.bbb"):
        (v / name).mkdir(parents=True)
        (v / name / "VERSION").write_text(name[:3] + "\n")
    return v


def test_a_link_is_made_read_and_told_from_a_folder(versions):
    app = versions.parent / "app"

    links.point(app, Path("versions") / "1.0.aaa")

    assert links.is_link(app) and not links.is_link(versions / "1.0.aaa")
    assert links.target(app).name == "1.0.aaa"
    assert (app / "VERSION").read_text() == "1.0\n"
    assert links.target(versions / "1.0.aaa") is None


def test_pointing_it_again_replaces_the_link_and_leaves_both_folders(versions):
    app = versions.parent / "app"
    links.point(app, Path("versions") / "1.0.aaa")

    links.point(app, Path("versions") / "1.1.bbb")

    assert (app / "VERSION").read_text() == "1.1\n"
    assert (versions / "1.0.aaa" / "VERSION").is_file()
    assert sorted(p.name for p in versions.parent.iterdir()) == ["app", "versions"]


def test_a_link_beside_its_target_names_it_by_its_own_folder(versions):
    previous = versions / "previous"

    links.point(previous, Path("1.0.aaa"))

    assert (previous / "VERSION").read_text() == "1.0\n"


def test_removing_a_link_leaves_what_is_in_the_folder_it_points_at(versions):
    app = versions.parent / "app"
    links.point(app, Path("versions") / "1.0.aaa")

    links.remove(app)

    assert not app.exists() and not links.is_link(app)
    assert (versions / "1.0.aaa" / "VERSION").read_text() == "1.0\n"


def test_a_link_whose_folder_is_gone_is_still_a_link(versions):
    app = versions.parent / "app"
    links.point(app, Path("versions") / "1.0.aaa")
    (versions / "1.0.aaa" / "VERSION").unlink()
    (versions / "1.0.aaa").rmdir()

    assert links.is_link(app) and links.target(app).name == "1.0.aaa"
    links.remove(app)
    assert not links.is_link(app)


def test_only_windows_has_a_moment_without_the_link():
    assert links.one_step() is (sys.platform != "win32")
