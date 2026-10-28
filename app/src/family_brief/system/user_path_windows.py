"""The PATH on Windows: the user's own Path under HKCU\\Environment, which Windows joins to the PC's
for every window opened afterwards. Changing it needs no administrator. Windows is told it
changed, so a PowerShell opened from the Start menu right after sees it."""
from __future__ import annotations

import ntpath
from pathlib import Path

KEY = "Environment"
VALUE = "Path"
HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG = 0xFFFF, 0x001A, 0x0002


def _same(entry: str, folder: str) -> bool:
    entry = ntpath.expandvars(entry.strip().strip('"'))
    return bool(entry) and ntpath.normcase(ntpath.normpath(entry)) == \
        ntpath.normcase(ntpath.normpath(folder))


def with_folder(path: str, folder: str) -> str:
    """`path` with `folder` at its end, unless it is already in it."""
    if any(_same(e, folder) for e in path.split(";")):
        return path
    return f"{path.rstrip(';')};{folder}" if path.strip(";") else folder


def without_folder(path: str, folder: str) -> str:
    """`path` without `folder`, and every other entry as it was."""
    return ";".join(e for e in path.split(";") if not _same(e, folder))


def _read() -> tuple[str, int]:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as k:
            value, kind = winreg.QueryValueEx(k, VALUE)
    except FileNotFoundError:
        return "", winreg.REG_EXPAND_SZ
    return (value if isinstance(value, str) else ""), kind


def _write(value: str, kind: int) -> None:
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as k:
        if value:
            winreg.SetValueEx(k, VALUE, 0, kind, value)
        else:  # it held only Parent Recap's folder, so add made it
            try:
                winreg.DeleteValue(k, VALUE)
            except FileNotFoundError:
                pass
    _announce()


def _announce() -> None:
    """Tells Windows the environment changed, as the Settings app does, so Explorer's next
    windows read it. A program that doesn't answer within a few seconds is left behind."""
    import ctypes
    from ctypes import wintypes
    result = wintypes.DWORD()
    ctypes.windll.user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment",
                                             SMTO_ABORTIFHUNG, 5000, ctypes.byref(result))


def add(folder: Path) -> None:
    value, kind = _read()
    new = with_folder(value, str(folder))
    if new != value:
        _write(new, kind)


def has(folder: Path) -> bool:
    return any(_same(e, str(folder)) for e in _read()[0].split(";"))


def remove(folder: Path) -> None:
    value, kind = _read()
    new = without_folder(value, str(folder))
    if new != value:
        _write(new, kind)


def where() -> str:
    return "your user PATH"
