"""`parent-recap doctor` and `discover` speak English, whatever language the Brief is in."""
from __future__ import annotations

import imaplib
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

import fake_node
from family_brief import __main__ as cli, ops, setup_server, setup_steps
from family_brief.llm import codex as codex_backend
from family_brief.system import jobs
from conftest import program
from fake_sources import GmailFolders
from fake_system import LINUX, MACOS, WINDOWS

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
    for path in [*SKILLS.glob("*/SKILL.md"), *(root / "docs").glob("*.md"), root / "README.md"]:
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
    shown = [setup_server.health.health_check(c) for c in results]
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

    def __call__(self, *_a, **_k) -> "GmailImap":
        return self

    def login(self, *_a) -> None:
        pass

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


def gmail_line(out: str) -> str:
    [line] = [l for l in out.splitlines() if "Gmail:" in l]
    return line


def test_doctor_is_ok_with_a_gmail_that_only_sends_the_brief_beside_another_source(
        harness, gmail_with_no_sender, monkeypatch, capsys):
    harness.config["wilma"]["enabled"] = True
    wilma_cli_listing(harness, {"kids": [{"name": "Mia"}]})

    harness.cli("doctor", "--skip-llm")

    line = gmail_line(capsys.readouterr().out)
    assert line.startswith(ops.OK.strip())
    assert "parent@example.com signed in" in line and "only sends the Brief" in line
    assert gmail_with_no_sender.searches == []  # never the whole inbox


def test_doctor_fails_a_gmail_with_no_sender_and_no_other_source(harness, gmail_with_no_sender,
                                                                 capsys):
    harness.cli("doctor", "--skip-llm")

    line = gmail_line(capsys.readouterr().out)
    assert line.startswith(ops.FAIL.strip()) and "the sender allowlist is empty" in line
    assert gmail_with_no_sender.searches == []
