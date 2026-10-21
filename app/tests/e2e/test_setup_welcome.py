"""The setup page's Welcome step, driven with HTTP calls as the page makes them (ADR 0006).

The server runs in-process on a free port, inside the harness, as `setup_page.py` starts it, so the
config, the progress record, the secrets, the jobs, the wake and the desktop are the harness's.
Assertions are on what the page and the family see: the responses, the config and progress written,
and what was opened."""
from __future__ import annotations

import json
import re

import pytest
import yaml

from family_brief import setup_server
from family_brief.config import Config
from conftest import PILOT_FORM_FIELDS, PILOT_FORM_URL
from setup_page import PAGE_DIR, call, check_ai, config_file, inside, welcome
from setup_page import _listening, ai, clock, page  # noqa: F401


# ── Welcome


def test_each_welcome_choice_has_a_default(harness, page):
    harness.ship_pilot_form()
    call(page.url, "api/language", method="POST", body={"language": "zh"})

    assert call(page.url, "api/state").json()["welcome"] == {
        "ai": setup_server._ais()[0],  # Claude, or on Windows the gateway, for now
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
    assert cfg.feedback.household_label == "parent"  # the setup parent's, until changed on Check


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
def test_an_ai_check_that_cannot_run_says_so(harness, page, ai, monkeypatch):
    ai("claude")
    run = setup_server.subprocess.run

    def fails(cmd, *a, **k):
        if cmd[1:3] == ["auth", "status"]:
            raise setup_server.subprocess.TimeoutExpired(cmd, 20)
        return run(cmd, *a, **k)
    monkeypatch.setattr(setup_server.subprocess, "run", fails)

    assert check_ai(page.url, "claude").json() == {"result": "check-failed", "ai": "claude"}


def test_only_claude_or_chatgpt_is_checked(page):
    for body in [{"ai": "gemini"}, {}, {"ai": "claude", "x": 1}]:
        assert call(page.url, "api/ai", method="POST", body=body).status == 400, body


def test_every_ai_result_says_what_to_do_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        for name in setup_server.AIS:
            # The gateway has nothing to install or sign in to on the Mac: it's always ready.
            for result in ("ready",) if name == "gateway" else setup_server.AI_RESULTS:
                assert table.get(f"ai.{name}.{result}", "").strip(), (language, name, result)


def test_welcome_says_which_sources_come_next():
    text = json.loads((PAGE_DIR / "text.json").read_text())
    used = (PAGE_DIR / "index.html").read_text()

    for language, table in text.items():
        for source in ("Wilma", "Gmail", "WhatsApp", "MyClub"):
            assert source in table["welcome.sources"], (language, source)
    assert 'data-text="welcome.sources"' in inside(used, "welcome")


# What Welcome's "Have these ready" list says in each language (#298), in the words the steps and
# Wilma's own sign-in page use: the Wilma login and Wilma's link for a forgotten password, one
# MyClub link per Kid and the buttons that make it, 2-Step Verification for the App Password, and
# the phone that signs in WhatsApp for Mac and may confirm the App Password. The last line says
# WhatsApp and MyClub can wait.
READY_WORDS = {
    "en": {"wilma": ("username", "password", '"Forgot your password?"', "sign-in page"),
           "gmail": ("2-Step Verification", "App Password"),
           "myclub": ("each Kid", "Calendar subscription (Tilaa kalenteri)",
                      "Selected members (Valitut jäsenet)", "only that Kid", "copy the link"),
           "phone.why": ("WhatsApp for Mac", "installed", "QR code", "confirm", "App Password"),
           "later": ("WhatsApp and MyClub", "until later")},
    "fi": {"wilma": ("käyttäjätunnus", "salasana", "”Unohditko salasanasi?”", "kirjautumissivu"),
           "gmail": ("2-vaiheinen vahvistus", "sovellussalasana"),
           "myclub": ("jokaiselle lapselle", "Tilaa kalenteri", "Valitut jäsenet",
                      "vain tämä lapsi", "kopioi linkki"),
           "phone.why": ("WhatsAppin Mac-sovellu", "asennettu", "QR-koodi", "vahvistus",
                         "sovellussalasana"),
           "later": ("WhatsApp ja MyClub", "myöhemmäksi")},
    "zh": {"wilma": ("用户名", "密码", "“Forgot your password?”", "“Unohditko salasanasi?”", "登录页"),
           "gmail": ("两步验证", "应用专用密码"),
           "myclub": ("每个孩子", "“订阅日历”（Tilaa kalenteri）", "“选定成员”（Valitut jäsenet）",
                      "只勾这一个孩子", "复制链接"),
           "phone.why": ("WhatsApp Mac 版", "装好", "二维码", "确认", "应用专用密码"),
           "later": ("WhatsApp 和 MyClub", "以后再连")},
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
        # Said once: the list says what can wait, and the line above it no longer does.
        assert items["later"][-1] not in text[language]["welcome.sources"], language
    # The list replaces Welcome's old Wilma sentence rather than adding a second one.
    assert all("welcome.wilma" not in table for table in text.values())
    assert "welcome.wilma" not in page_html
