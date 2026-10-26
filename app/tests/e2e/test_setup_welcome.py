"""The setup page's Welcome step, driven with HTTP calls as the page makes them (ADR 0006).

The server runs in-process on a free port, inside the harness, as `setup_page.py` starts it, so the
config, the progress record, the secrets, the jobs, the wake and the desktop are the harness's.
Assertions are on what the page and the family see: the responses, the config and progress written,
and what was opened."""
from __future__ import annotations

import json
import re
import subprocess

import pytest
import yaml

from family_brief import setup_server
from family_brief.config import Config
from conftest import PILOT_FORM_FIELDS, PILOT_FORM_URL
from setup_page import PAGE_DIR, call, check_ai, config_file, inside, welcome
from setup_page import ai, clock, page  # noqa: F401


# ── Welcome


def test_each_welcome_choice_has_a_default(harness, page):
    harness.ship_pilot_form()
    call(page.url, "api/language", method="POST", body={"language": "zh"})

    state = call(page.url, "api/state").json()
    assert state["welcome"] == {
        "ai": state["ais"][0],  # Claude, or on Windows the gateway, for now
        "partner": {"add": True, "address": "", "language": "zh"},
        "feedback": True,
    }


@pytest.mark.macos
def test_the_welcome_answers_are_saved(harness, page):
    harness.ship_pilot_form()
    call(page.url, "api/language", method="POST", body={"language": "fi"})

    r = welcome(page.url, ai="codex", partner={"address": "partner@example.com", "language": "zh"},
                feedback=False)

    assert r.status == 200 and r.json()["result"] == "saved"
    cfg = Config.load(config_file(harness))
    assert cfg.llm.backend == "codex"
    assert cfg.feedback.enabled is False
    progress = call(page.url, "api/state").json()["progress"]
    assert progress["phase"] == "connect" and progress["source"] == "wilma"
    assert call(page.url, "api/state").json()["welcome"] == {
        "ai": "codex",
        "partner": {"add": True, "address": "partner@example.com", "language": "zh"},
        "feedback": False,
    }


def test_a_pilot_household_s_opt_in_writes_the_whole_feedback_section(harness, page):
    harness.ship_pilot_form()
    config_file(harness).parent.mkdir(parents=True, exist_ok=True)
    config_file(harness).write_text(yaml.safe_dump(harness.config, allow_unicode=True))

    assert welcome(page.url, feedback=True).status == 200

    cfg = Config.load(config_file(harness))
    assert cfg.feedback.active()
    assert cfg.feedback.prefill_base_url == PILOT_FORM_URL
    assert cfg.feedback.fields.model_dump() == PILOT_FORM_FIELDS
    assert re.fullmatch(r"household-[0-9a-f]{8}", cfg.feedback.household_label)  # random (#493)


def test_without_a_shipped_pilot_form_welcome_doesnt_ask_about_feedback(harness, page):
    assert call(page.url, "api/state").json()["welcome"]["feedback"] is None
    assert welcome(page.url, feedback=True).status == 400  # nothing to opt in to
    assert not config_file(harness).exists()
    assert welcome(page.url, feedback=False).status == 200

    assert "feedback" not in yaml.safe_load(config_file(harness).read_text())


def test_the_partner_comes_after_the_parent_once_the_parent_s_gmail_is_known(harness, page):
    config_file(harness).parent.mkdir(parents=True, exist_ok=True)
    config_file(harness).write_text(yaml.safe_dump(harness.config, allow_unicode=True))

    welcome(page.url, partner={"address": "partner@example.com", "language": "fi"})

    to = Config.load(config_file(harness)).email.to
    assert [(r.address, r.language) for r in to] == \
        [("parent@example.com", None), ("partner@example.com", "fi")]


def test_only_me_leaves_the_parent_as_the_only_recipient(harness, page):
    config_file(harness).parent.mkdir(parents=True, exist_ok=True)
    config_file(harness).write_text(yaml.safe_dump(harness.config, allow_unicode=True))

    assert welcome(page.url, partner=None).status == 200

    assert [r.address for r in Config.load(config_file(harness)).email.to] == ["parent@example.com"]
    assert call(page.url, "api/state").json()["welcome"]["partner"]["add"] is False


def test_before_the_parent_s_gmail_is_known_no_recipient_is_written(harness, page):
    welcome(page.url, partner={"address": "partner@example.com", "language": "fi"})

    assert Config.load(config_file(harness)).email.to == []  # the partner isn't the first one


def test_the_partner_s_email_and_language_are_asked_only_when_a_partner_is_added(harness, page):
    for partner in [{"address": "", "language": "fi"}, {"address": "not an address", "language": "fi"},
                    {"address": "partner@example.com"}, {"address": "partner@example.com",
                                                         "language": "sv"}]:
        r = welcome(page.url, partner=partner)
        assert r.status == 400 and r.json()["result"] == "invalid-answers", partner
    for body in [{"ai": "gemini", "partner": None, "feedback": True},
                 {"ai": "claude", "partner": None, "feedback": "yes"},
                 {"ai": "claude", "feedback": True}]:
        r = call(page.url, "api/welcome", method="POST", body=body)
        assert r.status == 400, body
    assert not config_file(harness).exists()

    assert welcome(page.url, partner=None).status == 200  # only me: nothing more to ask


@pytest.mark.macos
def test_a_missing_claude_code_gets_its_native_installer_and_is_checked_again(harness, page, ai):
    r = check_ai(page.url, "claude")

    assert r.status == 200
    assert r.json() == {"result": "not-installed", "ai": "claude",
                        "install": "curl -fsSL https://claude.ai/install.sh | bash"}

    ai("claude", harness.home / ".local" / "bin")  # the native installer's folder, not on PATH

    assert check_ai(page.url, "claude").json() == {"result": "ready", "ai": "claude"}
    assert ["auth", "status"] == harness.commands[-1][1:3]


@pytest.mark.macos
def test_the_install_box_shows_only_with_an_install_line_in_it(harness, page, ai):
    html = (PAGE_DIR / "index.html").read_text()
    css = (PAGE_DIR / "page.css").read_text()

    box = re.search(r'<div id="ai-install"[^>]*>.*?</div>', html, re.S).group(0)
    assert " hidden" in box.split(">", 1)[0] and 'id="ai-copy"' in box and 'id="ai-install-line"' in box
    # `hidden` wins over the box's own `display: flex`, here and in every other step.
    assert re.search(r"^\[hidden\]\s*\{\s*display:\s*none\s*!important;\s*\}", css, re.M)

    ai("claude")
    assert "install" not in check_ai(page.url, "claude").json()  # ready: nothing to install


@pytest.mark.macos
def test_a_signed_out_claude_code_is_caught_and_checked_again(harness, page, ai):
    ai("claude")
    harness.signed_in["claude"] = False

    assert check_ai(page.url, "claude").json() == {"result": "signed-out", "ai": "claude"}

    harness.signed_in["claude"] = True
    assert check_ai(page.url, "claude").json() == {"result": "ready", "ai": "claude"}
    assert not harness.model_calls  # checked with its own status, not a call to the model


@pytest.mark.macos
def test_a_missing_or_signed_out_codex_is_caught_and_checked_again(harness, page, ai):
    assert check_ai(page.url, "codex").json() == {"result": "not-installed", "ai": "codex"}

    ai("codex")
    harness.signed_in["codex"] = False
    assert check_ai(page.url, "codex").json() == {"result": "signed-out", "ai": "codex"}
    assert ["login", "status"] == harness.commands[-1][1:3]

    harness.signed_in["codex"] = True
    assert check_ai(page.url, "codex").json() == {"result": "ready", "ai": "codex"}


@pytest.mark.macos
@pytest.mark.parametrize("name", ["claude", "codex"])
def test_an_ai_only_the_login_shell_finds_is_ready(harness, page, ai, login_shell, name):
    folder = harness.home / "tools" / "bin"  # on the family's shell's PATH, not the page's
    ai(name, folder)
    login_shell(folder)

    assert check_ai(page.url, name).json() == {"result": "ready", "ai": name}
    assert harness.commands[-1][0] == str(folder / name)


@pytest.mark.macos
@pytest.mark.parametrize("error, said", [
    (lambda cmd: subprocess.TimeoutExpired(cmd, 20),
     "claude auth status didn't finish within 20 seconds."),
    (lambda cmd: PermissionError(13, "Permission denied"),
     "The check stopped: PermissionError: [Errno 13] Permission denied"),
], ids=["timeout", "cannot-run"])
def test_an_ai_check_that_cannot_run_says_so_and_why(harness, page, ai, monkeypatch, caplog,
                                                     error, said):
    ai("claude")
    run = subprocess.run

    def fails(cmd, *a, **k):
        if cmd[1:3] == ["auth", "status"]:
            raise error(cmd)
        return run(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", fails)

    assert check_ai(page.url, "claude").json() == \
        {"result": "check-failed", "reason": said, "ai": "claude"}
    assert said in caplog.text


def test_only_claude_or_chatgpt_is_checked(page):
    for body in [{"ai": "gemini"}, {}, {"ai": "claude", "x": 1}]:
        assert call(page.url, "api/ai", method="POST", body=body).status == 400, body


def test_every_ai_result_says_what_to_do_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        for name in setup_server.context.AIS:
            # The gateway has nothing to install or sign in to on the Mac: it's always ready.
            for result in ("ready",) if name == "gateway" else setup_server.welcome.AI_RESULTS:
                assert table.get(f"ai.{name}.{result}", "").strip(), (language, name, result)


def test_welcome_names_each_ai_s_plan_beside_it_and_nothing_comes_next():
    # #491: each AI says the plan it needs on its own line, and "What comes next" is gone.
    text = json.loads((PAGE_DIR / "text.json").read_text())
    welcome = inside((PAGE_DIR / "index.html").read_text(), "welcome")

    for name in setup_server.context.AIS:
        assert re.search(rf'<span data-text="welcome.ai.{name}"></span> '
                         rf'<span class="plan" data-text="welcome.ai.{name}.plan"></span>', welcome)
    assert "welcome.next" not in welcome and "welcome.sources" not in welcome
    for language, table in text.items():
        assert "Pro" in table["welcome.ai.claude.plan"] and "Max" in table["welcome.ai.claude.plan"]
        assert "welcome.ai.plan" not in table and "welcome.next" not in table, language
    # What pilot feedback sends is folded under the question.
    assert re.search(r'<details class="more">\s*<summary data-text="welcome.feedback.more"></summary>'
                     r'\s*<p data-text="welcome.feedback.note"></p>', welcome)


# What Welcome's "Have these ready" list says in each language (#298), one line each (#491): the
# Wilma login, 2-Step Verification for the App Password, one MyClub link per Kid, and the phone
# that signs in WhatsApp for Mac and may confirm it's you to Google. The last line says the rest can
# wait.
READY_WORDS = {
    "en": {"wilma": ("username", "password"),
           "gmail": ("2-Step Verification",),
           "myclub": ("calendar link", "each Kid"),
           "phone.why": ("WhatsApp for Mac", "Google"),
           "later": ("until later",)},
    "fi": {"wilma": ("käyttäjätunnu", "salasana"),
           "gmail": ("2-vaiheinen vahvistus",),
           "myclub": ("kalenterilinkki", "lapselle"),
           "phone.why": ("WhatsAppin Mac-sovellu", "Google"),
           "later": ("myöhemmäksi",)},
    "zh": {"wilma": ("用户名", "密码"),
           "gmail": ("两步验证",),
           "myclub": ("日历链接", "每个孩子"),
           "phone.why": ("WhatsApp Mac 版", "Google"),
           "later": ("以后",)},
}


def test_welcome_has_a_folded_list_of_what_to_have_ready():
    # A pilot parent asked for calm pages that don't show everything at once: it opens on a click.
    page_html = (PAGE_DIR / "index.html").read_text()

    folded = re.search(r'<details\b[^>]*\bid="welcome-ready"[^>]*>', inside(page_html, "welcome"))
    assert folded and not re.search(r"\bopen\b", folded.group(0))
    ready = inside(page_html, "welcome-ready")
    assert '<summary data-text="welcome.ready"></summary>' in ready
    # Each Source by the name the Source list gives it, then the phone, each with what it needs.
    items = re.findall(r'<dt data-text="([\w.]+)"></dt>\s*<dd data-text="([\w.]+)"></dd>', ready)
    assert items == [("source.wilma", "welcome.ready.wilma"),
                     ("source.gmail", "welcome.ready.gmail"),
                     ("source.myclub", "welcome.ready.myclub"),
                     ("welcome.ready.phone", "welcome.ready.phone.why")]
    assert 'data-text="welcome.ready.later"' in ready


def test_the_ready_list_names_what_setup_needs_in_each_language():
    page_html = (PAGE_DIR / "index.html").read_text()
    text = json.loads((PAGE_DIR / "text.json").read_text())

    assert set(READY_WORDS) == set(text)
    for language, items in READY_WORDS.items():
        for item, words in items.items():
            said = text[language][f"welcome.ready.{item}"]
            for word in words:
                assert word.lower() in said.lower(), (language, item, word)
    # The list replaces Welcome's old Wilma sentence rather than adding a second one.
    assert all("welcome.wilma" not in table for table in text.values())
    assert "welcome.wilma" not in page_html
