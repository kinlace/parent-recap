"""Adding an eVaka sign-in from a terminal (ADR 0039), for the maintainers to turn eVaka on for a
Household by hand. The setup page's eVaka step (#791) keeps and removes a sign-in through `keep`
and `remove` here, and the hosted sign-up (#790) reuses `check` and `_save`.

`setup evaka --city espoo --username EMAIL` has the guardian's password typed at the secret store's
own prompt in that terminal (`secrets.put_typed`), so it passes through no dialog, chat or command
line. It waits under a name of its own while this signs in to the city's eVaka with it and reads
the guardian's children; only once that works does it take the sign-in's own name, so a password
typed wrong never replaces one that works. Each child is matched to one of the Household's Kids by
name, and the sign-in goes into the config: the city, the username and the matched Kids by the
children's ids. Nothing else of the children is kept. The device cookie the sign-in gets is kept
beside the state, as the evening run keeps it. Adding the same username in the same city again
replaces that sign-in, and a second city is a sign-in of its own.
"""
from __future__ import annotations

import os
import shlex
import sys
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Any

import yaml

from . import install_record
from .collectors import evaka
from .config import Config, EvakaSignin
from .setup_report import PLAIN_TERMINAL, keychain_failure, program
from .setup_save import _write_private
from .system import secrets


def again(city: str, username: str) -> str:
    """The command that adds this sign-in again."""
    return f"{program()} setup evaka --city {shlex.quote(city)} --username {shlex.quote(username)}"


def check(cfg: Config, signin: EvakaSignin, password: str) -> dict[str, Any]:
    """Signs in to the sign-in's city with `password` and reads the guardian's children, the
    coming events and the first page of messages: each child with the Kid it is (None for none),
    how many events the next LOOKAHEAD_DAYS days have, and how many messages that page has, or why
    it couldn't be read. Raises evaka.EvakaError when the rest doesn't work."""
    with evaka.Portal(signin.city, evaka.devices_path(cfg)) as portal:
        portal.sign_in(signin.username, password)
        children, kids, events = evaka.read_all(portal, signin, cfg, evaka.today(cfg.timezone))
        try:
            threads = portal.received(1)["data"]
            messages: int | str = len(evaka.messages_of(threads, signin.city, kids))
        except evaka.EvakaError as e:
            messages = str(e)
    return {"children": [(c, kids[c.id]) for c in children], "events": len(events), "messages": messages}


def add(path: Path, city_given: str, username: str) -> tuple[str, str | None, dict[str, Any]]:
    """Adds the sign-in as `username` in the city `city_given` to the config at `path`: the step's
    result, what to do next when it isn't saved, and what else it reports."""
    try:
        cfg = Config.load(path)
    except FileNotFoundError:
        return "no-config", "The config isn't written yet. Write it with the Kids first, then run this again.", {}
    except Exception:  # not its error: a config check quotes values, which can be secrets
        return "bad-config", (f"The config at {path} can't be read. Run {program()} doctor, fix "
                              "what it names, then run this again."), {}
    city = evaka.city_of(city_given)
    if city is None:
        return "not-a-city", ("That isn't a city on eVaka. Give its name, one of "
                              f"{', '.join(evaka.CITIES)}, or the host of its eVaka."), {}
    username = username.strip()
    if not username:
        return "no-username", "Run it again with the guardian's eVaka email: --username EMAIL", {}
    retry = again(city, username)
    if not secrets.reachable():  # macOS in tmux or over SSH, where its prompt never shows
        return "keychain-not-reachable", (
            "macOS doesn't let Parent Recap keep the password in the Keychain from inside tmux or "
            f"SSH, and shows no prompt there, so nothing was asked or saved. Run this again "
            f"{PLAIN_TERMINAL}: {retry}"), {}

    # On stderr: the step's one line of JSON is all it prints on stdout.
    print(f"Type the eVaka password of {username} at {evaka.host_of(city)} "
          f"(the {secrets.STORE} asks for it; the input is hidden).", file=sys.stderr, flush=True)
    try:
        secrets.put_typed(evaka.TYPED)
        try:
            password = (secrets.get(evaka.TYPED) or "").strip()
        finally:
            secrets.delete(evaka.TYPED)
    except secrets.SecretsError as e:
        return "not-stored", f"The password wasn't stored, so nothing was saved: {e}. Run this again: {retry}", {}
    if not password:
        return "not-stored", f"No password was typed, so nothing was saved. Run this again: {retry}", {}

    result, extra = keep(path, cfg, city, username, password)
    return result, _next(result, city, username, extra), _reported(result, extra)


def keep(path: Path, cfg: Config, city: str, username: str, password: str,
         saving: AbstractContextManager[Any] = nullcontext()) -> tuple[str, dict[str, Any]]:
    """Signs in as `username` in `city` with `password` and, only once that works, keeps the
    password in the secret store under the sign-in's own name and the sign-in in the config at
    `path`, holding `saving` only while it writes the config: the result, with the city's host,
    each child with the Kid it matched (None for none), the events and the messages once saved, or
    the error otherwise. A password that doesn't work never replaces one that does. `setup evaka`
    and the setup page's eVaka step both keep a sign-in through this."""
    signin = EvakaSignin(city=city, username=username)
    try:
        checked = check(cfg, signin, password)
    except evaka.WrongPassword:
        return "wrong-password", {}
    except evaka.Changed as e:
        return "changed", {"error": str(e)}
    except evaka.EvakaError as e:
        return "no-answer", {"error": str(e)}

    matched = {c.id: kid for c, kid in checked["children"] if kid}
    signin = EvakaSignin(city=city, username=username, kids=matched)
    try:
        secrets.put(evaka.secret_name(city, username), password)
    except secrets.SecretsError as e:
        result, code = keychain_failure(e)
        if result == "keychain-not-reachable":
            return result, code
        return "not-stored", {"error": str(e), **code}
    install_record.add("keychain", evaka.secret_name(city, username))
    try:
        with saving:
            _save(path, signin)
    except (OSError, ValueError, yaml.YAMLError) as e:
        return "save-failed", {"error": str(e)[:300]}
    return "saved", {"city": evaka.host_of(city), "events": checked["events"],
                     "messages": checked["messages"],
                     "children": [{"name": c.called(), "kid": kid, "id": c.id, "first": c.first()}
                                  for c, kid in checked["children"]]}


def _next(result: str, city: str, username: str, extra: dict[str, Any]) -> str | None:
    """What the maintainer does next after `keep`'s `result`, or None once all is done."""
    retry = again(city, username)
    error = extra.get("error")
    if result == "saved":
        unmatched = [c["name"] for c in extra["children"] if c["kid"] is None]
        return None if not unmatched else (
            f"{', '.join(unmatched)} matched none of the Kids, so their events go to the whole "
            "Household. Add the name eVaka gives as an alias of the Kid in the config, then run "
            f"this again: {retry}")
    return {
        "wrong-password": (f"eVaka didn't take that email and password at {evaka.host_of(city)}, "
                           "so nothing was saved. A guardian who has only signed in with Suomi.fi "
                           "sets an email and password first, under Personal information (Omat "
                           f"tiedot) in eVaka. Then: {retry}"),
        "changed": (f"eVaka answered in a way Parent Recap doesn't know, so nothing was saved: "
                    f"{error}. Its API may have changed; tell the maintainers."),
        "no-answer": f"eVaka couldn't be read, so nothing was saved: {error}. Run this again: {retry}",
        "keychain-not-reachable": ("The sign-in works, but macOS doesn't let Parent Recap keep its "
                                   "password in the Keychain from inside tmux or SSH. Run this "
                                   f"again {PLAIN_TERMINAL}: {retry}"),
        "not-stored": f"The sign-in works, but the {secrets.STORE} didn't take its password: {error}",
        "save-failed": f"The sign-in works, but it couldn't be saved in the config: {error}",
    }[result]


def _reported(result: str, extra: dict[str, Any]) -> dict[str, Any]:
    """What `setup evaka` reports besides its result and what to do next."""
    if result == "saved":  # each child by name, as the maintainer knows them
        return {**{k: v for k, v in extra.items() if k != "messages"},
                "children": [{"name": c["name"], "kid": c["kid"]} for c in extra["children"]]}
    return {k: v for k, v in extra.items() if k == "code" and result == "keychain-not-reachable"}


def remove(path: Path, city: str, username: str,
           saving: AbstractContextManager[Any] = nullcontext()) -> bool:
    """Removes the sign-in as `username` in `city` from the config at `path`, holding `saving`
    while it writes, and deletes its password from the secret store: whether there was one."""
    with saving:
        cfg = Config.load(path)
        gone = [s for s in cfg.evaka.signins
                if s.city == city and s.username.casefold() == username.strip().casefold()]
        if not gone:
            return False
        _drop(path, gone)
    return True


def remove_all(path: Path) -> None:
    """Removes every eVaka sign-in from the config at `path`, with its password, as skipping
    eVaka on the setup page does: a Household that skips it doesn't read it (#500)."""
    try:
        cfg = Config.load(path)
    except FileNotFoundError:
        return
    if cfg.evaka.signins:
        _drop(path, cfg.evaka.signins)


def _drop(path: Path, gone: list[EvakaSignin]) -> None:
    names = {evaka.secret_name(s.city, s.username) for s in gone}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    section = data["evaka"] if isinstance(data.get("evaka"), dict) else {}
    data["evaka"] = {**section, "signins": [
        s for s in section.get("signins") or []
        if not (isinstance(s, dict) and evaka.secret_name(
            evaka.city_of(str(s.get("city", ""))) or "", str(s.get("username", ""))) in names)]}
    new = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    Config.model_validate(yaml.safe_load(new))
    _write_private(path, new)
    for name in names:
        secrets.delete(name)
        install_record.remove("keychain", name)

def _save(path: Path, signin: EvakaSignin) -> None:
    """Puts `signin` in the config's evaka.signins, in place of one for the same username in the
    same city. A config without an evaka section gets it added at its end, every other line and
    comment as it was; one with it is written again whole, as `setup save` writes it."""
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    entry = signin.model_dump(exclude_defaults=False)
    if "evaka" not in data:
        new = text.rstrip("\n") + "\n" + yaml.safe_dump({"evaka": {"signins": [entry]}},
                                                        allow_unicode=True, sort_keys=False)
        if yaml.safe_load(new).get("evaka") != {"signins": [entry]}:
            raise ValueError(f"couldn't add evaka to {path}; add it by hand")
    else:
        section = data["evaka"] if isinstance(data["evaka"], dict) else {}
        signins = [s for s in section.get("signins") or []
                   if not (isinstance(s, dict) and evaka.city_of(str(s.get("city", ""))) == signin.city
                           and str(s.get("username", "")).strip().casefold() == signin.username.casefold())]
        data["evaka"] = {**section, "signins": [*signins, entry]}
        new = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    Config.model_validate(yaml.safe_load(new))
    _write_private(path, new)


def cmd(args: Any) -> tuple[str, str | None, dict[str, Any]]:
    path = Path(os.path.expanduser(os.path.expandvars(args.config or "~/.family/config.yaml")))
    return add(path, args.city or "", args.username or "")
