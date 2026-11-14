"""The Sources in folders of their own, `sources/<group>/<name>` (ADR 0051): shared code names one
only where it lists the Sources, so a change to a Source's folder changes nothing the other
Sources' Households run. Each folder there is is checked, so a Source moved in is covered too."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import family_brief
from family_brief import sources

PROGRAM = Path(family_brief.__file__).parent
ROOT = PROGRAM.name  # family_brief

# The places shared code may name a Source's folder from: where it lists the Sources.
LISTS = {
    "sources/__init__.py": "the registry's list of Sources",
    "config.py": "the config's sections",
    "setup_steps.py": "the setup commands",
}

# A module named in a string, as a record's `_in` names its hooks: sources.<group>.<name>...
NAMED_IN_TEXT = re.compile(r"""["'](sources(?:\.\w+){2,})""")


def folders() -> list[str]:
    """Each Source's folder, as `<group>/<name>`: a package in a package under `sources`."""
    return sorted(f"{group.name}/{source.name}"
                  for group in (PROGRAM / "sources").iterdir() if (group / "__init__.py").is_file()
                  for source in group.iterdir() if (source / "__init__.py").is_file())


def module_of(path: str) -> str:
    """The module a file under the program is, `sources/__init__.py` being `family_brief.sources`."""
    parts = [ROOT, *Path(path).with_suffix("").parts]
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def named(path: str, text: str) -> set[str]:
    """Each module the file at `path`, under the program, imports or names in a string."""
    package = module_of(path) if path.endswith("__init__.py") else module_of(path).rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")[:len(package.split(".")) - node.level + 1]
                module = ".".join([*base, *([node.module] if node.module else [])])
            else:
                module = node.module or ""
            found |= {module, *(f"{module}.{alias.name}" for alias in node.names)}
    found |= {f"{ROOT}.{name}" for name in NAMED_IN_TEXT.findall(text)}
    return found


def reached(path: str, text: str, folder: str) -> set[str]:
    """The modules of the Source folder `folder` the file at `path` names."""
    top = f"{ROOT}.sources.{folder.replace('/', '.')}"
    return {m for m in named(path, text) if m == top or m.startswith(top + ".")}


def shared_files() -> list[str]:
    """Each Python file of the program outside the Sources' folders, relative to it."""
    inside = tuple(f"sources/{folder}/" for folder in folders())
    return sorted(p for p in (f.relative_to(PROGRAM).as_posix() for f in PROGRAM.rglob("*.py"))
                  if not p.startswith(inside))


def test_konnect_is_in_a_folder_of_its_own():
    assert "nl/konnect" in folders()


@pytest.mark.parametrize("folder", folders())
def test_shared_code_names_a_source_s_folder_only_where_it_lists_the_sources(folder):
    reaching = {path: sorted(modules) for path in shared_files()
                if (modules := reached(path, (PROGRAM / path).read_text(encoding="utf-8"), folder))}
    assert not set(reaching) - set(LISTS), (
        f"only {', '.join(f'{p} ({why})' for p, why in LISTS.items())} may name sources/{folder}: "
        f"{reaching}")


def test_each_place_that_may_name_a_source_s_folder_is_shared_code():
    assert set(LISTS) <= set(shared_files())


@pytest.mark.parametrize(("path", "text"), [
    ("ops.py", "from .sources.nl.konnect import collector"),
    ("ops.py", "from .sources.nl import konnect"),
    ("setup_server/health.py", "from ..sources.nl.konnect.collector import address"),
    ("run.py", "import family_brief.sources.nl.konnect.doctor"),
    ("sources/record.py", "from .nl.konnect.record import KONNECT"),
    ("uninstall.py", 'check = _in("sources.nl.konnect.doctor", "check")'),
])
def test_a_shared_module_reaching_into_a_source_s_folder_is_found(path, text):
    assert reached(path, text, "nl/konnect")


@pytest.mark.parametrize(("path", "text"), [
    ("ops.py", "from . import sources"),
    ("ops.py", "from .sources import record"),
    ("run.py", "import family_brief.sources"),
    ("collectors/evaka.py", "from ..sources.nl import other"),
    ("ops.py", 'add(FAIL, "konnect", "sources.nl")'),
])
def test_shared_code_naming_only_the_registry_is_not_reaching_into_a_folder(path, text):
    assert not reached(path, text, "nl/konnect")


def test_each_source_in_a_folder_says_which_part_it_is_and_the_rest_say_none():
    assert sorted(s.part for s in sources.ALL if s.part) == folders()
    for source in sources.ALL:
        if source.part:  # its folder is named after it
            assert source.part.rpartition("/")[2] == source.name
    assert sources.KONNECT.part == "nl/konnect"
    assert sources.WILMA.part is None
