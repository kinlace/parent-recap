"""The SchoolSoft capture tool (tools/schoolsoft-capture, #876) against a fake SchoolSoft
(fake_schoolsoft.py): the guardian's sign-in, what it reads and writes, the evening check's
refreshes, and that nothing it sends is a write and no token is ever shown or stored outside
the Keychain. The Keychain and launchd are in-memory fakes; their adapters are tested in
adapters/test_schoolsoft_capture_mac.py."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("fcntl")  # the tool runs on a Mac
from fake_schoolsoft import (EMAILS, LINKS, NAMES, PERSONNUMMER, PHONES, SCHOOL, FakeAgent,  # noqa: E402
                             FakeKeychain, FakeSchoolSoft, tool)

NOW = datetime(2026, 11, 10, 20, 0)


@pytest.fixture
def schoolsoft():
    fake = FakeSchoolSoft()
    fake.start()
    yield fake
    fake.stop()


class Guardian:
    """The guardian at the Terminal: their answers, and every line they see."""

    def __init__(self, schoolsoft: FakeSchoolSoft, folder: Path, answers: list[str]):
        self.answers = list(answers)
        self.lines: list[str] = []
        self.prompts: list[str] = []
        self.opened: list[Path] = []
        self.now = NOW
        self.ctx = tool.Context(folder=folder, base=schoolsoft.base, language="sv", keychain=FakeKeychain(),
                                agent=FakeAgent(), browser=schoolsoft.browser, show_file=self.opened.append,
                                ask=self.ask, say=self.lines.append, now=lambda: self.now, sign_in_seconds=10)

    def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0)

    def run(self, *argv: str) -> int:
        return tool.main(list(argv), self.ctx)

    def evening(self, days: int = 1) -> int:
        """The launchd job, `days` later, as a new process: only the Keychain and the files carry over."""
        self.now += timedelta(days=days)
        self.ctx.hidden = tool.Hidden()
        self.ctx.http = tool.Http(self.ctx.hidden, self.ctx.base)
        return self.run("refresh")

    def everything_shown_and_stored(self) -> str:
        files = "\n".join(p.read_text(encoding="utf-8") for p in self.ctx.folder.rglob("*") if p.is_file())
        return "\n".join([*self.lines, *self.prompts, files])

    def log(self) -> list[str]:
        return (self.ctx.folder / tool.LOG).read_text(encoding="utf-8").splitlines()


@pytest.fixture
def guardian(schoolsoft, tmp_path):
    return lambda *answers: Guardian(schoolsoft, tmp_path / "SchoolSoftCapture", answers)


def output(g: Guardian) -> dict:
    return json.loads((g.ctx.folder / tool.OUTPUT).read_text(encoding="utf-8"))


def test_a_capture_signs_in_reads_each_child_and_opens_one_redacted_file(guardian, schoolsoft):
    g = guardian("", "n")
    assert g.run("capture") == 0
    out = output(g)
    assert out["school"] == SCHOOL
    assert out["sign_in"]["callback_arrived"] and out["sign_in"]["refresh_token_given"]
    assert out["sign_in"]["access_token_claims"] == {
        "user_type": "PARENT", "login_method": "SAML", "client_id": "vApp",
        "aud": "https://schoolsoft.se/eva-backend", "lifetime_seconds": 900}
    assert out["sign_in"]["login_methods"] == [{"name": "SAML", "value": 3}]
    answers = {(a["what"], a.get("child")): a for a in out["answers"]}
    for kid in ("Kid A", "Kid B"):
        for what in ("news", "next calendar event", "lessons, this week", "lessons, next week",
                     "lessons agenda, two weeks", "school events agenda, two weeks", "assignments, this week",
                     "one assignment", "plannings", "activity log"):
            assert answers[what, kid]["status"] == 200, (what, kid)
    assert answers["news", "Kid A"]["count"] == 3
    assert answers["news", "Kid A"]["fields"][0]["creDate"] == "date and time"
    assert answers["news", "Kid A"]["fields"][0]["toDate"] == "date | null"
    assert len(answers["news", "Kid A"]["samples"]) == tool.SAMPLES
    assert answers["message inbox", None]["count"] == 2  # once: both children are at one school
    assert answers["one message", None]["status"] == 200
    assert g.opened == [g.ctx.folder / tool.OUTPUT]
    assert schoolsoft.browsers_done() == [200]
    assert any("Extern inloggning" in line for line in g.lines)  # in Swedish, the Mac's language
    assert any("~/SchoolSoftCapture/remove" in line for line in g.lines)


def test_the_file_names_no_one_and_has_no_number_address_or_link(guardian):
    g = guardian("", "n")
    g.run("capture")
    text = (g.ctx.folder / tool.OUTPUT).read_text(encoding="utf-8")
    samples = json.dumps([a.get("samples") for a in output(g)["answers"]], ensure_ascii=False)
    for name in NAMES:
        assert not re.search(rf"(?<![A-Za-zåäöÅÄÖ]){name}", samples), name
    for secret in [*PERSONNUMMER, *PHONES, *EMAILS, *LINKS, "Skansen", "astlin15"]:
        assert secret not in text, secret
    assert "⟦N" in samples and "⟦P1⟧" in samples and "⟦E1⟧" in samples and "⟦L1⟧" in samples
    assert "⟦S1⟧" in samples and ":s grupp" in samples  # Astrids grupp


def test_no_token_code_or_cookie_is_ever_shown_or_written(guardian, schoolsoft):
    g = guardian("", "j")
    g.run("capture")
    g.evening()
    g.evening()
    seen = g.everything_shown_and_stored()
    assert schoolsoft.issued
    for value in schoolsoft.issued:
        assert value not in seen


def test_every_request_is_a_listed_read(guardian, schoolsoft):
    g = guardian("", "j")
    g.run("capture")
    g.evening()
    templates = [(e.method, re.compile(f"/{SCHOOL}" + re.sub(r"\\\{\w+\\\}", r"[0-9]+", re.escape(e.path)) + "$"))
                 for e in tool.ENDPOINTS.values()]
    assert schoolsoft.requests
    for r in schoolsoft.requests:
        assert any(method == r.method and path.match(r.path) for method, path in templates), (r.method, r.path)
        assert r.method == "GET" or r.path.endswith(("/rest-api/login/token", "/rest/blogpost/getbyloggedinuser"))
    assert {r.method for r in schoolsoft.requests} == {"GET", "POST"}


def test_only_items_already_read_are_opened(guardian, schoolsoft):
    g = guardian("", "n")
    g.run("capture")
    opened = [r.path for r in schoolsoft.requests if re.search(r"/messages/\d+$|/assignments/\d+/view$", r.path)]
    assert opened and all(p.endswith(("/messages/9002", "/assignments/62/view")) for p in opened)
    assert not any("/news/5" in r.path for r in schoolsoft.requests)  # news is only listed


def test_each_child_s_web_session_is_minted_for_that_child(guardian, schoolsoft):
    g = guardian("", "n")
    g.run("capture")
    minted = [r.headers["childInFocus"] for r in schoolsoft.requests if r.path.endswith("/eva-apps/auth/login/parent")]
    assert minted == ["1201", "1202"]


def test_declined_the_evening_check_stores_nothing(guardian):
    g = guardian("", "nej")
    g.run("capture")
    assert g.ctx.keychain.token is None and not g.ctx.agent.installed
    assert not (g.ctx.folder / tool.STATE).exists()
    assert any("sparades inte" in line for line in g.lines)


def test_the_evening_check_renews_the_sign_in_and_saves_each_new_token_at_once(guardian, schoolsoft):
    g = guardian("", "ja")
    g.run("capture")
    assert g.ctx.agent.installed
    first = g.ctx.keychain.token
    assert first in schoolsoft.refresh_tokens
    assert g.evening() == 0
    second = g.ctx.keychain.token
    assert second != first and second in schoolsoft.refresh_tokens and first in schoolsoft.used_refresh_tokens
    assert g.evening(2) == 0
    assert g.ctx.keychain.token in schoolsoft.refresh_tokens
    log = g.log()
    assert log[0] == "2026-11-10  signed in, 14 evening checks start"
    assert log[1] == "2026-11-11  worked (token age 24.0 h, day 1)"
    assert log[2] == "2026-11-13  worked (token age 48.0 h, day 3)"


def test_a_sign_in_that_stops_working_says_on_which_day_and_cleans_up(guardian, schoolsoft):
    g = guardian("", "y")
    g.run("capture")
    schoolsoft.refreshes_left = 2
    g.evening(), g.evening()
    assert g.evening() == 0
    assert g.log()[-2:] == ["2026-11-13  failed: status 401: Sessionen har gått ut (token age 24.0 h, day 3)",
                            "2026-11-13  RESULT: the sign-in worked until day 2 and failed on day 3"]
    assert g.ctx.keychain.token is None and not g.ctx.agent.installed
    assert not (g.ctx.folder / tool.STATE).exists()
    assert (g.ctx.folder / tool.OUTPUT).exists()  # the guardian may not have sent it yet


def test_after_14_evenings_the_checks_are_done(guardian):
    g = guardian("", "y")
    g.run("capture")
    for _ in range(14):
        assert g.evening() == 0
    assert g.log()[-1] == "2026-11-24  RESULT: the sign-in still worked on day 14; the checks are done"
    assert g.ctx.keychain.token is None and not g.ctx.agent.installed


def test_two_checks_never_refresh_at_once(guardian, schoolsoft):
    g = guardian("", "y")
    g.run("capture")
    before = len(schoolsoft.requests)
    with tool.refresh_lock(g.ctx.folder) as got:
        assert got
        assert g.evening() == 0
    assert len(schoolsoft.requests) == before
    assert g.log()[-1] == "2026-11-11  skipped: another check was running"


def test_a_new_token_the_keychain_refuses_ends_the_check_and_says_so(guardian):
    g = guardian("", "y")
    g.run("capture")
    g.ctx.keychain.refuse = True
    assert g.evening() == 1
    assert g.log()[-2].startswith("2026-11-11  worked, but the new sign-in couldn't be saved")
    assert "the check stopped there, not SchoolSoft" in g.log()[-1]
    assert not g.ctx.agent.installed


def test_schoolsoft_out_of_reach_keeps_the_check_for_the_next_evening(guardian, schoolsoft):
    g = guardian("", "y")
    g.run("capture")
    token = g.ctx.keychain.token
    schoolsoft.stop()
    assert g.evening() == 1
    assert g.log()[-1].startswith("2026-11-11  not checked: SchoolSoft couldn't be reached")
    assert g.ctx.keychain.token == token and g.ctx.agent.installed
    schoolsoft.start()  # for the fixture's stop


def test_a_school_schoolsoft_doesnt_know_stops_before_the_sign_in(guardian, schoolsoft):
    g = guardian()
    assert g.run("capture", "--school", "https://sms.schoolsoft.se/nosuchschool/") == 2
    assert [r.path for r in schoolsoft.requests] == ["/nosuchschool/rest-api/login/methods/"]
    assert any("SchoolSoft har ingen nosuchschool" in line for line in g.lines)


def test_no_sign_in_within_the_time_says_so(guardian, schoolsoft):
    g = guardian("")
    g.ctx.browser = lambda _address: None  # the guardian never signs in
    g.ctx.sign_in_seconds = 1
    assert g.run("capture", "--language", "en") == 1
    assert "No sign-in arrived within 10 minutes. Run the command again." in g.lines
    assert not (g.ctx.folder / tool.OUTPUT).exists()


def test_a_callback_from_another_sign_in_is_turned_away(guardian, schoolsoft):
    g = guardian("", "n")
    real = schoolsoft.browser

    def twice(address: str) -> None:
        real(re.sub(r"state=[0-9a-f]+", "state=forged", address))
        real(address, delay=0.5)
    g.ctx.browser = twice
    assert g.run("capture") == 0
    assert schoolsoft.browsers_done() == [200, 400]
