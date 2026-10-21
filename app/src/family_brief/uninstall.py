"""uninstall — remove what setup created on this computer, after listing it and a confirmation.

It first looks in every place setup writes to (docs/setup-internals.md lists them) and lists what
it found. Something there that isn't this install's, such as a launchd job with Parent Recap's
name that runs another program, or a config Parent Recap didn't write, stops it before it removes
anything. The archive of past Briefs is kept or removed as the family chooses. The Wilma
sign-in, and Claude Code's plugin and marketplace go only when setup's record has that setup
installed them. The family's accounts (Gmail, Google, Wilma, WhatsApp, MyClub) are never
touched: it says where they can take back what they gave Parent Recap.

Windows doesn't let a program be removed while it runs, so there the program and its own Python,
which uninstall runs from, go from a hidden PowerShell once uninstall has ended.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Callable

import yaml
from pydantic import ValidationError

from . import (ai_filter_report, chat_install, install_record, ops, own_node, run_lock,
               setup_save, setup_wilma, sources, summarize, tools, whatsapp_python)
from .collectors.gmail import keychain_account
from .config import Config
from .system import desktop, jobs, macos_only, secrets, wake, windows_only

OK, FAIL = ops.OK, ops.FAIL
# install.sh's and install.ps1's pinned ones, and the folders they download them into.
RUNTIMES = re.compile(r"^(python-[\w.+-]+|node-v[\w.-]+|\.incoming|\.download)$")
# What `npm install -g --prefix` makes in the wilma CLI's folder; on Windows the package goes in
# node_modules, with npm's wrappers for each of its commands beside it (_npm_made).
NPM_PREFIX = {"bin", "lib", "etc", "share", "node_modules"}
DATED = re.compile(r"^\d{4}-\d{2}-\d{2}\.(md|raw\.json)$")  # an archived Brief and its messages
MARKETPLACE = "kinlace"


class NotRemoved(Exception):
    """An item that couldn't be removed; the message says what the family can do instead."""


@dataclass
class Item:
    what: str
    where: str
    remove: Callable[[], None]
    path: Path | None = None  # the file or folder it is, if it is one
    account: str | None = None  # the Keychain account it is, if it is one
    asks: str = ""            # what the system asks the family when it's removed
    archive: bool = False     # removed only when the family chooses not to keep the archive
    last: bool = False        # setup's record: removed only once everything else is gone
    running: bool = False     # what uninstall runs from: on Windows it goes once uninstall ends


def _file(what: str, p: Path, **kw) -> Item:
    remove = partial(shutil.rmtree, p) if p.is_dir() and not p.is_symlink() else p.unlink
    return Item(what, _show(p), remove, path=p, **kw)


@dataclass
class Survey:
    items: list[Item] = field(default_factory=list)     # in the order they're removed
    stops: list[str] = field(default_factory=list)      # not this install's: nothing is removed
    left: list[str] = field(default_factory=list)       # not Parent Recap's: left as it is
    notes: list[str] = field(default_factory=list)      # what the family can do themselves
    folders: list[Path] = field(default_factory=list)   # removed at the end if empty
    claude_left: str | None = None                      # how to remove the plugin in Claude Code: the last note


def register(sub) -> None:
    p = sub.add_parser("uninstall", help="Remove Parent Recap from this computer: lists what setup "
                       "created, and removes it once you confirm")
    p.add_argument("--confirm", action="store_true", help="Remove what it lists (without it, it "
                   "only lists, or asks when run in Terminal)")
    archive = p.add_mutually_exclusive_group()
    archive.add_argument("--keep-archive", dest="archive", action="store_const", const="keep",
                         help="Keep the archive of past Briefs")
    archive.add_argument("--remove-archive", dest="archive", action="store_const", const="remove",
                         help="Remove the archive of past Briefs too")
    p.set_defaults(func=cmd_uninstall, archive=None)


def cmd_uninstall(args: argparse.Namespace) -> int:
    config = Path(args.config).expanduser() if args.config else Path.home() / ".family" / "config.yaml"
    s = survey(config)
    _print_survey(s)
    if s.stops:
        print(f"\n{FAIL} Stopped, and nothing was removed. These are in the places Parent Recap "
              "uses, but they aren't this install's:")
        for stop in s.stops:
            print(f"  • {stop}")
        return 1
    if not s.items:
        print(f"\nParent Recap isn't installed on this {_computer()}: there's nothing to remove.")
        _print_notes(s)
        return 0
    has_archive = any(i.archive for i in s.items)
    if args.confirm:
        if has_archive and args.archive is None:
            print("\nSay whether to keep the archive of past Briefs: run it again with "
                  "--confirm --keep-archive, or --confirm --remove-archive. Nothing was removed.")
            return 2
        keep = args.archive != "remove"
    elif desktop.typing_at_terminal():
        try:
            keep = not has_archive or not input(
                "\nKeep the archive of past Briefs? [Y/n] ").strip().lower().startswith("n")
            sure = input("Remove everything listed above? Type yes to remove: ").strip().lower()
        except EOFError:  # the terminal closed its input, such as with Ctrl+D or Ctrl+Z
            sure = ""
        if sure != "yes":
            print("\nNothing was removed.")
            return 1
    else:
        print("\nNothing has been removed yet. To remove it, run this again with --confirm and "
              "--keep-archive (keeps the archive of past Briefs) or --remove-archive (removes it too).")
        _print_notes(s)
        return 0
    return _remove(s, keep_archive=keep)


# ---------------------------------------------------------------- what setup created

def survey(config: Path) -> Survey:
    s = Survey()
    home = Path.home()
    cfg = _config(config, s)
    defaults = cfg or Config(kids=[])
    usual = _usual_folders(home)
    programs = [Path(p) for p in install_record.entries("program")] or [f / "app" for f in usual]
    archive = defaults.archive.resolved_dir()

    _jobs(s, programs)
    _wake(s, defaults)
    _keychain(s, defaults)
    for source in sources.ALL:
        if source.survey:
            source.survey(s, programs)
    _claude_code(s)
    _codex_skills(s, home)
    _plugin_copies(s, usual)
    _archive(s, defaults)
    _config_and_state(s, config, cfg, defaults)
    logs = [*(Path(p) for p in install_record.entries("logs")), *(p.parent / "logs" for p in programs),
            *(f / "logs" for f in usual)]
    for d in dict.fromkeys(logs):
        if d.is_dir():
            s.items.append(_file("Logs", d))
    _job_logs(s, archive / "logs")
    for p in programs:
        _program(s, p)  # last of all: this command may be running from it
    runtimes = [*(Path(p) for p in install_record.entries("runtime")), *(f / "runtime" for f in usual)]
    pythons = [python for d in dict.fromkeys(runtimes) for python in _runtime(s, d)]
    if install_record.exists():
        s.items.append(_file("Setup's record of what it created", install_record.path(), last=True))

    for folder in dict.fromkeys([*(p.parent for p in programs), archive, config.parent]):
        _left_in(s, folder, archive)
    # Only the folders setup makes: an archive in a folder like ~/Documents stays, even empty.
    s.folders = list(dict.fromkeys([home / "ParentRecap" / "weekend_events", *usual,
                                    *(p.parent for p in programs), home / ".family"]))
    if cfg and run_lock.is_busy(cfg):
        s.stops.append("A Parent Recap run is going right now. Wait a few minutes for it to "
                       "finish, then run uninstall again.")
    _notes(s, defaults, pythons)
    return s


def _computer() -> str:
    return "PC" if sys.platform == "win32" else "Mac"


def _usual_folders(home: Path) -> list[Path]:
    """Where the install puts the program, for an install whose record is gone: install.ps1's
    %LOCALAPPDATA%\\ParentRecap on Windows, and install.sh's ~/ParentRecap."""
    local = os.environ.get("LOCALAPPDATA") if sys.platform == "win32" else None
    return list(dict.fromkeys([*([Path(local) / "ParentRecap"] if local else []),
                               home / "ParentRecap"]))


def _config(path: Path, s: Survey) -> Config | None:
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        cfg = Config.model_validate(data)
        if isinstance(data, dict) and ("gmail" in data or "email" in data):
            return cfg
    except (OSError, yaml.YAMLError, ValidationError):
        pass
    s.stops.append(f"{_show(path)} isn't a Parent Recap config (it has no Kids, Gmail and email "
                   "settings), so another program may be using that folder. If it's Parent "
                   "Recap's after all, fix it so `parent-recap doctor` reads it, then run "
                   "uninstall again.")
    return None


def _jobs(s: Survey, programs: list[Path]) -> None:
    # The venv's python is a symlink out of the venv, so only the folder it sits in is resolved.
    venvs = [(p / ".venv").resolve() for p in programs]
    found = jobs.found()
    for job in found:
        prog = job.program
        if not (prog[1:3] == ["-m", "family_brief"] and any(
                Path(prog[0]).parent.resolve().is_relative_to(v) for v in venvs)):
            runs = prog[0] if prog else "a program it can't read"
            s.stops.append(f"{_show(job.where)} is a scheduled job named {job.system_name}, but "
                           f"it runs {runs}, not this install in "
                           f"{', '.join(_show(p) for p in programs)}. It may be another Parent "
                           "Recap install from other folders: remove that one first, with its own "
                           "uninstall.")
    if found:
        s.items.append(Item(f"Scheduled job{'s' if len(found) != 1 else ''} "
                            f"{', '.join(j.system_name for j in found)}",
                            ", ".join(_show(j.where) for j in found), jobs.remove_all))


def _wake(s: Survey, cfg: Config) -> None:
    status = wake.status(cfg.schedule.daily_hour, cfg.schedule.daily_minute)
    if not wake.has_own_schedule():
        if status.ours:  # the wake goes with the jobs
            s.left.append(f"{status.ours}: your own power setting, which setup didn't change")
    elif status.ours and not status.others:
        s.items.append(Item("Wake schedule", status.ours, _cancel_wake,
                            asks="macOS asks for your Mac password to remove it"))
    elif status.ours:
        s.left.append(f"The wake schedule setup gave ({status.ours}): macOS can only cancel it "
                      f"together with {'; '.join(status.others)}. To cancel both, run this in "
                      f"Terminal: {wake.manual_cancel()}")
    elif status.others:
        s.left.append(f"This Mac's repeating power schedule ({'; '.join(status.others)}): setup "
                      "didn't set it")


def _cancel_wake() -> None:
    try:
        wake.cancel()
    except wake.NotCancelled as e:
        raise NotRemoved(str(e))


def _keychain(s: Survey, cfg: Config) -> None:
    accounts = [*install_record.entries("keychain"),
                *(account for source in sources.ALL for account in source.secrets(cfg)),
                "claude-oauth-token", "anthropic-api-key"]
    for account in dict.fromkeys(accounts):
        if secrets.has(account):  # the secret itself isn't read
            s.items.append(Item(f"{secrets.STORE} item", secrets.where(account),
                                partial(_delete_secret, account), account=account,
                                asks=secrets.deleting_asks()))


def gmail_secrets(cfg: Config) -> list[str]:
    """The App Password of the config's Gmail, also when setup's record of it is gone."""
    username = cfg.gmail.username or (cfg.kids[0].wilma_username if cfg.kids else None)
    return [keychain_account(username)] if username else []


def _delete_secret(account: str) -> None:
    try:
        secrets.delete(account)
    except secrets.SecretsError as e:
        raise NotRemoved(str(e))


def survey_wilma(s: Survey, programs: list[Path]) -> None:
    """The profile, with the Wilma password, that setup's record has, since one that was there
    before setup isn't Parent Recap's to remove, and the wilma CLI setup installed in Parent
    Recap's folder (ADR 0011). A wilma CLI of the Mac's own isn't touched."""
    config = setup_wilma.config_path()
    for profile_id in install_record.entries("wilma-profile"):
        profile = setup_wilma.profile(config, profile_id)
        if profile is not None:
            s.items.append(Item("Wilma sign-in", f"{profile.get('username')} at "
                                f"{profile.get('tenantUrl')}, in {_show(config)}",
                                partial(setup_wilma.remove_profile, config, profile_id)))
    # Before ADR 0011 the record held the npm package's name, not a folder.
    recorded = [Path(p) for p in install_record.entries("wilma-cli") if Path(p).is_absolute()]
    usual = [*(p.parent / "wilma" for p in programs), Path.home() / "ParentRecap" / "wilma"]
    for folder in dict.fromkeys([*recorded, *usual]):
        if not folder.is_dir() or folder.is_symlink():
            continue
        made = _npm_made(folder)
        others = sorted(p.name for p in folder.iterdir() if p.name not in made)
        if not others:
            s.items.append(_file("The wilma CLI", folder))
        else:
            s.stops.append(f"{_show(folder)} is where Parent Recap installs the wilma CLI, but "
                           f"it holds other files too: {', '.join(others)}")


def survey_whatsapp(s: Survey, programs: list[Path]) -> None:
    p = whatsapp_python.path()
    if p.exists():
        s.items.append(_file("Which Python reads WhatsApp", p))


def _npm_made(prefix: Path) -> set[str]:
    """The names npm gives what it puts in `prefix`: on Windows also a wrapper of each of the
    package's commands, without an extension, .cmd and .ps1, named in its package.json."""
    try:
        bins = json.loads((prefix / "node_modules" / own_node.WILMA_PACKAGE / "package.json")
                          .read_text(encoding="utf-8")).get("bin")
    except (OSError, ValueError, AttributeError):
        bins = None
    if isinstance(bins, str):  # one command, named after the package
        bins = {own_node.WILMA_PACKAGE.rsplit("/", 1)[-1]: bins}
    commands = list(bins) if isinstance(bins, dict) else []
    return NPM_PREFIX | {f"{c}{ext}" for c in commands for ext in ("", ".cmd", ".ps1")}


def _claude_code(s: Survey) -> None:
    """Claude Code's plugin and its marketplace, when setup's record has that setup installed
    them. Ones the family added themselves stay, and the note says how to remove them only when
    Claude Code lists them, since a family without them would get an error from Claude Code."""
    plugins = install_record.entries("claude-plugin")
    marketplaces = install_record.entries("claude-marketplace")
    program = summarize.find_claude()
    if program is None:
        return
    both = (f"/plugin uninstall {chat_install.PLUGIN}, then /plugin marketplace remove "
            f"{MARKETPLACE}.")
    try:
        installed, listed = chat_install.claude_installed(program)
    except (OSError, subprocess.SubprocessError, ValueError):
        s.claude_left = (f"In Claude Code, remove the plugin itself with {both}"
                         if plugins or marketplaces else
                         f"If you added the plugin in Claude Code yourself, remove it there with {both}")
        return
    if chat_install.PLUGIN in installed and chat_install.PLUGIN not in plugins:
        s.claude_left = f"In Claude Code, remove the plugin itself with {both}"
    elif MARKETPLACE in listed and MARKETPLACE not in marketplaces:
        s.claude_left = (f"In Claude Code, remove Parent Recap's marketplace with /plugin "
                         f"marketplace remove {MARKETPLACE}.")
    for plugin in plugins:
        if plugin in installed:
            s.items.append(Item("Claude Code plugin", plugin,
                                partial(_in_claude, chat_install.uninstall_claude_plugin, program,
                                        plugin, f"/plugin uninstall {plugin}")))
    for marketplace in marketplaces:  # after its plugin
        if marketplace in listed:
            s.items.append(Item("Claude Code marketplace", marketplace,
                                partial(_in_claude, chat_install.remove_claude_marketplace,
                                        program, marketplace,
                                        f"/plugin marketplace remove {marketplace}")))


def _in_claude(remove: Callable[[str, str], None], program: str, name: str, typed: str) -> None:
    try:
        remove(program, name)
    except (OSError, subprocess.SubprocessError):
        raise NotRemoved(f"Claude Code didn't remove it. In Claude Code, type {typed}")


def _codex_skills(s: Survey, home: Path) -> None:
    usual = [home / ".agents" / "skills" / f"parent-recap-{name}" for name in ("setup", "manage")]
    for d in dict.fromkeys([*usual, *(Path(p) for p in install_record.entries("codex-skill"))]):
        if not d.exists():
            continue
        try:
            text = (d / "SKILL.md").read_text(encoding="utf-8")
        except OSError:
            text = ""
        # install.sh --codex writes the skill's own name and a note saying where the plugin is.
        if f"\nname: {d.name}\n" in text and "This skill is installed in Codex" in text:
            s.items.append(_file("Codex skill", d))
        else:
            s.stops.append(f"{_show(d)} has the name of Parent Recap's Codex skill, but "
                           "Parent Recap's installer didn't write it")


def _plugin_copies(s: Survey, usual: list[Path]) -> None:
    for d in dict.fromkeys([*(f / "plugin" for f in usual),
                            *(Path(p) for p in install_record.entries("plugin"))]):
        if not d.exists():
            continue
        try:
            name = json.loads((d / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")).get("name")
        except (OSError, ValueError, AttributeError):
            name = None
        if name == "parent-recap":
            s.items.append(_file("Plugin copy", d))
        else:
            s.stops.append(f"{_show(d)} is where Parent Recap's plugin copy goes, but it isn't one")


def _archive(s: Survey, cfg: Config) -> None:
    archive = cfg.archive.resolved_dir()
    dated = sorted(p for p in archive.iterdir() if DATED.match(p.name)) if archive.is_dir() else []
    days = len({p.name.split(".", 1)[0] for p in dated})
    if dated:
        s.items.append(Item("Archive of past Briefs", f"{days} day{'s' if days != 1 else ''} in "
                            f"{_show(archive)}", partial(_unlink_all, dated), archive=True))
    # --held-back makes it from the archive again each time, so it goes also when the archive stays.
    listed = archive / ai_filter_report.HELD_BACK_LIST
    if listed.is_file():
        s.items.append(_file("List of held-back messages", listed))
    weekend = cfg.weekend_events.resolved_dir()
    picks = sorted(p for p in weekend.iterdir() if DATED.match(p.name)) \
        if weekend.is_dir() and weekend != archive else []
    if picks:
        s.items.append(Item("Archive of past Weekend Picks", _show(weekend),
                            partial(_unlink_all, picks), path=weekend, archive=True))


def _unlink_all(paths: list[Path]) -> None:
    for p in paths:
        p.unlink(missing_ok=True)


def _job_logs(s: Survey, logs: Path) -> None:
    """The scheduled jobs write their logs to the archive's logs/, which may be a folder of the
    family's own, such as ~/Documents/logs: then only the jobs' own files go."""
    if logs in {i.path for i in s.items} or not logs.is_dir():
        return
    mine = sorted(p for p in logs.iterdir() if re.match(r"^(run|weekend-events)-std(out|err)\.log$", p.name))
    if mine:
        s.items.append(Item("Logs", ", ".join(_show(p) for p in mine), partial(_unlink_all, mine)))


def _config_and_state(s: Survey, config: Path, cfg: Config | None, defaults: Config) -> None:
    family = config.parent
    state = defaults.resolved_state_path()
    found: list[tuple[str, Path]] = []
    if cfg is not None:
        found.append(("Config", config))
    found += [("Config backup", p) for p in sorted(family.glob(config.name + ".bak*"))]
    found += [("Setup's progress", setup_save.progress_path(config)),
              ("State", state), ("Run lock", run_lock.lock_path(defaults)),
              ("The program's text in other languages", state.parent / "languages"),
              ("Where Claude, Codex and Node were found", tools.remembered_path()),
              ("Google Calendar app file", family / "calendar_credentials.json"),
              ("Google Calendar authorization", family / "calendar_token.json")]
    for what, p in dict.fromkeys(found):
        if p.exists():
            s.items.append(_file(what, p))


def _program(s: Survey, program: Path) -> None:
    if not program.exists():
        return
    try:
        ours = re.search(r'^name\s*=\s*"family-brief"', (program / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    except OSError:
        ours = None
    if ours:
        s.items.append(_file("The program", program, running=True))
    else:
        s.stops.append(f"{_show(program)} is where the program goes, but it isn't Parent Recap")


def _runtime(s: Survey, runtime: Path) -> list[Path]:
    """install.sh's pinned Python and Node, after the program that runs on them. Returns the
    real path of each Python, which WhatsApp's permission names."""
    if not runtime.is_dir():
        return []
    inside = sorted(runtime.iterdir())
    if not all(p.is_dir() and not p.is_symlink() and RUNTIMES.match(p.name) for p in inside):
        s.stops.append(f"{_show(runtime)} is where Parent Recap keeps its own Python and Node, "
                       "but it holds other files too")
        return []
    s.items.append(_file("Parent Recap's own Python and Node", runtime, running=True))
    return [(p / "bin" / "python3").resolve() for p in inside
            if p.name.startswith("python-") and (p / "bin" / "python3").exists()]


def _left_in(s: Survey, folder: Path, archive: Path) -> None:
    """Say which other files share Parent Recap's folders, since they stay."""
    if not folder.is_dir():
        return
    ours = {i.path for i in s.items}
    for p in sorted(folder.iterdir()):
        if p not in ours and not (folder == archive and DATED.match(p.name)):
            s.left.append(f"{_show(p)}: not Parent Recap's")


def _notes(s: Survey, cfg: Config, pythons: list[Path]) -> None:
    accounts = ", ".join(source.label() for source in sources.ALL)
    s.notes.append(f"Your accounts aren't touched: {accounts} and Google stay as they are.")
    s.notes += [note for source in sources.ALL for note in source.notes(cfg, pythons)]
    if cfg.google_calendar.mode == "google" or any(i.what == "Google Calendar authorization"
                                                   for i in s.items):
        s.notes.append("Parent Recap's access to Google Calendar stays until you remove it in your "
                       "Google account: https://myaccount.google.com/connections")
    if s.claude_left:
        s.notes.append(s.claude_left)


def gmail_notes(cfg: Config, pythons: list[Path]) -> list[str]:
    return ["The Gmail App Password keeps working until you delete it in your Google account: "
            "https://myaccount.google.com/apppasswords (it's named Parent Recap)."]


def wilma_notes(cfg: Config, pythons: list[Path]) -> list[str]:
    wilma_config = setup_wilma.config_path()
    profiles = setup_wilma.profile_ids(wilma_config)
    # Each recorded profile still there is listed for removal above.
    ours = set(install_record.entries("wilma-profile"))
    if not wilma_config.exists() or (profiles and set(profiles) <= ours):
        return []
    folder = _show(wilma_config.parent)
    remove = (f"in PowerShell: Remove-Item -Recurse -Force {folder}" if sys.platform == "win32"
              else f"in Terminal: rm -rf {folder}")
    return [f"The wilma CLI keeps a Wilma password, not encrypted, in {folder}, from a sign-in "
            "setup didn't make, so uninstall leaves it. If you only used it for Parent Recap, "
            f"remove it {remove}"]


def whatsapp_notes(cfg: Config, pythons: list[Path]) -> list[str]:
    if not macos_only.offers("whatsapp"):
        return []
    python = f" ({', '.join(str(p) for p in pythons)})" if pythons else ""
    return [f"If you gave the program's Python{python} access to WhatsApp, remove it by hand from "
            "System Settings → Privacy & Security → Full Disk Access: a program can't change that "
            "list."]


# ---------------------------------------------------------------- listing and removing

def _print_survey(s: Survey) -> None:
    print(f"Parent Recap on this {_computer()}. Nothing has been removed yet.")
    if s.items and not install_record.exists():
        print("This install has no record of what setup created (it was set up with 0.4.1 or "
              "older), so these were found in the places setup uses. Check them before you confirm.")
    for heading, items in (("Uninstall removes:", [i for i in s.items if not i.archive]),
                           ("You choose whether to keep or remove:", [i for i in s.items if i.archive])):
        if items:
            print(f"\n{heading}")
            for i in items:
                print(f"  • {i.what}: {i.where}" + (f" ({i.asks})" if i.asks else ""))
    if s.left:
        print("\nLeft as it is:")
        for line in s.left:
            print(f"  • {line}")


def _print_notes(s: Survey) -> None:
    print("\nWhat uninstall leaves to you:")
    for note in s.notes:
        print(f"  • {note}")


def _remove(s: Survey, keep_archive: bool) -> int:
    print()
    # A Keychain deletion macOS refuses must still be found on the next try, after the config
    # that named its Gmail account is gone.
    for i in s.items:
        if i.account:
            install_record.add("keychain", i.account)
    failed = 0
    later: list[Item] = []  # on Windows, what goes once uninstall has ended

    def hand_over() -> None:
        nonlocal failed
        try:
            windows_only.remove_after_exit([i.path for i in later if i.path], s.folders)
        except OSError as e:
            failed += len(later)
            for i in later:
                print(f"{FAIL} {i.what} not removed ({i.where}): {e}", flush=True)
        else:
            for i in later:
                print(f"{OK} {i.what} goes once uninstall has ended: {i.where}", flush=True)
        later.clear()

    for i in s.items:
        if i.running and sys.platform == "win32":
            later.append(i)
            continue
        if later:  # before the record, which stays if it fails
            hand_over()
        if (i.archive and keep_archive) or (i.last and failed):
            continue
        try:
            i.remove()
        except Exception as e:
            failed += 1
            print(f"{FAIL} {i.what} not removed ({i.where}): {e}", flush=True)
        else:  # a line that can't be printed is no failed removal
            print(f"{OK} {i.what} removed: {i.where}", flush=True)
    if later:
        hand_over()
    if not failed:  # also when the lines above started the record
        install_record.path().unlink(missing_ok=True)
    for d in sorted(s.folders, key=lambda p: len(p.parts), reverse=True):
        try:
            d.rmdir()  # only when empty: what's left in it isn't Parent Recap's, or is kept
        except OSError:
            pass
    for i in s.items:
        if i.archive and keep_archive:
            print(f"Kept: {i.what}, {i.where}. Delete it yourself whenever you like.")
    _print_notes(s)
    if failed:
        print(f"\n{failed} item{'s' if failed != 1 else ''} couldn't be removed; see {FAIL} above. "
              "Once they're done, run uninstall again to finish.")
        return 1
    print(f"\nParent Recap is removed from this {_computer()}. Setup can install it again any time.")
    return 0


def _show(p: Path) -> str:
    home = Path.home()
    return f"~/{p.relative_to(home).as_posix()}" if p.is_relative_to(home) else str(p)
