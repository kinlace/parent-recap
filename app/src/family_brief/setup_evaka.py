"""Adding an eVaka sign-in from a terminal (ADR 0039), for the maintainers to turn eVaka on for a
Household by hand until the setup page and the hosted sign-up have a step for it (#768).

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

    signin = EvakaSignin(city=city, username=username)
    try:
        checked = check(cfg, signin, password)
    except evaka.WrongPassword:
        return "wrong-password", (f"eVaka didn't take that email and password at "
                                  f"{evaka.host_of(city)}, so nothing was saved. A guardian who "
                                  "has only signed in with Suomi.fi sets an email and password "
                                  "first, under Personal information (Omat tiedot) in eVaka. "
                                  f"Then: {retry}"), {}
    except evaka.Changed as e:
        return "changed", (f"eVaka answered in a way Parent Recap doesn't know, so nothing was "
                           f"saved: {e}. Its API may have changed; tell the maintainers."), {}
    except evaka.EvakaError as e:
        return "no-answer", f"eVaka couldn't be read, so nothing was saved: {e}. Run this again: {retry}", {}

    matched = {c.id: kid for c, kid in checked["children"] if kid}
    signin = EvakaSignin(city=city, username=username, kids=matched)
    try:
        secrets.put(evaka.secret_name(city, username), password)
    except secrets.SecretsError as e:
        result, extra = keychain_failure(e)
        if result == "keychain-not-reachable":
            return result, ("The sign-in works, but macOS doesn't let Parent Recap keep its "
                            f"password in the Keychain from inside tmux or SSH. Run this again "
                            f"{PLAIN_TERMINAL}: {retry}"), extra
        return "not-stored", f"The sign-in works, but the {secrets.STORE} didn't take its password: {e}", {}
    install_record.add("keychain", evaka.secret_name(city, username))
    try:
        _save(path, signin)
    except (OSError, ValueError, yaml.YAMLError) as e:
        return "save-failed", f"The sign-in works, but it couldn't be saved in the config: {str(e)[:300]}", {}

    children = [{"name": c.called(), "kid": kid} for c, kid in checked["children"]]
    unmatched = [c["name"] for c in children if c["kid"] is None]
    extra = {"city": evaka.host_of(city), "children": children, "events": checked["events"]}
    if unmatched:
        return "saved", (f"{', '.join(unmatched)} matched none of the Kids, so their events go to "
                         "the whole Household. Add the name eVaka gives as an alias of the Kid in "
                         f"the config, then run this again: {retry}"), extra
    return "saved", None, extra


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
