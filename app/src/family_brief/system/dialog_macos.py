"""The macOS adapter of `dialog`: an AppleScript dialog with hidden input, its script on
`osascript`'s standard input and the typed text back on its standard output, which is captured,
so the secret is never on a command line, where `ps` would show it."""
from __future__ import annotations

import os
import subprocess

from . import dialog  # its names are read at call time: dialog imports this module first

# macOS's own lock icon, so the password dialog doesn't show osascript's generic folder icon.
LOCK_ICON = "/System/Library/CoreServices/CoreTypes.bundle/Contents/Resources/LockedIcon.icns"
_SAVE, _CANCEL = "Save", "Cancel"


def quote(s: str) -> str:
    """`s` as an AppleScript string."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def ask(message: str, other: str | None) -> str:
    buttons = [b for b in (other, _CANCEL, _SAVE) if b]
    icon = f"((POSIX file {quote(LOCK_ICON)}) as alias)" if os.path.isfile(LOCK_ICON) else "note"
    # Returns "1" and the typed text for Save, "2" for the other button; Cancel exits with -128.
    script = (
        f"set r to display dialog {quote(message)} default answer \"\" with hidden answer "
        f"with title {quote(dialog.TITLE)} with icon {icon} "
        f"buttons {{{', '.join(quote(b) for b in buttons)}}} "
        f"default button {quote(_SAVE)} cancel button {quote(_CANCEL)}\n"
        f"if button returned of r is {quote(_SAVE)} then return \"1\" & text returned of r\n"
        "return \"2\"\n"
    )
    try:
        proc = subprocess.run(["osascript", "-"], input=script, capture_output=True, text=True, encoding="utf-8")
    except OSError as e:  # no osascript: not a Mac
        raise dialog.NoWayToAsk() from e
    if proc.returncode != 0:
        if "(-128)" in proc.stderr:
            raise dialog.Cancelled()
        raise dialog.NoWayToAsk()
    answer = proc.stdout.removesuffix("\n")
    if answer.startswith("1"):
        return answer[1:]
    raise dialog.OtherChosen()
