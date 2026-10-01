"""The release command builds the zip families install from, straight from a tag."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
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


def tag(repo: Path, name: str) -> None:
    git(repo, "commit", "-qam", name, "--allow-empty")
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
