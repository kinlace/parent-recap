"""Operational commands used during onboarding and troubleshooting.

doctor   — check every configured connection; prints status + counts only, never message content
discover — list candidate Gmail sender domains / WhatsApp group names / Wilma students
schedule — install, remove or inspect the evening jobs
full-disk-access — show the scheduled job's Python in Finder and open Full Disk Access, to grant
                   WhatsApp (setup whatsapp does this and waits for it)
bg       — run any of the above (or run/collect) as a one-off job with the evening job's
           permissions, for WhatsApp access
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from . import (google_packages, languages, own_node, private_files, secret_dialog,
               whatsapp_python)
from .config import Config
from .system import desktop_macos, jobs, macos_only, wake, windows_only

OK, WARN, FAIL = "✅", "⚠️ ", "❌"
# Why the WhatsApp check isn't OK, for the setup page and the chat skills to say what fixes it:
# the scheduled job's Python has no Full Disk Access, the background check didn't start, gave no
# result or timed out, a ticked group's name isn't on this Mac, or listing the groups failed. Any
# other failure has no reason, and gets the general advice.
WHATSAPP_REASONS = ("no-access", "not-finished", "missing-groups", "list-failed")
DEFAULT_CONFIG = Path.home() / ".family" / "config.yaml"
BG_ENV = jobs.BG_ENV  # set inside `bg` jobs so doctor doesn't recurse
# Where doctor says to grant WhatsApp's permission, after "Full Disk Access".
FULL_DISK_ACCESS_WHERE = ("(System Settings → Privacy & Security; parent-recap full-disk-access "
                          "shows the Python in Finder)")
FULL_DISK_ACCESS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
CODEX_STATUS_SECONDS = 20


class Check(NamedTuple):
    """One of doctor's checks. doctor prints its status, item and detail. A WhatsApp check that
    isn't OK can also have a `reason` from WHATSAPP_REASONS, and `groups` names the groups a
    missing-groups reason is about."""
    status: str
    item: str
    detail: str
    reason: str | None = None
    groups: tuple[str, ...] = ()


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

    # app-management: its name before WhatsApp's permission moved to Full Disk Access (ADR 0012).
    pfda = sub.add_parser("full-disk-access", aliases=["app-management"],
                          help="Show the scheduled job's Python in Finder and open Full Disk "
                          "Access, so you can drag it in for WhatsApp")
    pfda.set_defaults(func=cmd_full_disk_access)

    pbg = sub.add_parser("bg", help="Run a parent-recap command as a one-off launchd job "
                                    "(same Python and macOS permissions as the scheduled job)")
    pbg.add_argument("--timeout", type=int, default=900, help="Give up after this many seconds")
    pbg.add_argument("command", nargs=argparse.REMAINDER, help="e.g. discover whatsapp-chats")
    pbg.set_defaults(func=cmd_bg)


# ---------------------------------------------------------------- doctor

def cmd_doctor(args: argparse.Namespace) -> int:
    results = health_checks(args.config, skip_llm=args.skip_llm, whatsapp_only=args.whatsapp_only,
                            show=not args.whatsapp_only)
    fails = sum(1 for c in results if c.status == FAIL)
    if args.whatsapp_only:
        # For the doctor that started this bg job: one line of JSON per check, so that the reason
        # gets there too. That doctor prints the detail.
        for c in results:
            print(json.dumps(c._asdict(), ensure_ascii=False), flush=True)
    else:
        warns = sum(1 for c in results if c.status == WARN)
        print(f"\n{len(results)} checks: {len(results) - fails - warns} OK, {warns} warnings, "
              f"{fails} to fix")
    return 1 if fails else 0


def health_checks(config: str | None, skip_llm: bool = False, whatsapp_only: bool = False,
                  show: bool = False, schedule: bool = True) -> list[Check]:
    """Doctor's checks. With `show`, each is printed as it's done. Without `schedule`, the
    scheduled job's checks are left out, for before it's installed."""
    results: list[Check] = []

    def add(status: str, item: str, detail: str, reason: str | None = None,
            groups: list[str] | tuple[str, ...] = ()) -> None:
        results.append(Check(status, item, detail, reason, tuple(groups)))
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
    if cfg.whatsapp.enabled and macos_only.offers("whatsapp"):
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
        if windows_only.offers("wake-timers"):
            _check_wake_timers(cfg, add)
        if windows_only.offers("sign-in-after-update"):
            _check_sign_in_after_update(add)
    return results


def _check_gmail(cfg: Config, add) -> None:
    from .collectors import gmail
    username = cfg.gmail.username or (cfg.kids[0].wilma_username if cfg.kids else None)
    if not username:
        add(FAIL, "Gmail", "no gmail.username in the config")
        return
    from .system import secrets
    try:
        password = gmail.get_app_password(username)
    except secrets.SecretsError as e:
        if e.reason == "needs-prompt":
            add(FAIL, "Gmail", f"the App Password for {username} is in the Keychain, but macOS "
                f"asks for the Keychain password before reading it, which the evening Brief can't "
                "answer (store it again with parent-recap setup gmail, or the setup page's Gmail "
                "step)")
        else:
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
    elif cfg.llm.backend == "gateway":
        _check_gateway(cfg, add)
    else:
        _check_claude(add)


def _check_gateway(cfg: Config, add) -> None:
    """Whether the gateway answers at its configured address, and whether the Gateway Key works,
    from one small call. Without a readable key the call goes with none, which a gateway that
    answers refuses."""
    from . import gateway
    url = cfg.llm.gateway_url
    try:
        key, missing = gateway.key(), None
    except gateway.NoKey as e:
        key, missing = "", str(e)
    outcome, detail = gateway.test_call(url, key, timeout=120)
    if outcome == gateway.NO_ANSWER:
        add(FAIL, "AI gateway", f"no answer from {url}: {detail} (check the Mac is online and "
            "llm.gateway_url in the config)")
    else:
        add(OK, "AI gateway", f"answers at {url}")
    if missing:
        add(FAIL, "Gateway Key", f"{missing}, so the evening Brief can't call the gateway")
    elif outcome == gateway.WORKS:
        add(OK, "Gateway Key", "works, test call succeeded")
    elif outcome == gateway.UNKNOWN:
        add(FAIL, "Gateway Key", "the gateway doesn't know this Gateway Key, or it was revoked "
            f"(ask the Parent Recap maintainers for a new one and store it with {gateway.SETUP})")
    elif outcome == gateway.SPENT:
        add(WARN, "Gateway Key", "works, but has used up today's budget, so the gateway takes no "
            "more calls with it until tomorrow")
    elif outcome == gateway.FAILED:
        add(FAIL, "Gateway Key", f"test call failed: {detail}")


def _check_codex(cfg: Config, add) -> None:
    from .summarize import call_llm_json, codex_missing, find_codex
    codex = find_codex(cfg)
    if not codex:
        add(FAIL, "Codex", f"codex command not found: {codex_missing()}, or set llm.codex_path in "
            "the config")
        return
    # On Windows the setup page installs the CLI on its own, with no Codex app to sign in from.
    sign_in = ("sign in again on the setup page, or run codex login in PowerShell; Codex keeps "
               "the sign-in in ~/.codex unless its own config says otherwise"
               if sys.platform == "win32" else
               "open the Codex app and sign in again with your ChatGPT account")
    if _codex_signed_out(codex):
        add(FAIL, "Codex", f"not signed in ({sign_in})")
        return
    try:
        data = call_llm_json(cfg, 'Reply with only this JSON: {"ok": true}',
                             "This is a connectivity test.", timeout=120, budget=0)
    except Exception as e:
        add(FAIL, "Codex", f"call failed: {str(e)[:200]} (if you're signed out, {sign_in}. "
            "ChatGPT Free and Go can't use the Codex CLI; it needs Plus or higher)")
        return
    if data.get("ok") is True:
        add(OK, "Codex", f"call succeeded ({codex})")
    else:
        add(FAIL, "Codex", f"reachable, but the reply has the wrong shape: {str(data)[:80]}")


def _codex_signed_out(codex: str) -> bool:
    """Whether `codex login status` says signed out, which it reads wherever Codex keeps the
    sign-in: its auth.json, or the system's own store if the family chose that. A check that
    doesn't run leaves it to the test call. Only the exit code is read: the output names the
    account."""
    import subprocess
    try:
        proc = subprocess.run([codex, "login", "status"], capture_output=True, text=True, encoding="utf-8",
                              timeout=CODEX_STATUS_SECONDS, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode != 0


def _check_claude(add) -> None:
    from .summarize import _claude_env, claude_install, claude_test_call, find_claude
    windows = sys.platform == "win32"
    claude = find_claude()
    if not claude and windows:
        add(FAIL, "Claude", "claude command not found on PATH or in %USERPROFILE%\\.local\\bin "
            "(install Claude Code on the setup page, or in PowerShell with: "
            f"{claude_install()})")
        return
    if not claude:
        add(FAIL, "Claude", "claude command not found on PATH, in ~/.local/bin or the other usual "
            f"folders, or by your login shell (install Claude Code with: {claude_install()})")
        return
    from .setup_steps import PLAIN_TERMINAL
    from .system import secrets
    env, label = _claude_env()
    where = "" if secrets.reachable() else f", {PLAIN_TERMINAL}"
    if label == "inherited" and windows:
        # `setup claude`'s dialog is the Mac's: on Windows the token is pasted into the page.
        add(FAIL, "Claude", "no Claude token in Credential Manager, so the evening Brief can't "
            "call Claude (sign in to Claude on the setup page: paste the install line into "
            "PowerShell again)")
        return
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
    # From 2.0 the CLI signs in with every saved profile, so a stale one fails every evening. The
    # evening run doesn't put that in the Brief when the profile is for none of the Kids.
    from . import setup_wilma
    signins = setup_wilma.profiles(setup_wilma.config_path())
    kids = {k.name.casefold() for k in cfg.kids}
    for p in signins if len(signins) > 1 else []:
        if not setup_wilma.for_the_household(p, kids):
            add(WARN, "Wilma", f"the wilma CLI also signs in as {p.get('username')} at "
                f"{p.get('tenantName') or p.get('tenantUrl')} every evening, a sign-in setup didn't "
                "make, for none of the Kids, so the Brief doesn't say when it fails. If it's no "
                f"longer used, remove it: wilma accounts remove {p.get('username')}")


def _check_whatsapp(cfg: Config, add, config: str | None) -> None:
    from .collectors import whatsapp
    err = whatsapp.check_access()
    if err == whatsapp.NO_ACCESS and not os.environ.get(BG_ENV):
        # Terminal, Claude Code and Codex usually can't read WhatsApp, but the scheduled job's
        # Python can. Ask that Python instead of reporting a failure that doesn't apply.
        try:
            code, out = jobs.run_detached(["doctor", "--whatsapp-only"], 180, config=config,
                                          echo=False)
        except Exception as e:
            add(WARN, "WhatsApp", f"this process can't read WhatsApp, and the background check "
                f"didn't start ({e}). Run parent-recap bg doctor to confirm", "not-finished")
            return
        checks = [c for c in _job_checks(out) if c.item == "WhatsApp"]
        if not checks and code == jobs.TIMED_OUT:
            add(FAIL, "WhatsApp", "the background check didn't finish within 180 seconds and was "
                "stopped (check that WhatsApp for Mac opens and shows the chats, then run doctor "
                "again)", "not-finished")
            return
        if not checks:
            add(FAIL, "WhatsApp", f"the background check gave no result (exit code {code}): "
                f"{out.strip()[-200:]}", "not-finished")
            return
        for c in checks:
            add(c.status, "WhatsApp", c.detail + " (checked with the scheduled job's Python)",
                c.reason, c.groups)
        return
    if err == whatsapp.NO_ACCESS:
        python = os.path.realpath(sys.executable)
        add(FAIL, "WhatsApp", f"the scheduled job's Python {python} can't read WhatsApp: add it "
            f"to Full Disk Access {FULL_DISK_ACCESS_WHERE}", "no-access")
        return
    if err:
        add(FAIL, "WhatsApp", err)
        return
    try:
        known = {g["name"] for g in whatsapp.list_groups(days=3650)}
    except Exception as e:
        add(FAIL, "WhatsApp", f"couldn't list the groups: {e}", "list-failed")
        return
    missing = [c.name for c in cfg.whatsapp.chats if c.name not in known]
    if missing:
        add(WARN, "WhatsApp", "readable, but these group names aren't on this Mac (renamed, or a "
            f"new group for the new school year?): {json.dumps(missing, ensure_ascii=False)}",
            "missing-groups", missing)
    else:
        add(OK, "WhatsApp", f"readable, all {len(cfg.whatsapp.chats)} configured groups found")


def _job_checks(out: str) -> list[Check]:
    """The checks a `doctor --whatsapp-only` bg job printed, one line of JSON each, among
    whatever else the job printed."""
    checks = []
    for line in out.splitlines():
        try:
            c = json.loads(line)
            checks.append(Check(c["status"], c["item"], c["detail"], c.get("reason"),
                                tuple(c.get("groups") or ())))
        except (ValueError, KeyError, TypeError):
            continue
    return checks


def _check_whatsapp_python(add) -> None:
    """Whether the Python given Full Disk Access to read WhatsApp is still the evening job's (ADR
    0011, 0012). An update of Parent Recap's own Python moves the evening job to a new real path,
    which macOS hasn't given it, and the WhatsApp check alone can't tell that from never given."""
    job = _evening_job_python()
    if not os.path.exists(job):
        here = os.path.realpath(sys.executable)
        add(FAIL, "WhatsApp", f"the evening job's Python {job} points at nothing, so the "
            "evening job can't start and the Python given Full Disk Access to read WhatsApp is no "
            "longer the evening job's Python: run parent-recap schedule install to give it this "
            f"Python, then add {here} to Full Disk Access {FULL_DISK_ACCESS_WHERE}")
        return
    granted, real = whatsapp_python.granted(), os.path.realpath(job)
    if granted and granted != real:
        add(WARN, "WhatsApp", f"the Python given Full Disk Access to read WhatsApp, {granted}, is "
            f"no longer the evening job's Python, which is now {real}: add {real} to Full Disk "
            f"Access {FULL_DISK_ACCESS_WHERE}")


def _evening_job_python() -> str:
    """The Python the installed evening job starts, or this one before it's installed."""
    program = jobs.program(jobs.DAILY)
    return program[0] if program else sys.executable


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
    installed, waiting = jobs.installed(), jobs.waiting_for_login()
    wanted = [jobs.DAILY] + ([jobs.WEEKEND] if cfg.weekend_events.enabled else [])
    missing = [j for j in wanted if j not in installed]
    unloaded = [jobs.system_name(j) for j in missing if j in waiting]
    uninstalled = [jobs.system_name(j) for j in missing if j not in waiting]
    if uninstalled:
        add(WARN, "Schedule", f"not installed yet: {', '.join(uninstalled)} "
            "(run parent-recap schedule install)")
    if unloaded:
        add(WARN, "Schedule", f"installed but not loaded for this login: {', '.join(unloaded)}. "
            "The evening job only runs while you're logged in to the Mac (an SSH login doesn't "
            "count, so over SSH this is expected): after a restart, log in once or turn on "
            "automatic login, see \"No Brief after the Mac restarted\" in docs/troubleshooting.md")
    if not missing:
        add(OK, "Schedule", f"runs every day at {cfg.schedule.daily_hour:02d}:{cfg.schedule.daily_minute:02d}"
            + (", Weekend Picks once every Friday" if cfg.weekend_events.enabled else ""))
    last = State(cfg.resolved_state_path()).last_run_at
    if last:
        hours = (datetime.now(timezone.utc) - last).total_seconds() / 3600
        status = OK if hours < 30 else WARN
        why = ("the Mac may be asleep, consider a pmset wake-up" if sys.platform == "darwin" else
               "the PC may have been asleep, signed out or off")
        add(status, "Last run", f"{hours:.0f} hours ago" + ("" if hours < 30 else
            f" (no run for over a day; {why})"))
    if macos_only.offers("whatsapp"):
        add(OK, "Python path", f"{os.path.realpath(sys.executable)} "
            "(this is the path WhatsApp needs under Full Disk Access)")


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
    add(status, "Wake", words)


def _check_sign_in_after_update(add) -> None:
    """The evening job runs only while the family is signed in (ADR 0021), and nobody is after
    Windows restarts for an update unless it signs them back in."""
    item, setting = "Sign-in after updates", windows_only.SIGN_IN_SETTING
    on = windows_only.signs_back_in_after_update()
    if on:
        add(OK, item, "Windows signs you back in after it restarts for an update, so the "
            "evening run still happens")
    elif on is False:
        add(WARN, item, "after Windows restarts for an update nobody is signed in, so that "
            f"night's Brief waits until someone signs in. Turn on {setting}")
    else:
        add(WARN, item, "couldn't tell whether Windows signs you back in after it restarts for an "
            "update. If it doesn't, that night's Brief waits until someone signs in: check that "
            f"this is on: {setting}")


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


# ---------------------------------------------------------------- full-disk-access

def cmd_full_disk_access(args: argparse.Namespace) -> int:
    python, failed = show_python_for_full_disk_access()
    print(f"The scheduled job's Python is {python}")
    if failed:
        print(f"{WARN}Couldn't open Finder or System Settings from here. Run this in Terminal:")
        for c in failed:
            print(f"  {shlex.join(c)}")
    print(f"In Finder that Python file, {os.path.basename(python)}, is selected. Drag it into "
          "the list in System Settings → Privacy & Security → Full Disk Access, then turn its "
          "switch on.")
    return 0


def full_disk_access_python() -> str:
    """The Python file the family gives Full Disk Access: the real file of the scheduled job's
    Python, which Finder selects. Its name, such as python3.13, follows the pinned Python."""
    return os.path.realpath(sys.executable)


def show_python_for_full_disk_access() -> tuple[str, list[list[str]]]:
    """Selects the scheduled job's Python in Finder and opens Full Disk Access. Returns that Python
    and the commands that didn't work, for the family to run in Terminal."""
    # The real Python sits in a hidden folder, so finding it with ⌘⇧G is hard. Selecting it in
    # Finder next to the open pane leaves the family one drag.
    python = full_disk_access_python()
    return python, desktop_macos.show_beside(python, FULL_DISK_ACCESS_URL)


# ---------------------------------------------------------------- bg

def cmd_bg(args: argparse.Namespace) -> int:
    inner = args.command[1:] if args.command[:1] == ["--"] else list(args.command)
    if not inner or inner[0] == "bg":
        print("Usage: parent-recap bg <command>, e.g. parent-recap bg discover whatsapp-chats")
        return 2
    print(f"Running parent-recap {' '.join(inner)} as a background job "
          "(same Python and permissions as the scheduled job)", flush=True)
    code, _ = jobs.run_detached(inner, args.timeout, config=args.config)
    return code


# ---------------------------------------------------------------- schedule

def _set_wake(hour: int, minute: int, replace: bool) -> None:
    """`wake.set`, saying how it went. Without a desktop session it gives the command for
    Terminal instead."""
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
    wanted = [jobs.Job(jobs.DAILY, "run", None, sc.daily_hour, sc.daily_minute, log_dir)]
    if cfg.weekend_events.enabled:
        wanted.append(jobs.Job(jobs.WEEKEND, "weekend-events", sc.weekend_weekday, sc.weekend_hour,
                               0, log_dir))
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
