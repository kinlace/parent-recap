"""The release command builds the zip families install from, straight from a tag, and writes
release.json's version and parts (ADR 0051) from the paths changed since the release before."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, env=GIT_ENV, check=True,
                          capture_output=True, text=True).stdout


def set_versions(repo: Path, plugin: str, package: str) -> None:
    manifest = repo / ".claude-plugin" / "plugin.json"
    manifest.write_text(re.sub(r'"version": *"[^"]*"', f'"version": "{plugin}"', manifest.read_text()))
    pyproject = repo / "app" / "pyproject.toml"
    pyproject.write_text(re.sub(r'(?m)^version = "[^"]*"', f'version = "{package}"', pyproject.read_text()))


def tag(repo: Path, name: str, parts: bool = True) -> None:
    """Commits the tree and tags it, as the release steps do: with release.json's version and
    parts written first, unless `parts` is False."""
    if parts:
        written = release(repo, "--parts")
        assert written.returncode == 0, written.stderr
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", name, "--allow-empty")
    git(repo, "tag", name)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repo holding the current working tree (tracked + new, minus ignored files)."""
    dest = tmp_path / "repo"
    files = git(ROOT, "ls-files", "-co", "--exclude-standard", "-z").split("\0")
    for rel in filter(None, files):
        src = ROOT / rel
        if src.is_file():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / rel)
    git(dest, "init", "-q")
    git(dest, "add", "-A")
    git(dest, "commit", "-qm", "init")
    return dest


def release(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "scripts/release.py", *args], cwd=repo,
                          capture_output=True, text=True)


def zip_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as z:
        return z.namelist()


@pytest.mark.macos
def test_builds_versioned_zip_with_the_plugin_root_under_one_folder(repo):
    set_versions(repo, "1.2.0", "1.2.0")
    tag(repo, "v1.2.0")

    result = release(repo, "v1.2.0")

    assert result.returncode == 0, result.stderr
    out = repo / "dist" / "parent-recap-1.2.0.zip"
    assert str(out) in result.stdout
    names = zip_names(out)
    assert all(n.startswith("parent-recap/") for n in names)
    for expected in (
        "parent-recap/install.sh",
        "parent-recap/.claude-plugin/plugin.json",
        "parent-recap/.claude-plugin/marketplace.json",
        "parent-recap/app/pyproject.toml",
        "parent-recap/app/src/family_brief/__main__.py",
        # The Baileys tree's pin, which the installers' `npm ci` installs from.
        "parent-recap/app/src/family_brief/baileys/package.json",
        "parent-recap/app/src/family_brief/baileys/package-lock.json",
        # The Linked Device's own program, which the pinned Node runs on that tree.
        "parent-recap/app/src/family_brief/baileys/linked_device.mjs",
        # The setup page's picture of MyClub's page, which isn't text.
        "parent-recap/app/src/family_brief/page/myclub-uusi.webp",
        "parent-recap/skills/setup/SKILL.md",
        "parent-recap/skills/manage/SKILL.md",
    ):
        assert expected in names
    with zipfile.ZipFile(out) as z:
        assert z.getinfo("parent-recap/install.sh").external_attr >> 16 & 0o111, "install.sh lost its exec bit"


def test_zip_holds_the_tagged_content_not_local_changes(repo):
    set_versions(repo, "1.2.0", "1.2.0")
    tag(repo, "v1.2.0")
    (repo / "install.sh").write_text("uncommitted edit\n")
    (repo / "skills" / "stray.md").write_text("untracked\n")
    git(repo, "commit", "-qam", "after the tag")

    assert release(repo, "v1.2.0").returncode == 0

    with zipfile.ZipFile(repo / "dist" / "parent-recap-1.2.0.zip") as z:
        assert z.read("parent-recap/install.sh") != b"uncommitted edit\n"
        assert "parent-recap/skills/stray.md" not in z.namelist()


BANNED = re.compile(r"(^|/)(\.git|\.venv|__pycache__|\.scratch|\.agents|\.claude|\.github|dist)(/|$)"
                    r"|\.pyc$|(^|/)\.env$|^app/tests/|^scripts/|^(CLAUDE|CONTRIBUTING)\.md$|^skills-lock\.json$"
                    # operators hand the shared Google client to pilot households; it never ships (ADR 0003)
                    r"|(^|/)(google_oauth_client|calendar_credentials)\.json$")


def test_no_dev_files_in_the_zip(repo):
    set_versions(repo, "1.2.0", "1.2.0")
    tag(repo, "v1.2.0")

    assert release(repo, "v1.2.0").returncode == 0

    names = [n.removeprefix("parent-recap/") for n in zip_names(repo / "dist" / "parent-recap-1.2.0.zip")]
    assert [n for n in names if BANNED.search(n)] == []


@pytest.mark.parametrize("junk", [
    "app/.venv/bin/python", "app/src/family_brief/__pycache__/x.pyc", ".scratch/notes.md", "app/.env",
    "app/assets/google_oauth_client.json", "calendar_credentials.json",
])
def test_refuses_to_ship_local_artefacts_committed_by_mistake(repo, junk):
    set_versions(repo, "1.2.0", "1.2.0")
    (repo / junk).parent.mkdir(parents=True, exist_ok=True)
    (repo / junk).write_text("x")
    git(repo, "add", "-Af")
    tag(repo, "v1.2.0")

    result = release(repo, "v1.2.0")

    assert result.returncode != 0
    assert junk in result.stderr
    assert not (repo / "dist").exists()


@pytest.mark.parametrize(("tag_name", "plugin", "package"), [
    ("v1.3.0", "1.2.0", "1.2.0"),  # tag ahead of both
    ("v1.2.0", "1.2.0", "1.2.1"),  # package drifted
    ("v1.2.0", "1.2.1", "1.2.0"),  # manifest drifted
])
def test_refuses_to_build_when_versions_disagree(repo, tag_name, plugin, package):
    set_versions(repo, plugin, package)
    tag(repo, tag_name)

    result = release(repo, tag_name)

    assert result.returncode != 0
    for shown in (tag_name, f"plugin.json: {plugin}", f"pyproject.toml: {package}"):
        assert shown in result.stderr
    assert not (repo / "dist").exists()


def test_refuses_unknown_tag(repo):
    result = release(repo, "v9.9.9")

    assert result.returncode != 0
    assert "v9.9.9" in result.stderr


def is_highest(repo: Path, name: str) -> subprocess.CompletedProcess:
    return release(repo, "--is-highest", name)


@pytest.mark.parametrize(("tags", "new", "expected"), [
    ([], "v0.4.0", "true"),                      # first release
    (["v0.4.0"], "v0.5.0", "true"),
    (["v0.4.0", "v0.5.0"], "v0.4.1", "false"),   # hotfix on an older line
    (["v0.9.0"], "v0.10.0", "true"),             # numeric, not alphabetical
    (["v0.10.0"], "v0.9.1", "false"),
    (["v1.0.0-rc1", "not-a-version"], "v0.4.0", "true"),  # only vX.Y.Z tags count
])
def test_is_highest_only_for_the_newest_version(repo, tags, new, expected):
    for name in [*tags, new]:
        tag(repo, name)

    result = is_highest(repo, new)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def test_is_highest_refuses_unknown_tag(repo):
    result = is_highest(repo, "v9.9.9")

    assert result.returncode != 0
    assert "v9.9.9" in result.stderr


# ── A release that needs the family (ADR 0047, #918): release.json, which the zip carries

ASKS = {"en": "macOS asks for your Mac password once.", "fi": "macOS kysyy Macin salasanaa kerran.",
        "zh": "macOS 会问一次 Mac 密码。"}


def needs_family(repo: Path, value: object, asks: object = None) -> None:
    (repo / "release.json").write_text(json.dumps({"background_from": value, "asks": asks}))


def cut(repo: Path, version: str, parts: bool = True) -> subprocess.CompletedProcess:
    """A release as the release steps cut it: the versions bumped, its parts written, tagged and
    built."""
    set_versions(repo, version, version)
    tag(repo, f"v{version}", parts)
    return release(repo, f"v{version}")


def in_zip(repo: Path, version: str) -> dict:
    with zipfile.ZipFile(repo / "dist" / f"parent-recap-{version}.zip") as z:
        return json.loads(z.read("parent-recap/release.json"))


def test_a_release_nobody_said_anything_of_names_no_version(repo):
    assert cut(repo, "1.2.0").returncode == 0

    note = in_zip(repo, "1.2.0")
    assert (note["background_from"], note["asks"], note["version"]) == (None, None, "1.2.0")


def test_a_release_said_to_need_the_family_carries_the_version_and_what_it_asks_in_the_zip(repo):
    needs_family(repo, "1.2.0", ASKS)

    result = cut(repo, "1.2.0")

    assert result.returncode == 0, result.stderr
    note = in_zip(repo, "1.2.0")
    assert (note["background_from"], note["asks"]) == ("1.2.0", ASKS)


def test_later_releases_carry_the_value_forward_when_the_maintainer_says_nothing(repo):
    needs_family(repo, "1.2.0", ASKS)
    assert cut(repo, "1.2.0").returncode == 0

    for later in ("1.2.1", "1.3.0"):  # nothing said: the file is as the release before left it
        result = cut(repo, later)
        assert result.returncode == 0, result.stderr
        note = in_zip(repo, later)
        assert (note["background_from"], note["asks"]) == ("1.2.0", ASKS)


def test_a_later_release_may_raise_the_value(repo):
    needs_family(repo, "1.2.0", ASKS)
    assert cut(repo, "1.2.0").returncode == 0
    needs_family(repo, "1.3.0", {**ASKS, "en": "You scan a QR code with WhatsApp."})

    result = cut(repo, "1.3.0")

    assert result.returncode == 0, result.stderr
    assert in_zip(repo, "1.3.0")["background_from"] == "1.3.0"


@pytest.mark.parametrize(("value", "says"), [(None, "names no version"), ("1.1.0", "names 1.1.0")])
def test_refuses_a_release_that_dropped_or_lowered_the_value_of_the_release_before(repo, value, says):
    needs_family(repo, "1.2.0", ASKS)
    assert cut(repo, "1.2.0").returncode == 0
    needs_family(repo, value, ASKS if value else None)

    result = cut(repo, "1.3.0")

    assert result.returncode != 0
    assert says in result.stderr and "v1.2.0 named 1.2.0" in result.stderr
    assert not (repo / "dist" / "parent-recap-1.3.0.zip").exists()


def test_a_hotfix_on_an_older_line_answers_to_the_release_before_it_not_the_newest(repo):
    assert cut(repo, "1.2.0").returncode == 0
    needs_family(repo, "1.3.0", ASKS)
    assert cut(repo, "1.3.0").returncode == 0
    needs_family(repo, None)

    result = cut(repo, "1.2.1")

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(("value", "asks", "says"), [
    ("1.2.0", None, "missing: en, fi, zh"),
    ("1.2.0", {**ASKS, "fi": " "}, "missing: fi"),
    ("1.3.0", ASKS, "higher than the release itself"),
    ("soon", ASKS, "must be a version"),
    (None, ASKS, "asks but no background_from"),
])
def test_refuses_a_release_whose_note_the_program_could_not_read(repo, value, asks, says):
    needs_family(repo, value, asks)

    result = cut(repo, "1.2.0")

    assert result.returncode != 0 and says in result.stderr
    assert not (repo / "dist").exists()


def test_refuses_a_note_that_is_not_json(repo):
    (repo / "release.json").write_text("background_from: 1.2.0\n")

    assert "isn't JSON" in release(repo, "--parts").stderr
    result = cut(repo, "1.2.0", parts=False)

    assert result.returncode != 0 and "isn't JSON" in result.stderr


# ── What a release changed (ADR 0051, #958): release.json's version and parts

def load_release_script():
    spec = importlib.util.spec_from_file_location("release_script", ROOT / "scripts" / "release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release_script = load_release_script()
SOURCES = "app/src/family_brief/sources"
VERSION_FILES = {
    ".claude-plugin/plugin.json": '{\n  "name": "parent-recap",\n  "version": "{v}",\n  "x": 1\n}\n',
    "app/pyproject.toml": '[project]\nname = "family-brief"\nversion = "{v}"\ndependencies = ["a==1"]\n',
    "app/uv.lock": '[[package]]\nname = "family-brief"\nversion = "{v}"\nsource = { editable = "." }\n',
}


@pytest.mark.parametrize("path", [
    "docs/adr/0051-x.md", "docs/setup-internals.md",                    # the documents
    "README.md", "README.fi.md", "README.zh-CN.md", "CONTRIBUTING.md", "CONTEXT.md", "CLAUDE.md",
    "THIRD_PARTY_NOTICES.md",
    "app/tests/scripts/test_release.py", "app/tests/README.md",         # the tests
    ".github/workflows/tests.yml", ".github/pull_request_template.md",  # the workflows
    "CHANGELOG.md",
    "release.json",
])
def test_a_path_on_the_list_counts_for_nothing(path):
    assert release_script.part_of(path) is None


@pytest.mark.parametrize("path", [
    "install.sh", "install.ps1", "get.sh", "app/uv.lock", "LICENSE",
    "app/src/family_brief/update.py",
    "skills/manage/SKILL.md",                  # read by Claude on the family's computer
    "SECURITY.md",                             # a document the manage skill reads there too
    "app/src/family_brief/tests/check.py",     # a tests folder inside the program, which it runs
    "skills/setup/docs/guide.md",              # documents inside the program
    "app/src/family_brief/workflows.py",
    "scripts/release.py", "ops/gateway/README.md",
    f"{SOURCES}/__init__.py", f"{SOURCES}/record.py", f"{SOURCES}/fi/__init__.py",
    "docs.md", "app/docs/x.md", "x/CHANGELOG.md", "x/release.json",
])
def test_any_other_path_is_the_cores(path):
    assert release_script.part_of(path) == "core"


def test_a_path_inside_a_sources_folder_is_that_sources():
    assert release_script.part_of(f"{SOURCES}/nl/konnect/collector.py") == "nl/konnect"
    assert release_script.part_of(f"{SOURCES}/fi/myclub/tests/x.py") == "fi/myclub"


@pytest.mark.parametrize(("path", "template"), VERSION_FILES.items())
def test_the_line_that_holds_the_version_counts_for_nothing_and_the_rest_of_its_file_is_the_cores(
        path, template):
    old, new = template.replace("{v}", "1.2.0"), template.replace("{v}", "1.3.0")

    assert release_script.part_of(path, old, new) is None
    assert release_script.part_of(path, old, new + "more = 1\n") == "core"  # another line too
    assert release_script.part_of(path, None, new) == "core"  # a file that wasn't there


def parts(repo: Path) -> dict:
    return json.loads((repo / "release.json").read_text())["parts"]


def bump_and_write_parts(repo: Path, version: str) -> dict:
    set_versions(repo, version, version)
    result = release(repo, "--parts")
    assert result.returncode == 0, result.stderr
    return json.loads((repo / "release.json").read_text())


def change(repo: Path, path: str) -> None:
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    with (repo / path).open("a") as f:
        f.write("\n# changed\n")


@pytest.fixture
def released(repo: Path) -> Path:
    """A repo whose last release, v1.2.0, named every part at 1.2.0."""
    set_versions(repo, "1.2.0", "1.2.0")
    tag(repo, "v1.2.0")
    return repo


def test_writes_the_version_and_parts_and_writing_again_changes_nothing(released):
    change(released, f"{SOURCES}/nl/konnect/collector.py")

    first = bump_and_write_parts(released, "1.3.0")
    text = (released / "release.json").read_text()

    assert first["version"] == "1.3.0"
    assert first["parts"]["nl/konnect"] == "1.3.0"
    assert list(first) == ["version", "background_from", "asks", "parts"]
    assert bump_and_write_parts(released, "1.3.0") == first
    assert (released / "release.json").read_text() == text


def test_a_change_only_inside_one_sources_folder_raises_only_that_part(released):
    change(released, f"{SOURCES}/nl/konnect/collector.py")
    git(released, "commit", "-qam", "konnect fix")  # committed or not, the paths count

    written = bump_and_write_parts(released, "1.3.0")["parts"]

    assert written["nl/konnect"] == "1.3.0"
    assert {part: v for part, v in written.items() if part != "nl/konnect"} == \
        {part: "1.2.0" for part in written if part != "nl/konnect"}
    assert {"core", "fi/myclub", "nl/socialschools"} <= set(written)


def test_a_change_only_to_paths_that_count_for_nothing_raises_no_part(released):
    for path in ("docs/x.md", "README.md", "app/tests/test_x.py", ".github/workflows/tests.yml",
                 "CHANGELOG.md"):
        change(released, path)

    written = bump_and_write_parts(released, "1.3.0")["parts"]  # the version lines and release.json change too

    assert set(written.values()) == {"1.2.0"}


def test_any_other_change_raises_the_core(released):
    change(released, "install.sh")

    written = bump_and_write_parts(released, "1.3.0")["parts"]

    assert written["core"] == "1.3.0"
    assert {v for part, v in written.items() if part != "core"} == {"1.2.0"}


def test_a_release_whose_predecessor_has_no_parts_gets_the_core_at_its_own_version(repo):
    set_versions(repo, "1.2.0", "1.2.0")
    needs_family(repo, None)  # as release.json was before #958
    tag(repo, "v1.2.0", parts=False)
    change(repo, "docs/x.md")

    written = bump_and_write_parts(repo, "1.3.0")["parts"]

    assert set(written.values()) == {"1.3.0"} and "core" in written


def test_a_new_sources_folder_gets_the_release_version_and_a_removed_one_leaves_parts(released):
    change(released, f"{SOURCES}/xx/newone/collector.py")
    shutil.rmtree(released / SOURCES / "nl" / "socialschools")

    written = bump_and_write_parts(released, "1.3.0")["parts"]

    assert written["xx/newone"] == "1.3.0"
    assert "nl/socialschools" not in written
    assert written["nl/konnect"] == "1.2.0"


def test_a_hotfix_on_a_release_branch_is_compared_with_the_tag_it_comes_from(released):
    change(released, f"{SOURCES}/fi/myclub/collector.py")
    set_versions(released, "1.3.0", "1.3.0")
    tag(released, "v1.3.0")
    assert parts(released)["fi/myclub"] == "1.3.0"
    git(released, "checkout", "-q", "-b", "release-1.2", "v1.2.0")
    change(released, f"{SOURCES}/nl/konnect/collector.py")

    written = bump_and_write_parts(released, "1.2.1")["parts"]

    assert written["nl/konnect"] == "1.2.1"
    assert written["fi/myclub"] == "1.2.0"  # not 1.3.0, which the hotfix doesn't come from
    assert written["core"] == "1.2.0"
    set_versions(released, "1.2.1", "1.2.1")
    tag(released, "v1.2.1")
    assert release(released, "v1.2.1").returncode == 0


def test_a_release_on_main_after_a_hotfix_it_does_not_hold_answers_to_that_hotfix(released):
    git(released, "checkout", "-q", "-b", "release-1.2")
    change(released, "install.sh")  # the hotfix changes the core
    set_versions(released, "1.2.1", "1.2.1")
    tag(released, "v1.2.1")
    git(released, "checkout", "-q", "-")
    change(released, f"{SOURCES}/nl/konnect/collector.py")

    written = bump_and_write_parts(released, "1.3.0")["parts"]

    # A Household on 1.2.1 runs a core 1.3.0 doesn't have, so the core counts as changed.
    assert written["core"] == "1.3.0" and written["nl/konnect"] == "1.3.0"
    assert written["fi/myclub"] == "1.2.0"


def test_the_build_takes_a_tag_whose_parts_the_paths_give(released):
    change(released, f"{SOURCES}/nl/konnect/collector.py")

    result = cut(released, "1.3.0")

    assert result.returncode == 0, result.stderr
    assert in_zip(released, "1.3.0")["parts"]["nl/konnect"] == "1.3.0"


@pytest.mark.parametrize("edit", [
    lambda note: {**note, "version": "1.2.0"},
    lambda note: {key: value for key, value in note.items() if key != "version"},
    lambda note: {**note, "parts": {**note["parts"], "nl/konnect": "1.2.0"}},
    lambda note: {key: value for key, value in note.items() if key != "parts"},
])
def test_the_build_refuses_a_tag_whose_version_or_parts_are_not_what_the_paths_give(released, edit):
    change(released, f"{SOURCES}/nl/konnect/collector.py")
    note = bump_and_write_parts(released, "1.3.0")
    (released / "release.json").write_text(json.dumps(edit(note)))
    tag(released, "v1.3.0", parts=False)

    result = release(released, "v1.3.0")

    assert result.returncode != 0
    assert "release.py --parts" in result.stderr and "since v1.2.0" in result.stderr
    assert not (released / "dist" / "parent-recap-1.3.0.zip").exists()


def test_a_change_after_the_parts_were_written_is_refused_by_the_build(released):
    bump_and_write_parts(released, "1.3.0")
    change(released, "install.sh")  # the core, after --parts ran
    tag(released, "v1.3.0", parts=False)

    result = release(released, "v1.3.0")

    assert result.returncode != 0 and '"core": "1.3.0"' in result.stderr
