"""`family-brief uninstall` removes what setup created on this Mac, and nothing else.

macOS's edges are faked: `launchctl`, `pmset`, the Keychain's `security` command and the
administrator dialog (`osascript`). Everything setup writes is real files in a temporary HOME.
Assertions are on what the family reads and on what is left on the Mac afterwards."""
from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from test_codex_install import install_codex_skills

from family_brief import install_record, ops, run_lock
from family_brief.collectors import gmail
from family_brief.config import Config

SLEEPS = "AC Power:\n sleep                1\n"
OUR_WAKE = "wakepoweron at 8:55PM every day"
APP_PASSWORD = "abcdabcdabcdabcd"
CLAUDE_TOKEN = "sk-ant-oat01-secret"
REAL_RUN = subprocess.run


class FakeMac:
    """launchctl, pmset, the Keychain and the administrator dialog, as uninstall sees them."""

    def __init__(self, keychain: dict[str, str]) -> None:
        self.keychain = keychain
        self.loaded: set[str] = set()
        self.repeating: list[str] = []      # the lines under "Repeating power events:"
        self.admin_dialog = "allow"         # or "cancel"
        self.keychain_prompt = "allow"      # or "deny"
        self.ran: list[list[str]] = []

    def run(self, cmd: list[str], *_a: Any, **_k: Any) -> subprocess.CompletedProcess:
        cmd = list(cmd)
        self.ran.append(cmd)
        prog = Path(cmd[0]).name
        if prog == "launchctl":
            if cmd[1] == "load":
                self.loaded.add(Path(cmd[2]).stem)
            elif cmd[1] == "unload":
                self.loaded.discard(Path(cmd[2]).stem)
            elif cmd[1] == "list":
                return done(cmd, "".join(f"-\t0\t{label}\n" for label in sorted(self.loaded)))
            return done(cmd)
        if cmd[:2] == ["pmset", "-g"]:
            if cmd[2] == "custom":
                return done(cmd, SLEEPS)
            sched = ("Repeating power events:\n" + "".join(f"  {l}\n" for l in self.repeating)
                     if self.repeating else "")
            return done(cmd, sched)
        if prog == "security":
            account = cmd[cmd.index("-a") + 1]
            if account not in self.keychain:
                return done(cmd, code=44, err="The specified item could not be found in the keychain.")
            if cmd[1] == "delete-generic-password":
                if self.keychain_prompt == "deny":
                    return done(cmd, code=128, err="User canceled the operation.")
                del self.keychain[account]
                return done(cmd, "password has been deleted.\n")
            assert "-w" not in cmd, "uninstall must not read the secret itself"
            return done(cmd, f'keychain: "login.keychain-db"\n    "acct"<blob>="{account}"\n')
        if prog == "osascript":
            assert "pmset repeat cancel" in cmd[-1] and "administrator privileges" in cmd[-1]
            if self.admin_dialog == "cancel":
                return done(cmd, code=1, err="execution error: User canceled. (-128)")
            self.repeating = []
            return done(cmd)
        raise AssertionError(f"unexpected subprocess in test: {cmd[:3]}")

    def removals(self) -> list[list[str]]:
        return [c for c in self.ran if c[:2] in (["launchctl", "unload"],
                                                  ["security", "delete-generic-password"])
                or c[0] == "osascript"]


def done(cmd: list[str], out: str = "", code: int = 0, err: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(cmd, code, out, err)


@pytest.fixture
def mac(harness, monkeypatch) -> FakeMac:
    fake = FakeMac(harness.keychain)
    monkeypatch.setattr(subprocess, "run", fake.run)
    monkeypatch.setattr(ops, "LAUNCH_AGENTS", harness.home / "Library" / "LaunchAgents")
    return fake


def make_program(home: Path, monkeypatch) -> Path:
    """What install.sh leaves in ~/FamilyBrief/app, running as the program's own Python."""
    app = home / "FamilyBrief" / "app"
    (app / ".venv" / "bin").mkdir(parents=True)
    (app / ".venv" / "bin" / "python").write_text("")
    (app / "pyproject.toml").write_text('[project]\nname = "family-brief"\n')
    (app / "VERSION").write_text("0.5.0\n")
    (home / "FamilyBrief" / "logs").mkdir()
    monkeypatch.setattr(sys, "executable", str(app / ".venv" / "bin" / "python"))
    return app


def set_up(harness, mac: FakeMac, monkeypatch, *, record: bool = True) -> None:
    """Everything a Claude setup with Codex skills, Wilma and a wake schedule leaves on the Mac."""
    home = harness.home
    with monkeypatch.context() as real:  # install.sh's own Codex step, run for real
        real.setattr(subprocess, "run", REAL_RUN)
        assert install_codex_skills(home).returncode == 0
    app = make_program(home, monkeypatch)
    if record:  # what install.sh records
        install_record.main(["program", str(app), "logs", str(home / "FamilyBrief" / "logs"),
                             "codex-skill", str(home / ".agents/skills/parent-recap-setup"),
                             "codex-skill", str(home / ".agents/skills/parent-recap-manage")])
    gmail.store_app_password("parent@example.com", APP_PASSWORD)
    harness.keychain["claude-oauth-token"] = CLAUDE_TOKEN
    assert harness.cli("schedule", "install") == 0
    mac.repeating = [OUR_WAKE]
    harness.state_path.write_text("{}")
    (home / ".family" / "languages").mkdir()
    (home / ".family" / "languages" / "sv.json").write_text("{}")
    (home / ".family" / "config.yaml.bak-202609011200").write_text("old: config\n")
    harness.write_archive("2026-09-26", {"messages": []})
    (harness.archive_dir / "2026-09-26.md").write_text("# Brief\n")
    if not record:
        install_record.path().unlink(missing_ok=True)


def uninstall(harness, *args: str) -> int:
    # harness.cli rewrites the config first, so call main directly once it may be gone.
    cfg_path = harness.home / ".family" / "config.yaml"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", ["family-brief", "-c", str(cfg_path), "uninstall", *args])
        from family_brief import __main__ as cli
        return cli.main()


def leftovers(home: Path) -> list[str]:
    """Every file and folder in HOME, but the folders macOS and Codex own."""
    theirs = {"Library", "Library/LaunchAgents", ".agents", ".agents/skills"}
    return sorted(r for r in (str(p.relative_to(home)) for p in home.rglob("*")) if r not in theirs)


# ── listing


def test_lists_everything_setup_created_and_removes_nothing(harness, mac, monkeypatch, capsys):
    set_up(harness, mac, monkeypatch)
    before = leftovers(harness.home)
    mac.ran.clear()

    assert uninstall(harness) == 0

    out = capsys.readouterr().out
    for item in ("com.family.brief", "Library/LaunchAgents/com.family.brief.plist", OUR_WAKE,
                 "FamilyBrief/app", "FamilyBrief/logs", ".family/config.yaml",
                 ".family/config.yaml.bak-202609011200", ".family/state.json", ".family/languages",
                 ".family/install-record.json", "gmail-imap-parent@example.com",
                 "claude-oauth-token", ".agents/skills/parent-recap-setup",
                 ".agents/skills/parent-recap-manage", "/plugin uninstall parent-recap@kinlace"):
        assert item in out, item
    assert "1 day" in out and "keep" in out.lower()  # the archive, and the question about it
    assert "--confirm" in out
    assert APP_PASSWORD not in out and CLAUDE_TOKEN not in out
    assert leftovers(harness.home) == before
    assert mac.removals() == []
    assert mac.keychain and mac.loaded and mac.repeating


def test_names_the_family_accounts_it_leaves_alone(harness, mac, monkeypatch, capsys):
    set_up(harness, mac, monkeypatch)

    assert uninstall(harness) == 0

    out = capsys.readouterr().out
    assert "https://myaccount.google.com/apppasswords" in out
    assert "Wilma" in out and "WhatsApp" in out


# ── removing


def test_confirmed_with_the_archive_kept_removes_all_but_the_archive(harness, mac, monkeypatch,
                                                                    capsys):
    set_up(harness, mac, monkeypatch)

    assert uninstall(harness, "--confirm", "--keep-archive") == 0

    assert leftovers(harness.home) == ["FamilyBrief", "FamilyBrief/2026-09-26.md",
                                       "FamilyBrief/2026-09-26.raw.json"]
    assert mac.keychain == {} and mac.loaded == set() and mac.repeating == []
    out = capsys.readouterr().out
    assert "kept" in out.lower() and "FamilyBrief" in out
    assert "https://myaccount.google.com/apppasswords" in out
    assert APP_PASSWORD not in out and CLAUDE_TOKEN not in out


def test_confirmed_with_the_archive_removed_leaves_nothing_of_parent_recap(harness, mac,
                                                                          monkeypatch):
    set_up(harness, mac, monkeypatch)
    weekend = harness.archive_dir / "weekend_events"
    weekend.mkdir()
    (weekend / "2026-09-26.md").write_text("picks\n")

    assert uninstall(harness, "--confirm", "--remove-archive") == 0

    assert leftovers(harness.home) == []


def test_confirming_needs_a_choice_about_the_archive(harness, mac, monkeypatch, capsys):
    set_up(harness, mac, monkeypatch)
    before = leftovers(harness.home)
    mac.ran.clear()

    assert uninstall(harness, "--confirm") == 2

    assert "--keep-archive" in capsys.readouterr().out
    assert leftovers(harness.home) == before and mac.removals() == []


def test_leaves_files_it_did_not_create_in_its_folders(harness, mac, monkeypatch, capsys):
    set_up(harness, mac, monkeypatch)
    (harness.archive_dir / "notes.txt").write_text("mine\n")
    (harness.home / ".family" / "other-tool.yaml").write_text("a: 1\n")

    assert uninstall(harness, "--confirm", "--remove-archive") == 0

    assert leftovers(harness.home) == [".family", ".family/other-tool.yaml",
                                       "FamilyBrief", "FamilyBrief/notes.txt"]
    out = capsys.readouterr().out
    assert "notes.txt" in out and "other-tool.yaml" in out


def test_a_denied_keychain_deletion_is_reported_and_the_rest_still_removed(harness, mac,
                                                                         monkeypatch, capsys):
    set_up(harness, mac, monkeypatch)
    mac.keychain_prompt = "deny"

    assert uninstall(harness, "--confirm", "--keep-archive") == 1

    out = capsys.readouterr().out
    assert "claude-oauth-token" in out.split("❌", 1)[1]
    assert "Keychain Access" in out
    assert not (harness.home / "FamilyBrief" / "app").exists()


def test_a_cancelled_administrator_dialog_leaves_the_wake_schedule_and_says_how(harness, mac,
                                                                              monkeypatch, capsys):
    set_up(harness, mac, monkeypatch)
    mac.admin_dialog = "cancel"

    assert uninstall(harness, "--confirm", "--keep-archive") == 1

    assert mac.repeating == [OUR_WAKE]
    assert "sudo pmset repeat cancel" in capsys.readouterr().out


def test_leaves_a_wake_schedule_setup_did_not_set(harness, mac, monkeypatch, capsys):
    set_up(harness, mac, monkeypatch)
    mac.repeating = ["wakepoweron at 7:00AM weekdays only"]

    assert uninstall(harness, "--confirm", "--keep-archive") == 0

    assert mac.repeating == ["wakepoweron at 7:00AM weekdays only"]
    assert not any(c[0] == "osascript" for c in mac.ran)
    assert "wakepoweron at 7:00AM weekdays only" in capsys.readouterr().out


def test_removes_a_gmail_app_password_stored_for_an_earlier_address(harness, mac, monkeypatch,
                                                                    capsys):
    set_up(harness, mac, monkeypatch)
    gmail.store_app_password("old@example.com", "oldoldoldoldoldo")

    assert uninstall(harness, "--confirm", "--keep-archive") == 0

    assert mac.keychain == {}
    assert "gmail-imap-old@example.com" in capsys.readouterr().out


# ── installs from before the record


def test_an_install_without_a_record_is_recognised_and_listed_for_confirmation(harness, mac,
                                                                             monkeypatch, capsys):
    set_up(harness, mac, monkeypatch, record=False)

    assert uninstall(harness) == 0

    out = capsys.readouterr().out
    assert "record" in out.lower()
    for item in ("com.family.brief", "FamilyBrief/app", ".family/config.yaml",
                 "gmail-imap-parent@example.com", ".agents/skills/parent-recap-setup"):
        assert item in out, item

    assert uninstall(harness, "--confirm", "--keep-archive") == 0
    assert leftovers(harness.home) == ["FamilyBrief", "FamilyBrief/2026-09-26.md",
                                       "FamilyBrief/2026-09-26.raw.json"]


# ── stopping on what isn't this install's


def assert_stopped(harness, mac: FakeMac, before: list[str], capsys) -> str:
    assert uninstall(harness, "--confirm", "--remove-archive") == 1
    out = capsys.readouterr().out
    assert "❌" in out and "nothing" in out.lower()
    assert leftovers(harness.home) == before
    assert mac.removals() == []
    return out


def test_stops_on_a_launchd_job_with_its_name_that_runs_another_program(harness, mac,
                                                                        monkeypatch, capsys):
    set_up(harness, mac, monkeypatch)
    plist = harness.home / "Library" / "LaunchAgents" / "com.family.brief.plist"
    data = plistlib.loads(plist.read_bytes())
    data["ProgramArguments"][0] = "/Users/maintainer/family-brief/.venv/bin/python"
    plist.write_bytes(plistlib.dumps(data))
    before = leftovers(harness.home)
    mac.ran.clear()

    out = assert_stopped(harness, mac, before, capsys)

    assert "/Users/maintainer/family-brief/.venv/bin/python" in out
    assert "com.family.brief" in out


def test_stops_on_a_config_parent_recap_did_not_write(harness, mac, monkeypatch, capsys):
    set_up(harness, mac, monkeypatch)
    (harness.home / ".family" / "config.yaml").write_text("theme: dark\nfont_size: 12\n")
    before = leftovers(harness.home)
    mac.ran.clear()

    out = assert_stopped(harness, mac, before, capsys)

    assert ".family/config.yaml" in out


def test_stops_on_a_program_folder_that_is_not_parent_recap(harness, mac, monkeypatch, capsys):
    set_up(harness, mac, monkeypatch)
    (harness.home / "FamilyBrief" / "app" / "pyproject.toml").write_text(
        '[project]\nname = "something-else"\n')
    before = leftovers(harness.home)
    mac.ran.clear()

    out = assert_stopped(harness, mac, before, capsys)

    assert "FamilyBrief/app" in out


def test_stops_on_a_codex_skill_with_its_name_that_it_did_not_install(harness, mac, monkeypatch,
                                                                      capsys):
    set_up(harness, mac, monkeypatch)
    (harness.home / ".agents/skills/parent-recap-manage/SKILL.md").write_text("my own skill\n")
    before = leftovers(harness.home)
    mac.ran.clear()

    out = assert_stopped(harness, mac, before, capsys)

    assert "parent-recap-manage" in out


def test_stops_while_a_run_is_going(harness, mac, monkeypatch, capsys):
    set_up(harness, mac, monkeypatch)
    cfg = Config.load(harness.home / ".family" / "config.yaml")

    with run_lock.exclusive(cfg):
        before = leftovers(harness.home)
        mac.ran.clear()
        out = assert_stopped(harness, mac, before, capsys)

    assert "run" in out.lower()


# ── installing again


def test_a_fresh_setup_works_after_uninstall(harness, mac, monkeypatch, capsys):
    set_up(harness, mac, monkeypatch)
    assert uninstall(harness, "--confirm", "--keep-archive") == 0

    assert not (harness.home / ".family").exists()
    app = make_program(harness.home, monkeypatch)
    install_record.main(["program", str(app)])
    gmail.store_app_password("parent@example.com", APP_PASSWORD)
    assert harness.cli("schedule", "install") == 0
    capsys.readouterr()

    assert uninstall(harness, "--confirm", "--keep-archive") == 0

    assert mac.keychain == {} and mac.loaded == set()
    assert not (harness.home / ".family").exists() and not app.exists()


# ── one command in Terminal


def test_in_terminal_it_asks_about_the_archive_and_for_confirmation(harness, mac, monkeypatch,
                                                                    capsys):
    set_up(harness, mac, monkeypatch)
    answers = iter(["n", "yes"])  # don't keep the archive, then confirm
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    assert uninstall(harness) == 0

    assert leftovers(harness.home) == []


def test_in_terminal_anything_but_yes_removes_nothing(harness, mac, monkeypatch, capsys):
    set_up(harness, mac, monkeypatch)
    before = leftovers(harness.home)
    answers = iter(["", "no"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    assert uninstall(harness) == 1

    assert leftovers(harness.home) == before
    assert "nothing" in capsys.readouterr().out.lower()


def test_the_setup_internals_document_names_everything_uninstall_removes(harness, mac,
                                                                         monkeypatch, capsys):
    set_up(harness, mac, monkeypatch)
    weekend = harness.archive_dir / "weekend_events"
    weekend.mkdir()
    for name in ("calendar_credentials.json", "calendar_token.json"):
        (harness.home / ".family" / name).write_text("{}")
    harness.keychain["anthropic-api-key"] = "sk-ant-api-secret"
    doc = (Path(__file__).resolve().parents[2] / "docs" / "setup-internals.md").read_text()

    assert uninstall(harness) == 0

    out = capsys.readouterr().out
    items = out.split("\n\nLeft as it is:")[0].split("\n\nNothing has been removed yet")[0]
    listed = [line.split(": ", 1)[1].split(" (", 1)[0] for line in items.splitlines()
              if line.startswith("  • ")]
    assert len(listed) == 18
    for where in listed:
        name = where.rsplit("/", 1)[-1].strip().replace("parent@example.com", "<address>")
        name = name.replace("202609011200", "<date>").replace("1 day in ~", "~")
        assert name in doc or where.startswith("wakepoweron"), where
    assert "pmset repeat cancel" in doc


def test_the_record_is_owner_only(harness, mac, monkeypatch):
    set_up(harness, mac, monkeypatch)

    assert install_record.path().stat().st_mode & 0o077 == 0
