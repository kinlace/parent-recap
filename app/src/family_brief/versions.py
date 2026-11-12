"""The installed program's versions, and switching between them (ADR 0047).

`install.sh` installs each release in a folder of its own, `versions/<version>.<random>` in
Parent Recap's folder, with its own venv, its `VERSION` and its `RUNTIMES` (the folders in
`runtime/` its venv and the wilma CLI run on), while the running version goes on as it was. Only
once that install finished does `switch` point `app` at it: `app` is the one path everything
outside the program starts it from (the evening jobs, the Linked Device service, Settings and
Check, the `parent-recap` command), so it stays the same across versions and an update leaves
them as they are. The version `app` pointed at before is kept, one version deep, as
`versions/previous`, and `parent-recap switch-back` (`switch_back`) points `app` at it again.

An install from before this kept the program in `app` itself. The first install after it moves
that folder into `versions/` as the previous version: its venv names `app`, so it runs again once
`app` points at it.

Only the standard library at the top, as `install.sh` runs `switch` with the new version's Python
before anything else of the program has run there.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

APP = "app"
VERSIONS = "versions"
PREVIOUS = "previous"  # in VERSIONS, a link to the version switched from
RUNTIMES = "RUNTIMES"  # in a version's folder: the runtime/ folders it runs on, a line each
RUNTIME = "runtime"


class NoPrevious(Exception):
    """There is no previous version to switch back to."""


def stable(path: Path) -> Path:
    """`path` with a version's folder in it, `<folder>/versions/<version>/…`, as reached through
    `<folder>/app/…`, which every version is run from. A venv's own scripts name their version's
    folder, and so does the program's own code: the jobs and the Linked Device service must name
    `app` instead, or the next version would change them. Any other path as it is."""
    parts = path.parts
    for i in range(len(parts) - 2, 0, -1):
        if parts[i] != VERSIONS or parts[i + 1] == PREVIOUS:
            continue
        folder = Path(*parts[:i])
        if (folder / APP).is_symlink() and (folder / RUNTIME).is_dir():
            return folder.joinpath(APP, *parts[i + 2:])
    return path


def current(folder: Path) -> Path | None:
    """The version's folder `app` points at, or None when `app` isn't a link to one."""
    return _followed(folder / APP, folder / VERSIONS)


def previous(folder: Path) -> Path | None:
    """The version's folder switched from, kept to switch back to, or None."""
    return _followed(folder / VERSIONS / PREVIOUS, folder / VERSIONS)


def version(of: Path) -> str:
    """The version a version's folder holds, as its `VERSION` says."""
    try:
        return (of / "VERSION").read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


def _followed(link: Path, versions: Path) -> Path | None:
    if not link.is_symlink():
        return None
    target = versions / Path(os.readlink(link)).name
    return target if target.is_dir() else None


def _point(link: Path, to: str) -> None:
    """Points `link` at `to` in one step: a new link, renamed over the old one, so whatever
    starts the program in between finds one version or the other, never none."""
    incoming = link.with_name(f".{link.name}.incoming")
    incoming.unlink(missing_ok=True)
    os.symlink(to, incoming)
    os.replace(incoming, link)


def _take_over(folder: Path) -> Path | None:
    """Moves an install from before versions, the program in `app` itself, into `versions/`, and
    returns its new folder, or None when there is none. It runs on whatever `runtime/` holds now,
    which its RUNTIMES then names."""
    app = folder / APP
    if app.is_symlink() or not app.is_dir():
        return None
    versions = folder / VERSIONS
    versions.mkdir(mode=0o700, exist_ok=True)
    name = f"{version(app)}.before"
    tries = 1
    while (versions / name).exists() or (versions / name).is_symlink():
        tries += 1
        name = f"{version(app)}.before{tries}"
    if not (app / RUNTIMES).exists():
        names = sorted(p.name for p in (folder / RUNTIME).glob("*") if not p.name.startswith("."))
        (app / RUNTIMES).write_text("".join(f"{r}\n" for r in names), encoding="utf-8")
    # `app` is missing from here until switch's link: the one moment an install from before
    # versions has none, once.
    os.rename(app, versions / name)
    return versions / name


def switch(folder: Path, new: Path) -> None:
    """Points `app` in `folder` at the version installed in `new`, once its install finished.
    The version `app` pointed at before is kept as the previous one, unless it's the same version
    as `new` (the install run again, as manage does to add Google's packages), which keeps the
    previous one there was; that copy stays until the next switch, as a run started from it may
    still be going. Then removes every other version's folder, such as one an install that
    failed left, and the runtimes none of them runs on."""
    new = folder / VERSIONS / new.name
    old = _take_over(folder) or current(folder)
    before = previous(folder)
    spare = None
    if old is not None and old != new:
        if version(old) != version(new):
            before = old
        else:
            spare = old
    _point(folder / APP, f"{VERSIONS}/{new.name}")
    if before is not None and before != new:
        _point(folder / VERSIONS / PREVIOUS, before.name)
    else:
        (folder / VERSIONS / PREVIOUS).unlink(missing_ok=True)
    _clean(folder, spare)


def switch_back(folder: Path) -> tuple[str, str]:
    """Points `app` at the previous version, and keeps the one switched from as the previous one,
    so running it again switches forward. Returns the version now running and the one kept.
    Raises NoPrevious when there is none."""
    now, before = current(folder), previous(folder)
    if now is None or before is None or before == now:
        raise NoPrevious()
    _point(folder / APP, f"{VERSIONS}/{before.name}")
    _point(folder / VERSIONS / PREVIOUS, now.name)
    return version(before), version(now)


def _runtimes(of: Path) -> set[str] | None:
    try:
        text = (of / RUNTIMES).read_text(encoding="utf-8")
    except OSError:
        return None
    return {line.strip() for line in text.splitlines() if line.strip()}


def _clean(folder: Path, spare: Path | None = None) -> None:
    kept = [v for v in (current(folder), previous(folder), spare) if v is not None]
    for child in (folder / VERSIONS).iterdir():
        if child.name != PREVIOUS and child not in kept:
            if child.is_symlink() or not child.is_dir():
                child.unlink()
            else:
                shutil.rmtree(child, ignore_errors=True)
    used: set[str] = set()
    for v in kept:
        names = _runtimes(v)
        if names is None:
            return  # a version that doesn't say what it runs on keeps every runtime
        used |= names
    runtime = folder / RUNTIME
    if not runtime.is_dir():
        return
    for child in runtime.iterdir():
        if not child.name.startswith(".") and child.name not in used:
            shutil.rmtree(child, ignore_errors=True)


# ------------------------------------------------------------------ parent-recap switch-back

def _steps(folder: Path) -> list[list[str]]:
    """What install.sh runs once it has switched, run with the program `app` now points at, so
    the version switched to puts its own wilma CLI, Baileys tree, Settings and Check and evening
    jobs back as it had them. Each never fails, and each is in every version that has `app`.
    Settings and Check and the evening jobs only where install.sh makes them: on a Mac, not on a
    server."""
    from .system import entries
    python = str(folder / APP / ".venv" / "bin" / "python")
    steps = [[python, "-m", "family_brief.command_path", "install", str(folder)],
             [python, "-m", "family_brief.setup_wilma", "update"],
             [python, "-m", "family_brief.baileys_install", "install"]]
    if entries.offered():
        steps += [[python, "-m", "family_brief.program_entries", "install", str(folder)],
                  [python, "-m", "family_brief.schedule_update"]]
    return steps


def cmd_switch_back(_args) -> int:
    from . import own_node
    from .system import jobs
    folder = own_node.home()
    try:
        running, kept = switch_back(folder)
    except NoPrevious:
        print("There is no previous version to switch back to: only the one running now is "
              "installed. Nothing was changed.")
        return 1
    for step in _steps(folder):
        subprocess.run(step, stdin=subprocess.DEVNULL, check=False)
    # From here, not with the version switched to: one from before versions has no restart.
    try:
        jobs.restart_always()  # the Linked Device's, so it runs the version switched to
    except Exception as e:  # noqa: BLE001 - it runs that version from its next start
        print(f"The Linked Device wasn't restarted ({e}); it runs version {running} from its "
              "next start.")
    print(f"✅ Parent Recap runs version {running} again. Version {kept} is kept: "
          "`parent-recap switch-back` switches to it again.")
    return 0


def register(sub) -> None:
    p = sub.add_parser("switch-back", help="Run the version installed before this one again: the "
                       "evening job, the Linked Device and Settings and Check then run it")
    p.set_defaults(func=cmd_switch_back)


def main(argv: list[str]) -> int:
    """`python -m family_brief.versions switch <Parent Recap's folder> <new version's folder>`,
    which install.sh runs with the new version's Python once its install finished."""
    if len(argv) != 3 or argv[0] != "switch":
        print("Usage: python -m family_brief.versions switch <install folder> <version folder>",
              file=sys.stderr)
        return 2
    switch(Path(argv[1]), Path(argv[2]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
