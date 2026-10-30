"""Operational commands used during onboarding and troubleshooting.

doctor   — check every configured connection; prints status + counts only, never message content
discover — list candidate Gmail sender domains / Wilma students
schedule — install, remove or inspect the evening jobs
bg       — run any of the above (or run/collect) as a one-off job with the evening job's
           permissions
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

from . import (baileys_install, google_packages, languages, own_node, private_files, secret_dialog,
               sources)
from .config import Config
from .system import jobs, profile, wake, windows_only

OK, WARN, FAIL = "✅", "⚠️ ", "❌"
_STATUS_OF = {"ok": OK, "warn": WARN, "fail": FAIL}  # by llm.Finding's level
DEFAULT_CONFIG = Path.home() / ".family" / "config.yaml"


# Each of doctor's checks: its key, which the setup page goes by and stays the same whatever the
# check's words, and the item doctor prints, with `{}` for the subject it's about. A Source's are
# in its record.
CHECK_ITEMS = (
    ("config", "Config file"), ("recipients", "Recipients"), ("language", "Language {}"),
    ("gateway-sends", "Brief sending"),
    ("claude", "Claude"), ("codex", "Codex"),
    ("gateway", "AI gateway"), ("gateway-key", "Gateway Key"),
    *(item for s in sources.ALL for item in s.doctor_items().items()),
    ("whatsapp-library", "WhatsApp library"),
    ("calendar", "Calendar"), ("feedback", "Pilot feedback"), ("weekend", "Weekend Picks"),
    ("schedule", "Schedule"), ("last-run", "Last run"),
    ("wake", "Wake"), ("sign-in-after-update", "Sign-in after updates"),
)
_ITEMS = dict(CHECK_ITEMS)


def _item(key: str) -> str:
    """The item doctor prints for the check `key`, also of a Source registered after this module
    was imported."""
    if key in _ITEMS:
        return _ITEMS[key]
    return next(items[key] for s in sources.ALL if key in (items := s.doctor_items()))


class Check(NamedTuple):
    """One of doctor's checks. doctor prints its status, item and detail; `key` names the check
    and `subject` what it's about, such as a language or a Kid. A check that isn't OK can also have
    a `reason`, which the setup page has its own text for, and `groups` names the groups that
    reason is about."""
    status: str
    key: str
    item: str
    detail: str
    reason: str | None = None
    groups: tuple[str, ...] = ()
    subject: str | None = None


def register(sub) -> None:
    pd = sub.add_parser("doctor", help="Check all configured connections (no message content shown)")
    pd.add_argument("--skip-llm", action="store_true", help="Don't make a test call to Claude / Codex")
    pd.set_defaults(func=cmd_doctor)

    pdis = sub.add_parser("discover", help="List candidates to put in config during onboarding")
    pdis.add_argument("what", choices=["gmail-senders", "wilma-students"])
    pdis.add_argument("--days", type=int, default=None)
    pdis.add_argument("--json", action="store_true", help="gmail-senders only: one line of JSON, "
                      "each sender domain with whether it looks like school, city or club mail")
    pdis.set_defaults(func=cmd_discover)

    psch = sub.add_parser("schedule", help="Install / remove / inspect the scheduled jobs, or start "
                          "one now with run-now")
    psch.add_argument("action", choices=["install", "uninstall", "status", "run-now"])
    psch.add_argument("--weekend", action="store_true",
                      help="With run-now: start the Weekend Picks job instead of the evening one")
    psch.add_argument("--replace-wake", action="store_true",
                      help="With install: replace the Mac's other repeating wake schedule")
    psch.epilog = ("run-now asks the system's scheduler (launchd on macOS, Task Scheduler on "
                   "Windows) to start the installed job right now, so it runs exactly "
                   "as the scheduler would. A Brief it sends goes to every Recipient, like the "
                   "evening one.")
    psch.set_defaults(func=cmd_schedule)

    pbg = sub.add_parser("bg", help="Run a parent-recap command as a one-off launchd job "
                                    "(same Python and macOS permissions as the scheduled job)")
    pbg.add_argument("--timeout", type=int, default=900, help="Give up after this many seconds")
    pbg.add_argument("command", nargs=argparse.REMAINDER, help="e.g. doctor")
    pbg.set_defaults(func=cmd_bg)


# ---------------------------------------------------------------- doctor

def cmd_doctor(args: argparse.Namespace) -> int:
    results = health_checks(args.config, skip_llm=args.skip_llm, show=True)
    fails = sum(1 for c in results if c.status == FAIL)
    warns = sum(1 for c in results if c.status == WARN)
    print(f"\n{len(results)} checks: {len(results) - fails - warns} OK, {warns} warnings, "
          f"{fails} to fix")
    return 1 if fails else 0


def health_checks(config: str | None, skip_llm: bool = False, show: bool = False,
                  schedule: bool = True) -> list[Check]:
    """Doctor's checks. With `show`, each is printed as it's done. Without `schedule`, the
    scheduled job's checks are left out, for before it's installed."""
    results: list[Check] = []

    def add(status: str, key: str, detail: str, reason: str | None = None,
            groups: list[str] | tuple[str, ...] = (), subject: str | None = None) -> None:
        item = _item(key).format(subject)
        results.append(Check(status, key, item, detail, reason, tuple(groups), subject))
        if show:
            print(f"{status} {item}: {detail}", flush=True)

    cfg_path = Path(config).expanduser() if config else DEFAULT_CONFIG
    try:
        cfg = Config.load(config)
    except Exception as e:
        add(FAIL, "config", f"couldn't read {cfg_path}: {e}")
        return results
    add(OK, "config", f"{cfg_path}, {len(cfg.kids)} kids, "
        f"Brief goes to {', '.join(r.address for r in cfg.email.to) or '(no recipients set)'}")
    if not cfg.email.to:
        add(FAIL, "recipients", "email.to is empty, so nobody gets the Brief")
    weekend = cfg.weekend_events.enabled
    for language in dict.fromkeys([*cfg.brief_languages(), *(cfg.weekend_languages() if weekend else [])]):
        ready, detail = languages.describe(cfg, language, languages.TABLES if weekend else (languages.BRIEF,))
        add(OK if ready else WARN, "language", detail, subject=language)

    # The Brief is sent through the gateway, whatever the AI (ADR 0032), then the AI writes it,
    # then the Sources it's written from.
    if cfg.email.enabled:
        _check_gateway_sends(cfg, add)
    if not skip_llm:
        _check_llm(cfg, add)
    for source in sources.in_connect_order():
        _check_source(source, cfg, add, config)
    if sources.WHATSAPP.available():
        _check_whatsapp_library(add)
    _check_calendar(cfg, add)
    if cfg.feedback.enabled:
        _check_feedback(cfg, add)
    if cfg.weekend_events.enabled:
        _check_weekend(add)
    if schedule:
        _check_schedule(cfg, add)
        if windows_only.offers("wake-timers"):
            _check_wake_timers(cfg, add)
        if windows_only.offers("sign-in-after-update"):
            _check_sign_in_after_update(add)
    return results


def _check_source(source: sources.Source, cfg: Config, add, config: str | None) -> None:
    if source.check:
        source.check(cfg, add, config)


def check_gmail(cfg: Config, add, config: str | None = None) -> None:
    """Gmail, when it's read: it never sends the Brief (ADR 0032), so a Gmail with no sender
    ticked isn't used and isn't checked."""
    if not sources.GMAIL.connected(cfg):
        return
    from .collectors import gmail
    username = gmail.username_of(cfg)
    if not username:
        add(FAIL, "gmail", "no gmail.username in the config")
        return
    from .system import secrets
    try:
        password = gmail.get_app_password(username)
    except secrets.SecretsError as e:
        if e.reason == "needs-prompt":
            add(FAIL, "gmail", f"the App Password for {username} is in the Keychain, but macOS "
                f"asks for the Keychain password before reading it, which the evening Brief can't "
                "answer (store it again with parent-recap setup gmail, or the setup page's Gmail "
                "step)")
        else:
            add(FAIL, "gmail", f"couldn't read the App Password for {username} from the Keychain: {e}")
        return
    if not password:
        add(FAIL, "gmail", f"No App Password in the Keychain for {username} "
            "(store one with parent-recap setup gmail)")
        return
    try:
        n = gmail.count_matching(cfg)
        # Gmail's search only filters by whole days; run() trims to lookback_hours afterwards.
        days = -(-cfg.gmail.lookback_hours // 24)
        add(OK, "gmail", f"{username} signed in, {n} allowlisted emails in the last {days} days"
            f" (the Brief only takes the last {cfg.gmail.lookback_hours} hours of them)")
    except Exception as e:
        add(FAIL, "gmail", f"IMAP login failed: {e} "
            "(the App Password may have been revoked; create a new one and store it again)")


def _check_gateway_sends(cfg: Config, add) -> None:
    """The gateway's Brief relay, which sends every Brief (ADR 0028, ADR 0032): whether it answers
    and takes the Gateway Key, from one request it refuses once it has checked the key, so nothing
    is sent. A Household that sent through its own Gmail before has no key until the Parent Recap
    team gives it one: `no-key`, which the setup page says as its own."""
    from . import gateway as api
    url = cfg.llm.gateway_url
    try:
        key = api.key()
    except api.NoKey as e:
        add(FAIL, "gateway-sends", f"{e}, so the evening Brief can't be sent: Parent Recap sends "
            "every Brief from its own address", reason="no-key" if e.missing else None)
        return
    outcome, detail = api.send_test(url, key)
    if outcome == api.WORKS:
        add(OK, "gateway-sends", f"the gateway at {url} sends the Brief from Parent Recap's address, "
            "and takes the Gateway Key")
    elif outcome == api.NO_ANSWER:
        add(FAIL, "gateway-sends", f"no answer from {url}: {detail} (check the computer is online "
            "and llm.gateway_url in the config)")
    elif outcome == api.UNKNOWN:
        add(FAIL, "gateway-sends", "the gateway doesn't know this Gateway Key, or it was revoked, so "
            "it won't send the Brief (ask the Parent Recap maintainers for a new one and store it "
            f"with {api.SETUP})")
    else:
        add(FAIL, "gateway-sends", f"the gateway at {url} couldn't be asked to send the Brief: {detail} "
            "(if it happens again, tell the Parent Recap maintainers)")


def _check_llm(cfg: Config, add) -> None:
    from . import llm
    for finding in llm.backend(cfg).check():
        add(_STATUS_OF[finding.level], finding.key, finding.detail)


def check_wilma(cfg: Config, add, config: str | None = None) -> None:
    if not cfg.wilma.enabled:
        return
    from .collectors.wilma import NOT_INSTALLED, _run_or_log
    if own_node.wilma() is None:
        add(FAIL, "wilma", NOT_INSTALLED)
        return
    data = _run_or_log(["kids", "list"])
    if data is None:
        add(FAIL, "wilma", "wilma kids list failed: not signed in yet, or the password changed "
            "(sign in again: parent-recap setup wilma)")
        return
    items = data if isinstance(data, list) else (data.get("kids") or data.get("students") or [])
    names = [str(k.get("name") or (k.get("student") or {}).get("name") or "?") for k in items]
    configured = {k.name for k in cfg.kids}
    missing = [n for n in names if n not in configured]
    detail = f"signed in, students in Wilma: {', '.join(names) or '(none)'}"
    if missing:
        add(WARN, "wilma", detail + f"; {', '.join(missing)} don't match any Kid name in the config, "
            "so their messages may go to the wrong Kid")
    else:
        add(OK, "wilma", detail)
    # From 2.0 the CLI signs in with every saved profile, so a stale one fails every evening. The
    # evening run doesn't put that in the Brief when the profile is for none of the Kids.
    from . import setup_wilma
    signins = setup_wilma.profiles(setup_wilma.config_path())
    kids = {k.name.casefold() for k in cfg.kids}
    for p in signins if len(signins) > 1 else []:
        if not setup_wilma.for_the_household(p, kids):
            add(WARN, "wilma", f"the wilma CLI also signs in as {p.get('username')} at "
                f"{p.get('tenantName') or p.get('tenantUrl')} every evening, a sign-in setup didn't "
                "make, for none of the Kids, so the Brief doesn't say when it fails. If it's no "
                f"longer used, remove it: wilma accounts remove {p.get('username')}")


def check_myclub(cfg: Config, add, config: str | None = None) -> None:
    """Each Kid's subscription link, since each Kid can have a club of their own."""
    for kid in cfg.kids:
        if kid.myclub_ical_url:
            _check_myclub(kid.name, kid.myclub_ical_url, add)


def _check_myclub(kid: str, url: str, add) -> None:
    from .collectors import myclub
    try:
        text = myclub.download(url)
        n = text.count("BEGIN:VEVENT")
        add(OK, "myclub", f"subscription link works, {n} events in the calendar", subject=kid)
    except Exception as e:
        add(FAIL, "myclub", f"subscription link doesn't open: {e} (save a new one with "
            f"parent-recap setup myclub --kid {shlex.quote(kid)})", subject=kid)


def check_whatsapp(cfg: Config, add, config: str | None = None) -> None:
    """The Linked Device's link, once groups are picked: connected, last connected, or needs a new
    pairing, as the always-running job's status file has it, with the picked groups by name.
    Groups kept picked with WhatsApp turned off say it's off."""
    if not cfg.whatsapp.chats:
        return
    if not cfg.whatsapp.enabled:
        add(OK, "whatsapp", "off, so the Brief has no WhatsApp messages (turn it on again on the "
            "setup page)")
        return
    from . import linked_device
    from .collectors import whatsapp

    def when(moment: datetime) -> str:
        return f"{moment.astimezone(ZoneInfo(cfg.timezone)):%Y-%m-%d %H:%M}"
    link = linked_device.link()
    if not whatsapp.connected(cfg):
        why = f"WhatsApp ended the link on {when(link.ended_at)}" if link.ended_at else \
            "the picked groups have no pairing with WhatsApp yet"
        add(FAIL, "whatsapp", f"needs a new pairing: {why}, so the Brief has no WhatsApp messages "
            "(pair again on the setup page)")
        return
    # By name, so the family knows which groups to post a test message in (#548).
    names = [f'"{c.name}"' for c in cfg.whatsapp.chats if c.id]
    picked = f"{len(names)} picked group{'' if len(names) == 1 else 's'}: {', '.join(names)}"
    if link.connected_since:
        add(OK, "whatsapp", f"connected since {when(link.connected_since)}, keeping {picked}")
    elif link.last_connected:
        add(WARN, "whatsapp", f"not connected right now, last connected {when(link.last_connected)}, "
            f"for {picked}. It connects again by itself while you're signed in")
    else:
        add(WARN, "whatsapp", f"paired, but not connected yet, for {picked}. It connects once "
            "you're signed in")


def _check_whatsapp_library(add) -> None:
    """The Baileys tree the install puts in place for the Linked Device (ADR 0025). A warning, not
    a failure, when it isn't the pinned one: WhatsApp is one Source, and the next install tries
    again."""
    pinned = baileys_install.BAILEYS_VERSION
    version = baileys_install.installed_version()
    again = " (run the install again)"
    if baileys_install.up_to_date():
        add(OK, "whatsapp-library", f"installed, the pinned version {pinned}")
    elif version is None:
        add(WARN, "whatsapp-library", "not installed, so WhatsApp can't be read as a Linked "
            "Device" + again)
    elif version != pinned:
        add(WARN, "whatsapp-library", f"version {version}, not the pinned {pinned}" + again)
    else:
        add(WARN, "whatsapp-library", f"version {version}, but not the pinned tree" + again)


def _check_calendar(cfg: Config, add) -> None:
    from .actions import calendar as cal
    mode = cfg.google_calendar.mode
    if mode == "off":
        add(OK, "calendar", "off, email only")
    elif mode == "ics":
        add(OK, "calendar", "ics mode: new events come as an .ics attachment on the Brief email, "
            "one tap adds them to your calendar")
    else:
        if not google_packages.installed():
            add(FAIL, "calendar", "google mode, but Google's packages for it aren't installed "
                "(run the plugin's install.sh again, which installs them for google mode)")
            return
        if not cal.is_configured():
            add(FAIL, "calendar", "google mode, but not authorized yet "
                "(run scripts/setup_google_calendar.py)")
            return
        try:
            n = len(cal.get_upcoming_events(cfg, 7))
            invite = cfg.google_calendar.invite_attendees
            add(OK, "calendar", f"Google Calendar authorization works, {n} events in the next 7 days"
                + (f"; new events invite {', '.join(invite)}" if invite else ""))
        except Exception as e:
            msg = str(e)
            if "invalid_grant" in msg or "expired" in msg or "revoked" in msg:
                add(FAIL, "calendar", "Google Calendar authorization expired or was revoked; "
                    "run scripts/setup_google_calendar.py again")
            else:
                add(FAIL, "calendar", f"Google Calendar call failed: {msg[:120]}")


def _check_feedback(cfg: Config, add) -> None:
    fb = cfg.feedback
    if not fb.active():
        add(WARN, "feedback", "feedback is on but prefill_base_url or fields is missing, so the "
            "Brief has no ⭐/❌ links (turn it on again with parent-recap setup save <<< "
            "'{\"feedback\": {\"enabled\": true}}', which writes the pilot form this version ships)")
    elif not fb.household_label:
        # One Form serves every pilot Household; without a label their rows can't be told apart.
        add(WARN, "feedback", "feedback.household_label is empty, so the feedback sheet "
            "can't tell which Household a row came from (set one with parent-recap setup save)")
    else:
        add(OK, "feedback", f"the Brief has ⭐/❌ links, Household label {fb.household_label}")


def _check_weekend(add) -> None:
    import requests
    try:
        r = requests.get("https://api.hel.fi/linkedevents/v1/event/?page_size=1", timeout=15)
        r.raise_for_status()
        add(OK, "weekend", "Linked Events API is reachable")
    except Exception as e:
        add(FAIL, "weekend", f"Linked Events API isn't reachable: {e}")


def _check_schedule(cfg: Config, add) -> None:
    from .state import State
    installed, waiting = jobs.installed(), jobs.waiting_for_login()
    wanted = [jobs.DAILY] + ([jobs.WEEKEND] if cfg.weekend_events.enabled else [])
    missing = [j for j in wanted if j not in installed]
    unloaded = [jobs.system_name(j) for j in missing if j in waiting]
    uninstalled = [jobs.system_name(j) for j in missing if j not in waiting]
    if uninstalled:
        add(WARN, "schedule", f"not installed yet: {', '.join(uninstalled)} "
            "(run parent-recap schedule install)")
    if unloaded:
        add(WARN, "schedule", f"installed but not loaded for this login: {', '.join(unloaded)}. "
            "The evening job only runs while you're logged in to the Mac (an SSH login doesn't "
            "count, so over SSH this is expected): after a restart, log in once or turn on "
            "automatic login, see \"No Brief after the Mac restarted\" in docs/troubleshooting.md")
    if not missing:
        add(OK, "schedule", f"runs every day at {cfg.schedule.daily_hour:02d}:{cfg.schedule.daily_minute:02d}"
            + (", Weekend Picks once every Friday" if cfg.weekend_events.enabled else ""))
    always = jobs.always_status()
    if always.installed:
        name = jobs.system_name(jobs.ALWAYS)
        if always.running:
            add(OK, "schedule", f"{name} runs all the time while you're signed in")
        else:
            add(WARN, "schedule", f"installed but not running: {name}. It starts again when you "
                "sign in, unless WhatsApp ended its link")
    last = State(cfg.resolved_state_path()).last_run_at
    if last:
        hours = (datetime.now(timezone.utc) - last).total_seconds() / 3600
        status = OK if hours < 30 else WARN
        why = profile.current().missed_run
        add(status, "last-run", f"{hours:.0f} hours ago" + ("" if hours < 30 else
            f" (no run for over a day; {why})"))


def _wake_timers(result: str, hour: int, minute: int) -> tuple[str, str]:
    """How the PC's wake stands on Windows, where the evening job's task wakes it once Allow wake
    timers lets it (ADR 0021): a status and its words, from `wake.set`'s result."""
    if result == "never-sleeps":
        return OK, "this PC never sleeps, so it needs no wake-up"
    if result == "already-set":
        return OK, "Allow wake timers is on, so the evening job wakes the PC from sleep (leave it plugged in)"
    return WARN, ("Allow wake timers is off, so the PC doesn't wake from sleep for the Brief, "
                  "which then only comes on evenings it's awake. To turn it on, "
                  f"{wake.manual_step(hour, minute)}")


def _check_wake_timers(cfg: Config, add) -> None:
    hour, minute = cfg.schedule.daily_hour, cfg.schedule.daily_minute
    # On Windows `set` only reads the family's own setting, and changes nothing.
    status, words = _wake_timers(wake.set(hour, minute, False), hour, minute)
    add(status, "wake", words)


def _check_sign_in_after_update(add) -> None:
    """The evening job runs only while the family is signed in (ADR 0021), and nobody is after
    Windows restarts for an update unless it signs them back in."""
    key, setting = "sign-in-after-update", windows_only.SIGN_IN_SETTING
    on = windows_only.signs_back_in_after_update()
    if on:
        add(OK, key, "Windows signs you back in after it restarts for an update, so the "
            "evening run still happens")
    elif on is False:
        add(WARN, key, "after Windows restarts for an update nobody is signed in, so that "
            f"night's Brief waits until someone signs in. Turn on {setting}")
    else:
        add(WARN, key, "couldn't tell whether Windows signs you back in after it restarts for an "
            "update. If it doesn't, that night's Brief waits until someone signs in: check that "
            f"this is on: {setting}")


# ---------------------------------------------------------------- discover

def cmd_discover(args: argparse.Namespace) -> int:
    if args.what == "wilma-students":
        # Needs no config: setup runs it before the household step, to prefill the Kids.
        from .collectors.wilma import _run_or_log
        data = _run_or_log(["kids", "list"])
        print(json.dumps(data, ensure_ascii=False, indent=2) if data is not None
              else f"{FAIL} not signed in to wilma")
        return 0 if data is not None else 1

    days = args.days or 60
    if args.json:
        return _discover_senders_json(args.config, days)
    cfg = Config.load(args.config)
    from .collectors import gmail
    print(f"Reading the senders of the last {days} days of mail. This usually takes 1 to 2 "
          "minutes; the count below goes up as it reads.", flush=True)
    rows = gmail.sender_domains(
        cfg, days=days, progress=lambda done, total: print(f"  read {done} of {total} senders",
                                                           flush=True))
    print(f"Sender domains in the last {days} days (only senders were read, no message bodies), "
          "most first:")
    for dom, n, ex in rows[:60]:
        print(f"  {n:>4}  {dom:<32} e.g. {ex}")
    return 0


# Mail services anyone has an address at. Allowlisting one as a domain would read the family's
# private mail, so they're never ticked: a teacher writing from one goes in allowlist_senders.
PUBLIC_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "hotmail.fi", "live.com",
    "live.fi", "msn.com", "icloud.com", "me.com", "mac.com", "yahoo.com", "yahoo.fi", "aol.com",
    "proton.me", "protonmail.com", "gmx.com", "gmx.net", "luukku.com", "suomi24.fi", "elisanet.fi",
    "kolumbus.fi", "saunalahti.fi", "welho.com", "qq.com", "163.com", "126.com", "foxmail.com",
})
# Senders families get school and club mail from wherever they live: MyClub's own mail, Wilma's,
# and Espoo's music institute, which docs/config.md names.
KNOWN_SENDERS = ("myclub.fi", "inschool.fi", "emo.fi")
# Words in a domain that make it a school's or a club's: koulu and skola are school, lukio upper
# secondary, opisto an institute such as a music school, kerho a club and seura a sports club.
SCHOOL_AND_CLUB_WORDS = ("koulu", "school", "skola", "lukio", "opisto", "kerho", "seura", "club")


def gmail_senders(cfg: Config, days: int, progress=None) -> list[dict]:
    """The sender domains of the last `days` days of mail, most first, from From: headers only,
    each with whether it looks like school, city or club mail (`likely`) and, for a public one,
    `public`. `discover gmail-senders --json` and the setup page's check page both use this."""
    from .collectors import gmail
    out = []
    for domain, count, example in gmail.sender_domains(cfg, days=days, progress=progress):
        public = domain in PUBLIC_DOMAINS
        out.append({"domain": domain, "count": count, "example": example,
                    "likely": not public and likely_sender(domain, cfg),
                    **({"public": True} if public else {})})
    return out


def likely_sender(domain: str, cfg: Config) -> bool:
    """Whether mail from `domain` looks like the Kids' school's, the city's or a club's, and
    isn't the mail of a Source that's connected."""
    if domain in PUBLIC_DOMAINS:
        return False
    # A connected Source's own mail: reading it too would put the same news in the Brief twice (#361).
    own_mail = [s.sender_domain for s in sources.ALL if s.sender_domain and s.connected(cfg)]
    if any(_within(domain, d) for d in own_mail):
        return False
    town = city_domain(cfg.city)
    ours = [*KNOWN_SENDERS, *([town] if town else [])]
    if any(_within(domain, d) for d in ours):
        return True
    return any(word in domain.rsplit(".", 1)[0] for word in SCHOOL_AND_CLUB_WORDS)


def _within(domain: str, parent: str) -> bool:
    return domain == parent or domain.endswith("." + parent)


def city_domain(city: str | None) -> str | None:
    """The domain the Household's town's schools likely write from: its preset's, else the town's
    own .fi domain, such as jarvenpaa.fi for Järvenpää."""
    from .setup_gmail import CITY_DOMAINS
    if not city:
        return None
    return CITY_DOMAINS.get(city) or f"{_slug(city)}.fi"


def _slug(city: str) -> str:
    """A town's name as its domain usually spells it: Järvenpää as jarvenpaa."""
    import unicodedata
    plain = unicodedata.normalize("NFKD", city).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")


def _discover_senders_json(config: str | None, days: int) -> int:
    """`discover gmail-senders --json`: one line of JSON on stdout, the count as it reads on
    stderr, so the line stays the only output a caller reads."""
    from .setup_report import report
    try:
        senders = gmail_senders(Config.load(config), days, progress=lambda done, total: print(
            f"  read {done} of {total} senders", file=sys.stderr, flush=True))
    except Exception:  # its error can name the address or the config: doctor says what's wrong
        return report("read-failed", "Gmail's senders couldn't be read. Run parent-recap doctor, "
                      "fix what it names for Gmail, then run this again.")
    return report("read", days=days, senders=senders)


# ---------------------------------------------------------------- bg

def cmd_bg(args: argparse.Namespace) -> int:
    inner = args.command[1:] if args.command[:1] == ["--"] else list(args.command)
    if not inner or inner[0] == "bg":
        print("Usage: parent-recap bg <command>, e.g. parent-recap bg doctor")
        return 2
    print(f"Running parent-recap {' '.join(inner)} as a background job "
          "(same Python and permissions as the scheduled job)", flush=True)
    code, _ = jobs.run_detached(inner, args.timeout, config=args.config)
    return code


# ---------------------------------------------------------------- schedule

def _set_wake(hour: int, minute: int, replace: bool) -> None:
    """`wake.set`, saying how it went. Without a desktop session it gives the command for
    Terminal instead."""
    if not wake.sleeps():
        return
    if windows_only.offers("wake-timers"):
        _say_wake_timers(hour, minute)
        return
    wake_h, wake_m = wake.time(hour, minute)
    existing = wake.status(hour, minute).others
    result = wake.set(hour, minute, replace)
    if result == "never-sleeps":
        print("\nThis Mac never sleeps (sleep 0 in pmset), so it needs no wake schedule.")
        return
    if result == "already-set":
        print(f"\nThe Mac already wakes at {wake_h:02d}:{wake_m:02d} every day, before the job starts.")
        return
    print("\nScheduled jobs don't run while the Mac is asleep.")
    step = wake.manual_step(hour, minute)
    in_terminal = (f"Run this once in Terminal (it asks for your Mac password):\n  {step}"
                   if step else "")
    if result == "other-schedule":
        print(f"{WARN}This Mac already has a repeating wake schedule, and Parent Recap's wake "
              "schedule replaces it:")
        for line in existing:
            print(f"  {line}")
        if secret_dialog.over_ssh():
            print("If something else needs that schedule, skip the command below. The Brief then "
                  f"only comes on nights the Mac is awake at {hour:02d}:{minute:02d}.")
            print(in_terminal)
        else:
            print("If something else needs that schedule, leave it. The Brief then only comes on "
                  f"nights the Mac is awake at {hour:02d}:{minute:02d}. To replace it, run "
                  "parent-recap schedule install --replace-wake, which asks for your Mac "
                  "password in a macOS dialog.")
        return
    if existing:
        print("Replacing this Mac's repeating wake schedule:")
        for line in existing:
            print(f"  {line}")
    if result == "cancelled":
        print("The wake schedule wasn't set: the Mac password dialog was closed. Run "
              "parent-recap schedule install again, or set it in Terminal instead.")
        print(in_terminal)
    elif result == "no-dialog":
        print(in_terminal)
    elif result == "set":
        print(f"{OK} Wake: the Mac wakes at {wake_h:02d}:{wake_m:02d} every day")
    else:
        print(f"{FAIL} The wake schedule wasn't set: macOS doesn't list it after the dialog.")
        print(in_terminal)


def _say_wake_timers(hour: int, minute: int) -> None:
    """`wake.set` on Windows, where the task wakes the PC once the family's own Allow wake timers
    lets it: it is only checked, and the family told where to turn it on."""
    status, words = _wake_timers(wake.set(hour, minute, False), hour, minute)
    print(f"\n{status} Wake: {words}")


def install_jobs(cfg: Config) -> None:
    """Installs the evening job at the config's time, and Weekend Picks' job when they're on."""
    log_dir = cfg.archive.resolved_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    private_files.tighten(cfg)
    sc = cfg.schedule
    wanted = [jobs.Job(jobs.DAILY, "run", None, sc.daily_hour, sc.daily_minute, log_dir,
                       cfg.timezone)]
    if cfg.weekend_events.enabled:
        wanted.append(jobs.Job(jobs.WEEKEND, "weekend-events", sc.weekend_weekday, sc.weekend_hour,
                               0, log_dir, cfg.timezone))
    jobs.install(wanted)
    # So a first evening missed before any run is caught up at sign-in on Windows (#386).
    from . import run_lock
    from .state import State
    try:
        with run_lock.exclusive(cfg):
            state = State(cfg.resolved_state_path())
            state.mark_scheduled_now()
            state.save()
    except run_lock.Busy:  # the run that is going notes its own time
        pass


def _run_now(name: str, log_dir: Path) -> int:
    """Asks the system to start the installed job, which then runs with the job's own Python,
    PATH, environment and log files. Doesn't wait for it."""
    label = jobs.system_name(name)
    logs = log_dir / ("weekend-events" if name == jobs.WEEKEND else "run")
    where = f"Its logs:\n  {logs}-stdout.log\n  {logs}-stderr.log"
    try:
        jobs.run_now(name)
    except jobs.NotInstalled:
        print(f"{FAIL} {label} isn't installed. Run family-brief schedule install first.")
        return 1
    except jobs.AlreadyRunning:
        # Exit 1: the Brief wasn't started by this call, which a script calling it should see.
        print(f"{WARN}{label} is already running, so no second run was started. {where}")
        return 1
    except jobs.NotStarted as e:
        print(f"{FAIL} {e}")
        return 1
    print(f"{OK} Started {label}. It runs in the background, so this doesn't wait for the Brief. "
          f"{where}")
    return 0


def cmd_schedule(args: argparse.Namespace) -> int:
    cfg = Config.load(args.config)
    log_dir = cfg.archive.resolved_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    private_files.tighten(cfg)
    sc = cfg.schedule

    if args.action == "run-now":
        return _run_now(jobs.WEEKEND if args.weekend else jobs.DAILY, log_dir)

    if args.action == "install":
        install_jobs(cfg)
        print(f"{OK} Brief: every day at {sc.daily_hour:02d}:{sc.daily_minute:02d}")
        if cfg.weekend_events.enabled:
            print(f"{OK} Weekend Picks: every Friday at {sc.weekend_hour:02d}:00")
        _set_wake(sc.daily_hour, sc.daily_minute, args.replace_wake)
        return 0

    if args.action == "uninstall":
        jobs.remove_all()
        print(f"{OK} Scheduled jobs removed (config and archive are kept)")
        return 0

    installed = jobs.installed()
    for name in (jobs.DAILY, jobs.WEEKEND):
        label = jobs.system_name(name)
        print(f"{OK if name in installed else '—'} {label}{'' if name in installed else ' (not installed)'}")
    for name in ("run-stderr.log", "weekend-events-stderr.log"):
        f = log_dir / name
        if f.exists():
            tail = f.read_text(errors="replace").splitlines()[-3:]
            print(f"\nLast lines of {name}:")
            for line in tail:
                print("  " + line[:200])
    return 0
