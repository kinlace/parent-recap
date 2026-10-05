"""The Google Calendar authorization script needs the Household's own copy of a client file."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup_google_calendar.py"


def test_without_a_client_file_it_stops_and_names_the_three_ways_forward(tmp_path):
    result = subprocess.run([sys.executable, str(SCRIPT)], env={**os.environ, "HOME": str(tmp_path)},
                            capture_output=True, text=True, timeout=30)

    assert result.returncode != 0
    for way in ("~/.family/calendar_credentials.json",  # where the file goes
                "maintainers",                           # the file operators hand out
                "sources.md",                            # the own-client guide
                "ics"):
        assert way in result.stderr
    assert not (tmp_path / ".family" / "calendar_credentials.json").exists()
    assert not (tmp_path / ".family" / "calendar_token.json").exists()


def test_without_googles_packages_it_says_how_to_install_them(tmp_path):
    # Google's packages are the `google` extra, which install.sh adds only for google mode.
    (tmp_path / ".family").mkdir()
    (tmp_path / ".family" / "calendar_credentials.json").write_text("{}")
    no_google = Path(__file__).with_name("no_google")
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=30,
                            env={**os.environ, "HOME": str(tmp_path), "PYTHONPATH": str(no_google)})

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "google_calendar.mode" in result.stderr and "install.sh" in result.stderr
    assert not (tmp_path / ".family" / "calendar_token.json").exists()
