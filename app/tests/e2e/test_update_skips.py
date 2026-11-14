"""A family's computer skips a release that changes nothing it uses (ADR 0051, #960): the look
reads `release.json` on `stable`, and installs the release only when the core, or the part of a
Source the Household has connected, was last changed in a version newer than the installed one.
Otherwise it stays on its version, downloads nothing, and no Brief names the release.

Driven through main() as test_update.py and test_evening_update.py drive `parent-recap update`
and the evening run, and over HTTP as test_setup_updates.py drives the setup page, against their
made-up `stable` (`Releases`), whose `parts` say which version last changed each part."""
from __future__ import annotations

import json

import pytest

from conftest import NOW
from family_brief import ops, sources, update
from family_brief.brief_text import TEXT
from family_brief.config import Config
from setup_page import PAGE_DIR, clock, connect_done, health, page, schedule  # noqa: F401
from test_doctor import nothing_loaded, updates_line  # noqa: F401
from test_evening_update import DAY, at, evening  # noqa: F401  the fixture
from test_evening_update_brief import UPDATE_MARK, a_message, evening_on, quiet, top, updates_off
from test_setup_updates import household, update_now, updates  # noqa: F401
from test_update import INSTALLED, github, mac, no_downloads, update_command, update_log  # noqa: F401

# install.sh's layout, on a Mac.
pytestmark = pytest.mark.posix



def parts(core: str = INSTALLED, **changed: str) -> dict[str, str]:
    """A release.json's `parts`, as scripts/release.py writes them: the last version that changed
    the core and each Source's folder, which is the installed one unless `changed` says another,
    by the Source's name (`konnect="0.20.0"`)."""
    said = {"core": core}
    for source in sources.ALL:
        if source.part is not None:
            said[source.part] = changed.get(source.name, INSTALLED)
    return said


def without(part: str) -> dict[str, str]:
    return {name: version for name, version in parts().items() if name != part}


KONNECT_ONLY = parts(konnect="0.20.0")
PAGE_TEXT = json.loads((PAGE_DIR / "text.json").read_text(encoding="utf-8"))


def out_on_stable(github, version: str, parts) -> None:  # noqa: F811
    """`version` is released and on `stable`, whose release.json says `parts`."""
    github.stable = version
    github.parts = parts
    github.publish(version)


def with_konnect(h) -> None:
    """The Household has a Konnect sign-in, so Konnect is one of its connected Sources."""
    h.config["konnect"] = {"signins": [{"portal": "mijnbso", "username": "parent@example.nl",
                                        "kids": {"c0ffee0000000000000000000000a001": "Mia"}}]}


def zips_asked(github) -> list[str]:  # noqa: F811
    return [r["url"] for r in github.requests if r["host"] != "raw.githubusercontent.com"]


# ---------------------------------------------------------------- parent-recap update

def test_a_release_that_changed_only_konnect_is_skipped_by_a_household_without_konnect(
        harness, github, mac, capsys):  # noqa: F811
    out_on_stable(github, "0.20.0", KONNECT_ONLY)

    code, out = update_command(harness, capsys)

    assert code == 0  # no failure
    assert mac.version() == INSTALLED and mac.installs == []
    # One request, for release.json: no zip, no signature, and nothing for plugin.json.
    assert [r["url"] for r in github.requests] == [update.STABLE_RELEASE]
    assert update.STABLE_RELEASE.endswith("/stable/release.json")
    assert no_downloads(mac)
    # Said in English, Finnish and Chinese: the command can't tell which the family reads.
    english, finnish, chinese = out.strip().split("\n")
    assert english == ("Version 0.20.0 of Parent Recap is out, but it changes nothing your family "
                       "uses, so it wasn't installed and nothing was downloaded. Parent Recap "
                       "stays on version 0.19.0.")
    assert "versio 0.20.0" in finnish and "ei muuta mitään, mitä perheesi käyttää" in finnish
    assert "0.20.0" in chinese and "没有改动你家用到的任何部分" in chinese
    assert "0.20.0 changes nothing this Household uses; not downloaded" in update_log(mac)


def test_the_same_release_is_installed_by_a_household_with_a_konnect_sign_in(
        harness, github, mac, capsys):  # noqa: F811
    with_konnect(harness)
    out_on_stable(github, "0.20.0", KONNECT_ONLY)

    code, out = update_command(harness, capsys)

    assert code == 0 and "updated from version 0.19.0 to 0.20.0" in out
    assert mac.version() == "0.20.0" and len(mac.installs) == 1


def test_a_release_whose_core_is_newer_is_installed_by_every_household(harness, github, mac, capsys):  # noqa: F811
    out_on_stable(github, "0.20.0", parts(core="0.20.0"))

    assert update_command(harness, capsys)[0] == 0

    assert mac.version() == "0.20.0"


def test_a_household_that_skipped_several_releases_goes_straight_to_the_newest(
        harness, github, mac, capsys):  # noqa: F811
    # 0.20.0 and 0.21.0 changed only Konnect, and were skipped; 0.22.0 changes the core.
    for version, core in (("0.20.0", INSTALLED), ("0.21.0", INSTALLED), ("0.22.0", "0.22.0")):
        out_on_stable(github, version, parts(core=core, konnect=version))
        code, _ = update_command(harness, capsys)
        assert code == 0

    assert mac.version() == "0.22.0" and len(mac.installs) == 1
    assert zips_asked(github) and all("0.22.0" in url for url in zips_asked(github))


def test_a_core_changed_in_a_release_between_is_installed_with_the_newest(harness, github, mac, capsys):  # noqa: F811
    # 0.20.0 changed the core, 0.21.0 only Konnect: a Household on 0.19.0 still lacks 0.20.0's.
    out_on_stable(github, "0.21.0", parts(core="0.20.0", konnect="0.21.0"))

    assert update_command(harness, capsys)[0] == 0

    assert mac.version() == "0.21.0"


@pytest.mark.parametrize("parts, konnect", [
    (None, False),                                     # a release.json from before `parts`
    ("everything", False),                             # `parts` that isn't one
    (without("core"), False),                          # without the core's
    (parts(core="soon"), False),                       # a version that isn't one
    (without("nl/konnect"), True),                     # without a connected Source's own part
    ({**parts(), "nl/konnect": None}, True),
])
def test_what_the_file_does_not_say_counts_as_changed(harness, github, mac, capsys, parts, konnect):  # noqa: F811
    if konnect:
        with_konnect(harness)
    out_on_stable(github, "0.20.0", parts)

    assert update_command(harness, capsys)[0] == 0

    assert mac.version() == "0.20.0"


def test_a_source_without_a_folder_of_its_own_needs_no_part(harness, github, mac, capsys):  # noqa: F811
    # The harness's Household reads Gmail and Wilma, whose changes are the core's, and MyClub,
    # which has a folder: the file needs a part for MyClub alone.
    used = [s.name for s in sources.ALL if s.connected(Config(**harness.config))]
    assert used == ["gmail", "myclub", "wilma"]
    out_on_stable(github, "0.20.0", {"core": INSTALLED, "fi/myclub": INSTALLED})

    code, out = update_command(harness, capsys)

    assert code == 0 and "changes nothing your family uses" in out
    assert mac.version() == INSTALLED and zips_asked(github) == []


def test_a_household_whose_config_cannot_be_read_installs_the_release(harness, github, mac, capsys):  # noqa: F811
    out_on_stable(github, "0.20.0", KONNECT_ONLY)
    harness.config["konnect"] = {"signins": "all of them"}  # which Sources it has can't be told

    update_command(harness, capsys)

    assert mac.version() == "0.20.0"


@pytest.mark.parametrize("file", [b"not json", b"[]", b'{"parts": {"core": "0.20.0"}}',
                                  b'{"version": 20}', b'{"version": "newest"}'])
def test_a_file_that_cannot_be_read_is_a_look_that_failed(harness, github, mac, capsys, file):  # noqa: F811
    github.publish("0.20.0")
    github.stable_file = file

    code, out = update_command(harness, capsys)

    assert code == 1 and "couldn't look for a newer release" in out and "Nothing was changed" in out
    assert mac.version() == INSTALLED and zips_asked(github) == []
    assert update.state(mac.folder)["last_result"].startswith("look failed: the version on stable")


# ---------------------------------------------------------------- the evening run

def test_the_evening_run_skips_it_with_one_request_and_no_brief_names_it(evening, github, mac):  # noqa: F811
    out_on_stable(github, "0.20.0", KONNECT_ONLY)

    for day in (0, 1, 2):  # a day old and more: a release it would install were in by now
        a_message(evening, day)
        evening_on(evening, day)

    assert mac.version() == INSTALLED and mac.installs == []
    assert [r["url"] for r in github.requests] == [update.STABLE_RELEASE] * 3
    assert len(evening.sent) == 3
    assert all(UPDATE_MARK not in brief.text and "0.20.0" not in brief.text for brief in evening.sent)
    state = update.state(mac.folder)
    assert state["skipped"] == "0.20.0" and state["seen"] is None
    assert state["last_result"] == ("0.20.0 is out and changes nothing this Household uses, so it "
                                    "isn't installed")
    # No failure, and nothing waits for the family.
    assert (state["failed"], state["tell_failed"], state["waiting"]) == (None, None, None)


def test_the_evening_run_installs_it_for_a_household_with_konnect_after_its_day(evening, github, mac):  # noqa: F811
    with_konnect(evening)
    evening.source_fakes["konnect"].portal("mijnbso")
    out_on_stable(github, "0.20.0", KONNECT_ONLY)

    assert evening.run() == 0
    assert mac.installs == []  # today's rules: new to this Mac
    with at(NOW + DAY):
        assert evening.run() == 0

    assert mac.version() == "0.20.0"


def test_the_day_s_wait_counts_from_the_first_release_it_would_install(evening, github, mac):  # noqa: F811
    out_on_stable(github, "0.20.0", KONNECT_ONLY)
    quiet(evening)
    evening_on(evening, 0)
    evening_on(evening, 1)  # skipped twice: no day has started
    out_on_stable(github, "0.21.0", parts(core="0.21.0", konnect="0.20.0"))

    evening_on(evening, 2)

    assert mac.installs == []  # first seen tonight, though a newer release has been out two days
    assert update.state(mac.folder)["seen"]["version"] == "0.21.0"
    assert update.state(mac.folder)["skipped"] is None
    evening_on(evening, 3)
    assert mac.version() == "0.21.0"


def test_a_skipped_release_the_household_now_uses_starts_its_day_then(evening, github, mac):  # noqa: F811
    out_on_stable(github, "0.20.0", KONNECT_ONLY)
    quiet(evening)
    evening_on(evening, 0)
    evening_on(evening, 1)
    with_konnect(evening)  # the family adds Konnect
    evening.source_fakes["konnect"].portal("mijnbso")

    evening_on(evening, 2)

    assert mac.installs == []  # not from the evenings that skipped it
    evening_on(evening, 3)
    assert mac.version() == "0.20.0"


@pytest.mark.parametrize("language", ["en", "fi", "zh"])
def test_with_updates_off_the_brief_names_only_a_release_that_changes_something_it_uses(
        evening, github, mac, language):  # noqa: F811
    evening.config["summary_language"] = language
    updates_off(evening)
    out_on_stable(github, "0.20.0", KONNECT_ONLY)
    quiet(evening)
    evening_on(evening, 0)  # the look finds 0.20.0, and skips it
    a_message(evening, 1)
    evening_on(evening, 1)

    [brief] = evening.sent
    assert UPDATE_MARK not in brief.text and "0.20.0" not in brief.text
    assert update.state(mac.folder)["tell_available"] is None
    assert [r["url"] for r in github.requests] == [update.STABLE_RELEASE] * 2

    out_on_stable(github, "0.21.0", parts(core="0.21.0", konnect="0.20.0"))
    quiet(evening)
    evening_on(evening, 2)
    a_message(evening, 3)
    evening_on(evening, 3)

    assert top(evening.sent[-1].text) == [TEXT[language].update_available.format(version="0.21.0")]
    assert mac.version() == INSTALLED and zips_asked(github) == []


def test_with_updates_off_a_release_said_to_be_out_is_not_named_once_it_is_skipped(evening, github, mac):  # noqa: F811
    updates_off(evening)
    with_konnect(evening)
    evening.source_fakes["konnect"].portal("mijnbso")
    out_on_stable(github, "0.20.0", KONNECT_ONLY)
    quiet(evening)
    evening_on(evening, 0)  # kept for the next Brief: this Household has Konnect
    assert update.state(mac.folder)["tell_available"] == "0.20.0"
    del evening.config["konnect"]  # the family removes Konnect before a Brief said so
    evening_on(evening, 1)

    a_message(evening, 2)
    evening_on(evening, 2)

    assert UPDATE_MARK not in evening.sent[-1].text


# ---------------------------------------------------------------- doctor and the setup page

def test_doctor_says_a_skipped_release_is_no_failure_and_no_waiting_update(
        harness, nothing_loaded, github, mac, capsys):  # noqa: F811
    out_on_stable(github, "0.20.0", KONNECT_ONLY)
    harness.cli("update")

    line = updates_line(harness, capsys)

    assert line.startswith(f"{ops.OK} Updates: version {INSTALLED}; updates are on")
    assert line.endswith("0.20.0 is out and changes nothing this Household uses, so it isn't "
                         "installed; nothing to install: version 0.20.0 changes nothing this "
                         "Household uses")
    assert "waiting" not in line and "fail" not in line
    status = update.status(None)
    assert (status.skipped, status.newer, status.waiting) == ("0.20.0", None, None)


def test_a_look_that_finds_a_release_to_install_no_longer_says_one_is_skipped(
        harness, github, mac, capsys):  # noqa: F811
    out_on_stable(github, "0.20.0", KONNECT_ONLY)
    harness.cli("update")
    out_on_stable(github, "0.21.0", parts(core="0.21.0", konnect="0.20.0"))

    assert update.update(None, wait=DAY).result == "waiting"  # as the evening run looks

    status = update.status(None)
    assert (status.skipped, status.newer) == (None, "0.21.0")
    assert "changes nothing" not in status.line()


def test_update_now_skips_the_same_release_and_the_page_says_so(household, github, page):  # noqa: F811
    out_on_stable(github, "0.20.0", KONNECT_ONLY)

    out = update_now(page.url)

    assert (out["result"], out["version"], out["skipped"]) == ("skipped", INSTALLED, "0.20.0")
    assert "tried" not in out and "reason" not in out  # nothing downloaded, nothing failed
    assert [r["url"] for r in github.requests] == [update.STABLE_RELEASE]
    # The update line: skipped, with no Update now to press and nothing waiting.
    shown = updates(page.url)
    assert out["updates"] == shown
    assert (shown["skipped"], shown["newer"], shown["waiting"]) == ("0.20.0", None, None)
    assert shown["report"] == update.status(None).line()
    assert "0.20.0 changes nothing this Household uses" in shown["report"]


def test_update_now_installs_it_for_a_household_with_konnect(household, github, page):  # noqa: F811
    with_konnect(household)
    connect_done(household)  # saved, as `setup konnect` saves it
    out_on_stable(github, "0.20.0", KONNECT_ONLY)

    out = update_now(page.url)

    assert (out["result"], out["version"]) == ("updated", "0.20.0")
    assert out["updates"]["skipped"] is None


def test_the_page_says_a_release_is_skipped_in_all_three_languages():
    says = {"en": "changes nothing your family uses", "fi": "ei muuta mitään, mitä perheesi käyttää",
            "zh": "没有改动你家用到的任何部分"}
    for language, table in PAGE_TEXT.items():
        text = table["finish.updates.skipped"]
        assert says[language] in text, language
        assert "{skipped}" in text and "{version}" in text, language
        # The same words as `parent-recap update` says it in.
        assert any(says[language] in line for line in update.SKIPPED), language
    html = (PAGE_DIR / "index.html").read_text()
    assert 'id="finish-updates-skipped" hidden' in html
