"""The installed program's versions, and switching between them (ADR 0047).

`install.sh`, and on Windows `windows_install.py`, install each release in a folder of its own,
`versions/<version>.<random>` in Parent Recap's folder, with its own venv, its `VERSION` and its
`RUNTIMES` (the folders in `runtime/` its venv and the wilma CLI run on), while the running
version goes on as it was. Only once that install finished does `switch` point `app` at it: `app`
is the one path everything outside the program starts it from (the evening jobs, the Linked
Device's job, Settings and Check, the `parent-recap` command), so it stays the same across
versions and an update leaves them as they are. The version `app` pointed at before is kept, one
version deep, as `versions/previous`, and `parent-recap switch-back` (`switch_back`) points `app`
at it again.

`app` and `previous` are links (system/links.py): symlinks on a Mac and a Linux server, junctions
on Windows. A junction isn't replaced in one step, so there `app` is missing for a moment, and a
switch happens only under the run lock, with the Linked Device's job stopped before it and started
again after it (`_still`).

An install from before this kept the program in `app` itself. The first install after it moves
that folder into `versions/` as the previous version: its venv names `app`, so it runs again once
`app` points at it. Windows doesn't let a folder be moved while a program started from it runs,
so there the Linked Device's job is stopped for the move too, and anything else that holds the
folder stops the switch with `Held`, the install left as it was.

Only the standard library at the top, as `install.sh` runs `switch` with the new version's Python
before anything else of the program has run there.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Iterator

from .system import links

APP = "app"
VERSIONS = "versions"
PREVIOUS = "previous"  # in VERSIONS, a link to the version switched from
RUNTIMES = "RUNTIMES"  # in a version's folder: the runtime/ folders it runs on, a line each
RUNTIME = "runtime"
MOVE_TRIES, MOVE_WAIT = 6, 0.5  # an install from before versions that is held, in seconds


class NoPrevious(Exception):
    """There is no previous version to switch back to."""


class Held(Exception):
    """An install from before versions couldn't be moved into `versions/`, since a program holds
    its folder, as Windows' running programs do; nothing was changed."""

    def __init__(self, app: Path, error: OSError):
        super().__init__(
            f"Parent Recap couldn't move its program in {app} to make room for the new version, "
            f"since another program uses that folder ({error.strerror or error}). Nothing was "
            "changed: the version installed before runs as it did. Close Parent Recap Settings, "
            "and any window that runs Parent Recap or was opened in that folder, then run the "
            "install line again.")


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
        if links.is_link(folder / APP) and (folder / RUNTIME).is_dir():
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
    to = links.target(link)
    if to is None:
        return None
    target = versions / to.name
    return target if target.is_dir() else None


def _point(link: Path, to: str) -> None:
    """Points `link` at `to`, relative to its folder: in one step on a Mac and a Linux server, so
    whatever starts the program in between finds one version or the other, never none; on
    Windows with `link` missing for a moment, which `_still` covers."""
    links.point(link, Path(to))


def _unpoint(link: Path) -> None:
    if links.is_link(link):
        links.remove(link)


@contextmanager
def _still(folder: Path, config: str | None = None, locked: bool = False) -> Iterator[None]:
    """Where a link isn't replaced in one step (Windows), holds the run lock for the block, so no
    Brief runs while `app` may be missing, and stops the Linked Device's job, which runs from
    `app`, starting it again after. Raises run_lock.Busy when a run holds the lock of the config
    at `config` (the Household's own unless given). With `locked`, the caller holds that lock
    already in this process, as the update's switch back does, so it isn't taken again: a
    second hold of it in one process would find it busy. On a Mac and a Linux server, nothing."""
    if links.one_step():
        yield
        return
    from .system import jobs
    with ExitStack() if locked else _run_lock(config):
        try:
            running = jobs.always_status().running
        except Exception:  # noqa: BLE001 - one that can't be read is left as it is
            running = False
        if running:
            jobs.stop_always()
        try:
            yield
        finally:
            if running:
                try:
                    jobs.start_always()
                except Exception as e:  # noqa: BLE001 - it starts again at the next sign-in
                    print(f"The Linked Device wasn't started again ({e}); it starts at the next "
                          "sign-in.")


@contextmanager
def _run_lock(config: str | None) -> Iterator[None]:
    """The run lock (run_lock.py) of the config at `config`, for the block. Not taken when an
    update the family started holds it itself, nor when the evening run's own update does, as the
    switch then runs inside that run; nor where no run has ever been, which has no lock to take."""
    from . import run_lock
    from .config import Config
    from .schedule_update import HOLDS_RUN_LOCK, UPDATING
    try:
        cfg = Config.load(config)
    except Exception:  # noqa: BLE001 - doctor says what's wrong with it
        cfg = Config(kids=[])
    path = run_lock.lock_path(cfg)
    held = ExitStack()
    if path.parent.is_dir() and os.environ.get(HOLDS_RUN_LOCK) != str(path):
        try:
            held.enter_context(run_lock.exclusive(cfg))
        except run_lock.Busy:
            if not os.environ.get(UPDATING):
                raise
    with held:
        yield


def _take_over(folder: Path) -> Path | None:
    """Moves an install from before versions, the program in `app` itself, into `versions/`, and
    returns its new folder, or None when there is none. It runs on whatever `runtime/` holds now,
    which its RUNTIMES then names. Raises Held when a program holds the folder."""
    app = folder / APP
    if links.is_link(app) or not app.is_dir():
        return None
    versions = folder / VERSIONS
    versions.mkdir(mode=0o700, exist_ok=True)
    name = f"{version(app)}.before"
    tries = 1
    while (versions / name).exists() or links.is_link(versions / name):
        tries += 1
        name = f"{version(app)}.before{tries}"
    # `app` is missing from here until switch's link: the one moment an install from before
    # versions has none, once. Tried a few times: Windows' search indexer or Defender may have a
    # file in it open for a moment.
    for tries_left in range(MOVE_TRIES - 1, -1, -1):
        try:
            os.rename(app, versions / name)
            break
        except OSError as e:
            if not tries_left:
                raise Held(app, e) from e
            time.sleep(MOVE_WAIT)
    moved = versions / name
    if not (moved / RUNTIMES).exists():
        names = sorted(p.name for p in (folder / RUNTIME).glob("*") if not p.name.startswith("."))
        (moved / RUNTIMES).write_text("".join(f"{r}\n" for r in names), encoding="utf-8")
    return moved


def switch(folder: Path, new: Path) -> None:
    """Points `app` in `folder` at the version installed in `new`, once its install finished.
    The version `app` pointed at before is kept as the previous one, unless it's the same version
    as `new` (the install run again, as manage does to add Google's packages), which keeps the
    previous one there was; that copy stays until the next switch, as a run started from it may
    still be going. Then removes every other version's folder, such as one an install that
    failed left, and the runtimes none of them runs on. `new` may be the folder `app` points at
    already, as the Windows installer installs the same version again in place: then only the
    rest is removed. Raises Held, or run_lock.Busy on Windows, having changed nothing."""
    new = folder / VERSIONS / new.name
    if current(folder) == new:
        _clean(folder)
        return
    spare = None
    with _still(folder):
        taken = _take_over(folder)
        if taken is not None:
            try:
                _point(folder / APP, f"{VERSIONS}/{new.name}")
            except OSError:
                os.rename(taken, folder / APP)  # back as it was, so the version before runs
                raise
        old = taken or current(folder)
        before = previous(folder)
        if old is not None and old != new:
            if version(old) != version(new):
                before = old
            else:
                spare = old
        _point(folder / APP, f"{VERSIONS}/{new.name}")
        if before is not None and before != new:
            _point(folder / VERSIONS / PREVIOUS, before.name)
        else:
            _unpoint(folder / VERSIONS / PREVIOUS)
    _clean(folder, spare)


def switch_back(folder: Path, config: str | None = None, locked: bool = False) -> tuple[str, str]:
    """Points `app` at the previous version, and keeps the one switched from as the previous one,
    so running it again switches forward. Returns the version now running and the one kept.
    Raises NoPrevious when there is none, or run_lock.Busy on Windows, having changed nothing.
    `locked`: the caller holds the run lock (`_still`)."""
    now, before = current(folder), previous(folder)
    if now is None or before is None or before == now:
        raise NoPrevious()
    with _still(folder, config, locked):
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
            if links.is_link(child):
                links.remove(child)  # never what is in the folder it points at
            elif not child.is_dir():
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
    Settings and Check and the evening jobs only where the install makes them: on a Mac and a PC,
    not on a server."""
    from .system import entries, profile
    python = str(folder / APP / ".venv" / profile.current().python)
    steps = [[python, "-m", "family_brief.command_path", "install", str(folder)],
             [python, "-m", "family_brief.setup_wilma", "update"],
             [python, "-m", "family_brief.baileys_install", "install"]]
    if entries.offered():
        steps += [[python, "-m", "family_brief.program_entries", "install", str(folder)],
                  [python, "-m", "family_brief.schedule_update"]]
    return steps


def switch_back_whole(folder: Path, config: str | None = None, locked: bool = False,
                      **run) -> tuple[str, str]:
    """`switch_back`, then has the version switched to put its own pieces back and restarts the
    Linked Device, as `parent-recap switch-back` and the update (update.py) do. `run` goes to
    each step's subprocess.run, such as where its output goes. `locked`: the caller holds the
    run lock (`_still`). Raises NoPrevious, or run_lock.Busy on Windows."""
    from .system import jobs
    running, kept = switch_back(folder, config, locked)
    for step in _steps(folder):
        try:
            subprocess.run(step, stdin=subprocess.DEVNULL, check=False, **run)
        except OSError as e:  # each never fails the switch back, which is done
            print(f"{step[2] if len(step) > 2 else step[0]} couldn't be started ({e}).",
                  file=run.get("stdout") or sys.stdout)
    # From here, not with the version switched to: one from before versions has no restart.
    try:
        jobs.restart_always()  # the Linked Device's, so it runs the version switched to
    except Exception as e:  # noqa: BLE001 - it runs that version from its next start
        print(f"The Linked Device wasn't restarted ({e}); it runs version {running} from its "
              "next start.")
    return running, kept


BUSY = ("A Parent Recap run is going right now, so the version that runs wasn't changed. Wait "
        "for it to finish (it can take several minutes), then {again}.")


def cmd_switch_back(args) -> int:
    from . import own_node, run_lock
    folder = own_node.home()
    try:
        running, kept = switch_back_whole(folder, args.config)
    except NoPrevious:
        print("There is no previous version to switch back to: only the one running now is "
              "installed. Nothing was changed.")
        return 1
    except run_lock.Busy:
        print(BUSY.format(again="run parent-recap switch-back again"))
        return run_lock.BUSY
    print(f"✅ Parent Recap runs version {running} again. Version {kept} is kept: "
          "`parent-recap switch-back` switches to it again.")
    return 0


def register(sub) -> None:
    p = sub.add_parser("switch-back", help="Run the version installed before this one again: the "
                       "evening job, the Linked Device and Settings and Check then run it")
    p.set_defaults(func=cmd_switch_back)


def main(argv: list[str]) -> int:
    """`python -m family_brief.versions switch <Parent Recap's folder> <new version's folder>`,
    which install.sh and the Windows installer run with the new version's Python once its install
    finished. It exits with 1, having said why, when it changed nothing: a program holds an
    install from before versions, or on Windows a run is going."""
    if len(argv) != 3 or argv[0] != "switch":
        print("Usage: python -m family_brief.versions switch <install folder> <version folder>",
              file=sys.stderr)
        return 2
    try:
        switch(Path(argv[1]), Path(argv[2]))
    except Held as e:
        print(e)
        return 1
    except OSError as e:
        # A link the system didn't let it make, in its own words, which the installer says and
        # an update keeps in its log (ADR 0052). links.point puts back a link it couldn't replace.
        print(f"Parent Recap couldn't switch {Path(argv[1]) / APP} to the new version "
              f"({type(e).__name__}: {e}).")
        return 1
    except Exception as e:
        # Only here: a switch the run lock stops, on Windows, has the program's packages to
        # import it with, and install.sh's tests run this module without them.
        from . import run_lock
        if not isinstance(e, run_lock.Busy):
            raise
        print(BUSY.format(again="run the install line again"))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
