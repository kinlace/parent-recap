"""Operational commands used during onboarding and troubleshooting.

doctor   — check every configured connection; prints status + counts only, never message content
discover — list candidate Gmail sender domains / WhatsApp group names / Wilma students
schedule — install, remove or inspect the launchd jobs
app-management — show the scheduled job's Python in Finder and open App Management, to grant WhatsApp
                 (setup whatsapp does this and waits for the permission)
bg       — run any of the above (or run/collect) as a one-off launchd job, for WhatsApp access
"""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import FrameType
from typing import Callable

from . import (google_packages, install_record, languages, own_node, private_files,
               secret_dialog, tools, whatsapp_python)
from .config import Config

OK, WARN, FAIL = "✅", "⚠️ ", "❌"
DEFAULT_CONFIG = Path.home() / ".family" / "config.yaml"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
JOB_DAILY = "com.parentrecap.daily"
JOB_WEEKEND = "com.parentrecap.weekend-events"
BG_ENV = "PARENT_RECAP_BG"  # set inside `bg` jobs so doctor doesn't recurse
TIMED_OUT = 124  # run_as_job's exit code for a job it stopped, as timeout(1) gives
APP_MANAGEMENT_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_AppBundles"


def register(sub) -> None:
    pd = sub.add_parser("doctor", help="Check all configured connections (no message content shown)")
    pd.add_argument("--skip-llm", action="store_true", help="Don't make a test call to Claude / Codex")
    pd.add_argument("--whatsapp-only", action="store_true", help=argparse.SUPPRESS)
    pd.set_defaults(func=cmd_doctor)

    pdis = sub.add_parser("discover", help="List candidates to put in config during onboarding")
    pdis.add_argument("what", choices=["gmail-senders", "whatsapp-chats", "wilma-students"])
    pdis.add_argument("--days", type=int, default=None)
    pdis.add_argument("--json", action="store_true", help="gmail-senders only: one line of JSON, "
                      "each sender domain with whether it looks like school, city or club mail")
    pdis.set_defaults(func=cmd_discover)

    psch = sub.add_parser("schedule", help="Install / remove / inspect the launchd jobs, or start one "
                          "now with run-now")
    psch.add_argument("action", choices=["install", "uninstall", "status", "run-now"])
    psch.add_argument("--weekend", action="store_true",
                      help="With run-now: start the Weekend Picks job instead of the evening one")
    psch.add_argument("--replace-wake", action="store_true",
                      help="With install: replace the Mac's other repeating wake schedule")
    psch.epilog = ("run-now asks launchd to start the installed job right now, so it runs exactly "
                   "as the scheduler would. A Brief it sends goes to every Recipient, like the "
                   "evening one.")
    psch.set_defaults(func=cmd_schedule)

    pam = sub.add_parser("app-management", help="Show the scheduled job's Python in Finder and open "
                         "App Management, so you can drag it in for WhatsApp")
    pam.set_defaults(func=cmd_app_management)

    pbg = sub.add_parser("bg", help="Run a parent-recap command as a one-off launchd job "
                                    "(same Python and macOS permissions as the scheduled job)")
    pbg.add_argument("--timeout", type=int, default=900, help="Give up after this many seconds")
    pbg.add_argument("command", nargs=argparse.REMAINDER, help="e.g. discover whatsapp-chats")
    pbg.set_defaults(func=cmd_bg)


# ---------------------------------------------------------------- doctor

def cmd_doctor(args: argparse.Namespace) -> int:
    results = health_checks(args.config, skip_llm=args.skip_llm, whatsapp_only=args.whatsapp_only,
                            show=True)
    fails = sum(1 for s, _, _ in results if s == FAIL)
    if not args.whatsapp_only:
        warns = sum(1 for s, _, _ in results if s == WARN)
        print(f"\n{len(results)} checks: {len(results) - fails - warns} OK, {warns} warnings, "
              f"{fails} to fix")
    return 1 if fails else 0


def health_checks(config: str | None, skip_llm: bool = False, whatsapp_only: bool = False,
                  show: bool = False, schedule: bool = True) -> list[tuple[str, str, str]]:
    """Doctor's checks, each (status, item, detail). With `show`, each is printed as it's done.
    Without `schedule`, the scheduled job's checks are left out, for before it's installed."""
    results: list[tuple[str, str, str]] = []

    def add(status: str, item: str, detail: str) -> None:
        results.append((status, item, detail))
        if show:
            print(f"{status} {item}: {detail}", flush=True)

    cfg_path = Path(config).expanduser() if config else DEFAULT_CONFIG
    try:
        cfg = Config.load(config)
    except Exception as e:
        add(FAIL, "Config file", f"couldn't read {cfg_path}: {e}")
        return results
    if whatsapp_only:
        _check_whatsapp(cfg, add, config)
        return results
    add(OK, "Config file", f"{cfg_path}, {len(cfg.kids)} kids, "
        f"Brief goes to {', '.join(r.address for r in cfg.email.to) or '(no recipients set)'}")
    if not cfg.email.to:
        add(FAIL, "Recipients", "email.to is empty, so nobody gets the Brief")
    weekend = cfg.weekend_events.enabled
    for language in dict.fromkeys([*cfg.brief_languages(), *(cfg.weekend_languages() if weekend else [])]):
        ready, detail = languages.describe(cfg, language, languages.TABLES if weekend else (languages.BRIEF,))
        add(OK if ready else WARN, f"Language {language}", detail)

    _check_gmail(cfg, add)
    if not skip_llm:
        _check_llm(cfg, add)
    if cfg.wilma.enabled:
        _check_wilma(cfg, add)
    if cfg.whatsapp.enabled:
        _check_whatsapp_python(add)
        _check_whatsapp(cfg, add, config)
    for kid in cfg.kids:
        if kid.myclub_ical_url:
            _check_myclub(kid.name, kid.myclub_ical_url, add)
    _check_calendar(cfg, add)
    if cfg.feedback.enabled:
        _check_feedback(cfg, add)
    if cfg.weekend_events.enabled:
        _check_weekend(add)
    if schedule:
        _check_schedule(cfg, add)
    return results


def _check_gmail(cfg: Config, add) -> None:
    from .collectors import gmail
    username = cfg.gmail.username or (cfg.kids[0].wilma_username if cfg.kids else None)
    if not username:
        add(FAIL, "Gmail", "no gmail.username in the config")
        return
    from .utils import keychain
    try:
        password = gmail.get_app_password(username)
    except keychain.NeedsPrompt:
        add(FAIL, "Gmail", f"the App Password for {username} is in the Keychain, but macOS asks "
            f"for the Keychain password before reading it, which the evening Brief can't answer "
            "(store it again with parent-recap setup gmail, or the setup page's Gmail step)")
        return
    except keychain.KeychainError as e:
        add(FAIL, "Gmail", f"couldn't read the App Password for {username} from the Keychain: {e}")
        return
    if not password:
        add(FAIL, "Gmail", f"No App Password in the Keychain for {username} "
            "(store one with parent-recap setup gmail)")
        return
    if not (cfg.gmail.allowlist_domains or cfg.gmail.allowlist_senders):
        add(FAIL, "Gmail", "the sender allowlist is empty (allowlist_domains / allowlist_senders)")
        return
    try:
        n = gmail.count_matching(cfg)
        # Gmail's search only filters by whole days; run() trims to lookback_hours afterwards.
        days = -(-cfg.gmail.lookback_hours // 24)
        add(OK, "Gmail", f"{username} signed in, {n} allowlisted emails in the last {days} days"
            f" (the Brief only takes the last {cfg.gmail.lookback_hours} hours of them)")
    except Exception as e:
        add(FAIL, "Gmail", f"IMAP login failed: {e} "
            "(the App Password may have been revoked; create a new one and store it again)")


def _check_llm(cfg: Config, add) -> None:
    if cfg.llm.backend == "codex":
        _check_codex(cfg, add)
    else:
        _check_claude(add)


def _check_codex(cfg: Config, add) -> None:
    from .summarize import call_llm_json, find_codex
    codex = find_codex(cfg)
    if not codex:
        add(FAIL, "Codex", "codex command not found: install Codex or the ChatGPT desktop app and "
            "sign in, or set llm.codex_path in the config")
        return
    try:
        data = call_llm_json(cfg, 'Reply with only this JSON: {"ok": true}',
                             "This is a connectivity test.", timeout=120, budget=0)
    except Exception as e:
        add(FAIL, "Codex", f"call failed: {str(e)[:200]} (if you're signed out, open the Codex app "
            "and sign in again with your ChatGPT account. ChatGPT Free and Go can't use the Codex "
            "CLI; it needs Plus or higher)")
        return
    if data.get("ok") is True:
        add(OK, "Codex", f"call succeeded ({codex})")
    else:
        add(FAIL, "Codex", f"reachable, but the reply has the wrong shape: {str(data)[:80]}")


def _check_claude(add) -> None:
    from .summarize import CLAUDE_INSTALL, _claude_env, claude_test_call, find_claude
    claude = find_claude()
    if not claude:
        add(FAIL, "Claude", "claude command not found on PATH, in ~/.local/bin or the other usual "
            f"folders, or by your login shell (install Claude Code with: {CLAUDE_INSTALL})")
        return
    from .setup_steps import PLAIN_TERMINAL
    from .utils import keychain
    env, label = _claude_env()
    where = f", {PLAIN_TERMINAL}" if keychain.unreachable_here() else ""
    if label == "inherited":
        # This shell's own sign-in can work while the evening job, which has none, can't.
        add(FAIL, "Claude", "no claude-oauth-token in the Keychain, so the evening Brief can't "
            f"call Claude (store one with parent-recap setup claude{where})")
        return
    if label == "keychain-needs-prompt":
        # Stored by an earlier version, so it trusts that version's Python and not security.
        add(FAIL, "Claude", "the Claude token is in the Keychain, but macOS asks for the Keychain "
            "password before reading it, which the evening Brief can't answer (store it again "
            f"with parent-recap setup claude{where})")
        return
    error = claude_test_call(claude, env)
    if error is not None:
        add(FAIL, "Claude", f"call failed (auth: {label}): {error[:120]}")
    else:
        add(OK, "Claude", f"call succeeded (auth: {label})")


def _check_wilma(cfg: Config, add) -> None:
    from .collectors.wilma import NOT_INSTALLED, _run_or_log
    if own_node.wilma() is None:
        add(FAIL, "Wilma", NOT_INSTALLED)
        return
    data = _run_or_log(["kids", "list"])
    if data is None:
        add(FAIL, "Wilma", "wilma kids list failed: not signed in yet, or the password changed "
            "(sign in again: parent-recap setup wilma)")
        return
    items = data if isinstance(data, list) else (data.get("kids") or data.get("students") or [])
    names = [str(k.get("name") or (k.get("student") or {}).get("name") or "?") for k in items]
    configured = {k.name for k in cfg.kids}
    missing = [n for n in names if n not in configured]
    detail = f"signed in, students in Wilma: {', '.join(names) or '(none)'}"
    if missing:
        add(WARN, "Wilma", detail + f"; {', '.join(missing)} don't match any Kid name in the config, "
            "so their messages may go to the wrong Kid")
    else:
        add(OK, "Wilma", detail)


def _check_whatsapp(cfg: Config, add, config: str | None) -> None:
    from .collectors import whatsapp
    err = whatsapp.check_access()
    if err == whatsapp.NO_ACCESS and not os.environ.get(BG_ENV):
        # Terminal, Claude Code and Codex usually can't read WhatsApp, but the scheduled job's
        # Python can. Ask that Python instead of reporting a failure that doesn't apply.
        try:
            code, out = run_as_job(["doctor", "--whatsapp-only"], config, timeout=180, echo=False)
        except Exception as e:
            add(WARN, "WhatsApp", f"this process can't read WhatsApp, and the background check "
                f"didn't start ({e}). Run parent-recap bg doctor to confirm")
            return
        lines = [l for l in out.splitlines() if "WhatsApp: " in l]
        if not lines and code == TIMED_OUT:
            add(FAIL, "WhatsApp", "the background check didn't finish within 180 seconds and was "
                "stopped (if macOS asked whether python3.x may access data from other apps, click "
                "Allow and run doctor again)")
            return
        if not lines:
            add(FAIL, "WhatsApp", f"the background check gave no result (exit code {code}): "
                f"{out.strip()[-200:]}")
            return
        for line in lines:
            status = next((s for s in (OK, WARN, FAIL) if line.startswith(s.strip())), WARN)
            add(status, "WhatsApp", line.split("WhatsApp: ", 1)[1]
                + " (checked with the scheduled job's Python)")
        return
    if err == whatsapp.NO_ACCESS:
        err = ("the scheduled job's Python can't read WhatsApp yet: run parent-recap "
               "app-management and drag the Python file it shows into System Settings → Privacy & "
               "Security → App Management, and if that's not enough, into Full Disk Access too")
    if err:
        add(FAIL, "WhatsApp", err)
        return
    try:
        known = {g["name"] for g in whatsapp.list_groups(days=3650)}
    except Exception as e:
        add(FAIL, "WhatsApp", f"couldn't list the groups: {e}")
        return
    missing = [c.name for c in cfg.whatsapp.chats if c.name not in known]
    if missing:
        add(WARN, "WhatsApp", "readable, but these group names aren't on this Mac (renamed, or a "
            f"new group for the new school year?): {json.dumps(missing, ensure_ascii=False)}")
    else:
        add(OK, "WhatsApp", f"readable, all {len(cfg.whatsapp.chats)} configured groups found")


def _check_whatsapp_python(add) -> None:
    """Whether the Python macOS allowed to read WhatsApp is still the evening job's (ADR 0011).
    An update of Parent Recap's own Python moves the evening job to a new real path, which macOS
    hasn't allowed, and the WhatsApp check alone can't tell that apart from never allowed."""
    job = _evening_job_python()
    allow = ("in System Settings → Privacy & Security → App Management (parent-recap "
             "app-management shows it in Finder)")
    if not os.path.exists(job):
        here = os.path.realpath(sys.executable)
        add(FAIL, "WhatsApp", f"the evening job's Python {job} points at nothing, so the "
            "evening job can't start and the Python macOS allowed to read WhatsApp is no longer "
            "the evening job's Python: run parent-recap schedule install to give it this "
            f"Python, then allow {here} {allow}")
        return
    granted, real = whatsapp_python.granted(), os.path.realpath(job)
    if granted and granted != real:
        add(WARN, "WhatsApp", f"the Python macOS allowed to read WhatsApp, {granted}, is no longer "
            f"the evening job's Python, which is now {real}: allow {real} {allow}")


def _evening_job_python() -> str:
    """The Python the installed evening job starts, or this one before it's installed."""
    try:
        argv = plistlib.loads((LAUNCH_AGENTS / f"{JOB_DAILY}.plist").read_bytes())["ProgramArguments"]
        return str(argv[0])
    except (OSError, ValueError, KeyError, IndexError):
        return sys.executable


def _check_myclub(kid: str, url: str, add) -> None:
    from .collectors import myclub
    try:
        text = myclub.download(url)
        n = text.count("BEGIN:VEVENT")
        add(OK, f"MyClub ({kid})", f"subscription link works, {n} events in the calendar")
    except Exception as e:
        add(FAIL, f"MyClub ({kid})", f"subscription link doesn't open: {e} (save a new one with "
            f"parent-recap setup myclub --kid {shlex.quote(kid)})")


def _check_calendar(cfg: Config, add) -> None:
    from .actions import calendar as cal
    mode = cfg.google_calendar.mode
    if mode == "off":
        add(OK, "Calendar", "off, email only")
    elif mode == "ics":
        add(OK, "Calendar", "ics mode: new events come as an .ics attachment on the Brief email, "
            "one tap adds them to your calendar")
    else:
        if not google_packages.installed():
            add(FAIL, "Calendar", "google mode, but Google's packages for it aren't installed "
                "(run the plugin's install.sh again, which installs them for google mode)")
            return
        if not cal.is_configured():
            add(FAIL, "Calendar", "google mode, but not authorized yet "
                "(run scripts/setup_google_calendar.py)")
            return
        try:
            n = len(cal.get_upcoming_events(cfg, 7))
            invite = cfg.google_calendar.invite_attendees
            add(OK, "Calendar", f"Google Calendar authorization works, {n} events in the next 7 days"
                + (f"; new events invite {', '.join(invite)}" if invite else ""))
        except Exception as e:
            msg = str(e)
            if "invalid_grant" in msg or "expired" in msg or "revoked" in msg:
                add(FAIL, "Calendar", "Google Calendar authorization expired or was revoked; "
                    "run scripts/setup_google_calendar.py again")
            else:
                add(FAIL, "Calendar", f"Google Calendar call failed: {msg[:120]}")


def _check_feedback(cfg: Config, add) -> None:
    fb = cfg.feedback
    if not fb.active():
        add(WARN, "Pilot feedback", "feedback is on but prefill_base_url or fields is missing, so the "
            "Brief has no ⭐/❌ links (turn it on again with parent-recap setup save <<< "
            "'{\"feedback\": {\"enabled\": true}}', which writes the pilot form this version ships)")
    elif not fb.household_label:
        # One Form serves every pilot Household; without a label their rows can't be told apart.
        add(WARN, "Pilot feedback", "feedback.household_label is empty, so the feedback sheet "
            "can't tell which Household a row came from (set one with parent-recap setup save)")
    else:
        add(OK, "Pilot feedback", f"the Brief has ⭐/❌ links, Household label {fb.household_label}")


def _check_weekend(add) -> None:
    import requests
    try:
        r = requests.get("https://api.hel.fi/linkedevents/v1/event/?page_size=1", timeout=15)
        r.raise_for_status()
        add(OK, "Weekend Picks", "Linked Events API is reachable")
    except Exception as e:
        add(FAIL, "Weekend Picks", f"Linked Events API isn't reachable: {e}")


def _check_schedule(cfg: Config, add) -> None:
    from .state import State
    loaded = launchctl_loaded()
    jobs = [JOB_DAILY] + ([JOB_WEEKEND] if cfg.weekend_events.enabled else [])
    missing = [j for j in jobs if j not in loaded]
    if missing:
        add(WARN, "Schedule", f"not installed yet: {', '.join(missing)} "
            "(run parent-recap schedule install)")
    else:
        add(OK, "Schedule", f"runs every day at {cfg.schedule.daily_hour:02d}:{cfg.schedule.daily_minute:02d}"
            + (", Weekend Picks once every Friday" if cfg.weekend_events.enabled else ""))
    last = State(cfg.resolved_state_path()).last_run_at
    if last:
        hours = (datetime.now(timezone.utc) - last).total_seconds() / 3600
        status = OK if hours < 30 else WARN
        add(status, "Last run", f"{hours:.0f} hours ago" + ("" if hours < 30 else
            " (no run for over a day; the Mac may be asleep, consider a pmset wake-up)"))
    add(OK, "Python path", f"{os.path.realpath(sys.executable)} "
        "(this is the path WhatsApp needs under App Management)")


def launchctl_loaded() -> set[str]:
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    return {line.split()[-1] for line in out.splitlines() if "com.parentrecap." in line}


# ---------------------------------------------------------------- discover

def cmd_discover(args: argparse.Namespace) -> int:
    if args.what == "whatsapp-chats":
        from .collectors import whatsapp
        err = whatsapp.check_access()
        if err:
            print(f"{FAIL} {err}")
            if err == whatsapp.NO_ACCESS and not os.environ.get(BG_ENV):
                print("   Use parent-recap bg discover whatsapp-chats instead, to read with the "
                      "scheduled job's Python and its permissions")
            return 1
        groups = whatsapp.list_groups(days=args.days or 180)
        print(f"WhatsApp groups with messages in the last {args.days or 180} days ({len(groups)}; "
              "names shown exactly as stored, mind spaces and quotes):")
        for g in groups:
            print(f"  {json.dumps(g['name'], ensure_ascii=False)}  last message {g['last']}"
                  + ("  [archived]" if g["archived"] else ""))
        return 0

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
                    "likely": not public and likely_sender(domain, cfg.city),
                    **({"public": True} if public else {})})
    return out


def likely_sender(domain: str, city: str | None) -> bool:
    """Whether mail from `domain` looks like the Kids' school's, the city's or a club's."""
    if domain in PUBLIC_DOMAINS:
        return False
    town = city_domain(city)
    ours = [*KNOWN_SENDERS, *([town] if town else [])]
    if any(domain == d or domain.endswith("." + d) for d in ours):
        return True
    return any(word in domain.rsplit(".", 1)[0] for word in SCHOOL_AND_CLUB_WORDS)


def city_domain(city: str | None) -> str | None:
    """The domain the Household's town's schools likely write from: its preset's, else the town's
    own .fi domain, such as jarvenpaa.fi for Järvenpää."""
    from .setup_steps import CITY_DOMAINS
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
    from .setup_steps import _report
    try:
        senders = gmail_senders(Config.load(config), days, progress=lambda done, total: print(
            f"  read {done} of {total} senders", file=sys.stderr, flush=True))
    except Exception:  # its error can name the address or the config: doctor says what's wrong
        return _report("read-failed", "Gmail's senders couldn't be read. Run parent-recap doctor, "
                       "fix what it names for Gmail, then run this again.")
    return _report("read", days=days, senders=senders)


# ---------------------------------------------------------------- app-management

def cmd_app_management(args: argparse.Namespace) -> int:
    python, failed = show_python_for_app_management()
    print(f"The scheduled job's Python is {python}")
    if failed:
        print(f"{WARN}Couldn't open Finder or System Settings from here. Run this in Terminal:")
        for c in failed:
            print(f"  {shlex.join(c)}")
    print("In Finder that Python file is selected. Drag it into the list in System Settings → "
          "Privacy & Security → App Management, then turn its switch on. If App Management isn't "
          "in the list, drag it into Full Disk Access the same way.")
    return 0


def show_python_for_app_management() -> tuple[str, list[list[str]]]:
    """Selects the scheduled job's Python in Finder and opens App Management. Returns that Python
    and the commands that didn't work, for the family to run in Terminal."""
    # The real Python sits in a hidden folder, so finding it with ⌘⇧G is hard. Selecting it in
    # Finder next to the open pane leaves the family one drag.
    python = os.path.realpath(sys.executable)
    commands = [["open", "-R", python], ["open", APP_MANAGEMENT_URL]]
    return python, [c for c in commands if subprocess.run(c, capture_output=True).returncode != 0]


# ---------------------------------------------------------------- bg

def cmd_bg(args: argparse.Namespace) -> int:
    inner = args.command[1:] if args.command[:1] == ["--"] else list(args.command)
    if not inner or inner[0] == "bg":
        print("Usage: parent-recap bg <command>, e.g. parent-recap bg discover whatsapp-chats")
        return 2
    print(f"Running parent-recap {' '.join(inner)} as a background job "
          "(same Python and permissions as the scheduled job)", flush=True)
    print('If macOS asks whether "python3.x" may access data from other apps, click Allow.', flush=True)
    code, _ = run_as_job(inner, args.config, timeout=args.timeout)
    return code


def _exit_on_signal(signum: int, _frame: FrameType | None) -> None:
    raise SystemExit(128 + signum)


def run_as_job(cmd_args: list[str], config: str | None, timeout: int = 900,
               echo: bool = True,
               output: Callable[[str], None] | None = None) -> tuple[int, str]:
    """Run `parent-recap <cmd_args>` as a one-off launchd job, wait for it, return (exit code, output).
    `output`, if given, is handed all of the output so far each time the job is checked on.

    macOS grants WhatsApp access per responsible process. Started from Terminal, Claude Code or
    Codex, that is the parent app, which usually has no access. A launchd job is its own
    responsible process, so it gets exactly the permission the scheduled job's Python has."""
    import tempfile
    import time
    work = Path(tempfile.mkdtemp(prefix="parent-recap-bg-"))
    label = f"com.parentrecap.bg.{os.getpid()}"
    domain = f"gui/{os.getuid()}"
    out = work / "output.log"
    prog = [sys.executable, "-m", "family_brief"]
    if config:
        prog += ["-c", str(Path(config).expanduser())]
    plist_path = work / f"{label}.plist"
    plist_path.write_bytes(plistlib.dumps({
        "Label": label,
        "ProgramArguments": prog + cmd_args,
        "RunAtLoad": True,
        "StandardOutPath": str(out),
        "StandardErrorPath": str(out),
        "WorkingDirectory": str(Path.home()),
        "Umask": private_files.UMASK,
        "EnvironmentVariables": {"PATH": _path_env(), "HOME": str(Path.home()),
                                 "PYTHONUNBUFFERED": "1", BG_ENV: "1"},
    }))
    shown = 0
    code: int | None = None
    # Python skips `finally` on SIGTERM, and the job belongs to launchd, so an agent's command
    # timeout would leave it running. Turning the signal into an exit lets the cleanup run.
    # Only the main thread can, so the setup page's reads, from its own threads, leave them be.
    main = threading.current_thread() is threading.main_thread()
    stops = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP) if main else ()
    before = {s: signal.signal(s, _exit_on_signal) for s in stops}
    try:
        r = subprocess.run(["launchctl", "bootstrap", domain, str(plist_path)], capture_output=True,
                           text=True)
        if r.returncode != 0:
            raise RuntimeError(f"launchctl bootstrap failed: {r.stderr.strip() or r.returncode}")
        deadline = time.time() + timeout
        while code is None and time.time() < deadline:
            time.sleep(1)
            code = _job_exit_code(domain, label)
            if (echo or output) and out.exists():
                text = out.read_text(errors="replace")
                if output:
                    output(text)
                if echo:
                    print(text[shown:], end="", flush=True)
                    shown = len(text)
    finally:
        for s in stops:  # a second signal mustn't cut the cleanup short
            signal.signal(s, signal.SIG_IGN)
        subprocess.run(["launchctl", "bootout", f"{domain}/{label}"], capture_output=True)
        text = out.read_text(errors="replace") if out.exists() else ""
        shutil.rmtree(work, ignore_errors=True)
        for s, handler in before.items():
            signal.signal(s, signal.SIG_DFL if handler is None else handler)  # None: set outside Python
    if echo:
        print(text[shown:], end="", flush=True)
    if code is None:
        if echo:
            print(f"{FAIL} the background job didn't finish within {timeout} seconds and was "
                  "stopped", flush=True)
        return TIMED_OUT, text
    return code, text


def _job_exit_code(domain: str, label: str) -> int | None:
    """The job's exit code once it has finished, None while it is starting or running."""
    r = subprocess.run(["launchctl", "print", f"{domain}/{label}"], capture_output=True, text=True)
    if r.returncode != 0 or re.search(r"^\tstate = running$", r.stdout, re.M):
        return None
    m = re.search(r"^\tlast exit code = (-?\d+)$", r.stdout, re.M)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------- schedule

def _path_env() -> str:
    from .summarize import native_claude_dir
    dirs: list[str] = []
    # Node for a claude installed with npm; the wilma CLI runs on Parent Recap's own (ADR 0011).
    for tool in ("claude", "node"):
        found = tools.find(tool)
        if found:
            dirs.append(str(Path(found).resolve().parent))
            dirs.append(str(Path(found).parent))
    # Claude Code's native installer puts claude there, and the shell running this may not have it.
    dirs += [str(native_claude_dir()), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    seen: list[str] = []
    for d in dirs:
        if d not in seen:
            seen.append(d)
    return ":".join(seen)


def _plist(label: str, subcommand: str, interval: dict, log_dir: Path) -> dict:
    return {
        "Label": label,
        "ProgramArguments": [sys.executable, "-m", "family_brief", subcommand],
        "StartCalendarInterval": interval,
        "StandardOutPath": str(log_dir / f"{subcommand}-stdout.log"),
        "StandardErrorPath": str(log_dir / f"{subcommand}-stderr.log"),
        "EnvironmentVariables": {"PATH": _path_env(), "HOME": str(Path.home())},
        "Umask": private_files.UMASK,  # launchd creates the log files with the job's umask
        "RunAtLoad": False,
    }


def _load(label: str, plist: dict) -> None:
    path = LAUNCH_AGENTS / f"{label}.plist"
    LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
    with path.open("wb") as f:
        plistlib.dump(plist, f)
    subprocess.run(["launchctl", "load", str(path)], check=True)
    install_record.add("launchd", str(path))


def _unload(label: str) -> None:
    path = LAUNCH_AGENTS / f"{label}.plist"
    if path.exists():
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        path.unlink()


def _pmset(*args: str) -> str:
    try:
        return subprocess.run(["pmset", *args], capture_output=True, text=True).stdout
    except OSError:
        return ""


def _repeating_wakes(sched: str) -> list[str]:
    """The lines under "Repeating power events:" in `pmset -g sched`, which `pmset repeat` replaces."""
    lines: list[str] = []
    inside = False
    for line in sched.splitlines():
        if not line.startswith((" ", "\t")):
            inside = line.strip() == "Repeating power events:"
        elif inside and line.strip():
            lines.append(line.strip())
    return lines


def repeating_wakes() -> list[str]:
    """This Mac's repeating power events, as `pmset -g sched` lists them."""
    return _repeating_wakes(_pmset("-g", "sched"))


def wake_time(hour: int, minute: int) -> tuple[int, int]:
    """When the Mac should wake for a job at hour:minute: 5 minutes before."""
    wake_h, wake_m = divmod(hour * 60 + minute - 5, 60)
    return wake_h % 24, wake_m


def is_our_wake(line: str, hour: int, minute: int) -> bool:
    """Whether a line of `pmset -g sched` is the daily wake setup gives for a job at hour:minute."""
    wake_h, wake_m = wake_time(hour, minute)
    wake_12h = f"{wake_h % 12 or 12}:{wake_m:02d}{'am' if wake_h < 12 else 'pm'}"  # as pmset prints it
    our_wake = re.compile(rf"wake.*(?<!\d)({wake_12h}|{wake_h}:{wake_m:02d}).*everyday")
    return bool(our_wake.search(line.lower().replace(" ", "")))


def never_sleeps() -> bool:
    """Whether this Mac is set never to sleep (sleep 0 in pmset), on every power source."""
    sleeps = re.findall(r"^\s*sleep\s+(\d+)", _pmset("-g", "custom"), re.M)
    return bool(sleeps) and all(s == "0" for s in sleeps)


def _pmset_wake(hour: int, minute: int) -> str:
    wake_h, wake_m = wake_time(hour, minute)
    return f"pmset repeat wakeorpoweron MTWRFSU {wake_h:02d}:{wake_m:02d}:00"


def wake_command(hour: int, minute: int) -> str:
    """The Terminal command that sets the daily wake setup gives for a job at hour:minute."""
    return f"sudo {_pmset_wake(hour, minute)}"


# What setting the daily wake can say: no wake needed (`never-sleeps`, `already-set`), another
# repeating wake schedule kept until the family agrees to replace it (`other-schedule`), set, or
# not: the dialog was closed (`cancelled`), there was no dialog to ask in (`no-dialog`), or macOS
# doesn't list it after the dialog (`not-set`).
WAKE_RESULTS = ("set", "already-set", "never-sleeps", "other-schedule", "cancelled", "no-dialog",
                "not-set")


def set_wake(hour: int, minute: int, replace: bool) -> tuple[str, list[str]]:
    """Sets the daily wake for a job at hour:minute through macOS's administrator dialog, where
    the family types their Mac password. Another repeating wake schedule is replaced only with
    `replace`, so the family can decline before any dialog opens. Returns one of WAKE_RESULTS and
    the Mac's other repeating wake schedule, as `pmset -g sched` lists it."""
    if never_sleeps():
        return "never-sleeps", []
    existing = repeating_wakes()
    if any(is_our_wake(line, hour, minute) for line in existing):
        return "already-set", []
    if existing and not replace:
        return "other-schedule", existing
    wake_h, wake_m = wake_time(hour, minute)
    try:
        secret_dialog.as_administrator(
            f"/usr/bin/{_pmset_wake(hour, minute)}",
            f"Parent Recap wants to wake your Mac at {wake_h:02d}:{wake_m:02d} every day, 5 "
            "minutes before the Brief. Enter your Mac password to allow this.")
    except secret_dialog.Cancelled:
        return "cancelled", existing
    except secret_dialog.NoWayToAsk:
        return "no-dialog", existing
    if any(is_our_wake(line, hour, minute) for line in repeating_wakes()):
        return "set", existing
    return "not-set", existing


def _set_wake(hour: int, minute: int, replace: bool) -> None:
    """`set_wake`, saying how it went. Without a desktop session it gives the `sudo` command for
    Terminal instead."""
    wake_h, wake_m = wake_time(hour, minute)
    result, existing = set_wake(hour, minute, replace)
    if result == "never-sleeps":
        print("\nThis Mac never sleeps (sleep 0 in pmset), so it needs no wake schedule.")
        return
    if result == "already-set":
        print(f"\nThe Mac already wakes at {wake_h:02d}:{wake_m:02d} every day, before the job starts.")
        return
    print("\nScheduled jobs don't run while the Mac is asleep.")
    in_terminal = (f"Run this once in Terminal (it asks for your Mac password):\n"
                   f"  {wake_command(hour, minute)}")
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


def install_jobs(cfg: Config) -> None:
    """Loads the evening job at the config's time, and Weekend Picks' job when they're on."""
    log_dir = cfg.archive.resolved_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    private_files.tighten(cfg)
    sc = cfg.schedule
    _load(JOB_DAILY, _plist(JOB_DAILY, "run",
                            {"Hour": sc.daily_hour, "Minute": sc.daily_minute}, log_dir))
    if cfg.weekend_events.enabled:
        _load(JOB_WEEKEND, _plist(JOB_WEEKEND, "weekend-events",
                                  {"Weekday": sc.weekend_weekday, "Hour": sc.weekend_hour, "Minute": 0},
                                  log_dir))
    else:
        _unload(JOB_WEEKEND)


def _run_now(label: str, log_dir: Path) -> int:
    """Asks launchd to start the installed job, which then runs with the job's own Python, PATH,
    environment and log files. Doesn't wait for it."""
    target = f"gui/{os.getuid()}/{label}"
    logs = log_dir / ("weekend-events" if label == JOB_WEEKEND else "run")
    where = f"Its logs:\n  {logs}-stdout.log\n  {logs}-stderr.log"
    probe = subprocess.run(["launchctl", "print", target], capture_output=True, text=True)
    if probe.returncode != 0:
        print(f"{FAIL} {label} isn't installed. Run family-brief schedule install first.")
        return 1
    # Plain kickstart (no -k) leaves a running job alone, so say so instead of looking like a start.
    # Exit 1: the Brief wasn't started by this call, which a script calling it should see.
    if re.search(r"^\tstate = running$", probe.stdout, re.M):
        print(f"{WARN}{label} is already running, so no second run was started. {where}")
        return 1
    kick = subprocess.run(["launchctl", "kickstart", target], capture_output=True, text=True)
    if kick.returncode != 0:
        print(f"{FAIL} launchctl couldn't start {label}: {kick.stderr.strip() or kick.returncode}")
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
        return _run_now(JOB_WEEKEND if args.weekend else JOB_DAILY, log_dir)

    if args.action == "install":
        install_jobs(cfg)
        print(f"{OK} Brief: every day at {sc.daily_hour:02d}:{sc.daily_minute:02d}")
        if cfg.weekend_events.enabled:
            print(f"{OK} Weekend Picks: every Friday at {sc.weekend_hour:02d}:00")
        _set_wake(sc.daily_hour, sc.daily_minute, args.replace_wake)
        return 0

    if args.action == "uninstall":
        for label in (JOB_DAILY, JOB_WEEKEND):
            _unload(label)
        print(f"{OK} Scheduled jobs removed (config and archive are kept)")
        return 0

    loaded = launchctl_loaded()
    for label in (JOB_DAILY, JOB_WEEKEND):
        print(f"{OK if label in loaded else '—'} {label}{'' if label in loaded else ' (not installed)'}")
    for name in ("run-stderr.log", "weekend-events-stderr.log"):
        f = log_dir / name
        if f.exists():
            tail = f.read_text(errors="replace").splitlines()[-3:]
            print(f"\nLast lines of {name}:")
            for line in tail:
                print("  " + line[:200])
    return 0
