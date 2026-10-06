"""Other accounts on the family Mac can't read what Parent Recap keeps, and the MyClub link's
personal token never shows up in what it prints, logs or archives."""
from __future__ import annotations

import json
import logging
import stat
from pathlib import Path

import pytest
import requests
import yaml

from conftest import msg
from family_brief import ops
from family_brief.collectors import myclub
from family_brief.collectors.myclub import collect_events as real_collect_events  # before the harness fakes it

TOKEN = "s3cr3t-t0ken"
LINK = f"https://example.myclub.fi/ical/{TOKEN}?key={TOKEN}"


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


class _Response:
    def __init__(self, status: int) -> None:
        self.status_code, self.text, self.url = status, "", LINK


def _failing_get(kind: str):
    def get(url: str, **_k):
        if kind == "refused":
            raise requests.ConnectionError(
                f"HTTPSConnectionPool(host='example.myclub.fi', port=443): Max retries exceeded "
                f"with url: /ical/{TOKEN}?key={TOKEN} (Caused by NewConnectionError('refused'))")
        return _Response(404)
    return get


def _real_myclub(harness, monkeypatch, kind: str) -> None:
    harness.config["kids"][0]["myclub_ical_url"] = LINK
    monkeypatch.setattr(myclub, "collect_events", real_collect_events)
    monkeypatch.setattr(myclub.requests, "get", _failing_get(kind))


# ── MyClub token

@pytest.mark.parametrize("kind", ["refused", "404"])
def test_a_failed_myclub_fetch_leaves_the_token_out_of_logs_and_archive(harness, monkeypatch, caplog, kind):
    _real_myclub(harness, monkeypatch, kind)
    harness.sources["gmail"] = [msg("gmail", "g1", "2026-09-27T18:00:00+03:00", "Retkipäivä huomenna",
                                    sender="teacher@kilo.example.fi", subject="Retki")]
    caplog.set_level(logging.INFO)

    assert harness.run() == 0

    archived = (harness.archive_dir / "2026-09-27.raw.json").read_text()
    error = json.loads(archived)["summary"]["_coverage"]["myclub"]["error"]
    assert "example.myclub.fi" in error
    if kind == "404":
        assert "404" in error
    for text in (archived, caplog.text, harness.sent[0].text):
        assert TOKEN not in text and "/ical/" not in text


@pytest.mark.parametrize("kind", ["refused", "404"])
def test_doctor_names_only_the_myclub_host_and_status(harness, monkeypatch, capsys, kind):
    _real_myclub(harness, monkeypatch, kind)
    harness.config["wilma"]["enabled"] = False
    harness.config["whatsapp"]["enabled"] = False
    monkeypatch.setattr(ops, "launchctl_loaded", lambda: set())

    harness.cli("doctor", "--skip-llm")

    line = next(l for l in capsys.readouterr().out.splitlines() if "MyClub (Mia)" in l)
    assert "example.myclub.fi" in line
    assert "parent-recap setup myclub --kid Mia" in line
    if kind == "404":
        assert "404" in line
    assert TOKEN not in line and "/ical/" not in line


def test_a_link_saved_from_terminal_keeps_the_rest_of_the_config(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "timezone: Europe/Helsinki  # where we live\n"
        "kids:\n"
        "  - name: \"Mia\"  # the older one\n"
        "    grade: 3\n"
        "    myclub_ical_url: null # Personal MyClub link\n"
        "  - name: Leo\n"
        "    grade: 1\n"
        "gmail:\n"
        "  username: parent@example.com\n")
    cfg.chmod(0o600)

    myclub.save_link(cfg, "Mia", LINK)
    myclub.save_link(cfg, "Leo", "https://example.myclub.fi/ical/leo")
    myclub.save_link(cfg, "Mia", LINK + "2")

    text = cfg.read_text()
    assert "# where we live" in text and "# the older one" in text and "# Personal MyClub link" in text
    data = yaml.safe_load(text)
    assert [k.get("myclub_ical_url") for k in data["kids"]] == [LINK + "2",
                                                                "https://example.myclub.fi/ical/leo"]
    assert data["gmail"] == {"username": "parent@example.com"}
    assert _mode(cfg) == 0o600


def test_saving_a_link_for_an_unknown_kid_changes_nothing(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("kids:\n  - name: Mia\n")

    with pytest.raises(ValueError, match="Mia"):
        myclub.save_link(cfg, "Emma", LINK)
    assert cfg.read_text() == "kids:\n  - name: Mia\n"


# ── Owner-only files

def test_archive_and_logs_are_owner_only(harness):
    harness.sources["gmail"] = [msg("gmail", "g1", "2026-09-27T18:00:00+03:00", "Retkipäivä huomenna",
                                    sender="teacher@kilo.example.fi", subject="Retki")]
    harness.model_error = "boom"  # the model fails, so its diagnostics are saved too

    assert harness.run() == 0

    logs = harness.archive_dir / "logs"
    assert _mode(harness.archive_dir) == 0o700 and _mode(logs) == 0o700
    assert list(logs.glob("claude_cli_failed.txt"))
    for f in [*harness.archive_dir.glob("2026-09-27.*"), *logs.iterdir()]:
        assert _mode(f) == 0o600, f.name


def test_an_existing_install_is_tightened_on_the_next_run(harness):
    logs = harness.archive_dir / "logs"
    logs.mkdir(parents=True)
    old = harness.archive_dir / "2026-09-20.raw.json"
    old.write_text("{}")
    (logs / "run-stderr.log").write_text("")
    for p in (harness.archive_dir, logs):
        p.chmod(0o755)
    backup = harness.home / ".family" / "config.yaml.bak-202609201200"  # holds the MyClub link
    backup.parent.mkdir()
    backup.write_text("kids: []\n")
    for p in (old, logs / "run-stderr.log", backup):
        p.chmod(0o644)

    assert harness.run() == 0  # a quiet night still tightens

    assert _mode(harness.archive_dir) == 0o700 and _mode(logs) == 0o700
    assert _mode(old) == 0o600 and _mode(logs / "run-stderr.log") == 0o600
    assert _mode(backup) == 0o600


def test_scheduled_jobs_write_their_logs_owner_only(tmp_path):
    plist = ops._plist("com.parentrecap.daily", "run", {"Hour": 21, "Minute": 0}, tmp_path)
    assert plist["Umask"] == 0o077


def test_installer_makes_the_install_folder_owner_only():
    installer = (Path(__file__).parents[2] / "install.sh").read_text()
    assert 'chmod 700 "$TARGET"' in installer
    assert 'chmod -R go-rwx "$TARGET"' in installer
