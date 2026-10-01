#!/usr/bin/env python3
"""Build dist/parent-recap-<version>.zip from a release tag.

Usage: python scripts/release.py v0.4.0
       python scripts/release.py --is-highest v0.4.0   # prints true or false

The zip comes from `git archive` of the tag, so local edits never leak in, and
`.gitattributes` export-ignore rules drop the developer-only files. Unzipping it
gives a `parent-recap/` folder that families move to ~/FamilyBrief/plugin.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "parent-recap/"
VERSION_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
# Local artefacts and secrets that only reach the zip if someone force-added them to git.
LOCAL_ARTEFACT = re.compile(r"(^|/)(\.git|\.venv|venv|__pycache__|\.scratch|\.pytest_cache|dist)(/|$)"
                            r"|\.py[co]$|(^|/)\.env(\..+)?$|(^|/)\.DS_Store$"
                            r"|(^|/)(google_oauth_client|calendar_credentials|calendar_token)\.json$")


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def fail(message: str) -> None:
    sys.exit(f"❌ {message}")


def require_tag(tag: str) -> None:
    if git("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}").returncode != 0:
        fail(f"tag {tag} does not exist")


def is_highest(tag: str) -> bool:
    """Whether `tag` is the highest vX.Y.Z tag so far, so `stable` may move to it.

    A hotfix on an older release line is not: moving `stable` back would downgrade every
    marketplace family, because Claude Code updates on any version change.
    """
    def version(name: str) -> tuple[int, ...] | None:
        m = VERSION_TAG.match(name)
        return tuple(map(int, m.groups())) if m else None

    require_tag(tag)
    if not (new := version(tag)):
        fail(f"{tag} is not a vX.Y.Z tag")
    return new == max(filter(None, map(version, git("tag", "--list").stdout.split())))


def build(tag: str) -> Path:
    require_tag(tag)

    def at_tag(path: str) -> str:
        return git("show", f"{tag}:{path}").stdout

    plugin = json.loads(at_tag(".claude-plugin/plugin.json"))["version"]
    package = tomllib.loads(at_tag("app/pyproject.toml"))["project"]["version"]
    if not tag.removeprefix("v") == plugin == package:
        fail(f"versions disagree at {tag}:\n"
             f"   tag: {tag}\n"
             f"   .claude-plugin/plugin.json: {plugin}\n"
             f"   app/pyproject.toml: {package}")

    with tempfile.TemporaryDirectory() as tmp:
        built = Path(tmp) / "release.zip"
        result = git("archive", "--format=zip", f"--prefix={PREFIX}", f"--output={built}", tag)
        if result.returncode != 0:
            fail(f"git archive failed:\n{result.stderr}")
        with zipfile.ZipFile(built) as z:
            artefacts = [n.removeprefix(PREFIX) for n in z.namelist() if LOCAL_ARTEFACT.search(n.removeprefix(PREFIX))]
        if artefacts:
            fail(f"{tag} contains local artefacts that must not ship; remove them from git:\n   "
                 + "\n   ".join(artefacts))
        out = ROOT / "dist" / f"parent-recap-{plugin}.zip"
        out.parent.mkdir(exist_ok=True)
        shutil.move(built, out)
    return out


def main() -> None:
    match sys.argv[1:]:
        case ["--is-highest", tag]:
            print("true" if is_highest(tag) else "false")
        case [tag] if not tag.startswith("-"):
            print(f"✅ {build(tag)}")
        case _:
            sys.exit("usage: python3 scripts/release.py <tag>   e.g. v0.4.0\n"
                     "       python3 scripts/release.py --is-highest <tag>")


if __name__ == "__main__":
    main()
