"""The Linux adapter of `family_brief.system.desktop`: a server has no browser, editor or window to
open, so a page's link is printed for the maintainer to open elsewhere, and the languages are the
locale's (ADR 0020). Callers' tests use fake_system.FakeDesktop instead. Nothing is faked but the
environment: the adapter starts no program."""
from __future__ import annotations

import subprocess

import pytest

from family_brief.system import desktop, desktop_linux


@pytest.fixture
def linux(monkeypatch) -> list[list[str]]:
    """The Linux adapter in place of the harness's fake; records any program it would start."""
    started: list[list[str]] = []
    monkeypatch.setattr(desktop, "_adapter", desktop_linux)
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **k: started.append(cmd))
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, *a, **k: started.append(cmd))
    return started


def test_a_page_s_link_is_printed_instead_of_opened(linux, capsys):
    assert not desktop.open_url("http://127.0.0.1:8765/setup")

    printed = capsys.readouterr()
    assert "http://127.0.0.1:8765/setup" in printed.err
    assert printed.out == ""  # a command's own output, such as JSON, stays as it is
    assert linux == []


def test_no_file_or_terminal_window_opens(linux, home):
    assert not desktop.open_file(home / "list.txt")
    assert not desktop.open_terminal(["parent-recap", "doctor"], {}, "Parent Recap")
    desktop.forget_terminal("Parent Recap")

    assert linux == []


@pytest.mark.parametrize("env, languages", [
    (dict(LANGUAGE="fi:en_GB", LANG="fi_FI.UTF-8"), ["fi", "en-GB"]),
    (dict(LC_ALL="zh_CN.UTF-8", LANG="en_US.UTF-8"), ["zh-CN"]),
    (dict(LANG="fi_FI.UTF-8"), ["fi-FI"]),
    (dict(LANG="C.UTF-8"), []),
    (dict(), []),
])
def test_the_languages_are_the_locale_s(linux, monkeypatch, env, languages):
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)
    for var, value in env.items():
        monkeypatch.setenv(var, value)

    assert desktop.preferred_languages() == languages
