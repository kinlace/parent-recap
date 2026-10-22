"""`parent-recap uninstall` removes what setup created on this Mac, and nothing else.

The OS services (secrets, the evening jobs, the wake schedule) and Claude Code's plugin commands
are the harness's fakes. Everything setup writes is real files in a temporary HOME. Assertions are
on what the family reads and on what is left on the Mac afterwards."""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
import fake_node
from test_codex_install import install_codex_skills

from family_brief import __main__ as cli, install_record, run_lock, setup_save, setup_wilma, tools
from family_brief import whatsapp_python
from family_brief.collectors import gmail
from family_brief.config import Config
from family_brief.system import jobs, wake, windows_only
from family_brief.system.secrets_error import SecretsError

OTHER_WAKE = "wake every weekday at 07:00"
APP_PASSWORD = "abcdabcdabcdabcd"
CLAUDE_TOKEN = "sk-ant-oat01-secret"


class Handover:
    """What uninstall hands over to Windows to remove once it has ended: each time, the paths,
    then the folders to remove if empty. They go at once, since the tests run from another
    Python, unless the test `keeps` them, as Windows does while uninstall runs, or it `refuses`."""

    def __init__(self) -> None:
        self.handed: list[tuple[list[Path], list[Path]]] = []
        self.keeps = False
        self.refuses: Exception | None = None

    def __call__(self, paths: list[Path], folders: list[Path]) -> None:
        if self.refuses is not None:
            raise self.refuses
        self.handed.append((list(paths), list(folders)))
        if self.keeps:
            return
        for p in paths:
            shutil.rmtree(p)
        for f in sorted(folders, key=lambda f: len(f.parts), reverse=True):
            if f.is_dir() and not any(f.iterdir()):
                f.rmdir()


@pytest.fixture(autouse=True)
def handover(monkeypatch) -> Handover:
    # Windows' own removal starts a process that outlives uninstall, which a test can't wait for.
    h = Handover()
    monkeypatch.setattr(windows_only, "remove_after_exit", h)
    return h


def store_app_password(harness, address: str, password: str) -> None:
    """What setup's Gmail step leaves: the Keychain item and its entry in the record."""
    harness.secrets[gmail.keychain_account(address)] = password
    install_record.add("keychain", gmail.keychain_account(address))


PINNED_PYTHON = "ParentRecap/runtime/python-3.13.16-20261003/bin/python3.13"


def make_program(home: Path, monkeypatch) -> Path:
    """What install.sh leaves in ~/ParentRecap/app, running as the program's own Python, and its
    pinned Python and Node in ~/ParentRecap/runtime."""
    python = home / PINNED_PYTHON
    python.parent.mkdir(parents=True)
    python.write_text("")
    (python.parent / "python3").symlink_to(python.name)
    fake_node.pinned_node(home)
    app = home / "ParentRecap" / "app"
    (app / ".venv" / "bin").mkdir(parents=True)
    (app / ".venv" / "bin" / "python").write_text("")
    (app / "pyproject.toml").write_text('[project]\nname = "family-brief"\n')
    (app / "VERSION").write_text("0.5.0\n")
    (home / "ParentRecap" / "logs").mkdir()
    # Uninstall finds the program it runs from by its own Python.
    monkeypatch.setattr(sys, "executable", str(app / ".venv" / "bin" / "python"))
    return app


def our_wake(harness) -> str:
    """The wake schedule setup gave for the evening job at 21:00."""
    return harness.wake.line(21, 0)


def claude_code_removals(harness) -> list[list[str]]:
    """Each `claude plugin` command that removed something, or tried to."""
    return [c for c in harness.plugin_calls if "--json" not in c]


def claude_code_has(harness) -> tuple[list[str], list[str]]:
    """The plugins (by id) and the marketplaces (by name) Claude Code has."""
    return [p["id"] for p in harness.claude_plugins], [m["name"] for m in harness.claude_marketplaces]


def nothing_removed(harness) -> bool:
    """Everything setup stored, scheduled and set is still there, and nothing was asked to go."""
    return bool(harness.secrets) and harness.jobs.installed() == {jobs.DAILY} \
        and harness.wake.wakes == [our_wake(harness)] and harness.wake.asked == [] \
        and claude_code_removals(harness) == []


def set_up(harness, monkeypatch, *, record: bool = True) -> None:
    """Everything a Claude setup with Codex skills, Wilma and a wake schedule leaves on the Mac."""
    home = harness.home
    assert install_codex_skills(home).returncode == 0  # install.sh's own Codex step, run for real
    app = make_program(home, monkeypatch)
    if record:  # what install.sh records
        install_record.main(["program", str(app), "logs", str(home / "ParentRecap" / "logs"),
                             "runtime", str(home / "ParentRecap" / "runtime"),
                             "codex-skill", str(home / ".agents/skills/parent-recap-setup"),
                             "codex-skill", str(home / ".agents/skills/parent-recap-manage")])
    store_app_password(harness, "parent@example.com", APP_PASSWORD)
    harness.secrets["claude-oauth-token"] = CLAUDE_TOKEN
    assert harness.cli("schedule", "install") == 0
    assert harness.wake.wakes == [our_wake(harness)]
    harness.wake.asked.clear()
    harness.state_path.write_text("{}")
    (home / ".family" / "languages").mkdir()
    (home / ".family" / "languages" / "sv.json").write_text("{}")
    (home / ".family" / "config.yaml.bak-202609011200").write_text("old: config\n")
    tools.remembered_path().write_text('{"node": "/nix/store/node/bin/node"}\n')  # found off PATH
    whatsapp_python.record(str(home / PINNED_PYTHON))  # setup's WhatsApp step
    # The progress the setup page and the chat share.
    saved = setup_save.save(home / ".family" / "config.yaml", {"progress": {"phase": "finish"}})
    assert saved["result"] == "saved"
    harness.write_archive("2026-09-26", {"messages": []})
    (harness.archive_dir / "2026-09-26.md").write_text("# Brief\n")
    if not record:
        install_record.path().unlink(missing_ok=True)


def uninstall(harness, *args: str) -> int:
    # harness.cli rewrites the config first, so call main directly once it may be gone.
    cfg_path = harness.home / ".family" / "config.yaml"
    with pytest.MonkeyPatch.context() as mp:  # the command line, which names the program too
        mp.setattr(sys, "argv", ["family-brief", "-c", str(cfg_path), "uninstall", *args])
        return cli.main()


def leftovers(home: Path) -> list[str]:
    """Every file and folder in HOME, but the folders Codex and Windows own, such as the one
    PowerShell keeps its startup data in."""
    theirs = {".agents", ".agents/skills", "AppData", "AppData/Local"}
    windows = ("AppData/Local/Microsoft", "AppData/Roaming")
    return sorted(r for r in (p.relative_to(home).as_posix() for p in home.rglob("*"))
                  if r not in theirs and not any(r == w or r.startswith(w + "/") for w in windows))


# ── listing


def test_lists_everything_setup_created_and_removes_nothing(harness, monkeypatch, capsys):
    set_up(harness, monkeypatch)
    before = leftovers(harness.home)

    assert uninstall(harness) == 0

    out = capsys.readouterr().out
    for item in (jobs.system_name(jobs.DAILY), f"Scheduler/{jobs.system_name(jobs.DAILY)}",
                 our_wake(harness),
                 "ParentRecap/app", "ParentRecap/logs", "ParentRecap/runtime", ".family/config.yaml",
                 ".family/config.yaml.bak-202609011200", ".family/state.json", ".family/languages",
                 ".family/install-record.json", ".family/setup-progress.json",
                 ".family/programs.json", ".family/whatsapp-python.json", "gmail-imap-parent@example.com",
                 "claude-oauth-token", ".agents/skills/parent-recap-setup",
                 ".agents/skills/parent-recap-manage"):
        assert item in out, item
    assert "1 day" in out and "keep" in out.lower()  # the archive, and the question about it
    assert "--confirm" in out
    assert APP_PASSWORD not in out and CLAUDE_TOKEN not in out
    assert leftovers(harness.home) == before
    assert nothing_removed(harness)


@pytest.mark.macos
def test_says_to_remove_its_python_from_full_disk_access_by_hand(harness, monkeypatch, capsys):
    set_up(harness, monkeypatch)

    assert uninstall(harness, "--confirm", "--keep-archive") == 0

    out = capsys.readouterr().out
    note = next(line for line in out.splitlines() if "Full Disk Access" in line)
    assert "App Management" not in out
    assert "by hand" in note and str(harness.home / PINNED_PYTHON) in note
    assert not (harness.home / "ParentRecap" / "runtime").exists()  # the Node with it


@pytest.mark.macos
def test_stops_on_a_wilma_cli_folder_holding_something_else(harness, monkeypatch, tmp_path,
                                                            capsys):
    set_up(harness, monkeypatch)
    wilma_and_claude(harness, monkeypatch, tmp_path, by_setup=True)
    (fake_node.wilma_folder(harness.home) / "notes.txt").write_text("mine\n")

    assert uninstall(harness, "--confirm", "--remove-archive") != 0

    assert "ParentRecap/wilma" in capsys.readouterr().out
    assert (fake_node.wilma_folder(harness.home) / "notes.txt").exists()


def test_names_the_family_accounts_it_leaves_alone(harness, monkeypatch, capsys):
    set_up(harness, monkeypatch)

    assert uninstall(harness) == 0

    out = capsys.readouterr().out
    assert "https://myaccount.google.com/apppasswords" in out
    assert "Wilma" in out and "WhatsApp" in out


# ── removing


def test_confirmed_with_the_archive_kept_removes_all_but_the_archive(harness, monkeypatch,
                                                                    capsys):
    set_up(harness, monkeypatch)

    assert uninstall(harness, "--confirm", "--keep-archive") == 0

    assert leftovers(harness.home) == ["ParentRecap", "ParentRecap/2026-09-26.md",
                                       "ParentRecap/2026-09-26.raw.json"]
    assert harness.secrets == {} and harness.jobs.found() == [] and harness.wake.wakes == []
    out = capsys.readouterr().out
    assert "kept" in out.lower() and "ParentRecap" in out
    assert "https://myaccount.google.com/apppasswords" in out
    assert APP_PASSWORD not in out and CLAUDE_TOKEN not in out


def test_the_list_of_held_back_messages_goes_also_when_the_archive_stays(harness, monkeypatch, capsys):
    set_up(harness, monkeypatch)
    (harness.archive_dir / "held-back-list.txt").write_text("1 message held back from the AI, by date\n")

    assert uninstall(harness) == 0
    listing = capsys.readouterr().out
    assert uninstall(harness, "--confirm", "--keep-archive") == 0

    removes = listing[listing.index("Uninstall removes:"):listing.index("You choose whether")]
    assert "List of held-back messages: ~/ParentRecap/held-back-list.txt" in removes
    assert "not Parent Recap's" not in listing
    assert leftovers(harness.home) == ["ParentRecap", "ParentRecap/2026-09-26.md",
                                       "ParentRecap/2026-09-26.raw.json"]


def test_confirmed_with_the_archive_removed_leaves_nothing_of_parent_recap(harness,
                                                                          monkeypatch):
    set_up(harness, monkeypatch)
    weekend = harness.archive_dir / "weekend_events"
    weekend.mkdir()
    (weekend / "2026-09-26.md").write_text("picks\n")

    assert uninstall(harness, "--confirm", "--remove-archive") == 0

    assert leftovers(harness.home) == []


def test_confirming_needs_a_choice_about_the_archive(harness, monkeypatch, capsys):
    set_up(harness, monkeypatch)
    before = leftovers(harness.home)

    assert uninstall(harness, "--confirm") == 2

    assert "--keep-archive" in capsys.readouterr().out
    assert leftovers(harness.home) == before and nothing_removed(harness)


def test_leaves_files_it_did_not_create_in_its_folders(harness, monkeypatch, capsys):
    set_up(harness, monkeypatch)
    (harness.archive_dir / "notes.txt").write_text("mine\n")
    (harness.home / ".family" / "other-tool.yaml").write_text("a: 1\n")

    assert uninstall(harness, "--confirm", "--remove-archive") == 0

    assert leftovers(harness.home) == [".family", ".family/other-tool.yaml",
                                       "ParentRecap", "ParentRecap/notes.txt"]
    out = capsys.readouterr().out
    assert "notes.txt" in out and "other-tool.yaml" in out


def test_a_denied_keychain_deletion_is_reported_and_the_rest_still_removed(harness,
                                                                         monkeypatch, capsys):
    set_up(harness, monkeypatch)
    harness.secrets.refuses = SecretsError("macOS didn't allow it. Delete it in the Keychain "
                                           "Access app instead.", "failed", -128)

    assert uninstall(harness, "--confirm", "--keep-archive") == 1

    out = capsys.readouterr().out
    assert "claude-oauth-token" in out.split("❌", 1)[1]
    assert "Keychain Access" in out
    assert not (harness.home / "ParentRecap" / "app").exists()


def test_a_cancelled_administrator_dialog_leaves_the_wake_schedule_and_says_how(harness,
                                                                              monkeypatch, capsys):
    set_up(harness, monkeypatch)
    harness.wake.dialog = "cancel"

    assert uninstall(harness, "--confirm", "--keep-archive") == 1

    assert harness.wake.wakes == [our_wake(harness)]
    assert wake.manual_cancel() in capsys.readouterr().out


def test_leaves_a_wake_schedule_setup_did_not_set(harness, monkeypatch, capsys):
    set_up(harness, monkeypatch)
    harness.wake.wakes = [OTHER_WAKE]

    assert uninstall(harness, "--confirm", "--keep-archive") == 0

    assert harness.wake.wakes == [OTHER_WAKE]
    assert harness.wake.asked == []
    assert OTHER_WAKE in capsys.readouterr().out


def test_removes_a_gmail_app_password_stored_for_an_earlier_address(harness, monkeypatch,
                                                                    capsys):
    set_up(harness, monkeypatch)
    store_app_password(harness, "old@example.com", "oldoldoldoldoldo")

    assert uninstall(harness, "--confirm", "--keep-archive") == 0

    assert harness.secrets == {}
    assert "gmail-imap-old@example.com" in capsys.readouterr().out


def test_a_keychain_item_macos_refused_to_delete_is_found_again_on_the_next_try(harness,
                                                                              monkeypatch, capsys):
    # Without a record, the Gmail account comes from the config, which the first try removes.
    set_up(harness, monkeypatch, record=False)
    harness.secrets.refuses = SecretsError("macOS didn't allow it", "failed", -128)
    assert uninstall(harness, "--confirm", "--keep-archive") == 1
    capsys.readouterr()
    harness.secrets.refuses = None

    assert uninstall(harness, "--confirm", "--keep-archive") == 0

    assert "gmail-imap-parent@example.com" in capsys.readouterr().out
    assert harness.secrets == {}
    assert not (harness.home / ".family").exists()


def test_an_archive_in_a_shared_folder_takes_only_parent_recaps_files(harness, monkeypatch,
                                                                     capsys):
    harness.config["archive"] = {"dir": "~/Documents"}
    harness.config["weekend_events"] = {"dir": "~/Documents"}
    set_up(harness, monkeypatch)
    for p in harness.archive_dir.glob("2026-*"):  # set_up's archive goes to the usual folder
        p.unlink()
    docs = harness.home / "Documents"
    (docs / "logs").mkdir(exist_ok=True)
    (docs / "logs" / "my-diary.txt").write_text("mine\n")
    (docs / "2026-09-26.md").write_text("# Brief\n")

    assert uninstall(harness, "--confirm", "--remove-archive") == 0

    assert leftovers(harness.home) == ["Documents", "Documents/logs", "Documents/logs/my-diary.txt"]


# ── on Windows


def as_windows(harness, monkeypatch) -> None:
    """From here on the system is Windows, whose Credential Manager asks nobody and whose wake is
    the family's own "Allow wake timers"."""
    monkeypatch.setattr(sys, "platform", "win32")
    if os.name != "nt":  # Windows' own `which`, which asks for Claude Code, can't run here
        monkeypatch.setattr(tools.shutil, "which", lambda *_a, **_k: None)
    harness.wake.settable = False
    harness.secrets.DELETING_ASKS = ""


def test_on_windows_the_program_and_its_python_go_once_uninstall_has_ended(harness, handover,
                                                                          monkeypatch, capsys):
    set_up(harness, monkeypatch)
    as_windows(harness, monkeypatch)
    handover.keeps = True
    capsys.readouterr()
    app, runtime = harness.home / "ParentRecap" / "app", harness.home / "ParentRecap" / "runtime"

    assert uninstall(harness, "--confirm", "--remove-archive") == 0

    [(paths, folders)] = handover.handed
    assert paths == [app, runtime]
    assert harness.home / "ParentRecap" in folders
    # Windows can't remove them while uninstall runs from them; all the rest is gone already.
    assert app.exists() and runtime.exists()
    assert [r for r in leftovers(harness.home)
            if not r.startswith(("ParentRecap/app", "ParentRecap/runtime"))] == ["ParentRecap"]
    assert harness.secrets == {} and harness.jobs.found() == []
    out = capsys.readouterr().out
    assert "The program goes once uninstall has ended: ~/ParentRecap/app" in out
    assert "this PC" in out and "Mac" not in out
    assert "Full Disk Access" not in out and "macOS" not in out


def test_on_windows_a_program_that_couldn_t_be_handed_over_is_reported(harness, handover,
                                                                       monkeypatch, capsys):
    set_up(harness, monkeypatch)
    as_windows(harness, monkeypatch)
    handover.refuses = OSError("PowerShell didn't start")

    assert uninstall(harness, "--confirm", "--remove-archive") == 1

    failed = capsys.readouterr().out.split("❌", 1)[1]
    assert "~/ParentRecap/app" in failed and "PowerShell didn't start" in failed
    assert install_record.exists()  # so the next try finds them


def test_on_windows_allow_wake_timers_is_the_family_s_own_and_stays(harness, monkeypatch,
                                                                    capsys):
    set_up(harness, monkeypatch)
    as_windows(harness, monkeypatch)
    allowed = harness.wake.wakes[0]  # the wake on, as the family's power plan says it

    assert uninstall(harness) == 0
    listing = capsys.readouterr().out
    assert uninstall(harness, "--confirm", "--remove-archive") == 0

    assert allowed not in listing.split("Left as it is:")[0]
    assert allowed in listing.split("Left as it is:")[1]
    assert harness.wake.wakes == [allowed] and harness.wake.asked == []


def test_on_windows_the_wilma_cli_npm_installed_is_recognised(harness, monkeypatch):
    set_up(harness, monkeypatch)
    as_windows(harness, monkeypatch)
    # What `npm install -g --prefix` makes on Windows: the package, and npm's three wrappers for
    # each of the commands its package.json names.
    folder = fake_node.wilma_folder(harness.home)
    package = folder / "node_modules" / "@wilm-ai" / "wilma-cli"
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps(
        {"name": "@wilm-ai/wilma-cli", "bin": {"wilma": "dist/index.js", "wilmai": "dist/index.js"}}))
    for name in ("wilma", "wilma.cmd", "wilma.ps1", "wilmai", "wilmai.cmd", "wilmai.ps1"):
        (folder / name).write_text("")
    install_record.add("wilma-cli", str(folder))

    assert uninstall(harness, "--confirm", "--remove-archive") == 0

    assert not folder.exists()


def test_a_wilma_cli_folder_holding_something_else_names_it(harness, monkeypatch, capsys):
    set_up(harness, monkeypatch)
    folder = fake_node.wilma_folder(harness.home)
    (folder / "node_modules").mkdir(parents=True)
    (folder / "notes.txt").write_text("mine\n")
    install_record.add("wilma-cli", str(folder))

    assert uninstall(harness, "--confirm", "--remove-archive") == 1

    assert "holds other files too: notes.txt" in capsys.readouterr().out
    assert (folder / "notes.txt").exists()


def test_on_windows_credential_manager_items_are_listed_without_a_question(harness,
                                                                         monkeypatch, capsys):
    set_up(harness, monkeypatch)
    as_windows(harness, monkeypatch)

    assert uninstall(harness) == 0

    [line] = [l for l in capsys.readouterr().out.splitlines() if "claude-oauth-token" in l]
    assert line.endswith("claude-oauth-token")


# ── the wilma CLI and Claude Code's plugin

ESPOO = {"url": "https://espoo.inschool.fi", "name": "Espoo"}
OUR_PROFILE = "https://espoo.inschool.fi|mia.parent"
WILMA_PASSWORD = "fake-wilma-password"  # no real Wilma password, ever


def wilma_and_claude(harness, monkeypatch, tmp_path: Path, *, by_setup: bool) -> Path:
    """A wilma CLI with a profile, and Claude Code with Parent Recap's plugin and marketplace,
    installed by setup (and in its record) or there before it. Setup's CLI is in Parent Recap's
    folder; the Mac's own is on PATH. Returns the CLI's config."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("wilma", "npm", "claude"):
        (bin_dir / name).write_text("#!/bin/sh\n")
        (bin_dir / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    if by_setup:
        fake_node.install_wilma(harness.home, "#!/bin/sh\n")
    config = setup_wilma.config_path()
    setup_wilma.write_profile(config, ESPOO, "mia.parent", WILMA_PASSWORD)
    harness.claude_plugins = [{"id": "parent-recap@kinlace", "scope": "user"}]
    harness.claude_marketplaces = [{"name": "claude-plugins-official", "source": "github"},
                                   {"name": "kinlace", "source": "github"}]
    if by_setup:
        install_record.main(["wilma-cli", str(fake_node.wilma_folder(harness.home)),
                             "wilma-profile", OUR_PROFILE,
                             "claude-plugin", "parent-recap@kinlace",
                             "claude-marketplace", "kinlace"])
    return config


@pytest.mark.macos
def test_lists_the_wilma_cli_its_profile_and_the_plugin_setup_installed(harness, monkeypatch,
                                                                         tmp_path, capsys):
    set_up(harness, monkeypatch)
    config = wilma_and_claude(harness, monkeypatch, tmp_path, by_setup=True)

    assert uninstall(harness) == 0

    removes = capsys.readouterr().out.split("Uninstall removes:", 1)[1].split("\n\n", 1)[0]
    for item in ("The wilma CLI: ~/ParentRecap/wilma", "mia.parent at https://espoo.inschool.fi",
                 ".config/wilmai/config.json", "Claude Code plugin: parent-recap@kinlace",
                 "Claude Code marketplace: kinlace"):
        assert item in removes, item
    assert "claude-plugins-official" not in removes
    assert nothing_removed(harness) and config.exists()
    assert WILMA_PASSWORD not in removes


@pytest.mark.macos
def test_removes_the_wilma_cli_its_profile_and_the_plugin_setup_installed(harness, monkeypatch,
                                                                          tmp_path, capsys):
    set_up(harness, monkeypatch)
    wilma_and_claude(harness, monkeypatch, tmp_path, by_setup=True)

    assert uninstall(harness, "--confirm", "--remove-archive") == 0

    assert not fake_node.wilma_folder(harness.home).exists()
    assert claude_code_has(harness) == ([], ["claude-plugins-official"])
    assert leftovers(harness.home) == [".config"]  # the folder other programs share
    out = capsys.readouterr().out
    assert "/plugin uninstall" not in out and "rm -rf ~/.config/wilmai" not in out


def cli_files_beside(config: Path) -> None:
    """What the wilma CLI keeps next to its config: from 2.0 the Wilma sessions its commands
    carry on with, which give the same access as the password, and its note of the newest version."""
    (config.parent / "wilmai-sessions.json").write_text('{"x": {"state": "a session", "savedAt": 0}}')
    (config.parent / "version-check.json").write_text('{"latestVersion": "2.1.2", "checkedAt": 0}')


@pytest.mark.macos
def test_the_wilma_cli_s_sessions_go_with_setup_s_profile(harness, monkeypatch, tmp_path,
                                                          capsys):
    set_up(harness, monkeypatch)
    config = wilma_and_claude(harness, monkeypatch, tmp_path, by_setup=True)
    cli_files_beside(config)

    assert uninstall(harness, "--confirm", "--remove-archive") == 0

    assert not config.parent.exists()
    assert leftovers(harness.home) == [".config"]


@pytest.mark.macos
def test_leaves_a_wilma_cli_profile_and_plugin_that_were_there_before_setup(harness,
                                                                            monkeypatch, tmp_path,
                                                                            capsys):
    set_up(harness, monkeypatch)
    config = wilma_and_claude(harness, monkeypatch, tmp_path, by_setup=False)
    before = config.read_text()

    assert uninstall(harness, "--confirm", "--remove-archive") == 0

    assert config.read_text() == before
    assert (tmp_path / "bin" / "wilma").exists()
    assert claude_code_has(harness) == (["parent-recap@kinlace"], ["claude-plugins-official", "kinlace"])
    assert claude_code_removals(harness) == []
    out = capsys.readouterr().out  # what the family can do themselves
    assert "/plugin uninstall parent-recap@kinlace" in out and "~/.config/wilmai" in out


@pytest.mark.macos
def test_says_nothing_about_a_plugin_claude_code_does_not_have(harness, monkeypatch, tmp_path,
                                                                capsys):
    set_up(harness, monkeypatch)
    wilma_and_claude(harness, monkeypatch, tmp_path, by_setup=False)
    harness.claude_plugins = []
    harness.claude_marketplaces = harness.claude_marketplaces[:1]  # claude-plugins-official

    assert uninstall(harness, "--confirm", "--keep-archive") == 0

    out = capsys.readouterr().out
    assert "/plugin" not in out and "Claude Code" not in out


@pytest.mark.macos
def test_says_how_to_remove_a_marketplace_left_without_its_plugin(harness, monkeypatch,
                                                                  tmp_path, capsys):
    set_up(harness, monkeypatch)
    wilma_and_claude(harness, monkeypatch, tmp_path, by_setup=False)
    harness.claude_plugins = []

    assert uninstall(harness, "--confirm", "--keep-archive") == 0

    out = capsys.readouterr().out
    assert "/plugin marketplace remove kinlace" in out and "/plugin uninstall" not in out


def test_says_nothing_about_claude_code_where_it_is_not_installed(harness, monkeypatch,
                                                                  capsys):
    set_up(harness, monkeypatch)
    monkeypatch.setenv("PATH", str(harness.home / "no-programs"))  # not this Mac's own Claude Code

    assert uninstall(harness, "--confirm", "--keep-archive") == 0

    out = capsys.readouterr().out
    assert "/plugin" not in out and "Claude Code" not in out


@pytest.mark.macos
def test_says_if_you_added_the_plugin_when_claude_code_can_t_list_its_plugins(harness,
                                                                              monkeypatch, tmp_path,
                                                                              capsys):
    set_up(harness, monkeypatch)
    wilma_and_claude(harness, monkeypatch, tmp_path, by_setup=False)
    harness.claude_plugins = {"error": "not signed in"}  # type: ignore[assignment]

    assert uninstall(harness, "--confirm", "--keep-archive") == 0

    note = next(line for line in capsys.readouterr().out.splitlines() if "/plugin" in line)
    assert "If you added" in note and "/plugin uninstall parent-recap@kinlace" in note


@pytest.mark.macos
def test_says_to_remove_the_plugin_setup_installed_when_claude_code_can_t_list_it(harness,
                                                                                  monkeypatch,
                                                                                  tmp_path, capsys):
    set_up(harness, monkeypatch)
    wilma_and_claude(harness, monkeypatch, tmp_path, by_setup=True)
    harness.claude_plugins = {"error": "not signed in"}  # type: ignore[assignment]

    assert uninstall(harness, "--confirm", "--keep-archive") == 0

    note = next(line for line in capsys.readouterr().out.splitlines() if "/plugin" in line)
    assert "If you added" not in note and "/plugin uninstall parent-recap@kinlace" in note


@pytest.mark.macos
def test_removes_only_setup_s_profile_from_the_wilma_cli_s_config(harness, monkeypatch,
                                                                  tmp_path, capsys):
    set_up(harness, monkeypatch)
    config = wilma_and_claude(harness, monkeypatch, tmp_path, by_setup=True)
    helsinki = {"url": "https://helsinki.inschool.fi", "name": "Helsinki"}
    setup_wilma.write_profile(config, helsinki, "dad", "another-fake-password")
    setup_wilma.write_profile(config, ESPOO, "mia.parent", WILMA_PASSWORD)  # the last used
    cli_files_beside(config)

    assert uninstall(harness, "--confirm", "--remove-archive") == 0

    left = json.loads(config.read_text())
    assert [p["id"] for p in left["profiles"]] == ["https://helsinki.inschool.fi|dad"]
    assert left["lastProfileId"] == "https://helsinki.inschool.fi|dad"
    assert config.stat().st_mode & 0o777 == 0o600
    assert WILMA_PASSWORD not in config.read_text()
    # The sessions go, as when the CLI removes a sign-in itself: the other signs in again.
    assert sorted(p.name for p in config.parent.iterdir()) == ["config.json", "version-check.json"]
    assert "~/.config/wilmai" in capsys.readouterr().out  # the other sign-in stays there


@pytest.mark.macos
def test_a_plugin_claude_code_did_not_remove_says_how_and_keeps_the_record(harness,
                                                                           monkeypatch, tmp_path,
                                                                           capsys):
    set_up(harness, monkeypatch)
    wilma_and_claude(harness, monkeypatch, tmp_path, by_setup=True)
    harness.claude_removes = False

    assert uninstall(harness, "--confirm", "--keep-archive") == 1

    failed = capsys.readouterr().out.split("❌", 1)[1]
    assert "/plugin uninstall parent-recap@kinlace" in failed
    assert install_record.entries("claude-plugin") == ["parent-recap@kinlace"]  # found next time


# ── installs from before the record


def test_an_install_without_a_record_is_recognised_and_listed_for_confirmation(harness,
                                                                             monkeypatch, capsys):
    set_up(harness, monkeypatch, record=False)

    assert uninstall(harness) == 0

    out = capsys.readouterr().out
    assert "record" in out.lower()
    for item in (jobs.system_name(jobs.DAILY), "ParentRecap/app", ".family/config.yaml",
                 "gmail-imap-parent@example.com", ".agents/skills/parent-recap-setup"):
        assert item in out, item

    assert uninstall(harness, "--confirm", "--keep-archive") == 0
    assert leftovers(harness.home) == ["ParentRecap", "ParentRecap/2026-09-26.md",
                                       "ParentRecap/2026-09-26.raw.json"]


# ── stopping on what isn't this install's


def assert_stopped(harness, before: list[str], capsys) -> str:
    assert uninstall(harness, "--confirm", "--remove-archive") == 1
    out = capsys.readouterr().out
    assert "❌" in out and "nothing" in out.lower()
    assert leftovers(harness.home) == before
    assert nothing_removed(harness)
    return out


def test_stops_on_a_scheduled_job_with_its_name_that_runs_another_program(harness,
                                                                        monkeypatch, capsys):
    set_up(harness, monkeypatch)
    harness.jobs.programs[jobs.DAILY][0] = "/Users/maintainer/family-brief/.venv/bin/python"
    before = leftovers(harness.home)

    out = assert_stopped(harness, before, capsys)

    assert "/Users/maintainer/family-brief/.venv/bin/python" in out
    assert jobs.system_name(jobs.DAILY) in out


def test_stops_on_a_config_parent_recap_did_not_write(harness, monkeypatch, capsys):
    set_up(harness, monkeypatch)
    (harness.home / ".family" / "config.yaml").write_text("theme: dark\nfont_size: 12\n")
    before = leftovers(harness.home)

    out = assert_stopped(harness, before, capsys)

    assert ".family/config.yaml" in out


def test_stops_on_a_program_folder_that_is_not_parent_recap(harness, monkeypatch, capsys):
    set_up(harness, monkeypatch)
    (harness.home / "ParentRecap" / "app" / "pyproject.toml").write_text(
        '[project]\nname = "something-else"\n')
    before = leftovers(harness.home)

    out = assert_stopped(harness, before, capsys)

    assert "ParentRecap/app" in out


def test_stops_on_a_runtime_folder_holding_something_else(harness, monkeypatch, capsys):
    set_up(harness, monkeypatch)
    (harness.home / "ParentRecap" / "runtime" / "notes.txt").write_text("mine\n")
    before = leftovers(harness.home)

    out = assert_stopped(harness, before, capsys)

    assert "ParentRecap/runtime" in out


def test_stops_on_a_codex_skill_with_its_name_that_it_did_not_install(harness, monkeypatch,
                                                                      capsys):
    set_up(harness, monkeypatch)
    (harness.home / ".agents/skills/parent-recap-manage/SKILL.md").write_text("my own skill\n")
    before = leftovers(harness.home)

    out = assert_stopped(harness, before, capsys)

    assert "parent-recap-manage" in out


def test_stops_while_a_run_is_going(harness, monkeypatch, capsys):
    set_up(harness, monkeypatch)
    cfg = Config.load(harness.home / ".family" / "config.yaml")

    with run_lock.exclusive(cfg):
        before = leftovers(harness.home)
        out = assert_stopped(harness, before, capsys)

    assert "run" in out.lower()


# ── installing again


def test_a_fresh_setup_works_after_uninstall(harness, monkeypatch, capsys):
    set_up(harness, monkeypatch)
    assert uninstall(harness, "--confirm", "--keep-archive") == 0

    assert not (harness.home / ".family").exists()
    app = make_program(harness.home, monkeypatch)
    install_record.main(["program", str(app)])
    store_app_password(harness, "parent@example.com", APP_PASSWORD)
    assert harness.cli("schedule", "install") == 0
    capsys.readouterr()

    assert uninstall(harness, "--confirm", "--keep-archive") == 0

    assert harness.secrets == {} and harness.jobs.found() == [] and harness.wake.wakes == []
    assert not (harness.home / ".family").exists() and not app.exists()


# ── one command in Terminal


@pytest.fixture
def typed(harness, monkeypatch) -> Callable[..., None]:
    """Uninstall run in Terminal, where the family types each of the lines given, and then the
    input closes."""
    def typing(*lines: str) -> None:
        harness.desktop.terminal = True
        monkeypatch.setattr(sys, "stdin", io.StringIO("".join(f"{line}\n" for line in lines)))
    return typing


def test_in_terminal_it_asks_about_the_archive_and_for_confirmation(harness, typed, monkeypatch,
                                                                    capsys):
    set_up(harness, monkeypatch)
    typed("n", "yes")  # don't keep the archive, then confirm

    assert uninstall(harness) == 0

    assert leftovers(harness.home) == []


def test_in_terminal_anything_but_yes_removes_nothing(harness, typed, monkeypatch, capsys):
    set_up(harness, monkeypatch)
    before = leftovers(harness.home)
    typed("", "no")

    assert uninstall(harness) == 1

    assert leftovers(harness.home) == before
    assert "nothing" in capsys.readouterr().out.lower()


def test_in_terminal_closed_input_removes_nothing_without_a_traceback(harness, typed, monkeypatch,
                                                                     capsys):
    set_up(harness, monkeypatch)
    before = leftovers(harness.home)
    typed()

    assert uninstall(harness) == 1

    assert leftovers(harness.home) == before
    assert "Nothing was removed." in capsys.readouterr().out


def test_run_by_an_agent_it_lists_and_says_how_to_confirm(harness, monkeypatch, capsys):
    # On Windows an agent's stdin is NUL, which isatty() takes for a Terminal (#372).
    set_up(harness, monkeypatch)
    before = leftovers(harness.home)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    harness.desktop.terminal = False

    assert uninstall(harness) == 0

    assert leftovers(harness.home) == before
    assert "--confirm" in capsys.readouterr().out


def test_output_in_a_code_page_without_its_symbols_still_removes_everything(harness,
                                                                            monkeypatch):
    # Piped by an agent on Windows, stdout writes cp1252, which has no ✅ (#380).
    set_up(harness, monkeypatch)
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252"))

    assert uninstall(harness, "--confirm", "--keep-archive") == 0

    sys.stdout.flush()
    assert leftovers(harness.home) == ["ParentRecap", "ParentRecap/2026-09-26.md",
                                       "ParentRecap/2026-09-26.raw.json"]
    assert "✅" in raw.getvalue().decode("utf-8") and "❌" not in raw.getvalue().decode("utf-8")


def test_the_setup_internals_document_names_everything_uninstall_removes(harness,
                                                                         monkeypatch, capsys):
    set_up(harness, monkeypatch)
    weekend = harness.archive_dir / "weekend_events"
    weekend.mkdir()
    (weekend / "2026-09-26.md").write_text("picks\n")
    for name in ("calendar_credentials.json", "calendar_token.json"):
        (harness.home / ".family" / name).write_text("{}")
    harness.secrets["anthropic-api-key"] = "sk-ant-api-secret"
    doc = (Path(__file__).resolve().parents[3] / "docs" / "setup-internals.md").read_text()

    assert uninstall(harness) == 0

    out = capsys.readouterr().out
    items = out.split("\n\nLeft as it is:")[0].split("\n\nNothing has been removed yet")[0]
    listed = [line.split(": ", 1)[1].split(" (", 1)[0] for line in items.splitlines()
              if line.startswith("  • ")]
    assert len(listed) == 23
    for where in listed:
        name = where.rsplit("/", 1)[-1].strip().replace("parent@example.com", "<address>")
        name = name.replace("202609011200", "<date>").replace("1 day in ~", "~")
        ours = (our_wake(harness), f"~/Scheduler/{jobs.system_name(jobs.DAILY)}")  # the fakes' own
        assert name in doc or where in ours, where
    # What the macOS adapters keep in their place.
    assert "com.parentrecap.daily.plist" in doc and "pmset repeat cancel" in doc


@pytest.mark.macos
def test_the_record_is_owner_only(harness, monkeypatch):
    set_up(harness, monkeypatch)

    assert install_record.path().stat().st_mode & 0o077 == 0


def test_adds_at_the_same_time_all_land_in_the_record(harness):
    # The setup page's steps run in threads, and two of them can record at once (#333).
    start = threading.Barrier(8)
    errors: list[BaseException] = []

    def add(n: int) -> None:
        start.wait()
        try:
            for i in range(25):
                install_record.add("keychain", f"account-{n}-{i}")
        except BaseException as e:
            errors.append(e)

    threads = [threading.Thread(target=add, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(install_record.entries("keychain")) == 8 * 25
