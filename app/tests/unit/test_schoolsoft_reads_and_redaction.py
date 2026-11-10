"""The SchoolSoft capture tool's pure parts (tools/schoolsoft-capture, #876): the list of what it
may send, which is all reads, the redaction of made-up samples, the fields' shapes, and its text
for the guardian in Swedish, English and Chinese."""
from __future__ import annotations

import ast
import re

import pytest

pytest.importorskip("fcntl")  # the tool runs on a Mac
from fake_schoolsoft import TOOL_DIR, tool  # noqa: E402

SOURCE = (TOOL_DIR / "schoolsoft_capture.py").read_text(encoding="utf-8")


def test_it_sends_only_to_schoolsoft():
    assert tool.HOST == "sms.schoolsoft.se" and tool.BASE == "https://sms.schoolsoft.se"
    # Besides BASE, only the callback's own address and the User-Agent's link to this repository.
    assert set(re.findall(r"https?://([^/\"'\s{}]+)", SOURCE)) == {"127.0.0.1:", "github.com"}
    assert "github.com" in tool.USER_AGENT and tool.Http(tool.Hidden()).base == tool.BASE


def test_every_request_it_can_make_is_a_get_or_a_listed_read_only_post():
    posts = {name for name, e in tool.ENDPOINTS.items() if e.method != "GET"}
    assert posts == set(tool.READ_ONLY_POSTS) == {"token_code", "token_refresh", "activity_log"}
    assert {e.method for e in tool.ENDPOINTS.values()} == {"GET", "POST"}


@pytest.mark.parametrize("name", sorted(tool.ENDPOINTS))
def test_no_request_is_one_that_changes_something(name):
    path = tool.ENDPOINTS[name].path.casefold()
    for word in ("absence", "notice", "confirm", "setnotice", "markread", "read", "send", "delete", "update",
                 "booking", "leave", "save", "profile", "header/parent"):
        assert word not in path, (name, word)


def test_a_request_outside_the_list_is_refused_before_anything_is_sent(monkeypatch):
    http = tool.Http(tool.Hidden(), "http://127.0.0.1:9")
    sent = []
    monkeypatch.setattr(http, "_send", lambda *a: sent.append(a))
    monkeypatch.setitem(tool.ENDPOINTS, "mark_read", tool.Endpoint("PUT", "/rest-api/parent/news/{id}/read"))
    with pytest.raises(ValueError, match="would change something"):
        http.call("mark_read", "academediagrundskolor", ids={"id": 1})
    with pytest.raises(KeyError):
        http.call("absence_notice", "academediagrundskolor")
    with pytest.raises(ValueError, match="takes"):
        http.call("news", "academediagrundskolor", ids={"userId": 1, "orgId": 2}, query={"studentId": 3, "x": 1})
    with pytest.raises(ValueError, match="address name"):
        http.call("parent", "../other")
    assert sent == []


def test_one_place_in_the_tool_builds_a_request_and_none_follows_a_redirect():
    calls = [n.func for n in ast.walk(ast.parse(SOURCE)) if isinstance(n, ast.Call)]
    names = [getattr(f, "attr", getattr(f, "id", "")) for f in calls]
    assert names.count("Request") == 1 and names.count("build_opener") == 1
    assert "urlopen" not in names
    assert "build_opener(_NoRedirects)" in SOURCE


def redacted(answers: list) -> str:
    r = tool.Redactor()
    for a in answers:
        r.learn(a)
    return str([r.redact(a) for a in answers])


def test_people_named_in_person_fields_are_placeholders_wherever_their_names_stand():
    out = redacted([
        {"sender": {"firstName": "Märta", "lastName": "Öberg"}, "subject": "Märtas utflykt",
         "message": "Hej! Märta Öberg och Öbergs barn kommer. /Ann-Sofie"},
        {"author": "Ann-Sofie Lindgren", "teacher": "ANSV", "recipientsNamesString": "Pelle Svensson, Kim Ek"},
        {"description": "Ring ANSV eller Kim i morgon. Pelle får gärna komma."},
    ])
    for name in ("Märta", "Öberg", "Ann-Sofie", "Lindgren", "ANSV", "Pelle", "Svensson", "Kim", "Ek"):
        assert not re.search(rf"(?<![\w-]){name}(?![\w-])", out), name
    assert ":s utflykt" in out and ":s barn" in out


def test_contact_details_links_and_personal_numbers_are_placeholders():
    out = redacted([{"description": "Ring 070-123 45 67, +46 8 123 456 78 eller 08-123 45 67. Mejla "
                                    "rektor@skolan.example.se. Läs https://example.se/a?b=1 eller www.skolan.se. "
                                    "Personnummer 20150412-1234, 150412-1234, 1504121234 och 150472-1234. "
                                    "<a href='/academediagrundskolor/jsp/x.jsp'>här</a>"}])
    for value in ("070-123", "+46", "08-123", "rektor@", "https://", "www.", "skolan.se", "0412", "academediagrundskolor"):
        assert value not in out, value
    assert all(token in out for token in ("⟦P1⟧", "⟦E1⟧", "⟦L1⟧", "⟦S1⟧", "⟦S4⟧", "⟦link⟧"))


def test_other_capitalised_words_inside_a_sentence_are_placeholders_and_the_first_word_stays():
    out = redacted([{"title": "Utflykt", "description": "Vi åker till Skansen med Maja. Packa matsäck! "
                                                         "<p>Välkomna</p> [b]Hej[/b] alla"}])
    assert "Utflykt" in out and "Vi åker" in out and "Packa matsäck" in out and "Välkomna" in out and "Hej" in out
    assert "Skansen" not in out and "Maja" not in out


def test_contact_fields_and_pictures_are_left_out_and_ids_renumbered():
    r = tool.Redactor()
    out = r.redact({"userId": 4711, "studentId": "1201", "dayId": 3, "guid": "abc-123", "picture": "x.jpg",
                    "email": "a@b.se", "address": {"street": "Storgatan 1"}, "mobile": "0701234567",
                    "socialSecurityNumber": "201504121234", "items": [{"orgId": 4711}]})
    assert out == {"userId": 1, "studentId": "2", "dayId": 3, "guid": "⟦guid⟧", "picture": "⟦picture⟧",
                   "email": "⟦email⟧", "address": "⟦address⟧", "mobile": "⟦mobile⟧",
                   "socialSecurityNumber": "⟦socialSecurityNumber⟧", "items": [{"orgId": 1}]}


def test_a_person_field_the_names_can_t_cover_is_left_out_whole():
    r = tool.Redactor()
    assert r.redact({"author": "lärare i 3B"}) == {"author": "⟦N⟧"}


def test_long_texts_and_lists_are_cut():
    r = tool.Redactor()
    out = r.redact({"description": "a" * 1000, "rows": list(range(10))})
    assert out["description"] == "a" * tool.TEXT_LIMIT + "… [400 more characters]"
    assert out["rows"] == [0, 1, 2]


def test_a_shape_merges_every_item_of_a_list():
    assert tool.shape([{"a": 1, "b": "2026-11-10"}, {"a": None, "c": "<p>x</p>", "b": "2026-11-10 08:00"}]) == [
        {"a": "null | number", "b": "date | date and time", "c": "html"}]
    assert tool.shape([]) == [] and tool.shape(True) == "boolean"


def test_the_access_token_s_claims_keep_no_subject():
    token = "e30." + tool.base64.urlsafe_b64encode(
        b'{"sub":"9f0c","user_type":"PARENT","iat":100,"exp":1000,"name":"Karin"}').decode().rstrip("=") + ".x"
    assert tool.jwt_claims(token) == {"user_type": "PARENT", "lifetime_seconds": 900}
    assert tool.jwt_claims("not a token") == {}


@pytest.mark.parametrize("typed,name", [
    ("", ""), ("academediagrundskolor", "academediagrundskolor"),
    ("https://sms.schoolsoft.se/Taby/jsp/Login.jsp", "taby"), ("sms.schoolsoft.se/nacka/", "nacka")])
def test_the_school_is_the_name_in_its_address(typed, name):
    assert tool.school_name(typed) == name


def test_the_secrets_of_a_run_are_scrubbed_from_any_text():
    hidden = tool.Hidden()
    hidden.add("rt.fake-refresh-token", "short", None)
    assert hidden.scrub("refreshToken=rt.fake-refresh-token short") == "refreshToken=[hidden] short"


def test_every_text_for_the_guardian_is_in_swedish_english_and_chinese():
    for key, versions in tool.TEXT.items():
        assert set(versions) == {"sv", "en", "zh"}, key
        assert len({re.findall(r"\{\w+\}", v).__len__() for v in versions.values()}) == 1, key
    assert {"j", "ja", "y", "yes", "是"} <= tool.YES
