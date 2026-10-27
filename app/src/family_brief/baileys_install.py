"""The Baileys tree the Linked Device runs on (ADR 0025), installed with Parent Recap's own Node.

The tree is pinned by `package.json` and `package-lock.json` in `baileys/` beside this module,
with each package's integrity hash, at the Baileys release candidate tested in #181. The pinned
Node's npm installs exactly that tree with `npm ci` into `baileys/` in Parent Recap's folder,
next to the wilma CLI's (`own_node`), staged the same way, so an npm that fails or is ended leaves
the tree that was there. Package scripts don't run: the tree's only check the Node version.

install.sh (install.ps1 on Windows) runs `python -m family_brief.baileys_install` on every
install, once the runtimes are recorded.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from . import failure, install_record, own_node, private_files

BAILEYS_PACKAGE = "@whiskeysockets/baileys"
BAILEYS_VERSION = "7.0.0-rc14"
PINNED = Path(__file__).parent / "baileys"
NPM_FILES = ("package.json", "package-lock.json")
INSTALL_SECONDS = 600  # about 90 MB


def folder() -> Path:
    """Where the install puts the tree, next to the wilma CLI's folder."""
    return own_node.home() / "baileys"


def installed_version(where: Path | None = None) -> str | None:
    """The version of Baileys in `where` (Parent Recap's own tree unless given), as its package
    says, or None."""
    package = (where or folder()) / "node_modules" / BAILEYS_PACKAGE / "package.json"
    try:
        version = json.loads(package.read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return None
    return version if isinstance(version, str) else None


def up_to_date(where: Path | None = None) -> bool:
    """Whether the tree in `where` (Parent Recap's own unless given) is the pinned one: npm
    installed it from the committed npm files, and it has the pinned Baileys."""
    where = where or folder()
    try:
        same = all((where / name).read_bytes() == (PINNED / name).read_bytes() for name in NPM_FILES)
    except OSError:
        return False
    return same and installed_version(where) == BAILEYS_VERSION


def install() -> str:
    """Installs the pinned tree with Parent Recap's own Node, unless it's there, and adds its
    folder to setup's record. Returns `up-to-date`, `installed`, `updated` (another tree was
    there), `no-node` (the install didn't leave Parent Recap's own Node) or `failed`, which
    leaves the tree that was there as it was."""
    if up_to_date():
        return "up-to-date"
    npm = own_node.npm()
    if npm is None:
        return "no-node"
    there = folder().exists()
    # Before npm runs, so uninstall removes the folder however this install ends.
    install_record.add("baileys", str(folder()))
    reason = own_node.install_staged(folder(), lambda staging: _npm_ci(npm, staging),
                                     "The Linked Device's WhatsApp library didn't install",
                                     INSTALL_SECONDS)
    if reason:
        return "failed"
    return "updated" if there else "installed"


def _npm_ci(npm: list[str], staging: Path) -> str | None:
    """Has `npm` install the tree the committed npm files pin into `staging`. Returns why that
    didn't leave the pinned tree there, or None."""
    for name in NPM_FILES:
        shutil.copyfile(PINNED / name, staging / name)
    proc = own_node.run_npm(npm, ["ci", "--prefix", str(staging), "--ignore-scripts",
                                  "--no-audit", "--no-fund"], INSTALL_SECONDS, cwd=staging)
    if proc.returncode != 0:
        return failure.exited("npm", proc.returncode, proc.stderr, proc.stdout)
    version = installed_version(staging)
    if version is None:
        return "npm finished without installing the WhatsApp library."
    if version != BAILEYS_VERSION:
        return f"npm installed version {version} of the WhatsApp library, not {BAILEYS_VERSION}."
    return None


def main(argv: list[str]) -> int:
    """`python -m family_brief.baileys_install install`, which the installers run. It says what
    changed, if anything, and never fails the install: WhatsApp is one Source, and the next
    install tries again."""
    if argv != ["install"]:
        print("Usage: python -m family_brief.baileys_install install", file=sys.stderr)
        return 2
    private_files.restrict_new_files()  # install.sh runs this with the shell's umask
    try:
        result = install()
    except Exception as e:  # noqa: BLE001 - the install goes on, and the next one tries again
        failure.logged("The Linked Device's WhatsApp library didn't install",
                       failure.raised(e, "The install"))
        result = "failed"
    again = "WhatsApp can't be read as a Linked Device until it is. Run the install again later."
    said = {"installed": "✅ The Linked Device's WhatsApp library is installed.",
            "updated": f"✅ The Linked Device's WhatsApp library is updated to {BAILEYS_VERSION}.",
            "failed": f"⚠️  The Linked Device's WhatsApp library couldn't be installed. {again}",
            "no-node": "⚠️  The Linked Device's WhatsApp library couldn't be installed: Parent "
                       f"Recap's own Node is missing. {again}"}.get(result)
    if said:
        print(said)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
