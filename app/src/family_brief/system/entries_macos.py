"""The entries on macOS: two small app bundles in `~/Applications`, where Spotlight and Launchpad
find them, each run by a shell script. They're made on the Mac, so they carry no quarantine flag
and Gatekeeper doesn't stop them, and a script needs no signature on Apple silicon as a binary
does. Neither tells another app what to do with AppleScript, which macOS would ask the family to
allow: Settings starts the setup page in the background, and Check has Terminal open a `.command`
script inside its bundle, as opening one in Finder does.

Both are `LSUIElement` apps, without a Dock icon, and Settings' script ends as soon as the page is
started: an app that is still running is only brought forward when it's opened again, so the
setup page would open only once."""
from __future__ import annotations

import plistlib
import shlex
import shutil
from pathlib import Path

from .entries import CHECK, SETTINGS

# Each entry's bundle id, by which uninstall knows a bundle with its name is this install's, and
# the name of its script in Contents/MacOS.
BUNDLE_IDS = {SETTINGS: "com.kinlace.parent-recap.settings", CHECK: "com.kinlace.parent-recap.check"}
EXECUTABLES = {SETTINGS: "settings", CHECK: "check"}
CHECK_SCRIPT = "Parent Recap Check.command"  # Terminal's window is titled after it
MADE_BY = "# Made by Parent Recap's install; parent-recap uninstall removes it."


def offered() -> bool:
    return True


def folder() -> Path:
    return Path.home() / "Applications"


def path(name: str) -> Path:
    return folder() / f"{name}.app"


def _command(target: Path) -> str:
    return shlex.quote(str(target / "app" / ".venv" / "bin" / "parent-recap"))


def scripts(name: str, target: Path) -> dict[str, str]:
    """The entry's scripts, by their path in the bundle."""
    if name == SETTINGS:
        # Started apart from the script, which then ends, and served until 30 minutes without use.
        return {f"Contents/MacOS/{EXECUTABLES[name]}": (
            f"#!/bin/sh\n{MADE_BY}\n"
            f"nohup {_command(target)} setup page >/dev/null 2>&1 &\n")}
    return {
        f"Contents/MacOS/{EXECUTABLES[name]}": (
            f"#!/bin/sh\n{MADE_BY}\n"
            f'exec /usr/bin/open -a Terminal "$(dirname "$0")/../Resources/{CHECK_SCRIPT}"\n'),
        # Waits for Return, so the window stays open for a screenshot whatever Terminal's own
        # setting for a shell that ended.
        f"Contents/Resources/{CHECK_SCRIPT}": (
            f"#!/bin/sh\n{MADE_BY}\n"
            f"{_command(target)} doctor\n"
            "echo\n"
            "echo 'Take a screenshot of this window if the Parent Recap team asked for one.'\n"
            "echo 'Press Return to close it.'\n"
            "read -r _\n"),
    }


def info(name: str) -> dict:
    return {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleDisplayName": name,
        "CFBundleExecutable": EXECUTABLES[name],
        "CFBundleIdentifier": BUNDLE_IDS[name],
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": name,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1",
        "LSUIElement": True,
    }


def place(name: str, target: Path) -> None:
    bundle = path(name)
    if bundle.exists() or bundle.is_symlink():
        if not placed(name):
            raise OSError(f"{bundle} is there already, and Parent Recap's install didn't make it")
        shutil.rmtree(bundle)  # made again whole, so nothing an older version had stays
    contents = bundle / "Contents"
    contents.mkdir(parents=True)
    with open(contents / "Info.plist", "wb") as f:
        plistlib.dump(info(name), f)
    for inside, text in scripts(name, target).items():
        script = bundle / inside
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(text, encoding="utf-8", newline="\n")
        script.chmod(0o755)


def placed(name: str) -> bool:
    bundle = path(name)
    if bundle.is_symlink() or not bundle.is_dir():
        return False
    try:
        with open(bundle / "Contents" / "Info.plist", "rb") as f:
            listed = plistlib.load(f)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return False
    return isinstance(listed, dict) and listed.get("CFBundleIdentifier") == BUNDLE_IDS[name]


def remove(name: str) -> None:
    shutil.rmtree(path(name))


def where() -> str:
    return "~/Applications"
