"""Packages made here for tests that run the real pip offline, from a folder (`PIP_FIND_LINKS`).

`wheel` writes a prebuilt package. This module is also a build backend, for a source package
or a local project: its `pyproject.toml` names it with `backend-path = ["."]` and a copy sits
next to it, with `fake-package.json` saying what to build. Given a `build_marker`, building
touches that file and fails, so a test can see whether pip tried to compile the package.
"""
from __future__ import annotations

import json
import shutil
import tarfile
import zipfile
from pathlib import Path


def wheel(folder: Path, name: str, version: str, files: dict[str, str] | None = None,
          requires: tuple[str, ...] = (), tag: str = "py3-none-any",
          extras: dict[str, list[str]] | None = None) -> Path:
    dist = name.replace("-", "_")
    info = f"{dist}-{version}.dist-info"
    contents = {
        **(files or {}),
        f"{info}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
                            + "".join(f"Requires-Dist: {r}\n" for r in requires)
                            + "".join(f"Provides-Extra: {extra}\n" + "".join(
                                f'Requires-Dist: {r}; extra == "{extra}"\n' for r in rs)
                                for extra, rs in (extras or {}).items()),
        f"{info}/WHEEL": f"Wheel-Version: 1.0\nGenerator: tests\nRoot-Is-Purelib: true\nTag: {tag}\n",
    }
    path = folder / f"{dist}-{version}-{tag}.whl"
    with zipfile.ZipFile(path, "w") as z:
        for member, text in contents.items():
            z.writestr(member, text)
        z.writestr(f"{info}/RECORD", "".join(f"{m},,\n" for m in [*contents, f"{info}/RECORD"]))
    return path


def project(folder: Path, name: str, version: str, requires: tuple[str, ...] = (),
            build_marker: Path | None = None, extras: dict[str, list[str]] | None = None) -> Path:
    """A source tree pip builds with this module, with `extras` such as {"google": [...]}."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "pyproject.toml").write_text(
        '[build-system]\nrequires = []\nbuild-backend = "fake_packages"\nbackend-path = ["."]\n')
    shutil.copy(__file__, folder / "fake_packages.py")
    (folder / "fake-package.json").write_text(json.dumps({
        "name": name, "version": version, "requires": list(requires), "extras": extras or {},
        "build_marker": str(build_marker) if build_marker else None}))
    return folder


def sdist(folder: Path, name: str, version: str, build_marker: Path) -> Path:
    """A package published only as source, which pip would have to compile."""
    dist = name.replace("-", "_")
    src = project(folder / f".{dist}-src", name, version, build_marker=build_marker)
    (src / "PKG-INFO").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
    path = folder / f"{dist}-{version}.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        tar.add(src, arcname=f"{dist}-{version}")
    shutil.rmtree(src)
    return path


# --- PEP 517 hooks, run by pip in the source tree ------------------------------------------

def build_wheel(wheel_directory, config_settings=None, metadata_directory=None) -> str:
    spec = json.loads(Path("fake-package.json").read_text())
    if spec["build_marker"]:
        Path(spec["build_marker"]).touch()
        raise SystemExit("this package only builds from source")
    # Editable: the tree itself goes on the path.
    pth = {f"_{spec['name'].replace('-', '_')}.pth": f"{Path.cwd()}\n"}
    return wheel(Path(wheel_directory), spec["name"], spec["version"], pth, tuple(spec["requires"]),
                 extras=spec["extras"]).name


build_editable = build_wheel


def build_sdist(sdist_directory, config_settings=None) -> str:
    raise SystemExit("not needed by the tests")
