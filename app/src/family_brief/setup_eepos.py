"""Adding an Eepos sign-in from a terminal (ADR 0038), for the maintainers to turn Eepos on for a
Household by hand. The setup page's Eepos step (#791) keeps and removes a sign-in through `keep`
and `remove` here, and the hosted sign-up (#769) reuses `check` and `_save`.

`setup eepos --school emo --username NAME` has the guardian's password typed at the secret store's
own prompt in that terminal (`secrets.put_typed`), so it passes through no dialog, chat or command
line. It waits under a name of its own while this signs in to the school with it and reads the
guardian's students; only once that works does it take the sign-in's own name, so a password typed
wrong never replaces one that works. Each student is matched to one of the Household's Kids by
name, and the sign-in goes into the config: the school, the username and the matched Kids by the
students' ids. Nothing else of the students is kept. Adding the same username at the same school
again replaces that sign-in, and a second school is a sign-in of its own.
"""
from __future__ import annotations

import os
import shlex
import sys
from contextlib import AbstractContextManager, nullcontext
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml

from . import install_record
from .collectors import eepos
from .config import Config, EeposSignin
from .setup_report import PLAIN_TERMINAL, keychain_failure, program
from .setup_save import _write_private
from .system import secrets


def again(school: str, username: str) -> str:
    """The command that adds this sign-in again."""
    return f"{program()} setup eepos --school {shlex.quote(school)} --username {shlex.quote(username)}"


def check(cfg: Config, signin: EeposSignin, password: str) -> dict[str, Any]:
    """Signs in to the sign-in's school with `password` and reads the guardian's students and the
    calendar: each student's name with the Kid it is (None for none), and how many events the
    next LOOKAHEAD_DAYS days have. Raises eepos.EeposError when that doesn't work."""
    with eepos.Portal(signin.school) as portal:
        portal.sign_in(signin.username, password)
        students = portal.students()
        today = eepos.today(cfg.timezone)
        entries = portal.calendar(today, today + timedelta(days=eepos.LOOKAHEAD_DAYS))
    kids = eepos.kids_of(signin, students, cfg.kids)
    return {"students": [(s, kids[s.id]) for s in students],
            "events": len(eepos.events(entries, signin.school, kids, cfg.timezone))}


def add(path: Path, school_given: str, username: str) -> tuple[str, str | None, dict[str, Any]]:
    """Adds the sign-in as `username` at the school `school_given` to the config at `path`: the
    step's result, what to do next when it isn't saved, and what else it reports."""
    try:
        cfg = Config.load(path)
    except FileNotFoundError:
        return "no-config", "The config isn't written yet. Write it with the Kids first, then run this again.", {}
    except Exception:  # not its error: a config check quotes values, which can be secrets
        return "bad-config", (f"The config at {path} can't be read. Run {program()} doctor, fix "
                              "what it names, then run this again."), {}
    school = eepos.school_of(school_given)
    if school is None and eepos.is_website(school_given):
        return "school-website", ("That looks like the school's website. Copy the address from "
                                  "the browser on the school's Eepos sign-in page instead: it ends "
                                  "in .eepos.fi, such as espoo.eepos.fi."), {}
    if school is None:
        return "not-a-school", ("That isn't an Eepos school's address. Give the part before "
                                ".eepos.fi, such as emo for emo.eepos.fi."), {}
    username = username.strip()
    if not username:
        return "no-username", "Run it again with the guardian's Eepos username: --username NAME", {}
    retry = again(school, username)
    if not secrets.reachable():  # macOS in tmux or over SSH, where its prompt never shows
        return "keychain-not-reachable", (
            "macOS doesn't let Parent Recap keep the password in the Keychain from inside tmux or "
            f"SSH, and shows no prompt there, so nothing was asked or saved. Run this again "
            f"{PLAIN_TERMINAL}: {retry}"), {}

    # On stderr: the step's one line of JSON is all it prints on stdout.
    print(f"Type the Eepos password of {username} at {eepos.address(school)} "
          f"(the {secrets.STORE} asks for it; the input is hidden).", file=sys.stderr, flush=True)
    try:
        secrets.put_typed(eepos.TYPED)
        try:
            password = (secrets.get(eepos.TYPED) or "").strip()
        finally:
            secrets.delete(eepos.TYPED)
    except secrets.SecretsError as e:
        return "not-stored", f"The password wasn't stored, so nothing was saved: {e}. Run this again: {retry}", {}
    if not password:
        return "not-stored", f"No password was typed, so nothing was saved. Run this again: {retry}", {}

    result, extra = keep(path, cfg, school, username, password)
    return result, _next(result, school, username, extra), _reported(result, extra)


def keep(path: Path, cfg: Config, school: str, username: str, password: str,
         saving: AbstractContextManager[Any] = nullcontext()) -> tuple[str, dict[str, Any]]:
    """Signs in as `username` at `school` with `password` and, only once that works, keeps the
    password in the secret store under the sign-in's own name and the sign-in in the config at
    `path`, holding `saving` only while it writes the config: the result, with the school's
    address, each student with the Kid it matched (None for none) and the events once saved, or
    the error otherwise. A password that doesn't work never replaces one that does. `setup eepos`
    and the setup page's Eepos step both keep a sign-in through this."""
    signin = EeposSignin(school=school, username=username)
    try:
        checked = check(cfg, signin, password)
    except eepos.WrongPassword:
        return "wrong-password", {}
    except eepos.Changed as e:
        return "changed", {"error": str(e)}
    except eepos.EeposError as e:
        return "no-answer", {"error": str(e)}

    matched = {s.id: kid for s, kid in checked["students"] if kid}
    signin = EeposSignin(school=school, username=username, kids=matched)
    try:
        secrets.put(eepos.secret_name(school, username), password)
    except secrets.SecretsError as e:
        result, code = keychain_failure(e)
        if result == "keychain-not-reachable":
            return result, code
        return "not-stored", {"error": str(e), **code}
    install_record.add("keychain", eepos.secret_name(school, username))
    try:
        with saving:
            _save(path, signin)
    except (OSError, ValueError, yaml.YAMLError) as e:
        return "save-failed", {"error": str(e)[:300]}
    return "saved", {"school": eepos.address(school), "events": checked["events"],
                     "students": [{"name": s.called(), "kid": kid, "id": s.id, "first": s.first()}
                                  for s, kid in checked["students"]]}


def _next(result: str, school: str, username: str, extra: dict[str, Any]) -> str | None:
    """What the maintainer does next after `keep`'s `result`, or None once all is done."""
    retry = again(school, username)
    error = extra.get("error")
    if result == "saved":
        unmatched = [s["name"] for s in extra["students"] if s["kid"] is None]
        return None if not unmatched else (
            f"{', '.join(unmatched)} matched none of the Kids, so their lessons go to the whole "
            "Household. Add the name Eepos gives as an alias of the Kid in the config, then run "
            f"this again: {retry}")
    return {
        "wrong-password": (f"Eepos didn't take that username and password at "
                           f"{eepos.address(school)}, so nothing was saved. A family that signs in "
                           "by an emailed link sets a password first, with \"Forgot your "
                           f"password?\" on the school's Eepos login. Then: {retry}"),
        "changed": (f"Eepos answered in a way Parent Recap doesn't know, so nothing was saved: "
                    f"{error}. Its portal may have changed; tell the maintainers."),
        "no-answer": f"Eepos couldn't be read, so nothing was saved: {error}. Run this again: {retry}",
        "keychain-not-reachable": ("The sign-in works, but macOS doesn't let Parent Recap keep its "
                                   "password in the Keychain from inside tmux or SSH. Run this "
                                   f"again {PLAIN_TERMINAL}: {retry}"),
        "not-stored": f"The sign-in works, but the {secrets.STORE} didn't take its password: {error}",
        "save-failed": f"The sign-in works, but it couldn't be saved in the config: {error}",
    }[result]


def _reported(result: str, extra: dict[str, Any]) -> dict[str, Any]:
    """What `setup eepos` reports besides its result and what to do next."""
    if result == "saved":  # each student by name, as the maintainer knows them
        return {**extra, "students": [{"name": s["name"], "kid": s["kid"]} for s in extra["students"]]}
    return {k: v for k, v in extra.items() if k == "code" and result == "keychain-not-reachable"}


def remove(path: Path, school: str, username: str,
           saving: AbstractContextManager[Any] = nullcontext()) -> bool:
    """Removes the sign-in as `username` at `school` from the config at `path`, holding `saving`
    while it writes, and deletes its password from the secret store: whether there was one."""
    with saving:
        cfg = Config.load(path)
        gone = [s for s in cfg.eepos.signins
                if s.school == school and s.username.casefold() == username.strip().casefold()]
        if not gone:
            return False
        _drop(path, gone)
    return True


def remove_all(path: Path) -> None:
    """Removes every Eepos sign-in from the config at `path`, with its password, as skipping
    Eepos on the setup page does: a Household that skips it doesn't read it (#500)."""
    try:
        cfg = Config.load(path)
    except FileNotFoundError:
        return
    if cfg.eepos.signins:
        _drop(path, cfg.eepos.signins)


def _drop(path: Path, gone: list[EeposSignin]) -> None:
    names = {eepos.secret_name(s.school, s.username) for s in gone}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    section = data["eepos"] if isinstance(data.get("eepos"), dict) else {}
    data["eepos"] = {**section, "signins": [
        s for s in section.get("signins") or []
        if not (isinstance(s, dict) and eepos.secret_name(
            eepos.school_of(str(s.get("school", ""))) or "", str(s.get("username", ""))) in names)]}
    new = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    Config.model_validate(yaml.safe_load(new))
    _write_private(path, new)
    for name in names:
        secrets.delete(name)
        install_record.remove("keychain", name)

def _save(path: Path, signin: EeposSignin) -> None:
    """Puts `signin` in the config's eepos.signins, in place of one for the same username at the
    same school. A config without an eepos section gets it added at its end, every other line and
    comment as it was; one with it is written again whole, as `setup save` writes it."""
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    entry = signin.model_dump(exclude_defaults=False)
    if "eepos" not in data:
        new = text.rstrip("\n") + "\n" + yaml.safe_dump({"eepos": {"signins": [entry]}},
                                                        allow_unicode=True, sort_keys=False)
        if yaml.safe_load(new).get("eepos") != {"signins": [entry]}:
            raise ValueError(f"couldn't add eepos to {path}; add it by hand")
    else:
        section = data["eepos"] if isinstance(data["eepos"], dict) else {}
        signins = [s for s in section.get("signins") or []
                   if not (isinstance(s, dict) and eepos.school_of(str(s.get("school", ""))) == signin.school
                           and str(s.get("username", "")).strip().casefold() == signin.username.casefold())]
        data["eepos"] = {**section, "signins": [*signins, entry]}
        new = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    Config.model_validate(yaml.safe_load(new))
    _write_private(path, new)


def cmd(args: Any) -> tuple[str, str | None, dict[str, Any]]:
    path = Path(os.path.expanduser(os.path.expandvars(args.config or "~/.family/config.yaml")))
    return add(path, args.school or "", args.username or "")
