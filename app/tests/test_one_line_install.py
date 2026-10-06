"""The one line a family pastes into Terminal: `get.sh`, which opens the setup page, or the chat
setup's `get.sh --claude` and `get.sh --codex`.

Runs the real `get.sh` with a fake `claude` (which logs its calls and answers the two list
commands from files) and a fake `curl` (which hands over a tarball built here, standing in
for the `stable` branch's, whose install.sh puts a fake `parent-recap` where the real one
goes), in a fake home.
"""
from __future__ import annotations

import io
import json
import subprocess
import tarfile
from pathlib import Path

import pytest

from family_brief import install_record

ROOT = Path(__file__).resolve().parents[2]
NO_MARKETPLACES = "[]"
KINLACE = json.dumps([{"name": "kinlace", "source": "github", "repo": "kinlace/parent-recap"}], indent=2)
# What a family who installed from the release zip has: the same name, from the unzipped folder.
KINLACE_FROM_FOLDER = json.dumps([
    {"name": "claude-plugins-official", "source": "github", "repo": "anthropics/claude-plugins-official"},
    {"name": "kinlace", "source": "directory", "path": "/Users/mum/ParentRecap/plugin"},
], indent=2)
NO_PLUGINS = "[]"
PARENT_RECAP = json.dumps([{"id": "parent-recap@kinlace", "version": "0.4.1", "scope": "user"}], indent=2)


@pytest.fixture
def mac(tmp_path: Path) -> dict:
    home = tmp_path / "home"
    home.mkdir()
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "calls.log"
    fake(bin_ / "uname", "echo Darwin")
    fake(bin_ / "claude", f"""
echo "claude $*" >> "{log}"
case "$*" in
  "plugin marketplace list --json") cat "{tmp_path}/marketplaces.json" ;;
  "plugin list --json") cat "{tmp_path}/plugins.json" ;;
esac""")
    fake(bin_ / "curl", f"""
echo "curl $*" >> "{log}"
[ -f "{tmp_path}/stable.tar.gz" ] || exit 22
cat "{tmp_path}/stable.tar.gz\"""")
    (tmp_path / "marketplaces.json").write_text(NO_MARKETPLACES)
    (tmp_path / "plugins.json").write_text(NO_PLUGINS)
    return {"tmp": tmp_path, "home": home, "bin": bin_, "log": log}


def fake(path: Path, body: str) -> None:
    path.write_text("#!/bin/bash\n" + body + "\n")
    path.chmod(0o755)


def get(mac: dict, *args: str, without: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
    for name in without:
        (mac["bin"] / name).unlink()
    env = {"HOME": str(mac["home"]), "PATH": f"{mac['bin']}:/usr/bin:/bin"}
    return subprocess.run(["/bin/bash", str(ROOT / "get.sh"), *args], capture_output=True, text=True, env=env)


def calls(mac: dict) -> list[str]:
    return mac["log"].read_text().splitlines() if mac["log"].exists() else []


def stable_release(mac: dict, version: str, files: dict[str, str] | None = None, name: str = "parent-recap") -> None:
    """The tarball GitHub serves for the `stable` branch, with an install.sh that logs how it was run."""
    contents = {
        ".claude-plugin/plugin.json": json.dumps({"name": name, "version": version}),
        "install.sh": f'''echo "install.sh $* from $(cd "$(dirname "$0")" && pwd)" >> "{mac["log"]}"
mkdir -p "$HOME/ParentRecap/app/.venv/bin"
echo 'echo "parent-recap $*" >> "{mac["log"]}"' > "$HOME/ParentRecap/app/.venv/bin/parent-recap"
chmod +x "$HOME/ParentRecap/app/.venv/bin/parent-recap"
''',
        **(files or {}),
    }
    with tarfile.open(mac["tmp"] / "stable.tar.gz", "w:gz") as tar:
        for rel, text in contents.items():
            data = text.encode()
            info = tarfile.TarInfo(f"parent-recap-stable/{rel}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


# --- The line without a flag: the setup page ---------------------------------------------

def test_the_line_installs_the_stable_release_and_opens_the_setup_page(mac):
    stable_release(mac, "0.5.0")

    result = get(mac)

    assert result.returncode == 0, result.stderr
    plugin = mac["home"] / "ParentRecap" / "plugin"
    assert json.loads((plugin / ".claude-plugin" / "plugin.json").read_text())["version"] == "0.5.0"
    curl, install, page = calls(mac)
    assert "kinlace/parent-recap/archive/refs/heads/stable.tar.gz" in curl
    assert install == f"install.sh  from {plugin.resolve()}"  # neither Claude Code's nor Codex's
    assert page == "parent-recap setup page"
    assert not any(c.startswith("claude") for c in calls(mac))  # the page installs the plugin


def test_the_line_run_again_updates_and_opens_the_page_again(mac):
    stable_release(mac, "0.5.0", {"docs/gone-in-0.6.md": "old\n"})
    assert get(mac).returncode == 0
    stable_release(mac, "0.6.0")

    result = get(mac)

    assert result.returncode == 0, result.stderr
    plugin = mac["home"] / "ParentRecap" / "plugin"
    assert json.loads((plugin / ".claude-plugin" / "plugin.json").read_text())["version"] == "0.6.0"
    assert not (plugin / "docs" / "gone-in-0.6.md").exists()
    assert calls(mac).count("parent-recap setup page") == 2


def old_job(mac: dict, label: str = "com.family.brief") -> None:
    agents = mac["home"] / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    (agents / f"{label}.plist").write_text("<string>-m</string><string>family_brief</string>")


@pytest.mark.parametrize("flag", [(), ("--claude",), ("--codex",)])
def test_the_line_stops_over_an_install_from_before_the_rename(mac, flag):
    stable_release(mac, "0.5.0")
    (mac["home"] / "FamilyBrief" / "app").mkdir(parents=True)

    result = get(mac, *flag)

    assert result.returncode != 0
    assert "Uninstall it with its own version first" in result.stdout
    assert "~/FamilyBrief/app/.venv/bin/family-brief uninstall" in result.stdout
    assert calls(mac) == [] and not (mac["home"] / "ParentRecap").exists()


def test_the_line_stops_over_a_job_from_before_the_rename(mac):
    stable_release(mac, "0.5.0")
    old_job(mac, "com.family.weekend-events")

    result = get(mac)

    assert result.returncode != 0 and "older Parent Recap" in result.stdout
    assert calls(mac) == []


def test_a_kept_archive_in_the_old_folder_is_not_an_install(mac):
    stable_release(mac, "0.5.0")
    old = mac["home"] / "FamilyBrief"
    old.mkdir(parents=True)
    (old / "2026-09-26.md").write_text("a Brief")
    old_job(mac, "com.family.brief")
    (mac["home"] / "Library" / "LaunchAgents" / "com.family.brief.plist").write_text("a daily-brief job")  # not ours

    result = get(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    assert calls(mac).count("parent-recap setup page") == 1


def test_the_line_without_a_download_opens_nothing(mac):
    result = get(mac)

    assert result.returncode != 0
    assert not any(c.startswith(("install.sh", "parent-recap")) for c in calls(mac))


def test_an_unknown_flag_shows_the_line(mac):
    result = get(mac, "--chatgpt")

    assert result.returncode != 0
    assert 'stable/get.sh)"' in result.stdout + result.stderr
    assert calls(mac) == []


# --- Claude Code ---------------------------------------------------------------------------

def test_claude_adds_the_stable_marketplace_installs_for_the_user_and_starts_setup(mac):
    result = get(mac, "--claude")

    assert result.returncode == 0, result.stderr
    assert [c for c in calls(mac) if "list --json" not in c] == [
        "claude plugin marketplace add kinlace/parent-recap#stable",
        "claude plugin install parent-recap@kinlace --scope user",
        "claude /parent-recap:setup",
    ]


def recorded(mac: dict, monkeypatch, kind: str) -> list[str]:
    """What setup's record, as the program reads it, has of `kind`."""
    monkeypatch.setenv("HOME", str(mac["home"]))
    return install_record.entries(kind)


def test_claude_records_the_plugin_and_marketplace_it_installed_for_uninstall(mac, monkeypatch):
    assert get(mac, "--claude").returncode == 0

    assert recorded(mac, monkeypatch, "claude-plugin") == ["parent-recap@kinlace"]
    assert recorded(mac, monkeypatch, "claude-marketplace") == ["kinlace"]
    record = mac["home"] / ".family" / "install-record.json"
    assert record.stat().st_mode & 0o077 == 0 and record.parent.stat().st_mode & 0o077 == 0


def test_claude_adds_to_a_record_setup_started_and_only_once(mac, monkeypatch):
    monkeypatch.setenv("HOME", str(mac["home"]))
    install_record.add("program", "/Users/mum/ParentRecap/app")
    install_record.add("keychain", "claude-oauth-token")

    assert get(mac, "--claude").returncode == 0
    assert get(mac, "--claude").returncode == 0

    assert install_record.entries("program") == ["/Users/mum/ParentRecap/app"]
    assert install_record.entries("keychain") == ["claude-oauth-token"]
    assert install_record.entries("claude-plugin") == ["parent-recap@kinlace"]
    assert install_record.entries("claude-marketplace") == ["kinlace"]
    install_record.add("launchd", "/Users/mum/Library/LaunchAgents/com.parentrecap.daily.plist")
    assert install_record.entries("claude-plugin") == ["parent-recap@kinlace"]


def test_claude_run_again_updates_instead_of_adding_twice(mac, monkeypatch):
    (mac["tmp"] / "marketplaces.json").write_text(KINLACE)
    (mac["tmp"] / "plugins.json").write_text(PARENT_RECAP)

    result = get(mac, "--claude")

    assert result.returncode == 0, result.stderr
    assert [c for c in calls(mac) if "list --json" not in c] == [
        "claude plugin marketplace update kinlace",
        "claude plugin update parent-recap@kinlace",
        "claude /parent-recap:setup",
    ]
    # Installed before setup: uninstall leaves them.
    assert recorded(mac, monkeypatch, "claude-plugin") == []
    assert recorded(mac, monkeypatch, "claude-marketplace") == []


def test_claude_records_only_the_plugin_when_the_marketplace_was_there(mac, monkeypatch):
    (mac["tmp"] / "marketplaces.json").write_text(KINLACE)

    assert get(mac, "--claude").returncode == 0

    assert recorded(mac, monkeypatch, "claude-plugin") == ["parent-recap@kinlace"]
    assert recorded(mac, monkeypatch, "claude-marketplace") == []


def test_claude_switches_a_kinlace_marketplace_from_a_local_folder_to_the_stable_release(mac):
    (mac["tmp"] / "marketplaces.json").write_text(KINLACE_FROM_FOLDER)
    (mac["tmp"] / "plugins.json").write_text(PARENT_RECAP)

    result = get(mac, "--claude")

    assert result.returncode == 0, result.stderr
    assert [c for c in calls(mac) if "list --json" not in c] == [
        "claude plugin uninstall parent-recap@kinlace",
        "claude plugin marketplace remove kinlace",
        "claude plugin marketplace add kinlace/parent-recap#stable",
        "claude plugin install parent-recap@kinlace --scope user",
        "claude /parent-recap:setup",
    ]
    assert any("/Users/mum/ParentRecap/plugin" in line and "kinlace/parent-recap#stable" in line
               for line in result.stdout.splitlines())
    assert not (mac["home"] / ".family").exists()  # the family's own, from the release zip


def test_claude_switches_a_local_kinlace_marketplace_even_without_the_plugin_installed(mac):
    (mac["tmp"] / "marketplaces.json").write_text(KINLACE_FROM_FOLDER)

    assert get(mac, "--claude").returncode == 0

    assert [c for c in calls(mac) if "list --json" not in c] == [
        "claude plugin marketplace remove kinlace",
        "claude plugin marketplace add kinlace/parent-recap#stable",
        "claude plugin install parent-recap@kinlace --scope user",
        "claude /parent-recap:setup",
    ]


def test_claude_removes_the_family_brief_plugin_from_before_the_rename(mac):
    (mac["tmp"] / "marketplaces.json").write_text(json.dumps([{"name": "family-brief"}], indent=2))
    (mac["tmp"] / "plugins.json").write_text(json.dumps([{"id": "family-brief@family-brief"}], indent=2))

    assert get(mac, "--claude").returncode == 0

    made = [c for c in calls(mac) if "list --json" not in c]
    assert made[:2] == ["claude plugin uninstall family-brief@family-brief",
                        "claude plugin marketplace remove family-brief"]
    assert made[-1] == "claude /parent-recap:setup"


def test_claude_without_claude_code_says_to_install_it_first(mac):
    result = get(mac, "--claude", without=("claude",))

    assert result.returncode != 0
    assert "Claude Code" in result.stdout + result.stderr
    assert calls(mac) == []


# --- Codex ---------------------------------------------------------------------------------

def test_codex_puts_the_stable_release_in_familybrief_plugin_and_installs_it(mac):
    stable_release(mac, "0.5.0")

    result = get(mac, "--codex")

    assert result.returncode == 0, result.stderr
    plugin = mac["home"] / "ParentRecap" / "plugin"
    assert json.loads((plugin / ".claude-plugin" / "plugin.json").read_text())["version"] == "0.5.0"
    curl, install = calls(mac)
    assert "kinlace/parent-recap/archive/refs/heads/stable.tar.gz" in curl
    assert install == f"install.sh --codex from {plugin.resolve()}"
    assert "$parent-recap-setup" in result.stdout
    assert sorted(p.name for p in (mac["home"] / "ParentRecap").iterdir()) == ["app", "plugin"]


def test_codex_run_again_replaces_the_plugin_with_the_new_release(mac):
    stable_release(mac, "0.5.0", {"docs/gone-in-0.6.md": "old\n"})
    assert get(mac, "--codex").returncode == 0
    (mac["home"] / ".family").mkdir()
    (mac["home"] / ".family" / "config.yaml").write_text("household: {}\n")
    stable_release(mac, "0.6.0")

    result = get(mac, "--codex")

    assert result.returncode == 0, result.stderr
    plugin = mac["home"] / "ParentRecap" / "plugin"
    assert json.loads((plugin / ".claude-plugin" / "plugin.json").read_text())["version"] == "0.6.0"
    assert not (plugin / "docs" / "gone-in-0.6.md").exists()
    assert calls(mac).count(f"install.sh --codex from {plugin.resolve()}") == 2
    assert "upgrade Parent Recap" in result.stdout


def test_codex_replaces_a_family_brief_plugin_from_before_the_rename(mac):
    old = mac["home"] / "ParentRecap" / "plugin" / ".claude-plugin"
    old.mkdir(parents=True)
    (old / "plugin.json").write_text(json.dumps({"name": "family-brief", "version": "0.3.0"}))
    stable_release(mac, "0.5.0")

    assert get(mac, "--codex").returncode == 0

    assert json.loads((old / "plugin.json").read_text()) == {"name": "parent-recap", "version": "0.5.0"}


def test_codex_stops_when_familybrief_plugin_holds_something_else(mac):
    stable_release(mac, "0.5.0")
    other = mac["home"] / "ParentRecap" / "plugin"
    other.mkdir(parents=True)
    (other / "notes.txt").write_text("mine\n")

    result = get(mac, "--codex")

    assert result.returncode != 0
    assert str(other) in result.stdout + result.stderr
    assert sorted(p.name for p in other.iterdir()) == ["notes.txt"]
    assert not any("install.sh" in c for c in calls(mac))


def test_codex_leaves_the_installed_plugin_alone_when_the_download_fails(mac):
    stable_release(mac, "0.5.0")
    assert get(mac, "--codex").returncode == 0
    (mac["tmp"] / "stable.tar.gz").unlink()

    result = get(mac, "--codex")

    assert result.returncode != 0
    plugin = mac["home"] / "ParentRecap" / "plugin"
    assert json.loads((plugin / ".claude-plugin" / "plugin.json").read_text())["version"] == "0.5.0"
    assert sorted(p.name for p in (mac["home"] / "ParentRecap").iterdir()) == ["app", "plugin"]


def test_codex_refuses_a_download_that_is_not_parent_recap(mac):
    stable_release(mac, "0.5.0", name="something-else")

    result = get(mac, "--codex")

    assert result.returncode != 0
    assert not (mac["home"] / "ParentRecap" / "plugin").exists()


def test_only_runs_on_a_mac(mac):
    fake(mac["bin"] / "uname", "echo Linux")

    result = get(mac, "--claude")

    assert result.returncode != 0
    assert calls(mac) == []
