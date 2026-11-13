"""`parent-recap doctor` and `discover` speak English, whatever language the Brief is in."""
from __future__ import annotations

import imaplib
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import time_machine
import yaml

import fake_node
from family_brief import __main__ as cli, failure, ops, setup_server, setup_steps
from family_brief.llm import codex as codex_backend
from family_brief.state import State
from family_brief.system import entries, jobs
from conftest import NOW, TZ, program
from fake_sources import GmailFolders
from fake_system import LINUX, MACOS, WINDOWS
from test_update import INSTALLED, github, mac  # noqa: F401  the fixtures

HAN = re.compile(r"[　-〿一-鿿＀-￯]")


@pytest.fixture
def nothing_loaded(harness):
    """The system has loaded no Parent Recap job for this login, with doctor's network checks off."""
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["wilma"]["enabled"] = False
    harness.config["whatsapp"]["enabled"] = False


def test_doctor_reports_every_check_in_english(harness, nothing_loaded, capsys):
    code = harness.cli("doctor", "--skip-llm")

    out = capsys.readouterr().out
    assert code == 1  # no Gmail App Password in the Keychain
    assert "Config file" in out and "2 kids" in out
    assert "No App Password in the Keychain for parent@example.com" in out
    assert "parent-recap schedule install" in out
    assert not HAN.findall(out)


def schedule_line(out: str) -> str:
    [line] = [l for l in out.splitlines() if "Schedule:" in l]
    return line


def test_doctor_says_an_installed_job_isnt_loaded_until_someone_logs_in(harness, nothing_loaded,
                                                                       capsys):
    # After a restart, or over SSH, the job is installed but the system hasn't loaded it.
    harness.jobs.add(jobs.DAILY, loaded=False)

    harness.cli("doctor", "--skip-llm")

    line = schedule_line(capsys.readouterr().out)
    assert line.startswith(ops.WARN.strip())
    assert f"installed but not loaded for this login: {jobs.system_name(jobs.DAILY)}" in line
    assert "only runs while you're logged in" in line and "No Brief after the Mac restarted" in line
    assert "not installed yet" not in line
    assert not HAN.findall(line)


def test_doctor_says_a_job_that_isnt_installed_isnt_installed_yet(harness, nothing_loaded, capsys):
    harness.cli("doctor", "--skip-llm")

    line = schedule_line(capsys.readouterr().out)
    assert f"not installed yet: {jobs.system_name(jobs.DAILY)}" in line and "parent-recap schedule install" in line
    assert "not loaded" not in line


def test_doctor_fails_on_an_evening_brief_that_isnt_on_once_a_brief_has_gone_out(
        harness, nothing_loaded, capsys):
    # Setup turns the evening Brief on once the first Brief is sent (#733): off after that, no
    # Brief comes.
    harness.state_path.parent.mkdir(parents=True, exist_ok=True)
    state = State(harness.state_path)
    state.mark_delivered_now(["parent@example.com"])
    state.save()

    code = harness.cli("doctor", "--skip-llm")

    line = schedule_line(capsys.readouterr().out)
    assert code == 1
    assert line.startswith(ops.FAIL.strip())
    assert "the evening Brief isn't on" in line and jobs.system_name(jobs.DAILY) in line
    assert "parent-recap schedule install" in line and "not installed yet" not in line
    assert not HAN.findall(line)


def test_doctor_only_warns_of_an_evening_brief_that_isnt_on_before_the_first_brief(
        harness, nothing_loaded, capsys):
    harness.cli("doctor", "--skip-llm")

    line = schedule_line(capsys.readouterr().out)
    assert line.startswith(ops.WARN.strip()) and "evening Brief isn't on" not in line


def schedule_lines(out: str) -> list[str]:
    return [l for l in out.splitlines() if "Schedule:" in l]


def test_doctor_names_the_always_running_job_when_it_is_installed(harness, nothing_loaded,
                                                                  capsys):
    harness.cli("doctor", "--skip-llm")
    assert not any(jobs.system_name(jobs.ALWAYS) in l
                   for l in schedule_lines(capsys.readouterr().out))

    harness.jobs.install_always(["node", "linked-device.mjs", "follow"], harness.home)
    harness.cli("doctor", "--skip-llm")

    [line] = [l for l in schedule_lines(capsys.readouterr().out)
              if jobs.system_name(jobs.ALWAYS) in l]
    assert line.startswith(ops.OK.strip()) and "runs all the time" in line

    harness.jobs.stop_always()
    harness.cli("doctor", "--skip-llm")

    [line] = [l for l in schedule_lines(capsys.readouterr().out)
              if jobs.system_name(jobs.ALWAYS) in l]
    assert line.startswith(ops.WARN.strip()) and "installed but not running" in line
    assert not HAN.findall(line)


# On the PC's or Mac's own clock: the hosted version's server keeps UTC, and its timers the
# Household's zone.
@pytest.mark.skipif(not hasattr(time, "tzset"), reason="time_machine sets the computer's zone with tzset")
@pytest.mark.parametrize("on", [MACOS, WINDOWS])
def test_doctor_names_a_computer_time_zone_that_isnt_the_config_s(harness, nothing_loaded, capsys,
                                                                  on):
    # The scheduler starts the evening job at 21:00 in Karachi, 19:00 in Helsinki (#776).
    harness.system = on
    harness.jobs.add(jobs.DAILY)
    with time_machine.travel(NOW.astimezone(ZoneInfo("Asia/Karachi")), tick=False):
        harness.cli("doctor", "--skip-llm")

    [line] = [l for l in schedule_lines(capsys.readouterr().out) if "time zone" in l]
    assert line.startswith(ops.WARN.strip())
    assert "this computer's time zone is PKT (UTC+0500)" in line
    assert "the config's timezone is Europe/Helsinki (UTC+0300)" in line
    assert "starts at 21:00 on this computer's clock, which is 19:00 in Europe/Helsinki" in line


@pytest.mark.parametrize("on", [MACOS, LINUX])
def test_doctor_says_nothing_of_the_time_zone_where_it_starts_the_job_on_time(harness,
                                                                             nothing_loaded,
                                                                             capsys, on):
    # A Mac in the config's zone, or a server whose timers keep the Household's zone in UTC.
    harness.system = on
    harness.jobs.add(jobs.DAILY)
    with time_machine.travel(NOW.astimezone(ZoneInfo("UTC" if on is LINUX else TZ)), tick=False):
        harness.cli("doctor", "--skip-llm")

    assert not any("time zone" in l for l in schedule_lines(capsys.readouterr().out))


def whatsapp_library_line(out: str) -> str:
    [line] = [l for l in out.splitlines() if "WhatsApp library:" in l]
    return line


@pytest.mark.parametrize("version, status, says", [
    (None, ops.WARN, "not installed"),
    ("7.0.0-rc9", ops.WARN, "version 7.0.0-rc9, not the pinned 7.0.0-rc14"),
    ("7.0.0-rc14", ops.OK, "installed, the pinned version 7.0.0-rc14"),
], ids=["missing", "another version", "pinned"])
def test_doctor_says_whether_the_pinned_baileys_tree_is_installed(harness, nothing_loaded, capsys,
                                                                  version, status, says):
    if version:
        fake_node.install_baileys(fake_node.baileys_folder(harness.home), version)

    harness.cli("doctor", "--skip-llm")

    line = whatsapp_library_line(capsys.readouterr().out)
    assert line.startswith(status.strip()) and says in line
    assert ("run the install again" in line) is (status == ops.WARN)


def test_doctor_says_a_tree_from_another_lockfile_is_not_the_pinned_one(harness, nothing_loaded,
                                                                         capsys):
    fake_node.install_baileys(fake_node.baileys_folder(harness.home), lockfile=b"{}\n")

    harness.cli("doctor", "--skip-llm")

    line = whatsapp_library_line(capsys.readouterr().out)
    assert line.startswith(ops.WARN.strip()) and "not the pinned tree" in line


def entries_line(out: str) -> str:
    [line] = [l for l in out.splitlines() if "Settings and Check:" in l]
    return line


@pytest.mark.parametrize("on", [MACOS, WINDOWS], ids=["macos", "windows"])
def test_doctor_says_parent_recap_settings_and_check_are_there(harness, nothing_loaded, capsys, on):
    harness.system = on
    for name in entries.NAMES:
        harness.entries.place(name, harness.home / "ParentRecap")

    harness.cli("doctor", "--skip-llm")

    line = entries_line(capsys.readouterr().out)
    assert line == (f"{ops.OK} Settings and Check: Parent Recap Settings and Parent Recap Check "
                    f"are in {harness.entries.where()}")


@pytest.mark.parametrize("missing, says", [
    ((entries.CHECK,), "Parent Recap Check isn't in"),
    (entries.NAMES, "Parent Recap Settings and Parent Recap Check aren't in"),
], ids=["one", "both"])
def test_doctor_warns_of_a_missing_entry_and_says_to_paste_the_install_line_again(
        harness, nothing_loaded, capsys, missing, says):
    for name in entries.NAMES:
        if name not in missing:
            harness.entries.place(name, harness.home / "ParentRecap")

    harness.cli("doctor", "--skip-llm")

    line = entries_line(capsys.readouterr().out)
    assert line.startswith(ops.WARN.strip()) and says in line
    assert line.endswith("(paste the install line again)")


def test_on_the_hosted_version_s_server_doctor_has_no_entries_line(harness, nothing_loaded,
                                                                   capsys):
    harness.system = LINUX

    harness.cli("doctor", "--skip-llm")

    assert "Settings and Check" not in capsys.readouterr().out


# ── The program's own update, on one line (ADR 0047, #917)


def updates_line(harness, capsys) -> str:
    capsys.readouterr()
    harness.cli("doctor", "--skip-llm")
    [line] = [l for l in capsys.readouterr().out.splitlines() if " Updates: " in l]
    return line


@pytest.mark.posix
def test_doctor_says_the_version_that_updates_are_on_and_what_the_last_look_found(
        harness, nothing_loaded, github, mac, capsys):
    assert updates_line(harness, capsys) == (
        f"{ops.OK} Updates: version {INSTALLED}; updates are on (parent-recap update off turns "
        "them off); hasn't looked for a newer release yet")
    assert github.requests == []  # doctor reads what the last look left, and asks no host

    harness.cli("update")  # a look: stable holds the installed version

    line = updates_line(harness, capsys)
    assert re.fullmatch(rf"{ops.OK} Updates: version {re.escape(INSTALLED)}; updates are on \(.+\); "
                        rf"last looked 2026-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d: "
                        rf"up to date at {re.escape(INSTALLED)}", line), line


@pytest.mark.posix
def test_doctor_stays_ok_with_updates_off_and_says_the_evening_still_looks(
        harness, nothing_loaded, github, mac, capsys):
    harness.config["updates"] = {"enabled": False}

    line = updates_line(harness, capsys)

    assert line.startswith(f"{ops.OK} Updates: version {INSTALLED}; updates are off")
    assert "still looks for a newer release and downloads nothing" in line
    assert "parent-recap update on" in line


@pytest.mark.posix
def test_doctor_says_an_update_is_waiting_what_it_asks_and_how_to_start_it(
        harness, nothing_loaded, github, mac, capsys):
    from family_brief import update
    from test_update import ASKS, needing_family
    github.stable = "0.20.0"
    github.publish("0.20.0", needing_family("0.20.0"))
    harness.cli("doctor", "--skip-llm")  # writes the harness's config, as any command does here
    # An evening's update read that the release needs the family, and left it for them.
    assert update.update(None, by_family=False).result == "needs-family"

    line = updates_line(harness, capsys)

    assert line.startswith(f"{ops.OK} Updates: version {INSTALLED}; updates are on")
    assert line.endswith(f"an update is waiting: version 0.20.0 needs you at the computer "
                         f"({ASKS['en']}), so it isn't installed by itself (parent-recap update "
                         "installs it)")

    assert harness.cli("update") == 0  # the family starts it
    assert "waiting" not in updates_line(harness, capsys)


@pytest.mark.posix
def test_doctor_shows_an_update_that_failed_and_stays_ok(harness, nothing_loaded, github, mac, capsys):
    github.stable = "0.20.0"
    github.publish("0.20.0")
    mac.fails["0.20.0"] = ["Calendar"]
    assert harness.cli("update") == 1

    line = updates_line(harness, capsys)

    assert line.startswith(f"{ops.OK} Updates: version {INSTALLED}; updates are on")
    assert line.endswith(f"0.20.0 failed its check; back on {INSTALLED}")


@pytest.mark.posix
@pytest.mark.parametrize("on, hosted, why", [
    (WINDOWS, False, "Parent Recap doesn't update itself on Windows yet"),
    (LINUX, False, "This is the hosted version"),
    (MACOS, True, "This is the hosted version"),
], ids=["windows", "server", "hosted-household"])
def test_on_windows_and_for_a_hosted_household_doctor_says_updates_aren_t_available_here(
        harness, nothing_loaded, github, mac, capsys, on, hosted, why):
    harness.system = on
    harness.config["hosted"] = hosted

    line = updates_line(harness, capsys)

    assert line.startswith(f"{ops.OK} Updates: version ")
    assert f"updates aren't available here: {why}" in line
    assert github.requests == []


def test_a_program_not_run_from_an_installed_release_has_its_version_and_no_updates(
        harness, nothing_loaded, capsys):
    line = updates_line(harness, capsys)

    assert line.startswith(f"{ops.OK} Updates: version {failure.about()['version']}; "
                           "updates aren't available here: ")
    assert "doesn't run from an installed release" in line


@pytest.fixture
def claude_without_token(harness, monkeypatch):
    """Claude Code installed and answering, with no claude-oauth-token in the Keychain."""
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["wilma"]["enabled"] = False
    harness.config["whatsapp"]["enabled"] = False
    which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda cmd, *a, **k: f"/usr/local/bin/{cmd}"
                        if cmd == "claude" else which(cmd, *a, **k))


@pytest.mark.macos  # Windows' own words are in the claude_on_windows tests below
def test_doctor_fails_claude_without_a_token_for_the_evening_brief(harness, claude_without_token,
                                                                  capsys):
    assert harness.cli("doctor") == 1

    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if "Claude:" in l)
    assert line.startswith(ops.FAIL)
    assert "no claude-oauth-token" in line and "parent-recap setup claude" in line
    assert "tmux" not in line


@pytest.mark.macos  # tmux and SSH keep only macOS's Keychain out of reach
def test_doctor_in_tmux_says_to_store_the_token_outside_it(harness, claude_without_token, capsys):
    harness.secrets.reachable_here = False

    harness.cli("doctor")

    line = next(l for l in capsys.readouterr().out.splitlines() if "Claude:" in l)
    assert line.startswith(ops.FAIL) and setup_steps.PLAIN_TERMINAL in line


@pytest.mark.macos
def test_on_a_mac_doctor_finds_the_codex_the_setup_page_installed_without_the_config_naming_it(
        harness, nothing_loaded, monkeypatch, capsys):
    """OpenAI's installer puts codex in ~/.local/bin, on the family's PATH only in Terminal
    windows opened later (#473)."""
    monkeypatch.setattr(codex_backend, "CODEX_BUNDLED", ())  # not this Mac's own apps
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    harness.config["llm"]["backend"] = "codex"
    harness.model_reply = {"ok": True}
    assert "codex_path" not in harness.config["llm"]
    codex = program(harness.home / ".local" / "bin", "codex")

    harness.cli("doctor")

    line = codex_line(capsys)
    assert line.startswith(ops.OK) and str(codex) in line


@pytest.mark.macos
def test_on_a_mac_doctor_says_a_missing_codex_is_installed_on_the_setup_page_or_comes_with_the_app(
        harness, nothing_loaded, monkeypatch, capsys):
    monkeypatch.setattr(codex_backend, "CODEX_BUNDLED", ())
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    harness.config["llm"]["backend"] = "codex"

    harness.cli("doctor")

    line = codex_line(capsys)
    assert line.startswith(ops.FAIL) and "the setup page" in line and "ChatGPT desktop app" in line
    assert "curl -fsSL https://github.com/openai/codex/releases/latest/download/install.sh | sh" in line


@pytest.fixture
def codex_on_windows(harness, nothing_loaded, windows_programs, monkeypatch) -> Path:
    """A Household on ChatGPT, on a PC where codex.exe would be where OpenAI's installer puts it,
    which the test puts there or not. Returns where."""
    monkeypatch.setattr(codex_backend, "CODEX_BUNDLED", ())  # not this Mac's own apps
    harness.config["llm"]["backend"] = "codex"
    harness.model_reply = {"ok": True}
    return windows_programs / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe"


def codex_line(capsys) -> str:
    return next(l for l in capsys.readouterr().out.splitlines() if "Codex:" in l)


def test_on_windows_doctor_says_codex_is_missing_and_how_to_install_it(harness, codex_on_windows,
                                                                       capsys):
    harness.cli("doctor")

    line = codex_line(capsys)
    assert line.startswith(ops.FAIL) and "the setup page" in line
    assert "irm https://github.com/openai/codex/releases/latest/download/install.ps1 | iex" in line
    assert "ChatGPT desktop app" not in line


def test_on_windows_doctor_says_a_signed_out_codex_is_and_how_to_sign_in(harness, codex_on_windows,
                                                                        capsys):
    program(codex_on_windows.parent, codex_on_windows.name)
    harness.signed_in["codex"] = False

    harness.cli("doctor")

    line = codex_line(capsys)
    assert line.startswith(ops.FAIL) and "not signed in" in line
    assert "codex login" in line and "PowerShell" in line and ".codex" in line
    status = harness.commands[-1]
    assert [os.path.normcase(status[0]), *status[1:]] == \
        [os.path.normcase(codex_on_windows), "login", "status"]
    assert not harness.model_calls  # no call to a Codex that can't answer


def test_on_windows_doctor_calls_a_signed_in_codex(harness, codex_on_windows, capsys):
    program(codex_on_windows.parent, codex_on_windows.name)

    harness.cli("doctor")

    assert codex_line(capsys).startswith(ops.OK)
    # Windows' `which` names it codex.EXE, in PATHEXT's capitals, once its folder is on PATH.
    assert [os.path.normcase(p) for p in harness.model.reached] == \
        [os.path.normcase(codex_on_windows)]


@pytest.fixture
def claude_on_windows(harness, nothing_loaded, windows_programs) -> Path:
    """A Household on Claude, on a PC where claude.exe would be where Claude Code's own Windows
    installer puts it, which the test puts there or not. Returns where."""
    harness.config["llm"]["backend"] = "claude"
    return harness.home / ".local" / "bin" / "claude.exe"


def claude_line(capsys) -> str:
    return next(l for l in capsys.readouterr().out.splitlines() if "Claude:" in l)


def test_on_windows_doctor_says_claude_code_is_missing_and_how_to_install_it(
        harness, claude_on_windows, capsys):
    harness.cli("doctor")

    line = claude_line(capsys)
    assert line.startswith(ops.FAIL) and "the setup page" in line
    assert "irm https://claude.ai/install.ps1 | iex" in line
    assert "curl" not in line and "login shell" not in line


def test_on_windows_doctor_says_the_claude_token_is_missing_and_where_to_store_it(
        harness, claude_on_windows, capsys):
    program(claude_on_windows.parent, claude_on_windows.name)

    harness.cli("doctor")

    line = claude_line(capsys)
    assert line.startswith(ops.FAIL) and "Credential Manager" in line and "setup page" in line
    assert "Keychain" not in line and "setup claude" not in line
    assert not harness.model_calls  # the evening Brief has no sign-in to call Claude with


def test_on_windows_doctor_calls_claude_with_the_stored_token(harness, claude_on_windows, capsys):
    program(claude_on_windows.parent, claude_on_windows.name)
    harness.secrets["claude-oauth-token"] = "sk-ant-oat01-Abc_def-123456789"

    harness.cli("doctor")

    assert claude_line(capsys).startswith(ops.OK)
    assert [os.path.normcase(p) for p in harness.model.reached] == \
        [os.path.normcase(claude_on_windows)]


def test_doctor_names_an_unreadable_config_in_english(harness, capsys):
    harness.config = {"kids": "not a list"}

    assert harness.cli("doctor", "--skip-llm") == 1

    out = capsys.readouterr().out
    assert "Config file" in out
    assert not HAN.findall(out)


# ── WhatsApp: the Linked Device's link (ADR 0027)


def whatsapp_line(harness, capsys) -> str:
    """doctor's WhatsApp line, for a Household with WhatsApp's groups picked."""
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["wilma"]["enabled"] = False
    harness.jobs.add(jobs.DAILY)

    harness.cli("doctor", "--skip-llm")

    [line] = [l for l in capsys.readouterr().out.splitlines() if "WhatsApp:" in l]
    return line


@pytest.mark.parametrize("on", [MACOS, WINDOWS])
def test_doctor_says_the_linked_device_is_connected_on_any_system(harness, capsys, on):
    harness.system = on
    # Setup's old step that read the Mac app recorded which Python read it; nothing reads that now.
    record = harness.home / ".family" / "whatsapp-python.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text('{"python": "/Users/x/ParentRecap/runtime/python-3.12.6/bin/python3.12"}')
    harness.source_fakes["whatsapp"].status(paired=True, connected_since="2026-09-27T05:12:00.000Z",
                                            last_connected="2026-09-27T05:12:00.000Z", link_ended_at=None)

    line = whatsapp_line(harness, capsys)

    assert line == ('✅ WhatsApp: connected since 2026-09-27 08:12, keeping 2 picked groups: '
                    '"3B parents", "Leo piano"')
    assert "python-3.12.6" not in line


def test_doctor_says_when_the_linked_device_last_connected(harness, capsys):
    harness.source_fakes["whatsapp"].status(paired=True, connected_since=None,
                                            last_connected="2026-09-27T17:40:00.000Z", link_ended_at=None)

    assert whatsapp_line(harness, capsys).startswith(
        '⚠️  WhatsApp: not connected right now, last connected 2026-09-27 20:40, for 2 picked '
        'groups: "3B parents", "Leo piano".')


def test_doctor_says_a_link_whatsapp_ended_needs_a_new_pairing(harness, capsys):
    harness.source_fakes["whatsapp"].status(paired=False, connected_since=None,
                                            last_connected="2026-09-26T05:30:00.000Z",
                                            link_ended_at="2026-09-26T05:30:00.000Z")

    assert whatsapp_line(harness, capsys) == (
        "❌ WhatsApp: needs a new pairing: WhatsApp ended the link on 2026-09-26 08:30, so the Brief "
        "has no WhatsApp messages (pair again on the setup page)")


def test_doctor_says_groups_picked_without_a_pairing_need_a_new_pairing(harness, capsys):
    # As a Household that read WhatsApp for Mac's database has it: groups picked by name only.
    for chat in harness.config["whatsapp"]["chats"]:
        del chat["id"]

    assert whatsapp_line(harness, capsys) == (
        "❌ WhatsApp: needs a new pairing: the picked groups have no pairing with WhatsApp yet, so "
        "the Brief has no WhatsApp messages (pair again on the setup page)")


def test_doctor_has_no_whatsapp_line_without_picked_groups(harness, capsys):
    harness.config["whatsapp"] = {"enabled": False, "chats": []}
    harness.jobs.add(jobs.DAILY)

    harness.cli("doctor", "--skip-llm")

    assert "WhatsApp:" not in capsys.readouterr().out


@pytest.mark.parametrize("on, whatsapp", [(MACOS, True), (LINUX, False)])
def test_on_the_hosted_version_s_server_doctor_has_no_whatsapp_line(harness, capsys, on, whatsapp):
    harness.system = on
    harness.source_fakes["whatsapp"].status(paired=True, connected_since="2026-09-27T05:12:00.000Z",
                                            last_connected="2026-09-27T05:12:00.000Z", link_ended_at=None)
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.jobs.add(jobs.DAILY)

    harness.cli("doctor", "--skip-llm")

    items = {line.split(":")[0].split(" ", 1)[1].strip()
             for line in capsys.readouterr().out.splitlines() if ":" in line}
    assert ({"WhatsApp", "WhatsApp library"} <= items) is whatsapp
    assert not {"WhatsApp", "WhatsApp library"} & items or whatsapp
    # Nor any of Windows' own: a Linux server has no wake-up to check.
    assert not {"Wake", "Sign-in after updates"} & items
    assert {"Config file", "Wilma", "Schedule"} <= items


SKILLS = Path(__file__).resolve().parents[3] / "skills"


def test_the_skills_and_docs_never_send_the_parent_to_a_file_called_python3():
    # Finder selects the pinned Python's own file, such as python3.13 (#297).
    root = Path(__file__).resolve().parents[3]
    for path in [*SKILLS.glob("*/SKILL.md"), *(root / "docs").glob("*.md"), *root.glob("README*.md")]:
        assert not re.search(r"python3(?![.\d])", path.read_text()), path


# ── Each check's key, which the setup page goes by instead of doctor's words (#414)


def test_no_two_checks_share_a_key():
    keys = [key for key, _ in ops.CHECK_ITEMS]

    assert len(keys) == len(set(keys))


def test_a_check_keeps_its_key_whatever_language_or_kid_it_is_about(harness, nothing_loaded,
                                                                     monkeypatch):
    from family_brief.collectors import myclub
    harness.config["email"]["to"] = [{"address": "parent@example.com", "language": "zh"},
                                     {"address": "partner@example.com", "language": "fi"}]
    for kid in harness.config["kids"]:
        kid["myclub_ical_url"] = f"https://example.myclub.fi/ical/{kid['name']}.ics"
    monkeypatch.setattr(myclub, "download", lambda url: "BEGIN:VEVENT")
    config = harness.home / ".family" / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(yaml.safe_dump(harness.config, allow_unicode=True))

    results = ops.health_checks(str(config), skip_llm=True)

    about = [(c.key, c.subject, c.item) for c in results if c.subject is not None]
    assert about == [("language", "zh", "Language zh"), ("language", "fi", "Language fi"),
                     ("myclub", "Mia", "MyClub (Mia)"), ("myclub", "Leo", "MyClub (Leo)")]
    assert {c.key for c in results} <= dict(ops.CHECK_ITEMS).keys()


def test_renaming_a_check_in_doctor_s_words_leaves_what_the_setup_page_shows(harness,
                                                                              nothing_loaded,
                                                                              monkeypatch, capsys):
    from family_brief import setup_server
    monkeypatch.setitem(ops._ITEMS, "gmail", "Email inbox")
    monkeypatch.setitem(ops._ITEMS, "language", "Brief text in {}")
    config = harness.home / ".family" / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(yaml.safe_dump(harness.config, allow_unicode=True))

    results = ops.health_checks(str(config), skip_llm=True, show=True, schedule=False)

    out = capsys.readouterr().out
    assert "Email inbox:" in out and "Brief text in zh:" in out
    shown = [setup_server.health.health_check(c) for c in results
             if c.key not in setup_server.health.DOCTOR_ONLY]
    assert {"check": "gmail", "status": "fail"} in [{k: s[k] for k in ("check", "status")}
                                                    for s in shown]
    assert {"check": "language", "status": "ok", "language": "zh"} in shown
    assert not [s for s in shown if s["check"] == "other"]


def wilma_cli_listing(harness, kids: Any) -> None:
    """The wilma CLI, installed, printing `kids` for `kids list`."""
    wilma_cli = harness.source_fakes["wilma"]
    wilma_cli.set_up()
    wilma_cli.answers[("kids", "list")] = kids


def test_discover_wilma_students_runs_before_any_config_exists(harness, monkeypatch, capsys):
    # Setup signs in to Wilma before the household step, to prefill the Kids from what Wilma lists.
    wilma_cli_listing(harness, {"kids": [{"name": "Aino Virtanen"}]})
    monkeypatch.setattr(sys, "argv", ["family-brief", "-c", str(harness.home / "missing.yaml"),
                                      "discover", "wilma-students"])

    assert cli.main() == 0

    assert "Aino Virtanen" in capsys.readouterr().out


def test_discover_wilma_students_says_when_not_signed_in(harness, monkeypatch, capsys):
    wilma_cli_listing(harness, (1, {"status": "error", "code": "not_logged_in", "message": "Not signed in."}))
    monkeypatch.setattr(sys, "argv", ["family-brief", "-c", str(harness.home / "missing.yaml"),
                                      "discover", "wilma-students"])

    assert cli.main() == 1

    assert "not signed in to wilma" in capsys.readouterr().out


# ── A Gmail with no sender ticked (#361)


class GmailImap(GmailFolders):
    """Gmail's IMAP server: a sign-in, and each search doctor makes."""

    def __init__(self) -> None:
        self.searches: list[tuple] = []
        self.logins: list[tuple] = []

    def __call__(self, *_a, **_k) -> "GmailImap":
        return self

    def login(self, *a) -> None:
        self.logins.append(a)

    def search(self, *args) -> tuple[str, list[bytes]]:
        self.searching()
        self.searches.append(args)
        return "OK", [b"1 2 3"]

    def logout(self) -> None:
        pass


@pytest.fixture
def gmail_with_no_sender(harness, nothing_loaded, monkeypatch) -> GmailImap:
    harness.config["gmail"]["allowlist_domains"] = []
    harness.secrets["gmail-imap-parent@example.com"] = "abcdabcdabcdabcd"
    imap = GmailImap()
    monkeypatch.setattr(imaplib, "IMAP4_SSL", imap)
    return imap


@pytest.mark.parametrize("another_source", [True, False])
def test_doctor_leaves_a_gmail_with_no_sender_alone(harness, gmail_with_no_sender, capsys,
                                                    another_source):
    # It isn't read, and it sends nothing either: the gateway sends the Brief (ADR 0032).
    harness.config["wilma"]["enabled"] = another_source
    wilma_cli_listing(harness, {"kids": [{"name": "Mia"}]})

    harness.cli("doctor", "--skip-llm")

    assert "Gmail:" not in capsys.readouterr().out
    assert gmail_with_no_sender.logins == [] and gmail_with_no_sender.searches == []
