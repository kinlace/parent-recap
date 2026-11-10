"""The SchoolSoft capture's two commands a guardian pastes (tools/schoolsoft-capture, #876):
get.sh, which puts its pinned Python and its files in ~/SchoolSoftCapture and starts the
capture, and remove, which takes away everything it stored. Run with stand-ins for curl
(serving a stand-in Python tarball and the repository's own files), sysctl, security and
launchctl, in a fake home."""
from __future__ import annotations

import hashlib
import io
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.macos  # bash with shell-script stand-ins

ROOT = Path(__file__).resolve().parents[3]
TOOL = ROOT / "tools" / "schoolsoft-capture"


def pins(path: Path, names: str) -> dict[str, str]:
    return dict(re.findall(rf"^({names})=(\S+)$", path.read_text(encoding="utf-8"), re.M))


def test_it_pins_the_python_install_sh_pins():
    names = "PYTHON_VERSION|PYTHON_BUILD|PYTHON_SHA256_AARCH64|PYTHON_SHA256_X86_64"
    assert pins(TOOL / "get.sh", names) == pins(ROOT / "install.sh", names)
    assert len(pins(TOOL / "get.sh", names)) == 4


def test_remove_names_what_the_tool_stores():
    remove = (TOOL / "remove.sh").read_text(encoding="utf-8")
    source = (TOOL / "schoolsoft_capture.py").read_text(encoding="utf-8")
    for name in ("LABEL", "KEYCHAIN_SERVICE", "KEYCHAIN_ACCOUNT"):
        value = re.search(rf'^{name} = "([^"]+)"', source, re.M).group(1)
        assert value in remove, name
    assert 'FOLDER = Path.home() / "SchoolSoftCapture"' in source and '"$HOME/SchoolSoftCapture"' in remove
    assert 'REMOVE = "~/SchoolSoftCapture/remove"' in source


def fake(path: Path, body: str) -> None:
    path.write_text("#!/bin/bash\n" + body + "\n", encoding="utf-8")
    path.chmod(0o755)


def python_tarball() -> bytes:
    """python-build-standalone's layout, with a python3 that logs how it was started."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        body = b'#!/bin/bash\necho "python3 $*" >> "$HOME/../calls.log"\n'
        info = tarfile.TarInfo("python/bin/python3")
        info.size, info.mode = len(body), 0o755
        tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


@pytest.fixture
def mac(tmp_path: Path) -> dict:
    home, bin_, served = tmp_path / "home", tmp_path / "bin", tmp_path / "served"
    for d in (home, bin_, served):
        d.mkdir()
    (served / "python.tar.gz").write_bytes(python_tarball())
    fake(bin_ / "uname", "echo Darwin")
    fake(bin_ / "sysctl", "echo 1")
    fake(bin_ / "id", "echo 501")
    # Serves the repository's files from the stable branch's raw URLs, and the Python from either mirror.
    fake(bin_ / "curl", f"""
url=; out=
while [ $# -gt 0 ]; do case "$1" in -o) out=$2; shift ;; http*) url=$1 ;; esac; shift; done
echo "curl $url" >> "{tmp_path}/calls.log"
case "$url" in
  https://raw.githubusercontent.com/kinlace/parent-recap/stable/*) cp "{ROOT}/${{url#https://raw.githubusercontent.com/kinlace/parent-recap/stable/}}" "$out" ;;
  *cpython-*-aarch64-apple-darwin-install_only.tar.gz) cp "{served}/python.tar.gz" "$out" ;;
  *) exit 22 ;;
esac""")
    fake(bin_ / "security", f'echo "security $*" >> "{tmp_path}/calls.log"; [ "$1" = find-generic-password ] && exit 44; exit 0')
    fake(bin_ / "launchctl", f'echo "launchctl $*" >> "{tmp_path}/calls.log"')
    get = tmp_path / "get.sh"
    sha = hashlib.sha256((served / "python.tar.gz").read_bytes()).hexdigest()
    get.write_text(re.sub(r"^PYTHON_SHA256_AARCH64=\S+$", f"PYTHON_SHA256_AARCH64={sha}",
                          (TOOL / "get.sh").read_text(encoding="utf-8"), flags=re.M), encoding="utf-8")
    return {"tmp": tmp_path, "home": home, "bin": bin_, "get": get, "log": tmp_path / "calls.log"}


def run(mac: dict, script: Path, *args: str) -> subprocess.CompletedProcess:
    env = {"HOME": str(mac["home"]), "PATH": f"{mac['bin']}:/usr/bin:/bin"}
    return subprocess.run(["/bin/bash", str(script), *args], capture_output=True, text=True, encoding="utf-8", env=env)


def calls(mac: dict) -> list[str]:
    return mac["log"].read_text(encoding="utf-8").splitlines()


def test_the_line_puts_python_and_the_tool_in_one_folder_and_starts_the_capture(mac):
    r = run(mac, mac["get"], "--school", "taby")
    assert r.returncode == 0, r.stdout + r.stderr
    folder = mac["home"] / "SchoolSoftCapture"
    assert (folder / "schoolsoft_capture.py").read_bytes() == (TOOL / "schoolsoft_capture.py").read_bytes()
    assert (folder / "ai_filter.py").read_bytes() == (ROOT / "app/src/family_brief/ai_filter.py").read_bytes()
    assert (folder / "remove").stat().st_mode & 0o777 == 0o700
    assert folder.stat().st_mode & 0o777 == 0o700
    started = [c for c in calls(mac) if c.startswith("python3")]
    assert started == [f"python3 -E -s {folder}/schoolsoft_capture.py capture --school taby"]
    hosts = {re.match(r"curl https://([^/]+)", c).group(1) for c in calls(mac) if c.startswith("curl")}
    assert hosts == {"raw.githubusercontent.com", "releases.astral.sh"}
    assert sorted(p.name for p in folder.iterdir()) == ["ai_filter.py", "python-3.13.16-20261003", "remove",
                                                         "schoolsoft_capture.py"]


def test_run_again_it_keeps_its_python(mac):
    run(mac, mac["get"])
    run(mac, mac["get"])
    assert sum("cpython" in c for c in calls(mac)) == 1


def test_a_python_whose_checksum_doesn_t_match_is_never_run(mac):
    mac["get"].write_text(re.sub(r"^PYTHON_SHA256_AARCH64=\S+$", "PYTHON_SHA256_AARCH64=" + "0" * 64,
                                 mac["get"].read_text(encoding="utf-8"), flags=re.M), encoding="utf-8")
    r = run(mac, mac["get"])
    assert r.returncode == 1
    assert "checksum doesn't match" in r.stdout
    assert not any(c.startswith("python3") for c in calls(mac))
    assert not list((mac["home"] / "SchoolSoftCapture").glob("python-*"))


def test_remove_takes_the_job_the_keychain_item_and_the_folder(mac):
    run(mac, mac["get"])
    agents = mac["home"] / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    (agents / "com.kinlace.schoolsoft-capture.plist").write_text("<plist/>", encoding="utf-8")
    (agents / "com.parentrecap.daily.plist").write_text("<plist/>", encoding="utf-8")
    r = run(mac, mac["home"] / "SchoolSoftCapture" / "remove")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "✅" in r.stdout
    assert not (mac["home"] / "SchoolSoftCapture").exists()
    assert sorted(p.name for p in agents.iterdir()) == ["com.parentrecap.daily.plist"]
    assert "launchctl bootout gui/501/com.kinlace.schoolsoft-capture" in calls(mac)
    assert "security delete-generic-password -s schoolsoft-capture -a refresh-token" in calls(mac)
    assert run(mac, TOOL / "remove.sh").returncode == 0  # again, with nothing left


def test_the_tool_runs_beside_its_copy_of_the_ai_filter_without_parent_recap(tmp_path):
    """As get.sh leaves it: no family_brief package, only the two files in one folder."""
    for source in (TOOL / "schoolsoft_capture.py", ROOT / "app/src/family_brief/ai_filter.py"):
        shutil.copy(source, tmp_path / source.name)
    r = subprocess.run([sys.executable, "-S", "-E", str(tmp_path / "schoolsoft_capture.py"), "capture",
                        "--school", "not a school!", "--language", "en"], capture_output=True, text=True,
                       encoding="utf-8", cwd=tmp_path)
    assert r.returncode == 2, r.stderr
    assert "That isn't the name in a SchoolSoft address. Run the command again." in r.stdout
