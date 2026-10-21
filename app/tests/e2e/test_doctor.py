"""`parent-recap doctor` and `discover` speak English, whatever language the Brief is in."""
from __future__ import annotations

import imaplib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

import pytest
import yaml

from family_brief import __main__ as cli, ops, setup_steps, whatsapp_python
from family_brief.collectors import whatsapp, wilma
from family_brief.llm import codex as codex_backend
from family_brief.system import jobs
from conftest import program
from test_setup_whatsapp import WHATSAPP_FAILURES, break_whatsapp, fake_mac

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


@pytest.fixture
def claude_without_token(harness, monkeypatch):
    """Claude Code installed and answering, with no claude-oauth-token in the Keychain."""
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["wilma"]["enabled"] = False
    harness.config["whatsapp"]["enabled"] = False
    which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda cmd, *a, **k: f"/usr/local/bin/{cmd}"
                        if cmd == "claude" else which(cmd, *a, **k))
    harness.model_reply = "OK"


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
    assert [os.path.normcase(c.argv[0]) for c in harness.model_calls] == \
        [os.path.normcase(codex_on_windows)]


@pytest.fixture
def claude_on_windows(harness, nothing_loaded, windows_programs) -> Path:
    """A Household on Claude, on a PC where claude.exe would be where Claude Code's own Windows
    installer puts it, which the test puts there or not. Returns where."""
    harness.config["llm"]["backend"] = "claude"
    harness.model_reply = "OK"
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
    assert [os.path.normcase(c.argv[0]) for c in harness.model_calls] == \
        [os.path.normcase(claude_on_windows)]


def test_doctor_names_an_unreadable_config_in_english(harness, capsys):
    harness.config = {"kids": "not a list"}

    assert harness.cli("doctor", "--skip-llm") == 1

    out = capsys.readouterr().out
    assert "Config file" in out
    assert not HAN.findall(out)


@pytest.mark.macos
def test_doctor_in_a_bg_job_says_which_python_needs_whatsapp_access(harness, monkeypatch, capsys):
    monkeypatch.setenv(ops.BG_ENV, "1")
    monkeypatch.setattr(whatsapp, "check_access", lambda: whatsapp.NO_ACCESS)

    assert harness.cli("doctor", "--whatsapp-only") == 1

    out = capsys.readouterr().out
    assert os.path.realpath(sys.executable) in out and "add it to Full Disk Access" in out
    assert "App Management" not in out
    assert not HAN.findall(out)


# ── Full Disk Access, checked through bg on the fake Mac without a question from macOS (ADR 0012)


@pytest.fixture
def mac_with_whatsapp(harness, monkeypatch):
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["wilma"]["enabled"] = False
    monkeypatch.delenv(ops.BG_ENV, raising=False)
    harness.jobs.add(jobs.DAILY)
    mac = fake_mac(harness, monkeypatch)
    mac.install_whatsapp([("3B parents", 1, False), ("Leo piano", 2, False)])
    return mac


def whatsapp_lines(out: str) -> list[str]:
    return [l for l in out.splitlines() if "WhatsApp:" in l]


@pytest.mark.macos
@pytest.mark.parametrize("command", [["doctor", "--skip-llm"], ["bg", "doctor", "--skip-llm"]])
def test_doctor_fails_without_full_disk_access_and_macos_never_asks(harness, mac_with_whatsapp,
                                                                    capsys, command):
    mac = mac_with_whatsapp
    mac.full_disk_access_after = 10_000
    mac.allow = "allow"  # an Allow click would let one read through

    harness.cli(*command)

    [line] = whatsapp_lines(capsys.readouterr().out)
    assert line.lstrip().startswith(ops.FAIL.strip())
    assert os.path.realpath(sys.executable) in line and "add it to Full Disk Access" in line
    assert mac.prompts == 0 and mac.reads == []


@pytest.mark.macos
@pytest.mark.parametrize("command", [["doctor", "--skip-llm"], ["bg", "doctor", "--skip-llm"]])
def test_doctor_reads_whatsapp_with_full_disk_access_and_macos_never_asks(harness,
                                                                          mac_with_whatsapp,
                                                                          capsys, command):
    mac = mac_with_whatsapp

    harness.cli(*command)

    [line] = whatsapp_lines(capsys.readouterr().out)
    assert line.lstrip().startswith(ops.OK.strip()) and "all 2 configured groups found" in line
    assert mac.prompts == 0 and mac.reads and all(mac.reads)


def test_off_macos_doctor_has_no_whatsapp_check(harness, mac_with_whatsapp, monkeypatch, capsys):
    # WhatsApp for Mac's database and Full Disk Access exist only on macOS (ADR 0020).
    monkeypatch.setattr(sys, "platform", "win32")

    harness.cli("doctor", "--skip-llm")

    assert whatsapp_lines(capsys.readouterr().out) == []
    assert mac_with_whatsapp.reads == []


# ── the Python given Full Disk Access, against the evening job's (ADR 0011, 0012)


@pytest.fixture
def evening_job(harness, monkeypatch):
    """Parent Recap's own Python under ~/ParentRecap/runtime and the evening job's venv Python
    linking to it, as install.sh and schedule install lay them out. Returns the venv's link."""
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["wilma"]["enabled"] = False
    venv_python = harness.home / "ParentRecap" / "app" / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    harness.jobs.add(jobs.DAILY)
    harness.jobs.programs[jobs.DAILY] = [str(venv_python), "-m", "family_brief", "run"]
    return venv_python


def pinned_python(harness, version: str) -> str:
    python = harness.home / "ParentRecap" / "runtime" / f"python-{version}" / "bin" / "python3.12"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.touch()
    return os.path.realpath(python)


def allowed_python_lines(out: str) -> list[str]:
    return [l for l in out.splitlines() if "WhatsApp:" in l and "evening job's Python" in l]


@pytest.mark.macos
def test_doctor_is_quiet_when_the_python_allowed_is_the_evening_jobs(harness, evening_job, capsys):
    python = pinned_python(harness, "3.12.7")
    evening_job.symlink_to(python)
    whatsapp_python.record(python)

    harness.cli("doctor", "--skip-llm")

    assert allowed_python_lines(capsys.readouterr().out) == []


@pytest.mark.macos
def test_doctor_says_when_the_python_allowed_is_no_longer_the_evening_jobs(harness, evening_job,
                                                                         capsys):
    whatsapp_python.record(pinned_python(harness, "3.12.6"))
    python = pinned_python(harness, "3.12.7")
    evening_job.symlink_to(python)

    harness.cli("doctor", "--skip-llm")

    [line] = allowed_python_lines(capsys.readouterr().out)
    assert line.startswith(ops.WARN.strip())
    assert "no longer the evening job's Python" in line
    assert f"add {python} to Full Disk Access" in line
    assert "parent-recap full-disk-access" in line
    assert not HAN.findall(line)


@pytest.mark.macos
def test_doctor_fails_when_the_evening_jobs_python_points_at_nothing(harness, evening_job, capsys):
    whatsapp_python.record(pinned_python(harness, "3.12.6"))
    evening_job.symlink_to(harness.home / "ParentRecap" / "runtime" / "python-3.12.5" / "bin" / "python3.12")

    harness.cli("doctor", "--skip-llm")

    [line] = allowed_python_lines(capsys.readouterr().out)
    assert line.startswith(ops.FAIL)
    assert f"{evening_job} points at nothing" in line
    assert "no longer the evening job's Python" in line
    assert "parent-recap schedule install" in line
    assert f"add {os.path.realpath(sys.executable)} to Full Disk Access" in line


@pytest.mark.macos
def test_doctor_does_not_compare_an_install_from_before_the_record(harness, evening_job, capsys):
    evening_job.symlink_to(pinned_python(harness, "3.12.7"))

    harness.cli("doctor", "--skip-llm")

    assert not whatsapp_python.path().exists()
    assert allowed_python_lines(capsys.readouterr().out) == []


# ── why WhatsApp isn't OK, carried through the bg job for the setup page and the chat skills


@pytest.fixture
def mac_for_reasons(harness, monkeypatch):
    """The fake Mac, with doctor's other WhatsApp check and the network out of the way, and a
    clock that a job that hangs moves on."""
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    harness.config["wilma"]["enabled"] = False
    monkeypatch.delenv(ops.BG_ENV, raising=False)
    mac = fake_mac(harness, monkeypatch)
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    monkeypatch.setattr(time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    return mac


def whatsapp_check(harness) -> ops.Check:
    """Doctor's WhatsApp check, printed as doctor prints it."""
    config = harness.home / ".family" / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(yaml.safe_dump(harness.config, allow_unicode=True))
    results = ops.health_checks(str(config), skip_llm=True, show=True, schedule=False)
    [check] = [c for c in results if c.key == "whatsapp"]
    return check


@pytest.mark.macos
@pytest.mark.parametrize("failure", WHATSAPP_FAILURES)
def test_the_whatsapp_check_says_why_it_isn_t_ok_and_doctor_s_words_stay(harness, mac_for_reasons,
                                                                        monkeypatch, capsys,
                                                                        failure):
    break_whatsapp(mac_for_reasons, monkeypatch, failure)

    check = whatsapp_check(harness)

    assert (check.status, check.reason) == WHATSAPP_FAILURES[failure]
    assert check.groups == (("Leo piano",) if failure == "missing-groups" else ())
    # doctor prints the detail as before, with nothing of the reason the bg job sent along.
    [line] = whatsapp_lines(capsys.readouterr().out)
    assert line == f"{check.status} WhatsApp: {check.detail}"
    assert "reason" not in line and "{" not in line
    said = {"no-access": f"Python {os.path.realpath(sys.executable)} can't read WhatsApp: add it "
                        "to Full Disk Access",
            "hangs": "didn't finish within 180 seconds",
            "not-started": "the background check didn't start",
            "missing-groups": 'these group names aren\'t on this Mac (renamed, or a new group for '
                              'the new school year?): ["Leo piano"]',
            "list-failed": "couldn't list the groups: database disk image is malformed"}
    assert said[failure] in line
    assert line.endswith("(checked with the scheduled job's Python)") == \
        (failure in ("no-access", "missing-groups", "list-failed"))  # read in the bg job
    assert mac_for_reasons.prompts == 0
    # The chat skills give the same reason's fix, by doctor's own words.
    assert f'"{SKILL_WORDS[failure]}"' in skill_whatsapp_section() and SKILL_WORDS[failure] in line


# The words of doctor's WhatsApp line that the setup skill's advice goes by, for each failure.
SKILL_WORDS = {"no-access": "can't read WhatsApp: add it to Full Disk Access",
               "hangs": "didn't finish within 180 seconds",
               "not-started": "the background check didn't start",
               "missing-groups": "these group names aren't on this Mac",
               "list-failed": "couldn't list the groups"}
SKILLS = Path(__file__).resolve().parents[3] / "skills"


def skill_whatsapp_section() -> str:
    skill = (SKILLS / "setup" / "SKILL.md").read_text()
    [section] = re.findall(r"\n## When doctor's WhatsApp line isn't ✅\n(.*?)(?=\n## |\Z)", skill, re.S)
    return section


def test_the_chat_skills_say_only_the_parent_can_turn_on_full_disk_access():
    section = skill_whatsapp_section()
    assert "only the parent can turn it on" in section and "you can't do it for them" in section
    assert "`$FB full-disk-access`" in section
    manage = (SKILLS / "manage" / "SKILL.md").read_text()
    assert manage.count("\"When doctor's WhatsApp line isn't ✅\" in `PLUGIN/skills/setup/SKILL.md`") == 2
    assert "Only the user can turn on Full Disk Access" in manage


def test_the_skills_and_docs_never_send_the_parent_to_a_file_called_python3():
    # Finder selects the pinned Python's own file, such as python3.13 (#297).
    root = Path(__file__).resolve().parents[3]
    for path in [*SKILLS.glob("*/SKILL.md"), *(root / "docs").glob("*.md"), root / "README.md"]:
        assert not re.search(r"python3(?![.\d])", path.read_text()), path


@pytest.mark.macos
def test_the_whatsapp_check_is_ok_with_no_reason(harness, mac_for_reasons):
    mac_for_reasons.install_whatsapp([("3B parents", 1, False), ("Leo piano", 2, False)])

    check = whatsapp_check(harness)

    assert (check.status, check.reason, check.groups) == (ops.OK, None, ())


def test_the_bg_job_sends_each_whatsapp_check_as_one_line_of_json(harness, monkeypatch, capsys):
    monkeypatch.setenv(ops.BG_ENV, "1")
    monkeypatch.setattr(whatsapp, "check_access", lambda: None)
    monkeypatch.setattr(whatsapp, "list_groups", lambda days: [{"name": "3B parents"}])

    harness.cli("doctor", "--whatsapp-only")

    [line] = capsys.readouterr().out.splitlines()
    assert json.loads(line) == {
        "status": ops.WARN, "key": "whatsapp", "item": "WhatsApp", "reason": "missing-groups",
        "groups": ["Leo piano"], "subject": None,
        "detail": "readable, but these group names aren't on this Mac (renamed, or a new group for "
                  'the new school year?): ["Leo piano"]'}


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
    shown = [setup_server._health_check(c) for c in results]
    assert {"check": "gmail", "status": "fail"} in shown
    assert {"check": "language", "status": "ok", "language": "zh"} in shown
    assert not [s for s in shown if s["check"] == "other"]


def test_discover_without_whatsapp_access_points_to_bg(harness, monkeypatch, capsys):
    monkeypatch.delenv(ops.BG_ENV, raising=False)
    monkeypatch.setattr(whatsapp, "check_access", lambda: whatsapp.NO_ACCESS)

    assert harness.cli("discover", "whatsapp-chats") == 1

    out = capsys.readouterr().out
    assert "parent-recap bg discover whatsapp-chats" in out
    assert not HAN.findall(out)


def test_discover_wilma_students_runs_before_any_config_exists(tmp_path, monkeypatch, capsys):
    # Setup signs in to Wilma before the household step, to prefill the Kids from what Wilma lists.
    monkeypatch.setattr(wilma, "_run_or_log", lambda args: {"kids": [{"name": "Aino Virtanen"}]})
    monkeypatch.setattr(sys, "argv", ["family-brief", "-c", str(tmp_path / "missing.yaml"),
                                      "discover", "wilma-students"])

    assert cli.main() == 0

    assert "Aino Virtanen" in capsys.readouterr().out


def test_discover_wilma_students_says_when_not_signed_in(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(wilma, "_run_or_log", lambda args: None)
    monkeypatch.setattr(sys, "argv", ["family-brief", "-c", str(tmp_path / "missing.yaml"),
                                      "discover", "wilma-students"])

    assert cli.main() == 1

    assert "not signed in to wilma" in capsys.readouterr().out


# ── A Gmail with no sender ticked (#361)


class GmailImap:
    """Gmail's IMAP server: a sign-in, and each search doctor makes."""

    def __init__(self) -> None:
        self.searches: list[tuple] = []

    def __call__(self, *_a, **_k) -> "GmailImap":
        return self

    def login(self, *_a) -> None:
        pass

    def select(self, *_a, **_k) -> None:
        pass

    def search(self, *args) -> tuple[str, list[bytes]]:
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
    monkeypatch.setattr(wilma, "_run_or_log", lambda args: {"kids": [{"name": "Mia"}]})

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
