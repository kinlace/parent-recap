"""The pure parts of Weekend Picks (weekend_pipeline): trimming the candidates, reading the model's
picks, and writing the email. The model call, the calendar and delivery are tested end to end in
test_weekend_picks.py."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime

import pytest

from family_brief.brief_text import TEXT, WEEKEND_TEXT
from family_brief.collectors.weekend_events import Candidate
from family_brief.weekend_pipeline import _format_body, _format_html, _ranked_picks, _trim, _Version

WEEKEND = (date(2026, 10, 3), date(2026, 10, 4))

# Naive times, which the email shows as they are on any machine.
PUPPETS = Candidate(ext_id="le-1", title="Puppet theatre", description="For kids 4-10",
                    start=datetime(2026, 10, 3, 10, 0), end=None, price_eur=None, is_free=True,
                    location_name="Sello library", locality="Espoo", address="", url="https://example.fi/le-1")
FLOORBALL = Candidate(ext_id="le-2", title="Floorball <try>", description="Ages 6-12",
                      start=datetime(2026, 10, 4, 13, 30), end=None, price_eur=5.5, is_free=False,
                      location_name="", locality="Espoo", address="", url="")
CONCERT = Candidate(ext_id="le-3", title="Concert", description="", start=datetime(2026, 10, 4, 19, 0),
                    end=None, price_eur=None, is_free=False, location_name="", locality="", address="", url="")
BY_ID = {c.ext_id: c for c in (PUPPETS, FLOORBALL, CONCERT)}


def version(language: str, picks: list[dict], note: str = "") -> _Version:
    return _Version(WEEKEND_TEXT[language], TEXT[language].weekdays, [], picks, note)


# ── The candidates sent to the model

def test_few_enough_candidates_all_go():
    assert _trim([PUPPETS, CONCERT], cap=2) == [PUPPETS, CONCERT]


def test_trim_favours_kid_events_young_audiences_and_free_ones():
    plain = replace(CONCERT, ext_id="plain", locality="Vantaa")
    young = replace(plain, ext_id="young", audience_min_age=7)
    free = replace(plain, ext_id="free", is_free=True)
    kids = replace(plain, ext_id="kids", keywords=["Lasten"])

    assert [c.ext_id for c in _trim([plain, free, young, kids], cap=3)] == ["kids", "young", "free"]


def test_trim_caps_each_locality_so_helsinki_cannot_fill_the_list():
    helsinki = [replace(CONCERT, ext_id=f"h{i}", locality="Helsinki", title="Lasten") for i in range(8)]
    espoo = [replace(CONCERT, ext_id=f"e{i}", locality="Espoo") for i in range(3)]
    nowhere = replace(CONCERT, ext_id="n", locality="")

    kept = _trim([*helsinki, *espoo, nowhere], cap=10)

    assert [c.ext_id for c in kept] == ["h0", "h1", "h2", "h3", "h4", "e0", "e1", "e2", "n"]


# ── The model's picks

@pytest.mark.parametrize("odd", [
    {"ext_id": "le-1", "rank": None},
    {"ext_id": "le-1"},
    {"ext_id": "le-1", "rank": "first"},
    {"ext_id": "le-1", "rank": "nan"},
    {"ext_id": "le-1", "rank": True},
    {"ext_id": "le-9", "rank": 1},
    {"ext_id": 1, "rank": 1},
    "le-1",
    ["le-1", 1],
    None,
], ids=["null rank", "missing rank", "rank that isn't a number", "NaN rank", "boolean rank", "invented event",
        "ext_id that isn't text", "a string", "a list", "null"])
def test_an_odd_pick_is_skipped_and_the_rest_kept(odd):
    good = {"ext_id": "le-2", "rank": 1, "why": "Try floorball"}

    assert _ranked_picks([odd, good], {"le-1", "le-2"}) == [good]


def test_picks_are_ordered_by_rank_whether_written_as_numbers_or_text():
    picks = [{"ext_id": "le-2", "rank": "2"}, {"ext_id": "le-3", "rank": 10}, {"ext_id": "le-1", "rank": 1.5}]

    assert [p["ext_id"] for p in _ranked_picks(picks, set(BY_ID))] == ["le-1", "le-2", "le-3"]


@pytest.mark.parametrize("picks", [None, {"ext_id": "le-1", "rank": 1}, "le-1"])
def test_picks_that_arent_a_list_are_none(picks):
    assert _ranked_picks(picks, set(BY_ID)) == []


# ── The email

PICKS = [{"ext_id": "le-1", "rank": 1, "why": " Free puppets "}, {"ext_id": "le-9", "rank": 2, "why": "Gone"},
         {"ext_id": "le-2", "rank": 3, "why": "Kid A <3 floorball"}, {"ext_id": "le-3", "why": ""}]


def test_the_plain_text_lists_each_pick_with_when_where_and_price():
    assert _format_body(BY_ID, WEEKEND, version("en", PICKS)) == "\n".join([
        "🎪 Weekend Picks (2026-10-03 ~ 2026-10-04)",
        WEEKEND_TEXT["en"].intro,
        "",
        "1. Puppet theatre",
        "   ⏰ Sat 10/03 10:00  📍 Sello library (Espoo)  💰 Free",
        "   💡 Free puppets",
        "   🔗 https://example.fi/le-1",
        "",
        "3. Floorball <try>",
        "   ⏰ Sun 10/04 13:30  📍 Espoo (Espoo)  💰 €5.5",
        "   💡 Kid A <3 floorball",
        "",
        "?. Concert",
        "   ⏰ Sun 10/04 19:00  📍 Place unknown  💰 Price on the event page",
        "",
    ])


def test_the_plain_text_is_in_the_versions_language_with_its_translation_note():
    body = _format_body(BY_ID, WEEKEND, version("fi", PICKS[:1], note="⚠️ Ei käännetty"))

    assert body.startswith("🎪 Viikonlopun vinkit (2026-10-03 ~ 2026-10-04)\n⚠️ Ei käännetty\n\n")
    assert "   ⏰ la 3.10. klo 10.00  📍 Sello library (Espoo)  💰 Maksuton" in body


def test_the_html_escapes_what_events_and_the_model_wrote():
    html = _format_html(BY_ID, WEEKEND, version("en", PICKS, note="<b>not translated</b>"))

    assert html.startswith("<html><body style='font-family:-apple-system,Helvetica,Arial;font-size:14px'>"
                           "<h2>🎪 Weekend Picks (2026-10-03 ~ 2026-10-04)</h2>"
                           "<p style='color:#a33'>&lt;b&gt;not translated&lt;/b&gt;</p>"
                           f"<p>{WEEKEND_TEXT['en'].intro_html}</p><ol>")
    assert ("<b><a href=\"https://example.fi/le-1\">Puppet theatre</a></b><br>"
            "<span style='color:#666'>⏰ Sat 10/03 10:00 · 📍 Sello library (Espoo) · 💰 Free</span>"
            "<br>💡 Free puppets</li>") in html
    assert "<b>Floorball &lt;try&gt;</b><br>" in html and "💡 Kid A &lt;3 floorball" in html
    assert "<b>Concert</b><br><span style='color:#666'>⏰ Sun 10/04 19:00 · 📍 Place unknown · " \
           "💰 Price on the event page</span></li>" in html
    assert "Gone" not in html
    assert html.endswith("</ol></body></html>")
