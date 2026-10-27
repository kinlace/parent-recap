"""The Baileys tree the Linked Device runs on is pinned in the committed npm files (ADR 0025)."""
from __future__ import annotations

import json

from family_brief import baileys_install


def lockfile() -> dict:
    return json.loads((baileys_install.PINNED / "package-lock.json").read_text(encoding="utf-8"))


def test_the_lockfile_pins_the_baileys_version_the_program_names():
    package = json.loads((baileys_install.PINNED / "package.json").read_text(encoding="utf-8"))
    lock = lockfile()

    assert package["dependencies"] == {"@whiskeysockets/baileys": "7.0.0-rc14"}
    assert lock["packages"][""]["dependencies"] == package["dependencies"]
    assert lock["packages"]["node_modules/@whiskeysockets/baileys"]["version"] == "7.0.0-rc14"
    assert baileys_install.BAILEYS_VERSION == "7.0.0-rc14"  # the release candidate tested in #181


def test_every_package_in_the_tree_comes_from_npm_with_an_integrity_hash():
    packages = {name: p for name, p in lockfile()["packages"].items() if name}

    assert len(packages) > 50  # the whole tree, not only Baileys
    for name, p in packages.items():
        assert p["resolved"].startswith("https://registry.npmjs.org/"), name
        assert p["integrity"].startswith("sha512-"), name
