"""The wilma CLI's whole npm tree is pinned in the committed npm files (ADR 0008, #975)."""
from __future__ import annotations

import json
from typing import Any

import pytest

from family_brief import setup_wilma

CLI = "node_modules/@wilm-ai/wilma-cli"


def package() -> dict[str, Any]:
    return json.loads((setup_wilma.PINNED / "package.json").read_text(encoding="utf-8"))


def lockfile() -> dict[str, Any]:
    return json.loads((setup_wilma.PINNED / "package-lock.json").read_text(encoding="utf-8"))


def why_not_pinned(package: dict[str, Any], lock: dict[str, Any]) -> str | None:
    """Why these npm files don't pin the tree of the CLI at `WILMA_CLI_VERSION`, each package
    from npm's registry with its integrity hash, or None when they do."""
    pinned = {setup_wilma.PACKAGE: setup_wilma.WILMA_CLI_VERSION}
    if package.get("dependencies") != pinned:
        return f"package.json doesn't name only the CLI at {setup_wilma.WILMA_CLI_VERSION}"
    packages = lock["packages"]
    if packages[""].get("dependencies") != pinned:
        return "the lockfile was made from another package.json"
    if packages[CLI]["version"] != setup_wilma.WILMA_CLI_VERSION:
        return f"the lockfile has the CLI at {packages[CLI]['version']}"
    for name, p in packages.items():
        if not name:
            continue
        if not p.get("resolved", "").startswith("https://registry.npmjs.org/"):
            return f"{name} is resolved from {p.get('resolved')}"
        if not p.get("integrity", "").startswith("sha512-"):
            return f"{name} has no integrity hash"
    return None


def test_the_npm_files_pin_the_cli_version_setup_names_and_its_whole_tree():
    assert why_not_pinned(package(), lockfile()) is None
    assert len(lockfile()["packages"]) > 100  # the whole tree, not only the CLI


def test_no_package_in_the_tree_needs_its_install_script():
    # `npm ci --ignore-scripts` runs none, so a package that needs one wouldn't work.
    assert [name for name, p in lockfile()["packages"].items() if p.get("hasInstallScript")] == []


def another_cli_version(package: dict, lock: dict) -> None:
    lock["packages"][CLI]["version"] = "2.5.1"


def another_pin_in_package_json(package: dict, lock: dict) -> None:
    package["dependencies"][setup_wilma.PACKAGE] = "^2.5.0"


def lockfile_of_another_package_json(package: dict, lock: dict) -> None:
    lock["packages"][""]["dependencies"][setup_wilma.PACKAGE] = "2.4.0"


def no_integrity_hash(package: dict, lock: dict) -> None:
    del lock["packages"]["node_modules/zod"]["integrity"]


def another_host(package: dict, lock: dict) -> None:
    lock["packages"]["node_modules/zod"]["resolved"] = "https://registry.example.com/zod.tgz"


def a_git_dependency(package: dict, lock: dict) -> None:
    lock["packages"]["node_modules/zod"]["resolved"] = "git+ssh://git@github.com/colinhacks/zod.git"


@pytest.mark.parametrize("change, said", [
    (another_cli_version, "the lockfile has the CLI at 2.5.1"),
    (another_pin_in_package_json, "package.json doesn't name only the CLI at"),
    (lockfile_of_another_package_json, "the lockfile was made from another package.json"),
    (no_integrity_hash, "node_modules/zod has no integrity hash"),
    (another_host, "node_modules/zod is resolved from https://registry.example.com/zod.tgz"),
    (a_git_dependency, "node_modules/zod is resolved from git+ssh://"),
], ids=lambda v: v.__name__ if callable(v) else "")
def test_npm_files_that_pin_something_else_are_caught(change, said):
    files = package(), lockfile()
    change(*files)

    assert (why_not_pinned(*files) or "").startswith(said)
