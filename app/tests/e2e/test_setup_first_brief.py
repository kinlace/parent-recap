"""The setup page's First Brief step (ADR 0006): the real Brief in the page, then sent to the setup
parent only.

The server runs in-process on a free port, inside the harness, as `setup_page.py` starts it, so the
config, the progress record, the secrets, the jobs, the wake and the desktop are the harness's.
Assertions are on what the page and the family see: the responses, the config and progress written,
and what was opened."""
from __future__ import annotations

import html
import json
import re
import threading
from typing import Any

from family_brief import feedback, run_lock, setup_save, setup_server, setup_status
from family_brief.config import Config
from family_brief.system import jobs
from conftest import msg
from nights import FEEDBACK, FORM, feedback_links
from setup_page import (APP_PASSWORD, PAGE_DIR, Response, at_the_check_step, call, config_file, connect_done,
                        more_progress, wait_for)
from setup_page import _listening, clock, page  # noqa: F401
from test_setup_whatsapp import mac  # noqa: F401


# ── First Brief: the real Brief in the page, then sent to the setup parent only


BRIEF_TEXT = "Bring the signed trip form to school on Tuesday"


def at_the_first_brief(harness, **config: Any) -> None:
    """Check confirmed, with three days of school mail for the Brief."""
    connect_done(harness, **config)
    more_progress(harness, phase="first-brief")
    harness.sources["gmail"] = [msg("gmail", "g1", "2026-09-27T09:00:00+03:00", BRIEF_TEXT,
                                    sender="teacher@edu.espoo.fi", subject="Trip")]
    harness.model_reply = {"per_kid": [], "calendar_events": [], "message_digest": BRIEF_TEXT}
    harness.secrets["gmail-imap-parent@example.com"] = APP_PASSWORD


def make_brief(url: str) -> Response:
    return call(url, "api/brief", method="POST", body={})


def brief_made(url: str) -> dict[str, Any]:
    out: dict[str, Any] = {}

    def made() -> bool:
        out.clear()
        out.update(call(url, "api/brief/check", method="POST", body={}).json())
        return out["result"] != "making"
    wait_for(made)
    return out


def send_it_to_me(url: str) -> Response:
    return call(url, "api/brief/send", method="POST", body={})


def test_the_preview_makes_the_real_brief_and_sends_nothing(harness, page, mac):
    at_the_first_brief(harness)

    r = make_brief(page.url)

    assert r.status == 200 and r.json()["result"] == "making"
    out = brief_made(page.url)
    assert out["result"] == "made"
    # The email's own HTML, as the evening Brief renders it.
    assert out["html"].startswith("<html><body") and BRIEF_TEXT in out["html"]
    assert "Parent Recap" in out["html"]
    assert harness.sent == [] and harness.imessages == []
    assert not harness.state_path.exists()  # nothing recorded: the evening Brief reads it again
    assert not list(harness.home.glob("ParentRecap/*.md"))
    # Through a bg job, so WhatsApp can be read, over the last three days.
    assert harness.jobs.detached[-1][2:] == ["run", "--preview", "--lookback-hours", "72"]
    assert harness.lookback_hours["gmail"] == [72]


def test_the_preview_is_shown_in_a_sandboxed_frame(harness, page, mac):
    at_the_first_brief(harness)
    assert call(page.url, "brief.html").status == 404  # nothing made yet

    make_brief(page.url)
    out = brief_made(page.url)

    r = call(page.url, "brief.html")
    assert r.status == 200 and r.headers["content-type"] == "text/html; charset=utf-8"
    csp = r.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "sandbox" in csp and "frame-ancestors 'self'" in csp
    assert "script-src" not in csp
    # The email's HTML, with its links kept from taking the frame anywhere.
    assert r.body.decode() == out["html"].replace(
        "<html>", '<html><head><base target="_blank"></head>', 1)
    assert "frame-src 'self'" in call(page.url).headers["content-security-policy"]
    page_html = (PAGE_DIR / "index.html").read_text()
    assert re.search(r'<iframe id="brief-frame"[^>]* sandbox[ >]', page_html)


def test_three_quiet_days_still_make_a_brief(harness, page, mac):
    at_the_first_brief(harness)
    harness.sources["gmail"] = []

    make_brief(page.url)
    out = brief_made(page.url)

    assert out["result"] == "made" and "Parent Recap" in out["html"]
    assert harness.model_calls == []
    assert harness.sent == []


def test_the_page_shows_progress_while_the_brief_is_made(harness, page, monkeypatch):
    at_the_first_brief(harness)
    go = threading.Event()
    lines = ['{"preview": "reading", "sources": ["gmail", "wilma", "whatsapp"]}',
             '{"preview": "reading", "source": "gmail"}',
             '{"preview": "reading", "source": "wilma"}']

    def job(cmd_args: list[str], timeout: int, *, config: str | None = None, echo: bool = True,
            output=None) -> tuple[int, str]:
        output("12:00:00 INFO family_brief | Running collectors\n" + "\n".join(lines) + "\n")
        assert go.wait(5)
        lines.extend(['{"preview": "writing"}',
                      json.dumps({"preview": "made", "subject": "Parent Recap · 2026-09-27",
                                  "text": "Brief", "html": "<html><body>Brief</body></html>",
                                  "feedback": None})])
        return 0, "\n".join(lines) + "\n"
    monkeypatch.setattr(jobs, "run_detached", job)

    make_brief(page.url)

    check = call(page.url, "api/brief/check", method="POST", body={}).json()
    assert check == {"result": "making", "sources": ["gmail", "wilma", "whatsapp"], "step": "wilma"}
    assert make_brief(page.url).json()["result"] == "making"  # not made twice at once
    go.set()
    assert brief_made(page.url)["result"] == "made"


def test_a_brief_that_cannot_be_made_says_so_and_can_be_made_again(harness, page, mac):
    at_the_first_brief(harness)
    harness.jobs.detached_fails = "Bootstrap failed: 5: Input/output error"

    make_brief(page.url)
    assert brief_made(page.url) == {"result": "make-failed"}  # without the error
    assert call(page.url, "brief.html").status == 404

    harness.jobs.detached_fails = None
    make_brief(page.url)
    assert brief_made(page.url)["result"] == "made"


def test_the_brief_is_made_only_once_check_is_confirmed(harness, page, mac):
    at_the_check_step(harness)

    r = make_brief(page.url)

    assert r.status == 400 and r.json()["result"] == "invalid-answers"
    assert harness.jobs.detached == []
    assert call(page.url, "api/brief/check", method="POST", body={}).json() == {"result": "no-brief"}
    for body in [{"x": 1}, []]:
        assert call(page.url, "api/brief", method="POST", body=body).status == 400


def test_send_it_to_me_reaches_only_the_setup_parent(harness, page, mac):
    harness.config["email"]["to"] = ["parent@example.com",
                                     {"address": "partner@example.com", "language": "fi"}]
    at_the_first_brief(harness)
    make_brief(page.url)
    made = brief_made(page.url)

    r = send_it_to_me(page.url)

    assert r.status == 200
    assert r.json()["result"] == "sent" and r.json()["to"] == "parent@example.com"
    [email] = harness.sent
    assert email.to == ["parent@example.com"]  # never the partner: theirs is the evening one
    assert email.html == made["html"] and BRIEF_TEXT in email.text
    assert email.subject == "Parent Recap · 2026-09-27"
    assert email.from_addr == "parent@example.com"
    # The outcome check counts it as the first Brief delivered.
    state = harness.state()
    assert list(state["brief_delivered_at"]) == ["parent@example.com"]
    assert not state["seen_message_ids"]  # the evening Brief still reads these three days
    assert setup_status._brief(Config.load(config_file(harness))).ok


def test_after_the_first_brief_setup_moves_on_to_finish(harness, page, mac):
    at_the_first_brief(harness)
    assert call(page.url, "api/brief/done", method="POST", body={}).status == 400  # not yet sent
    make_brief(page.url)
    brief_made(page.url)
    send_it_to_me(page.url)

    out = call(page.url, "api/brief/done", method="POST", body={}).json()

    assert out["result"] == "saved" and out["progress"]["phase"] == "finish"
    assert setup_save.read(config_file(harness))["progress"]["phase"] == "finish"


def test_nothing_is_sent_before_the_brief_is_made(harness, page, mac):
    at_the_first_brief(harness)

    r = send_it_to_me(page.url)

    assert r.status == 400 and r.json()["result"] == "invalid-answers"
    assert harness.sent == []
    make_brief(page.url)
    brief_made(page.url)
    assert call(page.url, "api/brief/send", method="POST",
                body={"to": "partner@example.com"}).status == 400
    assert harness.sent == []


def test_a_send_that_fails_says_so_and_can_be_tried_again(harness, page, mac):
    at_the_first_brief(harness)
    make_brief(page.url)
    brief_made(page.url)
    harness.email_error = OSError("SMTP connection refused for parent@example.com")

    r = send_it_to_me(page.url)

    assert r.json() == {"result": "send-failed"}  # without the error, which names the address
    assert not harness.state_path.exists()
    harness.email_error = None
    assert send_it_to_me(page.url).json()["result"] == "sent"


def test_it_is_not_sent_while_an_evening_run_is_going(harness, page, mac):
    at_the_first_brief(harness)
    make_brief(page.url)
    brief_made(page.url)

    with run_lock.exclusive(Config.load(config_file(harness))):
        assert send_it_to_me(page.url).json() == {"result": "busy"}

    assert harness.sent == []
    assert send_it_to_me(page.url).json()["result"] == "sent"


def test_a_pilot_household_can_say_something_is_wrong_through_the_feedback_form(harness, page,
                                                                                 mac):
    at_the_first_brief(harness, feedback=FEEDBACK)
    make_brief(page.url)

    out = brief_made(page.url)

    assert out["feedback"] is True
    assert FORM not in json.dumps({k: v for k, v in out.items() if k != "html"})
    r = call(page.url, "api/brief/feedback", method="POST", body={})
    assert r.json() == {"result": "opened"}
    # The Brief's own Digest link, pre-filled with tonight's Digest.
    [(_, answers, _)] = feedback_links(f'<a href="{html.escape(harness.desktop.opened[-1])}">x</a>')
    assert answers["verdict"] == feedback.DIGEST_WRONG and answers["item_text"] == BRIEF_TEXT
    assert answers["household"] == "Household 24ebb1"  # 王家's pseudonym, as in the evening's Brief


def test_only_a_pilot_household_has_the_feedback_button(harness, page, mac):
    at_the_first_brief(harness, feedback={**FEEDBACK, "enabled": False})
    make_brief(page.url)

    assert brief_made(page.url)["feedback"] is False
    r = call(page.url, "api/brief/feedback", method="POST", body={})
    assert r.status == 400 and harness.desktop.opened == []


def test_every_first_brief_result_is_explained_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        for result in setup_server.BRIEF_RESULTS:
            assert table.get(f"brief.{result}", "").strip(), (language, result)
        for result in setup_server.SEND_RESULTS:
            assert table.get(f"brief.send.{result}", "").strip(), (language, result)
