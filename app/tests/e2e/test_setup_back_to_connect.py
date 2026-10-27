"""The way back from Check and Finish to Connect's list, for a Source the family skipped. Calls are
made as the page makes them (`setup_page.py`)."""
from __future__ import annotations

import json
from typing import Any

from family_brief import setup_save
from setup_page import (PAGE_DIR, Response, at_the_check_step, call, config_file, confirm,
                        health_result, more_progress, source, statuses)
from setup_page import clock, health, page  # noqa: F401


def back_to_connect(url: str, body: Any = None) -> Response:
    return call(url, "api/connect/back", method="POST", body={} if body is None else body)


def test_check_leads_back_to_connect_s_list_where_a_skipped_source_can_be_opened(harness, page,
                                                                                health):
    at_the_check_step(harness)  # MyClub skipped
    confirm(page.url)
    assert health_result(page.url)["result"] == "ok"
    more_progress(harness, phase="check")

    r = back_to_connect(page.url)

    assert r.status == 200
    progress = r.json()["progress"]
    assert (progress["phase"], progress["source"]) == ("connect", None)
    assert progress["sources"]["myclub"] == "skipped"  # until it's opened from the list
    assert call(page.url, "api/check/health", method="POST", body={}).json() == \
        {"result": "no-check"}
    assert source(page.url, "myclub", "open").json()["progress"]["source"] == "myclub"
    assert statuses(page.url)["myclub"] == "to-do"


def test_only_check_and_finish_go_back_to_connect_with_a_skipped_source_never_while_checking(
        harness, page, health):
    at_the_check_step(harness)
    assert back_to_connect(page.url, {"x": 1}).status == 400
    health.go.clear()
    confirm(page.url)
    assert back_to_connect(page.url).status == 409  # the health check is running
    health.go.set()
    health_result(page.url)

    more_progress(harness, phase="first-brief")
    assert back_to_connect(page.url).status == 400
    more_progress(harness, phase="check", sources={"myclub": "done"})
    assert back_to_connect(page.url).status == 400  # nothing skipped to add
    assert setup_save.read(config_file(harness))["progress"]["phase"] == "check"


def test_the_page_offers_the_way_back_to_connect_on_check_and_finish_only_with_a_skipped_source():
    html_text = (PAGE_DIR / "index.html").read_text()

    for section, link in (("check", "check-back"), ("finish", "finish-back")):
        part = html_text.split(f'<section id="{section}"', 1)[1].split("</section>", 1)[0]
        assert f'id="{link}"' in part and 'data-text="connect.back"' in part, section
    for language, table in json.loads((PAGE_DIR / "text.json").read_text()).items():
        assert table.get("connect.back", "").strip(), language
