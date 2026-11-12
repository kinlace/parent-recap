"""Adding a Konnect sign-in from a terminal (ADR 0050). The setup page has no Konnect step, so a
family elsewhere doesn't see one; `remove` and `remove_all` are for removing sign-ins by hand.

`setup konnect --portal partou --username NAME` has the parent's password typed at the secret
store's own prompt in that terminal (`secrets.put_typed`), so it passes through no dialog, chat or
command line. It waits under a name of its own while this signs in to the portal with it and reads
the parent's children; only once that works does it take the sign-in's own name, so a password
typed wrong never replaces one that works. Each child is matched to one of the Household's Kids by
name, and the sign-in goes into the config: the portal, the username and the matched Kids by the
children's ids. Nothing else of the children is kept. Adding the same username at the same portal
again replaces that sign-in, and a second provider is a sign-in of its own.
"""
from __future__ import annotations

import os
import shlex
import sys
from contextlib import AbstractContextManager, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from . import install_record
from .collectors import konnect
from .config import Config, KonnectSignin
from .setup_report import PLAIN_TERMINAL, keychain_failure, program
from .setup_save import _write_private
from .system import secrets


def again(portal: str, username: str) -> str:
    """The command that adds this sign-in again."""
    return f"{program()} setup konnect --portal {shlex.quote(portal)} --username {shlex.quote(username)}"


def check(cfg: Config, signin: KonnectSignin, password: str) -> dict[str, Any]:
    """Signs in to the sign-in's portal with `password` and reads, as the evening run does, the
    parent's children, the coming Agenda and the last day's lists: each child with the Kid it is
    (None for none), how many events the next LOOKAHEAD_DAYS days have, and how many messages the
    last day has, or why they couldn't be read. Raises konnect.KonnectError when the rest doesn't
    work."""
    with konnect.Portal(signin.portal) as portal:
        portal.sign_in(signin.username, password)
        got = konnect.read_all(portal, signin, cfg, konnect.today(),
                               datetime.now(timezone.utc) - timedelta(days=1))
    messages: int | str = str(got.messages_failed) if got.messages_failed else len(got.messages)
    return {"children": got.children, "events": len(got.events), "messages": messages}


def add(path: Path, portal_given: str, username: str) -> tuple[str, str | None, dict[str, Any]]:
    """Adds the sign-in as `username` at the portal `portal_given` to the config at `path`: the
    step's result, what to do next when it isn't saved, and what else it reports."""
    try:
        cfg = Config.load(path)
    except FileNotFoundError:
        return "no-config", "The config isn't written yet. Write it with the Kids first, then run this again.", {}
    except Exception:  # not its error: a config check quotes values, which can be secrets
        return "bad-config", (f"The config at {path} can't be read. Run {program()} doctor, fix "
                              "what it names, then run this again."), {}
    portal = konnect.portal_of(portal_given)
    if portal is None and konnect.is_website(portal_given):
        return "portal-website", ("That looks like the provider's website. Copy the address from "
                                  "the browser on its parent portal's sign-in page instead: it "
                                  "ends in .ouderportaal.nl, such as partou.ouderportaal.nl."), {}
    if portal is None:
        return "not-a-portal", ("That isn't a Konnect parent portal's address. Give the part "
                                "before .ouderportaal.nl, such as partou for partou.ouderportaal.nl."), {}
    username = username.strip()
    if not username:
        return "no-username", "Run it again with the parent's Konnect username: --username NAME", {}
    retry = again(portal, username)
    if not secrets.reachable():  # macOS in tmux or over SSH, where its prompt never shows
        return "keychain-not-reachable", (
            "macOS doesn't let Parent Recap keep the password in the Keychain from inside tmux or "
            f"SSH, and shows no prompt there, so nothing was asked or saved. Run this again "
            f"{PLAIN_TERMINAL}: {retry}"), {}

    # On stderr: the step's one line of JSON is all it prints on stdout.
    print(f"Type the Konnect password of {username} at {konnect.address(portal)} "
          f"(the {secrets.STORE} asks for it; the input is hidden).", file=sys.stderr, flush=True)
    try:
        secrets.put_typed(konnect.TYPED)
        try:
            password = (secrets.get(konnect.TYPED) or "").strip()
        finally:
            secrets.delete(konnect.TYPED)
    except secrets.SecretsError as e:
        return "not-stored", f"The password wasn't stored, so nothing was saved: {e}. Run this again: {retry}", {}
    if not password:
        return "not-stored", f"No password was typed, so nothing was saved. Run this again: {retry}", {}

    result, extra = keep(path, cfg, portal, username, password)
    return result, _next(result, portal, username, extra), _reported(result, extra)


def keep(path: Path, cfg: Config, portal: str, username: str, password: str,
         saving: AbstractContextManager[Any] = nullcontext()) -> tuple[str, dict[str, Any]]:
    """Signs in as `username` at `portal` with `password` and, only once that works, keeps the
    password in the secret store under the sign-in's own name and the sign-in in the config at
    `path`, holding `saving` only while it writes the config: the result, with the portal's
    address, each child with the Kid it matched (None for none), the events and the messages once
    saved, or the error otherwise. A password that doesn't work, or one the portal wants changed
    first, never replaces one that does. `setup konnect` keeps a sign-in
    through this."""
    signin = KonnectSignin(portal=portal, username=username)
    try:
        checked = check(cfg, signin, password)
    except konnect.WrongPassword:
        return "wrong-password", {}
    except konnect.MustResetPassword:
        return "must-reset-password", {}
    except konnect.Changed as e:
        return "changed", {"error": str(e)}
    except konnect.KonnectError as e:
        return "no-answer", {"error": str(e)}

    matched = {c.id: kid for c, kid in checked["children"] if kid}
    signin = KonnectSignin(portal=portal, username=username, kids=matched)
    try:
        secrets.put(konnect.secret_name(portal, username), password)
    except secrets.SecretsError as e:
        result, code = keychain_failure(e)
        if result == "keychain-not-reachable":
            return result, code
        return "not-stored", {"error": str(e), **code}
    install_record.add("keychain", konnect.secret_name(portal, username))
    try:
        with saving:
            _save(path, signin)
    except (OSError, ValueError, yaml.YAMLError) as e:
        return "save-failed", {"error": str(e)[:300]}
    return "saved", {"portal": konnect.address(portal), "events": checked["events"],
                     "messages": checked["messages"],
                     "children": [{"name": c.called(), "kid": kid, "id": c.id, "first": c.first()}
                                  for c, kid in checked["children"]]}


def reset_password(portal: str) -> str:
    """What the parent does when the portal wants a new password set first."""
    return (f"Sign in at https://{konnect.address(portal)} in a browser once and set the new "
            "password it asks for")


def _next(result: str, portal: str, username: str, extra: dict[str, Any]) -> str | None:
    """What the maintainer does next after `keep`'s `result`, or None once all is done."""
    retry = again(portal, username)
    error = extra.get("error")
    if result == "saved":
        unmatched = [c["name"] for c in extra["children"] if c["kid"] is None]
        return None if not unmatched else (
            f"{', '.join(unmatched)} matched none of the Kids, so their planned times go to the "
            "whole Household. Add the name Konnect gives as an alias of the Kid in the config, "
            f"then run this again: {retry}")
    return {
        "wrong-password": (f"Konnect didn't take that username and password at "
                           f"{konnect.address(portal)}, so nothing was saved. Check them by signing "
                           f"in there in a browser. Then: {retry}"),
        "must-reset-password": (f"Konnect takes that password, but wants a new one set first, so "
                                f"nothing was saved. {reset_password(portal)}, then: {retry}"),
        "changed": (f"Konnect answered in a way Parent Recap doesn't know, so nothing was saved: "
                    f"{error}. Its portal may have changed; tell the maintainers."),
        "no-answer": f"Konnect couldn't be read, so nothing was saved: {error}. Run this again: {retry}",
        "keychain-not-reachable": ("The sign-in works, but macOS doesn't let Parent Recap keep its "
                                   "password in the Keychain from inside tmux or SSH. Run this "
                                   f"again {PLAIN_TERMINAL}: {retry}"),
        "not-stored": f"The sign-in works, but the {secrets.STORE} didn't take its password: {error}",
        "save-failed": f"The sign-in works, but it couldn't be saved in the config: {error}",
    }[result]


def _reported(result: str, extra: dict[str, Any]) -> dict[str, Any]:
    """What `setup konnect` reports besides its result and what to do next."""
    if result == "saved":  # each child by name, as the maintainer knows them
        return {**{k: v for k, v in extra.items() if k != "messages"},
                "children": [{"name": c["name"], "kid": c["kid"]} for c in extra["children"]]}
    return {k: v for k, v in extra.items() if k == "code" and result == "keychain-not-reachable"}


def remove(path: Path, portal: str, username: str,
           saving: AbstractContextManager[Any] = nullcontext()) -> bool:
    """Removes the sign-in as `username` at `portal` from the config at `path`, holding `saving`
    while it writes, and deletes its password from the secret store: whether there was one."""
    with saving:
        cfg = Config.load(path)
        gone = [s for s in cfg.konnect.signins
                if s.portal == portal and s.username.casefold() == username.strip().casefold()]
        if not gone:
            return False
        _drop(path, gone)
    return True


def remove_all(path: Path) -> None:
    """Removes every Konnect sign-in from the config at `path`, with its password."""
    try:
        cfg = Config.load(path)
    except FileNotFoundError:
        return
    if cfg.konnect.signins:
        _drop(path, cfg.konnect.signins)


def _drop(path: Path, gone: list[KonnectSignin]) -> None:
    names = {konnect.secret_name(s.portal, s.username) for s in gone}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    section = data["konnect"] if isinstance(data.get("konnect"), dict) else {}
    data["konnect"] = {**section, "signins": [
        s for s in section.get("signins") or []
        if not (isinstance(s, dict) and konnect.secret_name(
            konnect.portal_of(str(s.get("portal", ""))) or "", str(s.get("username", ""))) in names)]}
    new = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    Config.model_validate(yaml.safe_load(new))
    _write_private(path, new)
    for name in names:
        secrets.delete(name)
        install_record.remove("keychain", name)


def _save(path: Path, signin: KonnectSignin) -> None:
    """Puts `signin` in the config's konnect.signins, in place of one for the same username at the
    same portal. A config without a konnect section gets it added at its end, every other line and
    comment as it was; one with it is written again whole, as `setup save` writes it."""
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    entry = signin.model_dump(exclude_defaults=False)
    if "konnect" not in data:
        new = text.rstrip("\n") + "\n" + yaml.safe_dump({"konnect": {"signins": [entry]}},
                                                        allow_unicode=True, sort_keys=False)
        if yaml.safe_load(new).get("konnect") != {"signins": [entry]}:
            raise ValueError(f"couldn't add konnect to {path}; add it by hand")
    else:
        section = data["konnect"] if isinstance(data["konnect"], dict) else {}
        signins = [s for s in section.get("signins") or []
                   if not (isinstance(s, dict) and konnect.portal_of(str(s.get("portal", ""))) == signin.portal
                           and str(s.get("username", "")).strip().casefold() == signin.username.casefold())]
        data["konnect"] = {**section, "signins": [*signins, entry]}
        new = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    Config.model_validate(yaml.safe_load(new))
    _write_private(path, new)


def cmd(args: Any) -> tuple[str, str | None, dict[str, Any]]:
    path = Path(os.path.expanduser(os.path.expandvars(args.config or "~/.family/config.yaml")))
    return add(path, args.portal or "", args.username or "")
