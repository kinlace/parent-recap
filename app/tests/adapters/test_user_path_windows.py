"""The Windows adapter of the user's PATH: the folder added once to the user's own Path, and taken
out again with every other entry as it was (#560). The Path's text is checked on any system; the
registry for real on Windows, in a key of the test's own instead of HKCU\\Environment."""
from __future__ import annotations

import uuid

import pytest

from family_brief.system import user_path_windows as user_path

FOLDER = r"C:\Users\Aino\AppData\Local\ParentRecap\bin"


@pytest.mark.parametrize("path, added", [
    ("", FOLDER),
    (r"C:\Tools", rf"C:\Tools;{FOLDER}"),
    (r"C:\Tools;", rf"C:\Tools;{FOLDER}"),
    (rf"C:\Tools;{FOLDER}", rf"C:\Tools;{FOLDER}"),
    # Already there in another spelling Windows reads the same.
    (r"%LOCALAPPDATA%\Microsoft\WindowsApps;c:\users\aino\appdata\local\parentrecap\bin\\",
     r"%LOCALAPPDATA%\Microsoft\WindowsApps;c:\users\aino\appdata\local\parentrecap\bin\\"),
])
def test_adds_the_folder_once(path, added):
    assert user_path.with_folder(path, FOLDER) == added


@pytest.mark.parametrize("path, left", [
    (FOLDER, ""),
    (rf"%USERPROFILE%\.cargo\bin;{FOLDER};C:\Tools", r"%USERPROFILE%\.cargo\bin;C:\Tools"),
    (rf"C:\Tools;;{FOLDER.upper()}", r"C:\Tools;"),
    (r"C:\Tools", r"C:\Tools"),
])
def test_removes_only_the_folder(path, left):
    assert user_path.without_folder(path, FOLDER) == left


@pytest.fixture
def key(monkeypatch):
    """A key of the test's own in place of HKCU\\Environment, deleted afterwards."""
    import winreg
    name = rf"Software\ParentRecapTest\{uuid.uuid4().hex}"
    monkeypatch.setattr(user_path, "KEY", name)
    yield name
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, name)


@pytest.mark.windows
def test_in_the_registry_it_adds_once_and_removes_only_its_folder(key, tmp_path):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
        winreg.SetValueEx(k, "Path", 0, winreg.REG_EXPAND_SZ, r"%USERPROFILE%\.cargo\bin")
    folder = tmp_path / "ParentRecap" / "bin"

    user_path.add(folder)
    user_path.add(folder)

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
        assert winreg.QueryValueEx(k, "Path") == (rf"%USERPROFILE%\.cargo\bin;{folder}",
                                                  winreg.REG_EXPAND_SZ)
    assert user_path.has(folder)

    user_path.remove(folder)

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
        assert winreg.QueryValueEx(k, "Path") == (r"%USERPROFILE%\.cargo\bin", winreg.REG_EXPAND_SZ)
    assert not user_path.has(folder)


@pytest.mark.windows
def test_in_the_registry_a_path_it_made_goes_with_its_folder(key, tmp_path):
    import winreg
    folder = tmp_path / "bin"

    user_path.add(folder)
    user_path.remove(folder)

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
        with pytest.raises(FileNotFoundError):
            winreg.QueryValueEx(k, "Path")
