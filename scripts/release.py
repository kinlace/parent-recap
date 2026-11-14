#!/usr/bin/env python3
"""Build dist/parent-recap-<version>.zip from a release tag.

Usage: python scripts/release.py v0.4.0
       python scripts/release.py --is-highest v0.4.0   # prints true or false
       python scripts/release.py --parts               # writes version and parts in release.json

The zip comes from `git archive` of the tag, so local edits never leak in, and
`.gitattributes` export-ignore rules drop the developer-only files. Unzipping it
gives a `parent-recap/` folder that families move to ~/ParentRecap/plugin.

The zip carries `release.json` as the tag has it: whether the release needs the family at the
computer (ADR 0047, CONTRIBUTING.md "A release that needs the family"). The file is in the tree,
so a release whose maintainer says nothing carries the value of the release before it forward as
it is, and the build refuses a tag that dropped or lowered it.

The file also says the release's `version` and its `parts` (ADR 0051): for the core and for each
Source with a folder of its own, the last version that changed it. `--parts` works them out in the
release's pull request from the paths changed since the release before, and the build works them
out again and refuses a tag whose file says otherwise.
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
from collections.abc import Callable
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

# What a release changed (ADR 0051). A path inside a Source's folder is that Source's part,
# `<group>/<name>`; a path on COUNTS_FOR_NOTHING is no part's; every other path is the core's, so
# a path nobody thought of costs one install too many, never one too few. Keep the list short:
# each entry is a path no family's computer runs, or one every release changes.
SOURCE_FOLDER = re.compile(r"^app/src/family_brief/sources/([^/]+)/([^/]+)/")
COUNTS_FOR_NOTHING = re.compile(
    # The documents. Not SECURITY.md: the manage skill reads it on the family's computer.
    r"^docs/|^(README(\.[a-zA-Z-]+)?|CONTRIBUTING|CONTEXT|CLAUDE|THIRD_PARTY_NOTICES)\.md$"
    r"|^app/tests/"        # the tests
    r"|^\.github/"         # the workflows, and GitHub's other settings
    r"|^CHANGELOG\.md$"
    r"|^release\.json$")
# The files that hold the version, and their line that does: a change to that line alone counts
# for nothing, any other change to the file is the core's.
VERSION_LINES = {
    ".claude-plugin/plugin.json": re.compile(r'(?m)^(\s*"version":\s*")[^"]*(")'),
    "app/pyproject.toml": re.compile(r'(?m)^(version = ")[^"]*(")'),
    "app/uv.lock": re.compile(r'(?m)^(name = "family-brief"\nversion = ")[^"]*(")'),
}
CORE = "core"
PARTS_COMMAND = "python3 scripts/release.py --parts"


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
    if not (before := release_before(_version(tag))):
        return
    carried = background_from(before)
    if carried and (value is None or _version(f"v{value}") < _version(f"v{carried}")):
        fail(f"{NEEDS_FAMILY} at {tag} names {value or 'no version'}, but {before} named {carried}: "
             f"carry it forward (background_from {carried}, with its asks) or raise it")


# ---------------------------------------------------------------- what a release changed (ADR 0051)

def part_of(path: str, old: str | None = None, new: str | None = None) -> str | None:
    """The part a changed path is: a Source's `<group>/<name>`, the core's, or None when it counts
    for nothing. `old` and `new` are the file's text before and after, read only for the files that
    hold the version (None where it didn't exist)."""
    if m := SOURCE_FOLDER.match(path):
        return f"{m[1]}/{m[2]}"
    if COUNTS_FOR_NOTHING.match(path):
        return None
    if (line := VERSION_LINES.get(path)) and old is not None and new is not None \
            and line.sub(r"\1\2", old) == line.sub(r"\1\2", new):
        return None
    return CORE


def release_before(version: tuple[int, ...]) -> str | None:
    """The release one at `version` follows: the highest vX.Y.Z tag below it, so a hotfix on a
    release branch follows the tag it comes from, not the highest tag. Not the newest in the
    release's history: a release on `main` after a hotfix it doesn't hold answers to that hotfix,
    which a Household may run."""
    older = [t for t in git("tag", "--list").stdout.split() if (v := _version(t)) and v < version]
    return max(older, key=_version, default=None)


def _show(ref: str, path: str) -> str | None:
    """The text of `path` at `ref`, or None where it isn't there."""
    shown = git("show", f"{ref}:{path}")
    return shown.stdout if shown.returncode == 0 else None


def _parts_at(tag: str) -> dict[str, str]:
    """The parts `tag`'s release.json names, leaving out what isn't a part and a version; empty
    for a release from before `parts`."""
    try:
        parts = json.loads(_show(tag, NEEDS_FAMILY) or "{}").get("parts")
    except (ValueError, AttributeError):
        return {}
    if not isinstance(parts, dict):
        return {}
    return {part: value for part, value in parts.items()
            if isinstance(part, str) and isinstance(value, str) and _version(f"v{value}")}


def _lines(output: str) -> list[str]:
    return [line for line in output.split("\0") if line]


def work_out_parts(version: str, before: str | None, files: list[str], changed: list[str],
                   read: Callable[[str], str | None]) -> dict[str, str]:
    """The parts of a release at `version` whose tree holds `files`, `changed` since `before`, the
    release it follows (None for the first): each part a changed path is raised to `version`, and
    one the release before didn't name too; the others keep the version they had. `read(path)` is
    the file's text in this release, or None."""
    sources = sorted({f"{m[1]}/{m[2]}" for path in files if (m := SOURCE_FOLDER.match(path))})
    previous = _parts_at(before) if before else {}

    raised = {part_of(path, *((before and _show(before, path), read(path)) if path in VERSION_LINES else ()))
              for path in changed}
    return {part: version if part in raised or part not in previous else previous[part]
            for part in (CORE, *sources)}


def parts_at_tag(tag: str, before: str | None) -> dict[str, str]:
    """What the paths changed from `before` to `tag` give as `tag`'s parts."""
    files = _lines(git("ls-tree", "-r", "--name-only", "-z", tag).stdout)
    changed = _lines(git("diff", "--name-only", "--no-renames", "-z", before, tag).stdout) if before else files
    return work_out_parts(tag.removeprefix("v"), before, files, changed, lambda path: _show(tag, path))


def write_parts() -> None:
    """Writes the release's `version` and `parts` into the working tree's release.json, from the
    version in .claude-plugin/plugin.json and the paths changed since the release before,
    committed or not. Running it again changes nothing."""
    version = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())["version"]
    if not (numbers := _version(f"v{version}")):
        fail(f".claude-plugin/plugin.json: version {version} isn't X.Y.Z")
    before = release_before(numbers)
    files = [f for f in _lines(git("ls-files", "-co", "--exclude-standard", "-z").stdout)
             if (ROOT / f).is_file()]
    if before:
        changed = sorted({*_lines(git("diff", "--name-only", "--no-renames", "-z", before).stdout),
                          *_lines(git("ls-files", "-o", "--exclude-standard", "-z").stdout)})
    else:
        changed = files

    def read(path: str) -> str | None:
        return (ROOT / path).read_text() if (ROOT / path).is_file() else None

    parts = work_out_parts(version, before, files, changed, read)
    path = ROOT / NEEDS_FAMILY
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
    except ValueError:
        fail(f"{NEEDS_FAMILY} isn't JSON")
    if not isinstance(data, dict):
        fail(f"{NEEDS_FAMILY} isn't a JSON object")
    data = {"version": version, "background_from": None, "asks": None, **data}
    data.update(version=version, parts=parts)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"{NEEDS_FAMILY}: version {version}, parts since {before or 'the start'}: "
          + ", ".join(f"{part} {value}" for part, value in parts.items()))


def check_parts(tag: str) -> None:
    """Refuses a tag whose release.json doesn't say the release's version, or parts other than
    the paths changed since the release before give."""
    version = tag.removeprefix("v")
    shown = git("show", f"{tag}:{NEEDS_FAMILY}")
    try:
        data = json.loads(shown.stdout) if shown.returncode == 0 else {}
    except ValueError:
        fail(f"{NEEDS_FAMILY} at {tag} isn't JSON")
    before = release_before(_version(tag))
    expected = parts_at_tag(tag, before)
    said = data.get("version"), data.get("parts")
    if said != (version, expected):
        fail(f"{NEEDS_FAMILY} at {tag} says version {said[0]} and parts {json.dumps(said[1])}, but the "
             f"release is {version} and the paths changed since {before or 'the start'} give "
             f"{json.dumps(expected)}: run `{PARTS_COMMAND}` on the release's commit and commit "
             f"{NEEDS_FAMILY}")


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
    check_parts(tag)

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
        case ["--parts"]:
            write_parts()
        case [tag] if not tag.startswith("-"):
            print(f"✅ {build(tag)}")
        case _:
            sys.exit("usage: python3 scripts/release.py <tag>   e.g. v0.4.0\n"
                     "       python3 scripts/release.py --is-highest <tag>\n"
                     "       python3 scripts/release.py --parts")


if __name__ == "__main__":
    main()
