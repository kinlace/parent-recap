"""Setup's steps that need the family, one command each, for the setup skill to run.

setup gmail — open Google's App passwords page, ask for the App Password in a macOS dialog,
              test the Gmail sign-in, store it in the Keychain
setup wilma — open the wilma CLI's sign-in in a Terminal window with a guide in the family's
              language, end it once signed in, then read the Kids and the city
setup claude — open Claude's sign-in for the nightly token in a Terminal window, ask for the
               token in a macOS dialog, make one test call, store it in the Keychain
setup whatsapp — read WhatsApp through a `bg` job; when the scheduled job's Python can't yet,
                 show it in Finder, open App Management and wait for the permission, then list
                 the chats with a hint on those that look like they're about a Kid
setup myclub — open MyClub, ask for a Kid's calendar link in a macOS dialog, download it once,
               save it in the owner-only config
setup save — save the Household's answers into the config, and setup's progress (in setup_save.py)
setup status — whether setup is done (in setup_status.py)
setup page — serve the setup page on this Mac and open it in the browser (in setup_server.py)

Each prints one line of JSON for the assistant, and nothing else: `result` says what happened
and, when something went wrong, `next` says what the family does about it. A secret is never in
it (ADR 0005).
"""
from __future__ import annotations

import argparse
import fcntl
import imaplib
import json
import math
import os
import pty
import re
import select
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import termios
import time
import tty
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import keyring.errors

from . import install_record, secret_dialog
from .config import Config, Kid

APP_PASSWORDS_URL = "https://myaccount.google.com/apppasswords"
TWO_STEP_URL = "https://myaccount.google.com/signinoptions/two-step-verification"
PAGE_UNAVAILABLE = "The page isn't available"

# The Wilma address of each city in "City presets" in docs/config.md.
WILMA_CITIES = {
    "espoo.inschool.fi": "Espoo",
    "helsinki.inschool.fi": "Helsinki",
    "yvkoulut.inschool.fi": "Helsinki",
    "vantaa.inschool.fi": "Vantaa",
    "kauniainen.inschool.fi": "Kauniainen",
}
WILMA_INSTALL = "npm install -g @wilm-ai/wilma-cli"
WILMA_POLL_SECONDS = 2
# The Wilma window's own text, in the reviewed languages; any other language gets the English.
# Wilma comes before the AI login in setup, so there's no AI yet to translate it.
WILMA_WINDOW = {
    "en": {
        "guide": ["Parent Recap: sign in to Wilma",
                  "1. Type your town in Finnish, for example Espoo, Helsinki or Vantaa, and pick "
                  "it from the list.",
                  "2. Sign in with the same username and password as on the Wilma website or "
                  "app. If you don't remember them, look in your browser's saved passwords."],
        "signed_in": "You're signed in to Wilma.",
        "close": "You can close this window.",
    },
    "zh": {
        "guide": ["Parent Recap：登录 Wilma",
                  "1. 用芬兰语输入你所在的城市，例如 Espoo、Helsinki 或 Vantaa，然后在列表里选中它。",
                  "2. 用你在 Wilma 网站或 App 上的用户名和密码登录。如果记不清，可以在浏览器保存的密码里找到。"],
        "signed_in": "已登录 Wilma。",
        "close": "可以关闭这个窗口了。",
    },
    "fi": {
        "guide": ["Parent Recap: kirjaudu Wilmaan",
                  "1. Kirjoita kuntasi nimi suomeksi, esimerkiksi Espoo, Helsinki tai Vantaa, ja "
                  "valitse se listasta.",
                  "2. Kirjaudu samalla käyttäjätunnuksella ja salasanalla kuin Wilman "
                  "verkkosivulla tai sovelluksessa. Jos et muista niitä, katso ne selaimesi "
                  "tallennetuista salasanoista."],
        "signed_in": "Olet kirjautunut Wilmaan.",
        "close": "Voit sulkea tämän ikkunan.",
    },
}
# Node's console.clear(), which the wilma CLI calls before each of its questions.
NODE_CLEAR = b"\x1b[1;1H\x1b[0J"
# What the wilma CLI asks once the sign-in has worked. Setup reads every Kid, so the family never
# sees either: the window answers the first itself and ends the CLI before the second.
WILMA_STUDENT_PICKER = b"Select student"
WILMA_MENU = b"What do you want to view?"
CLAUDE_TOKEN_ACCOUNT = "claude-oauth-token"
# Loose on purpose, like scripts/setup_claude_token.py: the test call decides whether it works.
CLAUDE_TOKEN = re.compile(r"sk-ant-[A-Za-z0-9_-]+")
WHATSAPP_POLL_SECONDS = 5
# Each read gets at least this long: while macOS shows its one-time Allow prompt, the read waits
# for the family's answer.
WHATSAPP_READ_SECONDS = 60
MYCLUB_URL = "https://id.myclub.fi"
_OK = ("saved", "signed-in", "readable", "read")


def register(sub) -> None:
    psetup = sub.add_parser("setup", help="Setup steps that ask the family for something")
    steps = psetup.add_subparsers(dest="step", required=True)

    pgmail = steps.add_parser("gmail", help="Ask for the Gmail App Password in a dialog, test it "
                              "and store it in the Keychain")
    pgmail.add_argument("--address", default=None,
                        help="The Gmail address (default: gmail.username in the config)")
    pgmail.add_argument("--no-open", action="store_true",
                        help="Don't open Google's App passwords page again")
    pgmail.set_defaults(func=cmd_gmail)

    pwilma = steps.add_parser("wilma", help="Open the Wilma sign-in in Terminal, wait for it, "
                              "then report the Kids and the city")
    pwilma.add_argument("--timeout", type=int, default=600,
                        help="Seconds to wait for the family to sign in (default: 600)")
    pwilma.add_argument("--no-open", action="store_true",
                        help="Don't open the sign-in again, only wait for it and read the Kids")
    pwilma.add_argument("--language", default="en",
                        help="The family's language code, for the window's guide (default: en)")
    pwilma.add_argument("--screen", default=None, help=argparse.SUPPRESS)  # in the window
    pwilma.set_defaults(func=cmd_wilma)

    pclaude = steps.add_parser("claude", help="Open Claude's sign-in for the nightly token in "
                               "Terminal, ask for the token in a dialog, test it and store it in "
                               "the Keychain")
    pclaude.add_argument("--no-open", action="store_true",
                         help="Don't open Claude's sign-in again, only ask for the token")
    pclaude.set_defaults(func=cmd_claude)

    pwhatsapp = steps.add_parser("whatsapp", help="Read WhatsApp with the scheduled job's Python, "
                                 "opening App Management and waiting for the permission when "
                                 "needed, then report the chats")
    pwhatsapp.add_argument("--timeout", type=int, default=600,
                           help="Seconds to wait for the permission (default: 600)")
    pwhatsapp.add_argument("--days", type=int, default=180,
                           help="List chats with messages in this many days (default: 180)")
    pwhatsapp.add_argument("--no-open", action="store_true",
                           help="Don't open Finder and App Management again, only wait and read")
    pwhatsapp.add_argument("--read", action="store_true", help=argparse.SUPPRESS)  # inside the bg job
    pwhatsapp.set_defaults(func=cmd_whatsapp)

    pmyclub = steps.add_parser("myclub", help="Ask for a Kid's MyClub calendar link in a dialog, "
                               "download it once and save it in the config")
    pmyclub.add_argument("--kid", default=None, help="The Kid's name, exactly as under kids: in "
                         "the config")
    pmyclub.add_argument("--no-open", action="store_true",
                         help="Don't open MyClub's page again, only ask for the link")
    pmyclub.set_defaults(func=cmd_myclub)

    from . import setup_save, setup_server, setup_status
    setup_save.register(steps)
    setup_status.register(steps)
    setup_server.register(steps)


def _report(result: str, next_: str | None = None, **extra: Any) -> int:
    out = {"result": result, **extra}
    if next_:
        out["next"] = next_
    print(json.dumps(out, ensure_ascii=False))
    return 0 if result in _OK else 1


# ---------------------------------------------------------------- gmail

def cmd_gmail(args: argparse.Namespace) -> int:
    from .collectors import gmail

    address = (args.address or _configured_address(args.config) or "").strip().lower()
    if "@" not in address:
        return _report("no-address", "Run it again with --address and the family's Gmail "
                       "address: family-brief setup gmail --address name@gmail.com")
    if not args.no_open:
        subprocess.run(["open", APP_PASSWORDS_URL], capture_output=True)

    turn_on_two_step = (f"Turn on 2-Step Verification at {TWO_STEP_URL}, then create the App "
                        "Password again and run this again. A work or school Google account may "
                        "not allow App Passwords at all; then use a personal Gmail.")
    try:
        password = secret_dialog.ask(
            f"Paste the App Password for {address}.\n\nCreate it on Google's App passwords page, "
            f"which is open in your browser ({APP_PASSWORDS_URL}), with the name Parent Recap, "
            "then copy the 16 letters Google shows.", other=PAGE_UNAVAILABLE)
    except secret_dialog.Cancelled:
        return _report("cancelled", "The family closed the dialog. Run this again when they're ready.")
    except secret_dialog.OtherChosen:
        return _report("app-passwords-unavailable", turn_on_two_step)
    except secret_dialog.NoWayToAsk:
        return _report("no-prompt", "No dialog or Terminal prompt could be shown here. The family "
                       f"runs this in Terminal: {_program()} setup gmail --address {address}")

    password = password.replace(" ", "")
    if len(password) != 16 or not password.isalpha():
        # Not sent to Google: it's most likely the family's own Google password.
        return _report("not-an-app-password", "That wasn't a 16-letter App Password. Copy the "
                       "App Password Google showed (not the Google password) and run this again.")

    try:
        with imaplib.IMAP4_SSL(gmail.IMAP_HOST, gmail.IMAP_PORT, timeout=gmail.IMAP_TIMEOUT) as imap:
            imap.login(address, password)
            imap.logout()
    except imaplib.IMAP4.error:
        return _report("rejected", f"Gmail didn't accept that App Password for {address}. "
                       "Check the address, create a new App Password and run this again. If it "
                       f"still fails: {turn_on_two_step}")
    except OSError:
        return _report("no-connection", "Couldn't reach Gmail. Check the Mac is online and run this again.")

    try:
        gmail.store_app_password(address, password)
    except keyring.errors.KeyringError:
        return _report("keychain-failed", "Gmail accepted it, but macOS didn't let Parent Recap "
                       "save it in the Keychain. Run this again and click Allow if macOS asks.")
    return _report("saved", address=address)


# ---------------------------------------------------------------- wilma

def cmd_wilma(args: argparse.Namespace) -> int:
    """The wilma CLI signs in only on its own interactive screen, so this runs that screen in a
    Terminal window and watches for its result: the CLI saves its config once the sign-in has
    worked, and the window's script records the screen's exit status when it closes. The
    password stays between the family and the CLI; this reads only the Wilma address from the
    CLI's config."""
    from .collectors import wilma

    if args.screen:
        return _sign_in_screen(args.screen, args.language)
    program = shutil.which(wilma.WILMA)
    if not program:
        return _report("not-installed", f"Install the wilma CLI in Terminal with {WILMA_INSTALL} "
                       "(it needs Node: brew install node), then run this again.")
    again = f"{_program()} setup wilma"
    config = _wilma_config_path()
    with tempfile.TemporaryDirectory(prefix="parent-recap-wilma-") as tmp:
        status = Path(tmp) / "exit-status"
        seen = _mtime(config)
        if not args.no_open:
            script = Path(tmp) / "Wilma sign-in.command"
            script.write_text(_sign_in_script(program, args.language, status))
            script.chmod(0o700)
            if subprocess.run(["open", "-a", "Terminal", str(script)],
                              capture_output=True).returncode != 0:
                return _report("no-terminal", "Terminal didn't open. The family runs wilma in "
                               f"Terminal and signs in there, then run: {again} --no-open")

        polls = max(1, math.ceil(args.timeout / WILMA_POLL_SECONDS))
        check_now = args.no_open  # already signed in, perhaps
        for poll in range(polls + 1):
            exit_status = _exit_status(status)
            config_mtime = _mtime(config)
            if check_now or config_mtime != seen or exit_status is not None:
                check_now, seen = False, config_mtime
                try:
                    kids = wilma.list_kids()
                except wilma.WilmaError:
                    kids = None
                if kids is not None:
                    address = _wilma_address(config)
                    return _report("signed-in", city=WILMA_CITIES.get(address or ""),
                                   wilma_address=address, kids=kids)
                if exit_status == 0:
                    return _report("not-signed-in", "The Wilma screen was closed before signing "
                                   f"in. Run this again when the family is ready: {again}")
                if exit_status is not None:
                    return _report("sign-in-failed", "Wilma didn't sign in. The family checks "
                                   "the city and the parent account's username and password, "
                                   f"then signs in again: {again}")
            if poll < polls:
                time.sleep(WILMA_POLL_SECONDS)
    return _report("timeout", f"The family didn't sign in to Wilma within {args.timeout} "
                   "seconds. If the Wilma window is still open, they finish there, then run: "
                   f"{again} --no-open. Otherwise run: {again}")


def _sign_in_script(program: str, language: str, status: Path) -> str:
    # This Python, since the window's PATH may not lead to this program.
    screen = (f"{shlex.quote(sys.executable)} -m family_brief setup wilma "
              f"--screen {shlex.quote(program)} --language {shlex.quote(language)}")
    return (
        "#!/bin/sh\n"
        "clear\n"
        f"{screen}\n"
        f"{{ echo $? > {shlex.quote(str(status))}; }} 2>/dev/null\n"
    )


def _sign_in_screen(program: str, language: str) -> int:
    """Runs the wilma CLI's sign-in in this window, through a pseudo-terminal so this sees what
    it shows: the guide stays on top of each question the CLI clears the screen for, the student
    picker is answered (setup reads every Kid, so which one doesn't matter) and hidden, and once
    the CLI has saved its profile it's ended, before its menu, whose default choice fails with a
    403 for some accounts. Returns 0 once signed in, otherwise the CLI's exit status."""
    text = WILMA_WINDOW.get(language.lower(), WILMA_WINDOW["en"])
    guide = "\r\n".join(text["guide"]).encode() + b"\r\n\r\n"
    out = sys.stdout.buffer
    out.write(guide)
    out.flush()
    config = _wilma_config_path()
    seen = _mtime(config)
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.execv(program, [program])
        finally:
            os._exit(127)
    # The CLI truncates its config before writing it, so a new one counts once it reads whole.
    signed_in = _show_sign_in(
        fd, guide, lambda: _mtime(config) != seen and _wilma_address(config) is not None)
    if signed_in:
        os.kill(pid, signal.SIGTERM)
    status = os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1])
    os.close(fd)
    if signed_in:
        out.write(b"\x1b[H\x1b[2J\x1b[3J")  # the screen and the scrollback
        out.write(f"{text['signed_in']} {text['close']}\n".encode())
    else:
        out.write(f"\n{text['close']}\n".encode())
    out.flush()
    return 0 if signed_in else status


def _show_sign_in(fd: int, guide: bytes, saved) -> bool:
    """Passes the family's typing to the CLI on `fd` and what it shows to the window, until the
    CLI has saved its profile (True) or has ended without (False)."""
    stdin, out = sys.stdin.fileno(), sys.stdout.buffer
    terminal = os.isatty(stdin)
    if terminal:
        def same_size(*_a: Any) -> None:
            fcntl.ioctl(fd, termios.TIOCSWINSZ, fcntl.ioctl(stdin, termios.TIOCGWINSZ, b"\0" * 8))
        same_size()
        signal.signal(signal.SIGWINCH, same_size)
        before = termios.tcgetattr(stdin)
        tty.setraw(stdin)
    reading, hidden, last = [fd, stdin], False, b""
    try:
        while not saved():
            ready = select.select(reading, [], [], 0.1)[0]
            if stdin in ready:
                typed = os.read(stdin, 1024)
                if typed:
                    os.write(fd, typed)
                else:
                    reading.remove(stdin)
            if fd in ready:
                try:
                    shown = os.read(fd, 65536)
                except OSError:  # the CLI has ended
                    shown = b""
                if not shown:
                    return saved()
                recent, last = last[-len(WILMA_MENU):] + shown, shown  # a question split in two
                if not hidden and WILMA_STUDENT_PICKER in recent:
                    os.write(fd, b"\r")  # once: the CLI shows the question again with the answer
                hidden = hidden or WILMA_STUDENT_PICKER in recent or WILMA_MENU in recent
                if not hidden:
                    out.write(shown.replace(NODE_CLEAR, NODE_CLEAR + guide))
                    out.flush()
        return True
    finally:
        if terminal:
            termios.tcsetattr(stdin, termios.TCSAFLUSH, before)
            signal.signal(signal.SIGWINCH, signal.SIG_DFL)


def _wilma_config_path() -> Path:
    """Where the wilma CLI keeps its config, found the way the CLI finds it."""
    if os.environ.get("WILMAI_CONFIG_PATH"):
        return Path(os.environ["WILMAI_CONFIG_PATH"])
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "wilmai" / "config.json"


def _mtime(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _exit_status(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _wilma_address(config: Path) -> str | None:
    """The host of the Wilma the family signed in to last, such as espoo.inschool.fi."""
    try:
        data = json.loads(config.read_text())
        profile = next(p for p in data["profiles"] if p.get("id") == data.get("lastProfileId"))
        url = str(profile["tenantUrl"])
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        return None
    return urlparse(url if "//" in url else f"//{url}").hostname


# ---------------------------------------------------------------- claude

def cmd_claude(args: argparse.Namespace) -> int:
    """`claude setup-token` signs in on Ink's interactive screen, which needs a real terminal
    (without one it stops with "Raw mode is not supported"), and it shows the token only on that
    screen, wrapped to the window. So this opens it in a Terminal window and asks for the token in
    the secret dialog, while the window's script is still there for Terminal to run."""
    from .summarize import CLAUDE_INSTALL, claude_test_call, claude_token_env, find_claude
    from .utils import keychain

    program = find_claude()
    if not program:
        return _report("not-installed", f"Install Claude Code's claude command in Terminal with "
                       f"{CLAUDE_INSTALL}, then run this again.")
    again = f"{_program()} setup claude"
    with tempfile.TemporaryDirectory(prefix="parent-recap-claude-") as tmp:
        if not args.no_open:
            script = Path(tmp) / "Claude sign-in.command"
            script.write_text(_setup_token_script(program))
            script.chmod(0o700)
            if subprocess.run(["open", "-a", "Terminal", str(script)],
                              capture_output=True).returncode != 0:
                return _report("no-terminal", "Terminal didn't open. The family runs claude "
                               f"setup-token in Terminal and signs in there, then run: {again} "
                               "--no-open")
        try:
            typed = secret_dialog.ask(
                "Paste the Claude token for the nightly Brief.\n\nClick Authorize on Claude's page "
                "in your browser. The Terminal window that opened then shows a token starting "
                "with sk-ant-oat01-. Copy all of it and paste it here.")
        except secret_dialog.Cancelled:
            return _report("cancelled", "The family closed the dialog. Run this again when "
                           f"they're ready: {again}")
        except secret_dialog.NoWayToAsk:
            return _report("no-prompt", "No dialog or Terminal prompt could be shown here. The "
                           "family runs claude setup-token in Terminal, then this in Terminal: "
                           f"{again} --no-open")

    # The token has no spaces, so any are from copying it across the lines Terminal wrapped it on.
    token = "".join(typed.split())
    if not CLAUDE_TOKEN.fullmatch(token):
        # Not sent to Claude: it's most likely the family's own password, or only part of the token.
        return _report("not-a-token", "That wasn't a Claude token. Copy the whole token Terminal "
                       f"showed, starting with sk-ant-oat01-, and run this again: {again} --no-open")

    error = claude_test_call(program, claude_token_env(token))
    if error is not None:
        return _report("test-call-failed", "Claude didn't accept the token, so it wasn't kept. "
                       "Check the plan is Claude Pro or Max, then run this again to make a new "
                       f"token: {again}", error=error.replace(token, "<token>").strip()[:200])

    try:
        keychain.set_(CLAUDE_TOKEN_ACCOUNT, token)
    except keyring.errors.KeyringError:
        return _report("keychain-failed", "The test call worked, but macOS didn't let Parent "
                       "Recap save the token in the Keychain. Run this again and click Allow if "
                       f"macOS asks: {again} --no-open")
    install_record.add("keychain", CLAUDE_TOKEN_ACCOUNT)
    return _report("saved", test_call="ok")


def _setup_token_script(program: str) -> str:
    return (
        "#!/bin/sh\n"
        "clear\n"
        "echo 'Parent Recap: sign in to Claude for the nightly Brief.'\n"
        "echo 'Click Authorize on the page that opens in your browser. If no page opens, open the'\n"
        "echo 'link shown below.'\n"
        "echo 'Copy the token this window then shows and paste it in the Parent Recap dialog.'\n"
        "echo\n"
        f"{shlex.quote(program)} setup-token\n"
        "echo\n"
        "echo 'When the token is in the Parent Recap dialog, press Enter here to clear it from'\n"
        "echo 'this window.'\n"
        "read _\n"
        # Clears the screen and the scrollback, so the token isn't left behind in the window.
        "clear; printf '\\033[3J'\n"
        "echo 'You can close this window.'\n"
    )


# ---------------------------------------------------------------- whatsapp

def cmd_whatsapp(args: argparse.Namespace) -> int:
    """macOS grants WhatsApp access per responsible process, so every read goes through a `bg`
    job, which reads with exactly the scheduled job's permission. This process never reads
    WhatsApp itself, so macOS never asks for Terminal, Claude Code or Codex to get access."""
    from . import ops

    if args.read:
        if not os.environ.get(ops.BG_ENV):
            print(f"Run it through bg: {_program()} setup whatsapp, which does that itself.")
            return 2
        return _read_whatsapp(args.days)

    python = os.path.realpath(sys.executable)
    again = f"{_program()} setup whatsapp --no-open"
    grant = ("In Finder the Python file is selected. The family drags it into the list in System "
             "Settings → Privacy & Security → App Management and turns its switch on (if App "
             "Management isn't there, into Full Disk Access the same way), clicks Allow if macOS "
             f"asks whether python3.x may access data from other apps, then run: {again}")
    deadline = time.time() + max(0, args.timeout)
    opened = args.no_open
    while True:
        try:
            read = _read_through_bg(args.config, args.days,
                                    max(WHATSAPP_READ_SECONDS, deadline - time.time()))
        except RuntimeError as e:  # launchctl wouldn't start the job
            return _report("bg-failed", "The background job that reads WhatsApp didn't start. "
                           f"Run this again; if it fails again, run: {_program()} bg doctor",
                           python=python, error=str(e)[:200])
        if read is None:
            return _report("waiting", "Reading WhatsApp didn't finish, most likely because macOS "
                           "is asking whether python3.x may access data from other apps. The "
                           f"family clicks Allow, then run: {again}", python=python)
        permission = read.get("permission")
        if permission == "readable":
            return _report("readable", python=python,
                           chats=_with_hints(read.get("chats") or [], _configured_kids(args.config)))
        if permission == "not-installed":
            return _report("not-installed", "WhatsApp for Mac isn't on this Mac, or has never been "
                           "signed in. The family installs it from the App Store (not the older "
                           "version from WhatsApp's website), links it to their phone and lets "
                           f"the chats sync, then run: {_program()} setup whatsapp")
        if permission != "none":
            return _report("unreadable", "Reading WhatsApp failed (see error). Run this again; if "
                           "it fails again, check that WhatsApp for Mac opens and shows the chats, "
                           f"and run: {_program()} bg doctor", python=python,
                           error=str(read.get("error"))[:200])
        if not opened:
            opened = True
            _, failed = ops.show_python_for_app_management()
            if failed:
                return _report("no-permission", "Finder or System Settings didn't open from "
                               "here. The family runs these in Terminal: "
                               f"{'; '.join(shlex.join(c) for c in failed)}. {grant}",
                               python=python)
        if time.time() + WHATSAPP_POLL_SECONDS > deadline:
            return _report("no-permission", f"The scheduled job's Python can't read WhatsApp yet. "
                           f"{grant}", python=python)
        time.sleep(WHATSAPP_POLL_SECONDS)


def _read_through_bg(config: str | None, days: int, timeout: float) -> dict[str, Any] | None:
    """What the bg job read, or None if it didn't finish in time."""
    from . import ops

    code, out = ops.run_as_job(["setup", "whatsapp", "--read", "--days", str(days)], config,
                               timeout=math.ceil(timeout), echo=False)
    for line in reversed(out.splitlines()):
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                break
    if code == ops.TIMED_OUT:
        return None
    return {"permission": "error", "error": f"exit code {code}: {out.strip()[-200:]}"}


def _read_whatsapp(days: int) -> int:
    """Inside the bg job: one JSON line saying whether WhatsApp could be read, and its chats."""
    from .collectors import whatsapp

    if not whatsapp.DB_FILE.exists():
        print(json.dumps({"permission": "not-installed"}))
        return 1
    error = whatsapp.check_access()
    if error is None:
        try:
            chats = whatsapp.list_groups(days=days)
        except Exception as e:
            error = str(e)
        else:
            print(json.dumps({"permission": "readable", "chats": chats}, ensure_ascii=False))
            return 0
    print(json.dumps({"permission": "none"} if error == whatsapp.NO_ACCESS
                     else {"permission": "error", "error": error}, ensure_ascii=False))
    return 1


def _configured_kids(config: str | None) -> list[Kid]:
    try:
        return Config.load(config).kids
    except Exception:  # no config yet: the chats are listed without hints
        return []


def _with_hints(chats: list[dict[str, Any]], kids: list[Kid]) -> list[dict[str, Any]]:
    """Adds a hint to each chat whose name mentions a Kid's name, class, school or club, naming
    the Kids by their name in the config, which is what whatsapp.chats[].kid takes."""
    out = []
    for chat in chats:
        matched_kids: list[str] = []
        matched: list[str] = []
        for kid in kids:
            hits = [t for t in kid.chat_hint_terms() if _mentions(chat["name"], t)]
            if hits:
                matched_kids.append(kid.name)
                matched += [t for t in hits if t not in matched]
        out.append({**chat, "hint": {"kids": matched_kids, "matched": matched}} if matched_kids
                   else chat)
    return out


def _mentions(text: str, term: str) -> bool:
    """Whether `term` is in `text` as a word of its own: Leo isn't in Leonardo, nor 3B in 13B.
    Chinese and Japanese aren't written with spaces, so next to their characters any term counts."""
    text, term = text.casefold(), term.strip().casefold()
    if len(term) < 2:
        return False

    def apart(c: str) -> bool:
        return not c.isalnum() or ord(c) >= 0x2E80
    for m in re.finditer(re.escape(term), text):
        before = text[m.start() - 1] if m.start() else " "
        after = text[m.end()] if m.end() < len(text) else " "
        if (apart(before) or apart(term[0])) and (apart(after) or apart(term[-1])):
            return True
    return False


# ---------------------------------------------------------------- myclub

def cmd_myclub(args: argparse.Namespace) -> int:
    """The link carries the Kid's personal token, so it's handled like the other secrets: the
    result and any error name only MyClub's server and the HTTP status, never the link."""
    from .collectors import myclub

    path = Path(os.path.expanduser(os.path.expandvars(args.config or "~/.family/config.yaml")))
    try:
        kids = [k.name for k in Config.load(path).kids]
    except FileNotFoundError:
        return _report("no-config", "The config isn't written yet. Write it with the Kids first, "
                       "then run this again.")
    except Exception:  # not its error: a config check quotes values, which can be other links
        return _report("bad-config", f"The config at {path} can't be read. Run {_program()} "
                       "doctor, fix what it names, then run this again.")
    if args.kid not in kids:
        return _report("no-kid" if args.kid is None else "no-such-kid", "Run it again with --kid "
                       f"and one of the Kids' names: {_program()} setup myclub --kid NAME",
                       kids=kids)
    again = f"{_program()} setup myclub --kid {shlex.quote(args.kid)}"
    if not args.no_open:
        subprocess.run(["open", MYCLUB_URL], capture_output=True)

    try:
        url = secret_dialog.ask(
            f"Paste {args.kid}'s MyClub calendar link.\n\nSign in to MyClub in your browser "
            f"({MYCLUB_URL}), open {args.kid}'s calendar, choose Calendar subscription (Tilaa "
            "kalenteri) and copy the link starting with webcal://.")
    except secret_dialog.Cancelled:
        return _report("cancelled", "The family closed the dialog. Run this again when they're "
                       f"ready: {again}")
    except secret_dialog.NoWayToAsk:
        return _report("no-prompt", "No dialog or Terminal prompt could be shown here. The family "
                       f"runs this in Terminal: {again} --no-open")

    url = "".join(url.split())
    if not _is_myclub_link(url):
        # Not downloaded: it's most likely the family's MyClub password, or another page's address.
        return _report("not-a-myclub-link", "That wasn't a MyClub calendar link. Copy the link "
                       "from Calendar subscription (Tilaa kalenteri) on the Kid's MyClub calendar, "
                       f"starting with webcal://, and run this again: {again} --no-open")

    try:
        text = myclub.download(url)
    except myclub.FetchError as e:  # names only the server and the HTTP status
        return _report("link-failed", "The link didn't open, so it wasn't saved. Copy it again "
                       f"from MyClub and run this again: {again} --no-open", error=str(e))
    if "BEGIN:VCALENDAR" not in text:
        return _report("not-a-calendar", "The link opened a page, not a calendar, so it wasn't "
                       "saved. Copy the link from Calendar subscription (Tilaa kalenteri) on the "
                       f"Kid's MyClub calendar, not the browser's address bar: {again} --no-open")

    try:
        myclub.save_link(path, args.kid, url)
    except (OSError, ValueError) as e:  # names the Kid and the config, never the link
        return _report("save-failed", "The link works, but it couldn't be saved in the config "
                       "(see error).", error=str(e)[:300])
    return _report("saved", kid=args.kid, events=text.count("BEGIN:VEVENT"))


def _is_myclub_link(url: str) -> bool:
    parts = urlparse(url)
    host = parts.hostname or ""
    return (parts.scheme in ("webcal", "https") and (host == "myclub.fi" or host.endswith(".myclub.fi"))
            and bool(parts.path.strip("/")))


def _program() -> str:
    """How to run this program again from Terminal, where it isn't on the PATH."""
    if sys.argv[0].endswith("__main__.py"):
        return f"{shlex.quote(sys.executable)} -m family_brief"
    return shlex.quote(sys.argv[0]) if "/" in sys.argv[0] else "family-brief"


def _configured_address(config: str | None) -> str | None:
    try:
        cfg = Config.load(config)
    except Exception:  # no config yet: setup can connect Gmail before writing it
        return None
    return cfg.gmail.username
