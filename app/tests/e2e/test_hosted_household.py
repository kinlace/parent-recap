"""What the maintainers see and change of one Hosted Household (#782): `signup show`, `signup
recipients` and `signup myclub`, which `sudo admin household` runs as the Household's own user.

The Household is made by the real sign-up over HTTP (`test_signup`'s fixture), so its config is
the one `signup household` writes. Each command is run as its user through the fake household
host, the program in-process, with what the maintainer types on its stdin. Assertions are on what
it prints, on the config it leaves, on the gateway's binding, and on the Household's next status.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from family_brief.config import Config
from family_brief.signup import status
from family_brief.signup.host import PROGRAM
from family_brief.signup.invites import Recipient
from test_hosted_status import NO_REBOOT
from test_signup import MIA_LINK, mia_plays, signup, submit, wilma_cli  # noqa: F401

pytestmark = pytest.mark.posix  # the fake wilma CLI is a script on a shell-script Node

LEO_LINK = "https://kilonpallo.myclub.fi/ical/leo-1002-token.ics"


def made(s) -> tuple[str, Path]:
    code = s.invite(Recipient("parent@example.com", "fi"))
    assert submit(s, code).status == 200
    label = s.store.households()[-1].user
    return label, s.host.users[label].home / ".family" / "config.yaml"


def run(s, label: str, command: str, typed: str = "") -> tuple[int, str]:
    return s.host.run_own(label, [PROGRAM, "signup", command], typed)


def config(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def healthy(s, capsys) -> None:
    """The Household's next status: `signup health` as its user, with every check passing."""
    capsys.readouterr()
    code = status.report(s.store, s.host, reboot_required=NO_REBOOT)
    out = capsys.readouterr().out
    assert code == 0 and "❌" not in out and "⚠️" not in out, out


# ── show

def test_show_prints_the_recipients_the_kids_and_their_myclub_links(signup):
    label, path = made(signup)
    data = config(path)
    data["email"]["weekend_to"] = [{"address": "grandma@example.com", "language": "en"}]
    data["kids"][0]["myclub_ical_url"] = MIA_LINK
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))

    code, out = run(signup, label, "show")

    assert code == 0, out
    lines = out.splitlines()
    assert lines[:2] == ["Recipients:", "  parent@example.com  fi"]
    assert "  grandma@example.com  en" in lines
    kids = [k["name"] for k in data["kids"]]
    assert kids == ["Mia Virtanen", "Leo Virtanen"]
    assert f"    MyClub: {MIA_LINK}" == lines[lines.index(next(
        line for line in lines if line.startswith("  Mia Virtanen"))) + 1]
    assert lines[-1] == "    MyClub: none"


def test_show_without_weekend_to_names_no_weekend_recipients(signup):
    label, _ = made(signup)
    code, out = run(signup, label, "show")
    assert code == 0 and "weekend_to" not in out


# ── recipients

def test_recipients_are_written_after_yes_in_the_shape_signup_household_writes(
        signup, outbox, capsys):
    label, path = made(signup)
    before = config(path)

    code, out = run(signup, label, "recipients",
                    "parent@example.com:fi\nIsa@Example.com:ZH\nleo.dad@example.com\n\nyes\n")

    assert code == 0, out
    # Without a language, the Household's own.
    default = before["summary_language"]
    assert "+ isa@example.com  zh" in out and f"+ leo.dad@example.com  {default}" in out
    after = config(path)
    new = [{"address": "parent@example.com", "language": "fi"},
           {"address": "isa@example.com", "language": "zh"},
           {"address": "leo.dad@example.com", "language": default}]
    assert after == {**before, "email": {**before["email"], "to": new}}
    # The Gateway Key is bound to the new list, as doctor binds it.
    assert outbox.binds[-1]["recipients"] == new
    assert Config.load(path).email.to[1].address == "isa@example.com"
    healthy(signup, capsys)


def test_recipients_only_reordered_say_so_and_are_written_after_yes(signup):
    label, path = made(signup)
    run(signup, label, "recipients", "parent@example.com:fi\nisa@example.com:zh\n\nyes\n")

    code, out = run(signup, label, "recipients",
                    "isa@example.com:zh\nparent@example.com:fi\n\nyes\n")

    assert code == 0 and "only the order changes" in out
    assert [r["address"] for r in config(path)["email"]["to"]] == ["isa@example.com",
                                                                  "parent@example.com"]


@pytest.mark.parametrize("answer", ["no\n", ""])
def test_recipients_without_yes_write_nothing(signup, answer):
    label, path = made(signup)
    before = path.read_text()

    code, out = run(signup, label, "recipients", f"other@example.com:en\n\n{answer}")

    assert code == 1 and "Not written" in out
    assert path.read_text() == before


@pytest.mark.parametrize("typed", ["not-an-address\n\nyes\n", "a@example.com:sv\n\nyes\n",
                                   "a@example.com\na@example.com\n\nyes\n", "\nyes\n"])
def test_recipients_that_won_t_do_write_nothing(signup, typed):
    label, path = made(signup)
    before = path.read_text()

    code, _ = run(signup, label, "recipients", typed)

    assert code == 1 and path.read_text() == before


# ── myclub

def test_myclub_sets_a_link_checked_once_keeps_an_empty_one_and_turns_off_none(
        signup, mia_plays, capsys):
    label, path = made(signup)
    data = config(path)
    data["kids"][1]["myclub_ical_url"] = LEO_LINK
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    before = config(path)

    code, out = run(signup, label, "myclub", f" {MIA_LINK} \n\nnone\nyes\n")

    assert code == 0, out
    assert mia_plays.gets == [MIA_LINK.replace("webcal://", "https://")]
    after = config(path)
    mia, leo = before["kids"]
    assert after == {**before, "kids": [{**mia, "myclub_ical_url": MIA_LINK},
                                        {k: v for k, v in leo.items() if k != "myclub_ical_url"}]}
    healthy(signup, capsys)

    # Left empty, each keeps what it has.
    code, out = run(signup, label, "myclub", "\n\n")
    assert code == 0 and "nothing to change" in out
    assert config(path) == after


def test_myclub_takes_a_kid_s_links_of_several_clubs_and_show_lists_them(
        signup, harness, mia_plays, capsys):
    """A Kid in several clubs has a link from each (#810): given separated by spaces or one a line,
    each checked once, the same one twice kept once."""
    swim, gym = (MIA_LINK.replace("kilonpallo", club) for club in ("uimaseura", "voimistelu"))
    harness.config["kids"][0]["myclub_ical_url"] = [MIA_LINK, swim, gym]  # served by MyClub
    label, path = made(signup)

    code, out = run(signup, label, "myclub", f"{MIA_LINK} {swim}\n{gym}\n{swim}\n\n\nyes\n")

    assert code == 0, out
    assert sorted(mia_plays.gets) == sorted(u.replace("webcal://", "https://")
                                            for u in (MIA_LINK, swim, gym))
    assert config(path)["kids"][0]["myclub_ical_url"] == [MIA_LINK, swim, gym]
    code, out = run(signup, label, "show")
    lines = out.splitlines()
    start = lines.index(next(line for line in lines if line.startswith("  Mia Virtanen"))) + 1
    assert lines[start:start + 3] == [f"    MyClub: {url}" for url in (MIA_LINK, swim, gym)]
    healthy(signup, capsys)

    # Another Kid can't have one of them; nothing is written then.
    before = path.read_text()
    code, out = run(signup, label, "myclub", f"\n{gym}\n\nyes\n")
    assert code == 1 and "same-link" in out and path.read_text() == before


def test_myclub_refuses_a_link_that_doesn_t_download_and_writes_nothing(
        signup, mia_plays):
    label, path = made(signup)
    before = path.read_text()
    mia_plays.failures.append(404)

    code, out = run(signup, label, "myclub", f"{MIA_LINK}\n\n\nyes\n")

    assert code == 1 and "link-failed" in out
    assert path.read_text() == before


def test_myclub_without_yes_writes_nothing(signup, mia_plays):
    label, path = made(signup)
    before = path.read_text()

    code, out = run(signup, label, "myclub", f"{MIA_LINK}\n\n\nno\n")

    assert code == 1 and "Not written" in out
    assert path.read_text() == before
