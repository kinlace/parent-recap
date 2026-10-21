"""The eval's deterministic scoring: a model summary checked against a case's expectations."""
from __future__ import annotations

from family_brief.eval.score import aggregate, is_clean, score_case

TZ = "Europe/Helsinki"


def summary(per_kid=(), events=(), verified=None):
    s = {"per_kid": list(per_kid), "calendar_events": list(events), "message_digest": ""}
    entries = sum(len(k.get("notices", [])) + len(k.get("action_items", [])) for k in per_kid)
    s["_citations"] = {"entries": entries, "unverified": entries - (verified if verified is not None else entries),
                       "legacy": 0}
    return s


def kid(name, notices=(), actions=()):
    return {"kid": name, "notices": [{"text": t, "refs": []} for t in notices],
            "action_items": [{"what": w, "by": by, "refs": []} for w, by in actions]}


def test_action_items_match_by_keyword_and_check_kid_and_due_date():
    expect = {"action_items": [
        {"kid": "Aino", "what": ["lupalappu", "同意书"], "by": "2026-10-06"},
        {"kid": "Eero", "what": ["eväät"], "by": "2026-10-08"},
    ]}
    got = summary([kid("Aino", actions=[("交 retki 同意书", "2026-10-06")]),
                   kid("Aino", actions=[("准备 Eväät", "2026-10-09")]),  # wrong kid and date
                   kid("全家", actions=[("买雨衣", "2026-10-07")])])     # not expected
    s = score_case(expect, got, TZ)
    assert s["actions"] == {"expected": 2, "predicted": 3, "matched": 2, "due_ok": 1, "kid_ok": 1}


def test_a_matching_item_for_the_right_kid_is_preferred_over_an_earlier_one():
    expect = {"action_items": [{"kid": "Eero", "what": ["eväät"], "by": "2026-10-08"},
                               {"kid": "Aino", "what": ["eväät"], "by": "2026-10-08"}]}
    got = summary([kid("Aino", actions=[("eväät", "2026-10-08")]),
                   kid("Eero", actions=[("eväät", "2026-10-08")])])
    assert score_case(expect, got, TZ)["actions"]["kid_ok"] == 2


def test_optional_entries_count_neither_as_misses_nor_as_extras():
    expect = {"action_items": [{"what": ["eväät"], "optional": True}]}
    assert score_case(expect, summary([kid("Aino", actions=[("eväät", "2026-10-08")])]), TZ)["actions"] \
        == {"expected": 0, "predicted": 0, "matched": 0, "due_ok": 0, "kid_ok": 0}
    assert score_case(expect, summary(), TZ)["actions"]["expected"] == 0


def test_an_optional_event_with_a_start_only_covers_an_event_on_that_start():
    expect = {"calendar_events": [{"title": ["syysloma"], "start": "2026-10-12", "optional": True}]}
    on_the_day = summary(events=[{"title": "Syysloma", "start": "2026-10-12"}])
    later = summary(events=[{"title": "Treenit jatkuvat syysloman jälkeen", "start": "2026-10-19T17:30:00"}])
    assert score_case(expect, on_the_day, TZ)["events"]["predicted"] == 0
    assert score_case(expect, later, TZ)["events"]["predicted"] == 1


def test_all_of_keyword_groups():
    expect = {"notices": {"must": [{"text": [["10:00", "10.00"], ["11:00", "11.00"]]}]}}
    one = summary([kid("Aino", notices=["比赛改到 11:00"])])
    both = summary([kid("Aino", notices=["日历上是 10:00，消息里是 11.00"])])
    assert score_case(expect, one, TZ)["notices"]["found"] == 0
    assert score_case(expect, both, TZ)["notices"]["found"] == 1


def test_required_notice_with_a_kid_must_sit_under_that_kid():
    expect = {"notices": {"must": [{"kid": "Eero", "text": ["peruttu", "取消"]}]}}
    assert score_case(expect, summary([kid("Aino", notices=["训练取消"])]), TZ)["notices"]["found"] == 0
    assert score_case(expect, summary([kid("Eero", notices=["训练取消"])]), TZ)["notices"]["found"] == 1


def test_forbidden_text_is_looked_for_where_the_rule_says():
    expect = {"notices": {"must_not": [{"text": ["冲突"], "where": "both"},
                                       {"text": ["再提醒"], "where": "action_items"}]}}
    got = summary([kid("Aino", notices=["（再提醒）不是待办"], actions=[("处理时间冲突", "2026-10-11")])])
    assert score_case(expect, got, TZ)["notices"] == {"required": 0, "found": 0, "forbidden": 2, "hits": 1}


def test_forbidden_text_in_the_digest_counts_where_the_rule_reaches_it():
    expect = {"notices": {"must_not": [{"text": ["没有新"], "where": "digest"},
                                       {"text": ["之前的 Brief"], "where": "anywhere"},
                                       {"text": ["24小时"]}]}}
    got = summary([kid("Aino", notices=["周四 Nuuksio 远足"])])
    got["message_digest"] = "**Eero**\n- 本次没有新的待办\n**全家**\n- 都已在之前的 Brief 中处理\n- 过去24小时没有新消息"
    assert score_case(expect, got, TZ)["notices"] == {"required": 0, "found": 0, "forbidden": 3, "hits": 2}


def test_a_notice_rule_for_some_languages_is_checked_only_on_briefs_in_them():
    expect = {"notices": {"must": [{"text": [["家长会", "parents' evening"], ["vanhempainil"]],
                                    "where": "both", "languages": ["en", "zh"]}],
                          "must_not": [{"text": ["eväät"], "where": "both", "languages": ["en", "zh"]}]}}
    untranslated = summary([kid("Aino", actions=[("带 eväät", "2026-10-16"), ("报名家长会", "2026-10-15")])])
    translated = summary([kid("Aino", actions=[("带午餐", "2026-10-16"),
                                               ("报名家长会（vanhempainilta）", "2026-10-15")])])
    assert score_case(expect, untranslated, TZ, language="zh")["notices"] == \
        {"required": 1, "found": 0, "forbidden": 1, "hits": 1}
    assert score_case(expect, translated, TZ, language="zh")["notices"] == \
        {"required": 1, "found": 1, "forbidden": 1, "hits": 0}
    assert score_case(expect, untranslated, TZ, language="fi")["notices"] == \
        {"required": 0, "found": 0, "forbidden": 0, "hits": 0}


def test_event_start_is_compared_as_local_wall_clock():
    expect = {"calendar_events": [{"title": ["turnaus", "锦标赛"], "start": "2026-10-25T10:00"},
                                  {"title": ["piano", "钢琴"], "start": "2026-10-27T17:00"}]}
    got = summary(events=[
        {"title": "Turnaus", "start": "2026-10-25T10:00:00+03:00"},  # summer offset on a winter day
        {"title": "钢琴课", "start": "2026-10-27T15:00:00Z"},         # 17:00 in Helsinki
        {"title": "Extra", "start": "2026-10-28T09:00:00"},
    ])
    assert score_case(expect, got, TZ)["events"] == {"expected": 2, "predicted": 3, "matched": 2, "start_ok": 2}


def test_citations_and_a_missing_summary():
    got = summary([kid("Aino", notices=["a", "b"], actions=[("c", "2026-10-06")])], verified=2)
    assert score_case({}, got, TZ)["citations"] == {"entries": 3, "verified": 2}
    failed = score_case({"action_items": [{"what": ["x"]}], "calendar_events": [{"title": ["y"]}]}, None, TZ)
    assert failed["actions"]["expected"] == 1 and failed["actions"]["predicted"] == 0
    assert failed["events"]["expected"] == 1


def test_a_date_written_as_yyyy_mm_dd_in_the_brief_text_is_counted():
    got = summary([kid("Aino", notices=["Historian koe ke 7.10.", "考试在 2026-10-07"],
                       actions=[("Sign by 2026-10-06", "2026-10-06")])])
    got["message_digest"] = "**Aino**\n- 10月7日 周三 历史考试\n- Exam on 2026-10-07"
    s = score_case({}, got, TZ)
    assert s["dates"] == {"texts": 6, "iso": 3}  # the Digest's three lines, two notices and an action
    s.update(valid_json=True)
    assert not is_clean(s)
    assert aggregate([s])["dates_as_written"] == 0.5


def test_aggregate_pools_counts_across_cases():
    a = score_case({"action_items": [{"kid": "Aino", "what": ["x"], "by": "2026-10-06"}]},
                   summary([kid("Aino", actions=[("x", "2026-10-06"), ("y", "2026-10-06")])]), TZ)
    b = score_case({"action_items": [{"kid": "Aino", "what": ["z"], "by": "2026-10-06"}],
                    "notices": {"must_not": [{"text": ["x"]}]}},
                   summary([kid("Aino", notices=["x"])]), TZ)
    a.update(valid_json=True, seconds=10.0, usd=0.004,
             usage={"input": 900, "cache_write": 100, "cached_input": 3000, "output": 500})
    b.update(valid_json=False, seconds=20.0, usd=None, usage=None)
    m = aggregate([a, b])
    assert m["action_recall"] == 0.5 and m["action_precision"] == 0.5
    assert m["due_date_ok"] == 1.0 and m["kid_ok"] == 1.0
    assert m["forbidden_hits"] == 1
    assert m["valid_json"] == 0.5
    assert m["seconds_per_night"] == 15.0
    # Only the nights the backend reported tokens for, each kind apart, priced per night and month.
    assert (m["input_tokens"], m["cache_write_tokens"], m["cached_tokens"], m["output_tokens"]) == \
        (900, 100, 3000, 500)
    assert m["usd_per_night"] == 0.004 and m["usd_per_month"] == 0.12
    assert m["event_recall"] is None  # nothing expected anywhere


def test_a_placeholder_shown_in_the_brief_or_put_back_as_a_word_is_counted():
    got = summary([kid("Aino", notices=["Soita ⟦P1⟧", "Opettaja ⟦N⟧ kertoi"],
                       actions=[("Maksa 〚L2〛", "2026-10-06")])],
                  [{"kid": "Aino", "title": "Retki", "description": "Lisätietoja ⟦L1⟧",
                    "start": "2026-10-08"}])
    got["message_digest"] = "**Aino**\n- Soita ⟦P1⟧"
    s = score_case({}, got, TZ, lost_placeholders=2)
    assert s["placeholders"] == {"shown": 5, "lost": 2}  # the Digest line, two notices, the action, the event
    s.update(valid_json=True)
    assert not is_clean(s)
    assert aggregate([s])["placeholders_left"] == 7


def test_a_brief_with_every_placeholder_put_back_is_clean():
    s = score_case({}, summary([kid("Aino", notices=["Soita Maijalle 040 123 4567"])]), TZ)
    s.update(valid_json=True)
    assert s["placeholders"] == {"shown": 0, "lost": 0} and is_clean(s)


# A Finnish Brief inflects its words, so the bundled cases list Finnish stems.
FINNISH_NIGHTS = {
    "fi-ensi-torstaina": summary([kid("Aino", actions=[("Palauta retken lupalappu allekirjoitettuna", "2026-10-06"),
                                                        ("Pakkaa omat eväät ja säänmukaiset vaatteet", "2026-10-08")])],
                                 [{"kid": "Aino", "title": "4B:n retki Nuuksioon", "start": "2026-10-08T09:00:00+03:00"}]),
    "calendar-disagrees-time": summary([kid("Aino", notices=[
        "la 10.10. ottelu PK-35:tä vastaan alkaa klo 11, kalenterissa klo 10"])]),
    "cross-night-time-changed": summary(
        [kid("Eero", notices=["ti 6.10. salibandytreeni alkaa vasta klo 18.30 (ei klo 17)"])],
        [{"kid": "Eero", "title": "Salibandytreeni", "start": "2026-10-06T18:30:00+03:00"}]),
    "whatsapp-two-groups": summary([kid("Eero", notices=["ti 6.10. salibandytreenit on peruttu"]),
                                    kid("Aino", notices=["ti 6.10. koulukuvaus"])]),
    # All-day events, the date alone; Monday's timetable gives none.
    "wilma-dated-exam-and-short-day": summary(
        [kid("Aino", notices=["ke 7.10. historian koe, luvut 3–5"])],
        [{"kid": "Aino", "title": "Historian koe", "start": "2026-10-07"},
         {"kid": "Aino", "title": "Ulkoilupäivä kodalla", "start": "2026-10-09",
          "description": "Koulupäivä päättyy klo 13.00"}]),
    "fi-koe-viikon-paasta": summary([kid("Aino", notices=["ma 26.10. matematiikan koe"])],
                                    [{"kid": "Aino", "title": "Matematiikan koe", "start": "2026-10-26"}]),
    "kid-alias-chinese": summary([kid("Aino", notices=["la 10.10. kiinan kurssi alkaa klo 14"],
                                      actions=[("Ota mukaan viime viikon tehtäväkirja", "2026-10-10")])],
                                 [{"kid": "Aino", "title": "Kiinan kurssi", "start": "2026-10-10T14:00:00"}]),
    "fi-inflected-names-places": summary(
        [kid("Eero", actions=[("Pakkaa Eerolle uimakassi mukaan", "2026-10-07")]),
         kid("Aino", actions=[("Ainolle kirjastokortti mukaan", "2026-10-09")])],
        [{"kid": "Eero", "title": "Uintitunti Leppävaaran uimahallissa", "start": "2026-10-07T10:00:00+03:00"}]),
    "fi-viikolla-43": summary([kid("Aino", actions=[("Palauta koulukirjaston kirjat", "2026-10-09")])],
                              [{"kid": "Aino", "title": "Ympäristöopin koe", "start": "2026-10-20"},
                               {"kid": "Aino", "title": "4B:n vanhempainilta",
                                "start": "2026-10-28T18:00:00+03:00"}]),
    "fi-puoli-viisi": summary([kid("Eero", notices=["Pianotunti siirtyy ma 12.10. → ti 13.10. klo 16.30"])],
                              [{"kid": "Eero", "title": "Pianotunti", "start": "2026-10-13T16:30:00+03:00"}]),
    # A Finnish Brief keeps the everyday words the other languages must translate.
    "everyday-finnish-translated": summary(
        [kid("Aino", actions=[("Ilmoittaudu vanhempainiltaan Wilman lomakkeella", "2026-10-15"),
                              ("Pakkaa omat eväät ja säänmukaiset ulkoiluvarusteet", "2026-10-16")])],
        [{"kid": "Aino", "title": "4B:n vanhempainilta", "start": "2026-10-20T18:00:00+03:00"}]),
    "wilma-kotipostikansio-papers": summary(
        [kid("Aino", actions=[("Allekirjoita kotipostikansion matematiikan ja englannin kokeet", "2026-10-19")])]),
    "fi-mixed-languages": summary(
        [kid("Aino", notices=["la 10.10. kiinan tunti on peruttu"],
             actions=[("Maksa luokkakuvat 15 €", "2026-10-12")])],
        [{"kid": "Aino", "title": "Ottelu HJK:ta vastaan", "start": "2026-10-10T12:00:00+03:00"}]),
}


def test_a_plain_finnish_brief_scores_clean_on_the_bundled_cases():
    from family_brief.eval.cases import BUNDLED, load_cases
    from family_brief.eval.score import is_clean
    cases = {c.name: c for c in load_cases(BUNDLED)}
    for name, night in FINNISH_NIGHTS.items():
        assert is_clean(score_case(cases[name].expect, night, TZ, language="fi")), name


def test_puoli_viisi_read_as_another_time_is_not_clean():
    from family_brief.eval.cases import BUNDLED, load_cases
    case = next(c for c in load_cases(BUNDLED) if c.name == "fi-puoli-viisi")
    for wrong in ("17.30", "14.30", "14:30"):  # half past five, or a 4:30 inside 14:30
        night = summary([kid("Eero", notices=[f"Pianotunti siirtyy ti 13.10. klo {wrong}"])],
                        [{"kid": "Eero", "title": "Pianotunti", "start": "2026-10-13T17:30:00+03:00"}])
        s = score_case(case.expect, night, TZ)
        assert s["events"]["start_ok"] == 0 and s["notices"]["found"] == 0, wrong


def test_a_deadline_or_a_training_that_carries_on_is_not_a_calendar_event():
    from family_brief.eval.cases import BUNDLED, load_cases
    cases = {c.name: c for c in load_cases(BUNDLED)}
    book_list = [kid("Eero", actions=[("Palauta lukudiplomin kirjalista", "2026-10-09")])]
    deadline = {"kid": "Eero", "title": "Lukudiplomin kirjalista", "start": "2026-10-09"}
    photos = [kid("Eero", actions=[("Tilaa luokkakuvat verkkokaupasta", "2026-10-13")])]
    photo_deadline = {"kid": "Eero", "title": "Luokkakuvien tilaus", "start": "2026-10-13"}
    autumn_break = [kid("Aino", notices=["Syysloma 12.–16.10.: ei jalkapallotreenejä"])]
    resumes = {"kid": "Aino", "title": "Jalkapallotreenit jatkuvat", "start": "2026-10-19T17:30:00"}
    school_closed = {"kid": "Aino", "title": "Syysloma", "start": "2026-10-12", "end": "2026-10-16"}
    nights = [("kid-alias-eetu", summary(book_list), True),
              ("kid-alias-eetu", summary(book_list, [deadline]), False),
              ("fi-tiistaihin-mennessa", summary(photos), True),
              ("fi-tiistaihin-mennessa", summary(photos, [photo_deadline]), False),
              ("fi-syysloma-ensi-viikolla", summary(autumn_break), True),
              ("fi-syysloma-ensi-viikolla", summary(autumn_break, [school_closed]), True),
              ("fi-syysloma-ensi-viikolla", summary(autumn_break, [resumes]), False)]
    for name, night, clean in nights:
        assert is_clean(score_case(cases[name].expect, night, TZ)) is clean, (name, night["calendar_events"])


def test_a_brief_that_says_there_is_nothing_new_or_explains_itself_is_not_clean():
    from family_brief.eval.cases import BUNDLED, load_cases
    cases = {c.name: c for c in load_cases(BUNDLED)}
    theatre = [kid("Aino", notices=["周四 10/8 4B 去 Espoon kaupunginteatteri 看戏，9:15 从学校出发，约 12 点回来"],
                   actions=[("把 6 € 戏票钱转到班级账户", "2026-10-07")])]
    play = {"kid": "Aino", "title": "4B 看戏（teatteri）", "start": "2026-10-08T09:15:00"}
    rereads = [kid("Aino", notices=["周日 10/11 对 Espoon Palloseura 的比赛改到 12:00"])]

    def night(per_kid, events, digest):
        s = summary(per_kid, events)
        s["message_digest"] = digest
        return s

    nights = [
        ("one-kid-news-family-none",
         night(theatre, [play], "**Aino**\n- 周四 10/8 4B 去剧院看戏，9:15 出发，约 12 点回来"), True),
        ("one-kid-news-family-none",
         night(theatre, [play], "**Aino**\n- 周四 10/8 去剧院看戏\n**Eero**\n- 本次无新的待办或日程事项"), False),
        ("one-kid-news-family-none",
         night(theatre, [play], "**Aino**\n- 周四 10/8 去剧院看戏\n**全家**\n- 本次没有新的日程变更、费用或其他需要处理的事项"),
         False),
        ("one-kid-news-family-none",
         night(theatre, [play], "**Aino**\n- 周四 10/8 去剧院看戏，周三前要付 6 € 戏票钱"), False),
        ("one-kid-news-family-none",
         night([kid("Aino", notices=["周三前付 6 € 戏票钱"], actions=[("付 6 € 戏票钱", "2026-10-07")])],
               [play], "**Aino**\n- 周四 10/8 去剧院看戏"), False),
        ("re-read-archive-night", night(rereads, [], "**Aino**\n- 周日 10/11 的比赛改到 12:00"), True),
        ("re-read-archive-night",
         night(rereads, [], "**Aino**\n- 比赛改到 12:00\n**Eero**\n- 还书的事都已在之前的 Brief 中处理，这里不再重复"), False),
        ("re-read-archive-night",
         night(rereads, [], "**Eero**\n- 还书还在提醒范围之外，所以这次不再重复提醒"), False),
        ("re-read-archive-night", night([], [], "**全家**\n- 过去24小时没有新消息"), False),
    ]
    for name, brief, clean in nights:
        s = score_case(cases[name].expect, brief, TZ, language="zh")
        s.update(valid_json=True)
        assert is_clean(s) is clean, (name, brief["message_digest"], s)


def test_papers_in_the_home_folder_are_not_placed_in_wilma():
    # A Wilma message about papers in the paper home folder put them "in Wilma's kotipostikansio" (#285).
    from family_brief.eval.cases import BUNDLED, load_cases
    case = next(c for c in load_cases(BUNDLED) if c.name == "wilma-kotipostikansio-papers")
    notice = "老师说数学和英语考卷已发回，下周一 10/19 前交回"
    nights = [
        ("签好 Aino 书包里家庭文件夹（kotipostikansio）中的数学和英语考卷，下周一交回", True),
        ("签好 Aino 的 kotipostikansio 里的考卷", True),
        ("签好班主任在Wilma的kotipostikansio里放入的考卷", False),
        ("在 Wilma 里签好数学和英语考卷", False),
        ("签好数学和英语考卷", False),  # doesn't say where they are
    ]
    for action, clean in nights:
        s = score_case(case.expect, summary([kid("Aino", notices=[notice], actions=[(action, "2026-10-19")])]),
                       TZ, language="zh")
        s.update(valid_json=True)
        assert is_clean(s) is clean, (action, s)


def test_kuljetusoppilaat_are_pupils_on_school_transport():
    # The Chinese Brief rendered kuljetusoppilaat as "transport class pupils" (#285).
    from family_brief.eval.cases import BUNDLED, load_cases
    case = next(c for c in load_cases(BUNDLED) if c.name == "everyday-finnish-translated")
    actions = [("周四 10/15 前用 Wilma 表格报名家长会", "2026-10-15")]
    meeting = {"kid": "Aino", "title": "4B 家长会", "start": "2026-10-20T18:00:00"}
    nights = [
        ("zh", "周五 10/16 户外日 12:30 放学，坐校车的学生 12:45 出发；周二 10/20 18:00 家长会（vanhempainilta）", True),
        ("en", "Fri 16.10 outdoor day ends at 12:30; pupils on school transport leave at 12:45. "
               "Parents' evening (vanhempainilta) Tue 20.10 at 18:00", True),
        # How the default Claude model put it in #289's run (#294).
        ("en", "Fri 16.10 outdoor day ends at 12:30; transport pupils' rides leave the school yard at 12:45. "
               "Parents' evening (vanhempainilta) Tue 20.10 at 18:00", True),
        ("zh", "周五 10/16 户外日 12:30 放学，接送学生的车 12:45 从校园出发；周二 10/20 18:00 家长会（vanhempainilta）", True),
        ("en", "Fri 16.10 outdoor day ends at 12:30; the transport class leaves at 12:45. "
               "Parents' evening (vanhempainilta) Tue 20.10 at 18:00", False),
        ("zh", "周五 10/16 户外日 12:30 放学，交通班学生 12:45 出发；周二 10/20 18:00 家长会（vanhempainilta）", False),
        ("zh", "周五 10/16 户外日 12:30 放学；周二 10/20 18:00 家长会（vanhempainilta）", False),
    ]
    for language, notice, clean in nights:
        s = score_case(case.expect, summary([kid("Aino", notices=[notice], actions=actions)], [meeting]),
                       TZ, language=language)
        s.update(valid_json=True)
        assert is_clean(s) is clean, (notice, s)


def test_a_keyword_ending_in_a_space_matches_at_the_end_of_the_text():
    from family_brief.eval.score import matches
    assert matches(["klo 10 "], "kalenterissa klo 10")
    assert not matches(["klo 10 "], "kokoontuminen klo 10.30")


def test_a_photo_word_counts_as_forbidden_but_a_training_that_continues_does_not():
    # A bare stem kuva (photo) also matches "jatkuvat" (continue), which failed right Briefs (#243).
    from family_brief.eval.cases import BUNDLED, load_cases
    cases = {c.name: c for c in load_cases(BUNDLED)}
    for name, other in (("whatsapp-two-groups", "Eero"), ("fi-tiistaihin-mennessa", "Aino")):
        rules = cases[name].expect["notices"]["must_not"]
        rule = next(r for r in rules if r["kid"] == other)

        def hits(text: str) -> int:
            got = summary([kid(other, notices=[text])])
            return score_case({"notices": {"must_not": [rule]}}, got, TZ)["notices"]["hits"]

        assert hits("Treenit jatkuvat normaalisti torstaina klo 17") == 0, name
        assert hits("Jatkuvia harjoituksia on kaksi viikossa") == 0, name
        assert hits("Koulukuvaus on tiistaina") == 1, name
        assert hits("Tilaa kuvat verkkokaupasta") == 1, name
