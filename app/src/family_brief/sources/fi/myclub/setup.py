"""MyClub's setup step, `setup myclub`, and connecting MyClub in setup: checking a Kid's calendar
links, one per club (#810), and saving them in the config. `register` adds the command to setup's,
which lists it among the Sources' (setup_steps).
`setup myclub` and the setup page's own field both save through this (ADR 0007), so both give the
same results; the hosted sign-up and `signup myclub` check links with `check_links`, all of them
reaching this module through MyClub's record (`Source.code`). The link carries the Kid's personal
token, so it's handled like the other secrets: the result and any error name only MyClub's server
and the HTTP status, never the link."""
from __future__ import annotations

import argparse
import os
import shlex
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .... import secret_dialog
from ....config import Config
from ....setup_report import program, report
from ....system import desktop
from . import collector as myclub
from .config import myclub_links

# MyClub's New calendar subscription form. Signed out, MyClub shows its sign-in page instead, and
# after signing in its front page, so the steps say to open this again.
MYCLUB_URL = "https://id.myclub.fi/flow/calendar_subscriptions/new"


def save_link(path: Path, kid: str, url: str,
              saving: AbstractContextManager[Any] = nullcontext(),
              another: bool = False) -> tuple[str, dict[str, Any]]:
    """Checks the MyClub calendar link `url` by downloading it once and saves it as `kid`'s in
    the config at `path`, in place of the links they have, or `another` beside them, for another
    club (#810). It holds `saving` only while it writes, and returns the step's result with what
    else it reports: the events found, or an error that names only MyClub's server, the HTTP
    status or the config, never the link."""
    url = "".join(url.split())
    if not _is_myclub_link(url):
        # Not downloaded: it's most likely the family's MyClub password, or another page's address.
        return "not-a-myclub-link", {}
    links = _links(path)
    other = next((name for name, theirs in links.items()
                  if name != kid and any(same_link(url, link) for link in theirs)), None)
    if other is not None:
        # One link for two Kids would write every event twice, under both names (#209).
        return "same-link", {"other_kid": other}
    if another and any(same_link(url, link) for link in links.get(kid, [])):
        return "already-saved", {}
    result, extra = check_link(url)
    if result != "checked":
        return result, extra
    try:
        with saving:
            kept = _links(path).get(kid, []) if another else []
            myclub.save_links(path, kid, [*kept, url])
    except (OSError, ValueError) as e:  # names the Kid and the config, never the link
        return "save-failed", {"error": str(e)[:300]}
    return "saved", extra


def remove_link(path: Path, kid: str, index: int,
                saving: AbstractContextManager[Any] = nullcontext()) -> str:
    """Removes the `index`th (from 0) of `kid`'s MyClub links from the config at `path`:
    `removed`, `no-such-link`, or `remove-failed`."""
    try:
        with saving:
            links = _links(path).get(kid, [])
            if not 0 <= index < len(links):
                return "no-such-link"
            myclub.save_links(path, kid, links[:index] + links[index + 1:])
    except (OSError, ValueError):  # names the Kid and the config, never the link
        return "remove-failed"
    return "removed"


def check_links(links: dict[str, list[str]], others: dict[str, list[str]] | None = None
                ) -> tuple[str, str | None, dict[str, list[str]]]:
    """Checks each Kid's MyClub links, as the sign-up and `signup myclub` take them: `checked`,
    no Kid and the links, each once, with their spaces taken out and the empty ones left out;
    or the first link's result that won't do, in `save_link`'s, with its Kid and no links. A
    link given twice for one Kid counts once; for two Kids it's `same-link` (#209), and so is one
    of `others`, the links other Kids keep, which aren't downloaded again."""
    kept: dict[str, list[str]] = {}
    taken = [link for kid, theirs in (others or {}).items() if kid not in links for link in theirs]
    for kid, given in links.items():
        kept[kid] = []
        for url in ("".join(u.split()) for u in given):
            if not url or any(same_link(url, link) for link in kept[kid]):
                continue
            if any(same_link(url, link) for other in [*kept.values(), taken] for link in other):
                return "same-link", kid, {}
            result, _ = check_link(url)
            if result != "checked":
                return result, kid, {}
            kept[kid].append(url)
    return "checked", None, kept


def check_link(url: str) -> tuple[str, dict[str, Any]]:
    """Checks the MyClub calendar link `url` by downloading it once, without saving it: `checked`
    with the events found, or why not, in `save_link`'s results. The hosted sign-up checks a
    Kid's link before their Household exists (ADR 0031)."""
    url = "".join(url.split())
    if not _is_myclub_link(url):
        return "not-a-myclub-link", {}
    try:
        text = myclub.download(url)
    except myclub.FetchError as e:  # names only the server and the HTTP status
        return "link-failed", {"error": str(e)}
    if "BEGIN:VCALENDAR" not in text:
        return "not-a-calendar", {}
    return "checked", {"events": text.count("BEGIN:VEVENT")}


def same_link(a: str, b: str) -> bool:
    """Whether two MyClub links are one calendar, as a link made with several members ticked is
    for each of them (#207)."""
    def plain(url: str) -> str:
        return myclub._normalize_url("".join(url.split())).rstrip("/")
    return plain(a) == plain(b)


def _links(path: Path) -> dict[str, list[str]]:
    """Each Kid's MyClub links in the config at `path`, by name; none when it can't be read."""
    import yaml

    try:
        kids = (yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}).get("kids") or []
    except (OSError, yaml.YAMLError, AttributeError):
        return {}
    return {k["name"]: myclub_links(k.get("myclub_ical_url")) for k in kids
            if isinstance(k, dict) and isinstance(k.get("name"), str)}


def _is_myclub_link(url: str) -> bool:
    parts = urlparse(url)
    host = parts.hostname or ""
    return (parts.scheme in ("webcal", "https") and (host == "myclub.fi" or host.endswith(".myclub.fi"))
            and bool(parts.path.strip("/")))


def register(steps: Any) -> None:
    """Adds `setup myclub` to setup's commands, `steps`."""
    step = steps.add_parser("myclub", help="Ask for a Kid's MyClub calendar link in a dialog, "
                            "download it once and save it in the config")
    step.add_argument("--kid", default=None, help="The Kid's name, exactly as under kids: in the "
                      "config")
    step.add_argument("--another", action="store_true",
                      help="Add the link beside the Kid's links, for another club, instead of in "
                           "place of them")
    step.add_argument("--no-open", action="store_true",
                      help="Don't open MyClub's page again, only ask for the link")
    step.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """`setup myclub`: asks for the link in a dialog, or a hidden Terminal prompt, and saves it
    with `save_link`."""
    path = Path(os.path.expanduser(os.path.expandvars(args.config or "~/.family/config.yaml")))
    try:
        kids = [k.name for k in Config.load(path).kids]
    except FileNotFoundError:
        return report("no-config", "The config isn't written yet. Write it with the Kids first, "
                      "then run this again.")
    except Exception:  # not its error: a config check quotes values, which can be other links
        return report("bad-config", f"The config at {path} can't be read. Run {program()} "
                      "doctor, fix what it names, then run this again.")
    if args.kid not in kids:
        return report("no-kid" if args.kid is None else "no-such-kid", "Run it again with --kid "
                      f"and one of the Kids' names: {program()} setup myclub --kid NAME",
                      kids=kids)
    again = (f"{program()} setup myclub --kid {shlex.quote(args.kid)}"
             + (" --another" if args.another else ""))
    if not args.no_open:
        desktop.open_url(MYCLUB_URL)

    try:
        url = secret_dialog.ask(
            f"Paste {args.kid}'s MyClub calendar link.\n\nIn MyClub's New calendar subscription "
            f"form ({MYCLUB_URL}, after signing in), type any name, choose Selected "
            f"member accounts (Valitut jäsentilit) with only {args.kid} ticked, leave Indication on "
            "All, press Create, then Copy link, and copy the link starting with webcal://.")
    except secret_dialog.Cancelled:
        return report("cancelled", "The family closed the dialog. Run this again when they're "
                      f"ready: {again}")
    except secret_dialog.NoWayToAsk:
        return report("no-prompt", "No dialog or Terminal prompt could be shown here. The family "
                      f"runs this in Terminal: {again} --no-open")

    result, extra = save_link(path, args.kid, url, another=args.another)
    next_ = {
        "not-a-myclub-link": "That wasn't a MyClub calendar link. Copy it with Copy link "
                             "(Kopioi linkki) on MyClub's Calendar subscriptions page, starting "
                             f"with webcal://, and run this again: {again} --no-open",
        "link-failed": "The link didn't open, so it wasn't saved. Copy it again from MyClub and "
                       f"run this again: {again} --no-open",
        "not-a-calendar": "The link opened a page, not a calendar, so it wasn't saved. Copy it "
                          "with Copy link (Kopioi linkki) on MyClub's Calendar subscriptions "
                          f"page, not from the browser's address bar: {again} --no-open",
        "save-failed": "The link works, but it couldn't be saved in the config (see error).",
        "already-saved": f"That link is already saved for {args.kid}, so nothing changed. For "
                         f"another club, copy the link from that club's MyClub: {again} --no-open",
        "same-link": f"That link is already saved for {extra.get('other_kid')}, so it wasn't saved "
                     f"again. Each Kid needs their own link: make a new calendar subscription, "
                     f"choose Valitut jäsentilit, tick only {args.kid}, and copy that link: "
                     f"{again} --no-open",
    }.get(result)
    return report(result, next_, **({"kid": args.kid} if result == "saved" else {}), **extra)
