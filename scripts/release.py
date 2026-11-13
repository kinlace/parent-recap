#!/usr/bin/env python3
"""Build dist/parent-recap-<version>.zip from a release tag.

Usage: python scripts/release.py v0.4.0
       python scripts/release.py --is-highest v0.4.0   # prints true or false

The zip comes from `git archive` of the tag, so local edits never leak in, and
`.gitattributes` export-ignore rules drop the developer-only files. Unzipping it
gives a `parent-recap/` folder that families move to ~/ParentRecap/plugin.

The zip carries `release.json` as the tag has it: whether the release needs the family at the
computer (ADR 0047, CONTRIBUTING.md "A release that needs the family"). The file is in the tree,
so a release whose maintainer says nothing carries the value of the release before it forward as
it is, and the build refuses a tag that dropped or lowered it.
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
# Whether the release needs the family (ADR 0047): `background_from`, the lowest version that can
# take it in the background, or null when every version can, and `asks`, a sentence in each of
# ASKS_LANGUAGES on what the family will be asked. The program reads it from the verified zip
# (family_brief/update.py).
NEEDS_FAMILY = "release.json"
ASKS_LANGUAGES = ("en", "fi", "zh")


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
    require_tag(tag)
    if not (new := _version(tag)):
        fail(f"{tag} is not a vX.Y.Z tag")
    return new == max(filter(None, map(_version, git("tag", "--list").stdout.split())))


def _version(name: str) -> tuple[int, ...] | None:
    """A vX.Y.Z tag's version as numbers, or None for any other name."""
    m =VERSION_TAG.match(name)
    return tuple(map(int, m.groups())) if m else None


def background_from(tag: str) -> str | None:
    """The lowest version that can take `tag`'s release in the background, as its release.json
    says, or None when it names none (or the tag is from before the file). Fails on a file the
    program couldn't read."""
    shown = git("show", f"{tag}:{NEEDS_FAMILY}")
    if shown.returncode != 0:
        return None
    where = f"{NEEDS_FAMILY} at {tag}"
    try:
        data = json.loads(shown.stdout)
    except ValueError:
        fail(f"{where} isn't JSON")
    if not isinstance(data, dict):
        fail(f"{where} isn't a JSON object")
    value, asks = data.get("background_from"), data.get("asks")
    if value is None:
        if asks:
            fail(f"{where} has asks but no background_from: name the version, or set asks to null")
        return None
    if not isinstance(value, str) or not _version(f"v{value}"):
        fail(f"{where}: background_from must be a version such as 0.4.0, or null")
    if _version(f"v{value}") > _version(tag):
        fail(f"{where}: background_from is {value}, higher than the release itself")
    missing = [language for language in ASKS_LANGUAGES
               if not (isinstance(asks, dict) and isinstance(asks.get(language), str)
                       and asks[language].strip())]
    if missing:
        fail(f"{where}: asks needs a sentence on what the family will be asked in each of "
             f"{', '.join(ASKS_LANGUAGES)}; missing: {', '.join(missing)}")
    return value


def check_needs_family(tag: str) -> None:
    """Refuses a release whose release.json can't be read, or that dropped or lowered the value
    of the release before it: a Household several releases behind is held back by the newest
    release alone, so each release carries the value forward unless it raises it."""
    value = background_from(tag)
    new = _version(tag)
    older = [t for t in git("tag", "--list").stdout.split() if (v := _version(t)) and v < new]
    if not older:
        return
    before = max(older, key=_version)
    carried = background_from(before)
    if carried and (value is None or _version(f"v{value}") < _version(f"v{carried}")):
        fail(f"{NEEDS_FAMILY} at {tag} names {value or 'no version'}, but {before} named {carried}: "
             f"carry it forward (background_from {carried}, with its asks) or raise it")


def build(tag: str) -> Path:
    require_tag(tag)
    if not _version(tag):
        fail(f"{tag} is not a vX.Y.Z tag")

    def at_tag(path: str) -> str:
        return git("show", f"{tag}:{path}").stdout

    plugin = json.loads(at_tag(".claude-plugin/plugin.json"))["version"]
    package = tomllib.loads(at_tag("app/pyproject.toml"))["project"]["version"]
    if not tag.removeprefix("v") == plugin == package:
        fail(f"versions disagree at {tag}:\n"
             f"   tag: {tag}\n"
             f"   .claude-plugin/plugin.json: {plugin}\n"
             f"   app/pyproject.toml: {package}")
    check_needs_family(tag)

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
