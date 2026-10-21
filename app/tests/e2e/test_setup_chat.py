"""The setup page and the chat (ADR 0006): what Welcome sets up so the family can ask for changes in
the chat later (#98), and continuing in the chat once setup is done.

The server runs in-process on a free port, inside the harness, as `setup_page.py` starts it, so the
config, the progress record, the secrets, the jobs, the wake and the desktop are the harness's.
Assertions are on what the page and the family see: the responses, the config and progress written,
and what was opened."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from family_brief import __main__ as cli, install_record, setup_server
from family_brief.config import Config
from setup_page import PAGE_DIR, ROOT, Response, call, chat_installed, config_file, source, welcome
from setup_page import _listening, ai, clock, page  # noqa: F401


# ── Welcome: what lets the family ask for changes in the chat later (#98)


@pytest.mark.macos
def test_picking_claude_installs_the_claude_code_plugin_from_the_stable_marketplace(harness, page,
                                                                                    ai):
    ai("claude")
    assert call(page.url, "api/state").json()["chat"] == {}

    assert welcome(page.url, ai="claude").status == 200

    assert chat_installed(page.url, "claude") == "installed"
    assert harness.plugin_calls == [
        ["plugin", "list", "--json"], ["plugin", "marketplace", "list", "--json"],
        ["plugin", "marketplace", "add", "kinlace/parent-recap#stable"],
        ["plugin", "install", "parent-recap@kinlace", "--scope", "user"],
    ]
    assert not (harness.home / ".agents").exists() and not harness.model_calls
    # So uninstall removes them.
    assert install_record.entries("claude-plugin") == ["parent-recap@kinlace"]
    assert install_record.entries("claude-marketplace") == ["kinlace"]


@pytest.mark.macos
def test_a_claude_code_plugin_already_there_is_updated(harness, page, ai):
    ai("claude")
    harness.claude_marketplaces = [{"name": "kinlace", "source": "github",
                                    "repo": "kinlace/parent-recap"}]
    harness.claude_plugins = [{"id": "parent-recap@kinlace", "scope": "user"}]

    welcome(page.url, ai="claude")

    assert chat_installed(page.url, "claude") == "installed"
    assert harness.plugin_calls[2:] == [["plugin", "marketplace", "update", "kinlace"],
                                        ["plugin", "update", "parent-recap@kinlace"]]
    # Installed before setup: uninstall leaves them.
    assert install_record.entries("claude-plugin") == []
    assert install_record.entries("claude-marketplace") == []


@pytest.mark.macos
def test_a_plugin_from_an_unzipped_release_or_its_old_name_is_switched_to_stable(harness, page, ai):
    ai("claude")
    harness.claude_marketplaces = [{"name": "family-brief"},
                                   {"name": "kinlace", "source": "directory",
                                    "path": "/Users/mum/ParentRecap/plugin"}]
    harness.claude_plugins = [{"id": "family-brief@family-brief"}, {"id": "parent-recap@kinlace"}]

    welcome(page.url, ai="claude")

    assert chat_installed(page.url, "claude") == "installed"
    assert harness.plugin_calls[2:] == [
        ["plugin", "uninstall", "family-brief@family-brief"],
        ["plugin", "marketplace", "remove", "family-brief"],
        ["plugin", "uninstall", "parent-recap@kinlace"],
        ["plugin", "marketplace", "remove", "kinlace"],
        ["plugin", "marketplace", "add", "kinlace/parent-recap#stable"],
        ["plugin", "install", "parent-recap@kinlace", "--scope", "user"],
    ]
    assert install_record.entries("claude-plugin") == []  # the family's own, from the release zip
    assert install_record.entries("claude-marketplace") == []


@pytest.mark.macos
def test_a_plugin_install_that_fails_says_so(harness, page, ai):
    ai("claude")
    harness.claude_plugins = {"error": "not signed in"}  # type: ignore[assignment]

    welcome(page.url, ai="claude")

    assert chat_installed(page.url, "claude") == "install-failed"
    assert install_record.entries("claude-plugin") == []


@pytest.mark.macos
def test_without_claude_code_the_plugin_is_not_installed(harness, page, ai):
    welcome(page.url, ai="claude")

    assert chat_installed(page.url, "claude") == "not-installed"
    assert harness.plugin_calls == []


@pytest.mark.macos
def test_picking_chatgpt_installs_the_codex_skills_from_the_install_line_s_copy(harness, page):
    install_record.add("plugin", str(ROOT))  # what install.sh recorded, run from the line's copy

    welcome(page.url, ai="codex")

    assert chat_installed(page.url, "codex") == "installed"
    skills = harness.home / ".agents" / "skills"
    assert sorted(p.name for p in skills.iterdir()) == ["parent-recap-manage", "parent-recap-setup"]
    setup = (skills / "parent-recap-setup" / "SKILL.md").read_text()
    assert "\nname: parent-recap-setup\n" in setup and "$parent-recap-manage" in setup
    assert f"PLUGIN (the plugin root folder) is `{ROOT}`" in setup
    assert install_record.entries("codex-skill") == [str(skills / "parent-recap-setup"),
                                                     str(skills / "parent-recap-manage")]
    assert harness.plugin_calls == []


@pytest.mark.macos
def test_without_the_install_line_s_copy_the_codex_skills_are_not_installed(harness, page):
    welcome(page.url, ai="codex")

    assert chat_installed(page.url, "codex") == "no-plugin-copy"
    assert not (harness.home / ".agents").exists()


# ── Continue in the chat


FAKE_CHAT_CLAUDE = """#!/bin/sh
printf '%s\\n' "$PWD" "$@" > "${0%/*}/claude-chat.ran"
"""


def continue_in_chat(url: str, ai_name: str) -> Response:
    return call(url, "api/chat", method="POST", body={"ai": ai_name})


@pytest.mark.macos
def test_continue_in_the_chat_opens_claude_code_with_the_setup_skill_in_terminal(harness, page,
                                                                                 ai):
    bin_dir = harness.home / "bin"
    ai("claude")
    (bin_dir / "claude").write_text(FAKE_CHAT_CLAUDE)

    r = continue_in_chat(page.url, "claude")

    assert r.status == 200 and r.json() == {"result": "opened"}
    [window] = harness.desktop.windows
    assert window.title == setup_server.CHAT_WINDOW
    # The window runs Claude Code at the setup skill, in the family's home folder.
    subprocess.Popen(window.argv, cwd=harness.home, env={**os.environ, **window.env},
                     stdout=subprocess.DEVNULL).wait(10)
    assert (bin_dir / "claude-chat.ran").read_text().splitlines() == \
        [str(harness.home), "/parent-recap:setup"]
    page.stop()
    assert setup_server.CHAT_WINDOW in harness.desktop.forgotten  # the window's folder goes


@pytest.mark.macos
def test_continue_in_the_chat_before_welcome_installs_the_plugin_first(harness, page, ai):
    ai("claude")

    assert continue_in_chat(page.url, "claude").json() == {"result": "opened"}

    assert ["plugin", "install", "parent-recap@kinlace", "--scope", "user"] in harness.plugin_calls
    assert call(page.url, "api/state").json()["chat"] == {"claude": "installed"}


@pytest.mark.macos
def test_continue_in_the_chat_tells_a_codex_family_what_to_type(harness, page, ai):
    ai("codex")
    install_record.add("plugin", str(ROOT))

    r = continue_in_chat(page.url, "codex")

    assert r.status == 200 and r.json() == {"result": "open-codex", "type": "$parent-recap-setup"}
    assert not harness.desktop.opened
    assert (harness.home / ".agents" / "skills" / "parent-recap-setup" / "SKILL.md").is_file()


@pytest.mark.macos
def test_continue_in_the_chat_without_claude_code_says_how_to_install_it(harness, page, ai):
    assert continue_in_chat(page.url, "claude").json() == {
        "result": "not-installed", "install": "curl -fsSL https://claude.ai/install.sh | bash"}
    assert not harness.desktop.opened


@pytest.mark.macos
def test_continue_in_the_chat_without_terminal_says_what_to_type_there(harness, page, ai):
    ai("claude")
    harness.desktop.opens = False

    assert continue_in_chat(page.url, "claude").json() == {
        "result": "no-terminal", "type": f"{harness.home / 'bin' / 'claude'} /parent-recap:setup"}


def test_continue_in_the_chat_takes_only_the_ai(page):
    # The gateway is no chat: a gateway Household has neither Claude Code nor Codex to open.
    for body in [{}, {"ai": "gemini"}, {"ai": "gateway"}, {"ai": "claude", "x": 1}, ["claude"]]:
        r = call(page.url, "api/chat", method="POST", body=body)
        assert r.status == 400 and r.json()["result"] == "invalid-answers", body


@pytest.mark.macos
def test_the_chat_reads_the_progress_and_answers_the_page_saved(harness, page, capsys,
                                                                monkeypatch):
    call(page.url, "api/language", method="POST", body={"language": "fi"})
    welcome(page.url, ai="codex", partner={"address": "partner@example.com", "language": "zh"})
    source(page.url, "wilma", "skip")
    shown = call(page.url, "api/state").json()["progress"]
    capsys.readouterr()

    monkeypatch.setattr(sys, "argv", ["family-brief", "-c", str(config_file(harness)), "setup",
                                      "save", "--read"])
    cli.main()

    read = json.loads(capsys.readouterr().out)
    assert read["progress"] == shown
    assert (read["progress"]["phase"], read["progress"]["source"]) == ("connect", "gmail")
    assert read["progress"]["partner"] == {"address": "partner@example.com", "language": "zh"}
    cfg = Config.load(config_file(harness))
    assert (cfg.summary_language, cfg.llm.backend, cfg.wilma.enabled) == ("fi", "codex", False)


def test_the_setup_skill_carries_on_from_the_page_s_step():
    skill = (Path(__file__).resolve().parents[3] / "skills" / "setup" / "SKILL.md").read_text()
    [section] = re.findall(r"\n## Coming from the setup page\n(.*?)\n## ", skill, re.S)

    for needed in ("Continue in the chat", "setup save --read", "`phase`", "`source`",
                   "summary_language", "partner", "whatsapp_chats"):
        assert needed in section, needed
    assert "Coming from the setup page" in skill.split("## Rules", 1)[1].split("\n## ", 1)[0]


def test_every_step_of_the_page_has_continue_in_the_chat():
    html = (PAGE_DIR / "index.html").read_text()
    script = (PAGE_DIR / "page.js").read_text()

    # Outside every step's own section, so it's under each of them, and shown once a language is.
    after = html.rsplit("</section>", 1)[1]
    assert 'id="chat-continue"' in after and 'data-text="chat.continue"' in after
    assert 'getElementById("chat").hidden = !page.chosen' in script
    assert '|| chatAI() === "gateway"' in script  # nothing to chat with on the gateway


def test_every_chat_result_is_explained_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        for name, results in setup_server.CHAT_RESULTS.items():
            for result in results:
                assert table.get(f"chat.{name}.{result}", "").strip(), (language, name, result)
