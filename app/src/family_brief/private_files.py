"""Keep what Parent Recap writes readable by the Mac account that runs it only.

Home folders on macOS let every standard account in (group `staff`), so a Kid's own account
could otherwise read the archive's raw messages, the logs and the model diagnostics."""
from __future__ import annotations

import os
import stat
from pathlib import Path

from .config import Config

UMASK = 0o077  # new files 600, new folders 700; also set on the launchd jobs


def restrict_new_files() -> None:
    os.umask(UMASK)


def write(path: Path, text: str) -> None:
    """Write `text` to `path` readable by this Mac account only, whatever the umask, also over a
    file that others could read."""
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
        os.fchmod(f.fileno(), 0o600)
        f.write(text)


def tighten(cfg: Config) -> None:
    """Make the archive, log, Weekend Picks and config folders owner-only, and the files directly
    in them (state, config backups), so installs from before this was done are fixed on their
    next run. A file it can't change is left as it is rather than stopping the run."""
    archive = cfg.archive.resolved_dir()
    default = Path.home() / "ParentRecap"  # model diagnostics always go to its logs/
    folders = [archive, archive / "logs", default, default / "logs",
               cfg.weekend_events.resolved_dir(), cfg.resolved_state_path().parent]
    for d in dict.fromkeys(folders):  # the same folder can be listed twice
        if not d.is_dir():
            continue
        _strip_others(d)
        for f in d.iterdir():
            if f.is_file() and not f.is_symlink():
                _strip_others(f)


def _strip_others(p: Path) -> None:
    try:
        mode = stat.S_IMODE(p.stat().st_mode)
        if mode & 0o077:
            p.chmod(mode & 0o700)
    except OSError:
        pass
