"""Adding a Social Schools sign-in from a terminal (ADR 0047), as `setup evaka` adds an eVaka one.

`setup socialschools --username EMAIL` has the parent's password typed at the secret store's own
prompt in that terminal (`secrets.put_typed`), so it passes through no dialog, chat or command
line. It waits under a name of its own while this signs in to Social Schools with it and reads the
parent's schools, children and coming events; only once that works does it take the sign-in's own
name, so a password typed wrong never replaces one that works. Each child is matched to one of the
Household's Kids by name, and the sign-in goes into the config: the email and the matched Kids by
the children's names. Nothing else of the children is kept. Adding the same email again replaces
that sign-in.

An account that signs in with Google, Microsoft or Apple, or asks for a further step after the
password, can't be signed in to this way: this says so, and saves nothing.
"""
from __future__ import annotations

import os
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from . import install_record
from .collectors import socialschools
from .config import Config, SocialSchoolsSignin
from .setup_report import PLAIN_TERMINAL, keychain_failure, program
from .setup_save import _write_private
from .system import secrets

# What the family is told when the sign-in can't be done with a password: as setup, `doctor` and
# docs/sources.md say it.
OTHER_SIGN_IN = ("Parent Recap can only sign in to Social Schools with an email and password on its "
                 "own login page, with no further step. An account that signs in with Google, "
                 "Microsoft or Apple, or asks for a code after the password, can't be read this way.")


def again(username: str) -> str:
    """The command that adds this sign-in again."""
    return f"{program()} setup socialschools --username {shlex.quote(username)}"


def check(cfg: Config, signin: SocialSchoolsSignin, password: str) -> dict[str, Any]:
    """Signs in with `password` and reads the parent's schools, children, coming events and the
    first page of each school's announcements: each child with the Kid it is (None for none), the
    schools by name, how many events the next LOOKAHEAD_DAYS days have, and how many announcements
    the first pages have, or why they couldn't be read. Raises socialschools.SocialSchoolsError
    when the rest doesn't work."""
    with socialschools.Portal() as portal:
        portal.sign_in(signin.username, password)
        schools, kids, events = socialschools.read_events(portal, signin, cfg, datetime.now(timezone.utc))
        try:
            posts: int | str = sum(len(portal.posts(s.id, 0)) for s in schools)
        except socialschools.SocialSchoolsError as e:
            posts = str(e)
    return {"children": list(kids.items()), "schools": [s.name for s in schools], "events": len(events),
            "posts": posts}


def add(path: Path, username: str) -> tuple[str, str | None, dict[str, Any]]:
    """Adds the sign-in as `username` to the config at `path`: the step's result, what to do next
    when it isn't saved, and what else it reports."""
    try:
        cfg = Config.load(path)
    except FileNotFoundError:
        return "no-config", "The config isn't written yet. Write it with the Kids first, then run this again.", {}
    except Exception:  # not its error: a config check quotes values, which can be secrets
        return "bad-config", (f"The config at {path} can't be read. Run {program()} doctor, fix "
                              "what it names, then run this again."), {}
    username = username.strip()
    if not username:
        return "no-username", "Run it again with the parent's Social Schools email: --username EMAIL", {}
    retry = again(username)
    if not secrets.reachable():  # macOS in tmux or over SSH, where its prompt never shows
        return "keychain-not-reachable", (
            "macOS doesn't let Parent Recap keep the password in the Keychain from inside tmux or "
            f"SSH, and shows no prompt there, so nothing was asked or saved. Run this again "
            f"{PLAIN_TERMINAL}: {retry}"), {}

    # On stderr: the step's one line of JSON is all it prints on stdout.
    print(f"Type the Social Schools password of {username} (the {secrets.STORE} asks for it; the "
          "input is hidden).", file=sys.stderr, flush=True)
    try:
        secrets.put_typed(socialschools.TYPED)
        try:
            password = (secrets.get(socialschools.TYPED) or "").strip()
        finally:
            secrets.delete(socialschools.TYPED)
    except secrets.SecretsError as e:
        return "not-stored", f"The password wasn't stored, so nothing was saved: {e}. Run this again: {retry}", {}
    if not password:
        return "not-stored", f"No password was typed, so nothing was saved. Run this again: {retry}", {}

    result, extra = keep(path, cfg, username, password)
    return result, _next(result, username, extra), _reported(result, extra)


def keep(path: Path, cfg: Config, username: str, password: str) -> tuple[str, dict[str, Any]]:
    """Signs in as `username` with `password` and, only once that works, keeps the password in the
    secret store under the sign-in's own name and the sign-in in the config at `path`: the result,
    with the schools, each child with the Kid it matched (None for none) and the events once saved,
    or the error otherwise. A password that doesn't work never replaces one that does."""
    signin = SocialSchoolsSignin(username=username)
    try:
        checked = check(cfg, signin, password)
    except socialschools.WrongPassword:
        return "wrong-password", {}
    except socialschools.Unsupported as e:
        return "other-sign-in", {"error": str(e)}
    except socialschools.Changed as e:
        return "changed", {"error": str(e)}
    except socialschools.SocialSchoolsError as e:
        return "no-answer", {"error": str(e)}

    matched = {c.called(): kid for c, kid in checked["children"] if kid}
    signin = SocialSchoolsSignin(username=username, kids=matched)
    name = socialschools.secret_name(username)
    try:
        secrets.put(name, password)
    except secrets.SecretsError as e:
        result, code = keychain_failure(e)
        if result == "keychain-not-reachable":
            return result, code
        return "not-stored", {"error": str(e), **code}
    install_record.add("keychain", name)
    try:
        _save(path, signin)
    except (OSError, ValueError, yaml.YAMLError) as e:
        return "save-failed", {"error": str(e)[:300]}
    return "saved", {"schools": checked["schools"], "events": checked["events"],
                     "children": [{"name": c.called(), "kid": kid} for c, kid in checked["children"]]}


def _next(result: str, username: str, extra: dict[str, Any]) -> str | None:
    """What the family does next after `keep`'s `result`, or None once all is done."""
    retry = again(username)
    error = extra.get("error")
    if result == "saved":
        unmatched = [c["name"] for c in extra["children"] if c["kid"] is None]
        return None if not unmatched else (
            f"{', '.join(unmatched)} matched none of the Kids, so their announcements and events go "
            "to the whole Household. Add the name Social Schools gives as an alias of the Kid in the "
            f"config, then run this again: {retry}")
    return {
        "wrong-password": ("Social Schools didn't take that email and password, so nothing was "
                           f"saved. {OTHER_SIGN_IN} Check the password in the Social Schools app, "
                           f"then: {retry}"),
        "other-sign-in": f"Nothing was saved: {error}. {OTHER_SIGN_IN}",
        "changed": (f"Social Schools answered in a way Parent Recap doesn't know, so nothing was "
                    f"saved: {error}. Its sign-in or API may have changed; tell the maintainers."),
        "no-answer": f"Social Schools couldn't be read, so nothing was saved: {error}. Run this again: {retry}",
        "keychain-not-reachable": ("The sign-in works, but macOS doesn't let Parent Recap keep its "
                                   "password in the Keychain from inside tmux or SSH. Run this "
                                   f"again {PLAIN_TERMINAL}: {retry}"),
        "not-stored": f"The sign-in works, but the {secrets.STORE} didn't take its password: {error}",
        "save-failed": f"The sign-in works, but it couldn't be saved in the config: {error}",
    }[result]


def _reported(result: str, extra: dict[str, Any]) -> dict[str, Any]:
    """What `setup socialschools` reports besides its result and what to do next."""
    if result == "saved":
        return extra
    return {k: v for k, v in extra.items() if k == "code" and result == "keychain-not-reachable"}


def _save(path: Path, signin: SocialSchoolsSignin) -> None:
    """Puts `signin` in the config's socialschools.signins, in place of one for the same email. A
    config without a socialschools section gets it added at its end, every other line and comment
    as it was; one with it is written again whole, as `setup save` writes it."""
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    entry = signin.model_dump(exclude_defaults=False)
    if "socialschools" not in data:
        new = text.rstrip("\n") + "\n" + yaml.safe_dump({"socialschools": {"signins": [entry]}},
                                                        allow_unicode=True, sort_keys=False)
        if yaml.safe_load(new).get("socialschools") != {"signins": [entry]}:
            raise ValueError(f"couldn't add socialschools to {path}; add it by hand")
    else:
        section = data["socialschools"] if isinstance(data["socialschools"], dict) else {}
        signins = [s for s in section.get("signins") or []
                   if not (isinstance(s, dict)
                           and str(s.get("username", "")).strip().casefold() == signin.username.casefold())]
        data["socialschools"] = {**section, "signins": [*signins, entry]}
        new = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    Config.model_validate(yaml.safe_load(new))
    _write_private(path, new)


def cmd(args: Any) -> tuple[str, str | None, dict[str, Any]]:
    path = Path(os.path.expanduser(os.path.expandvars(args.config or "~/.family/config.yaml")))
    return add(path, args.username or "")
