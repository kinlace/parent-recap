"""What a Hosted Household has, and the changes the maintainers make for a family that can't open a
setup page (#782): `signup show`, `signup recipients` and `signup myclub`, which `sudo admin
household ph-XXXXXXXX [recipients|myclub]` runs as the Household's own user with the shared
program (ADR 0035), on the maintainer's terminal.

`show` reads only. It prints the Recipients with their languages (and the Weekend Picks'
Recipients when `email.weekend_to` names others), the Kids as Wilma gave them, and each Kid's
MyClub link or "none". So its output holds addresses, Kids' names and links: it is read on the
server, never pasted into a chat (ops/gateway/README.md "What can be pasted").

`recipients` asks for the new list on stdin, one `ADDRESS[:LANGUAGE]` a line until an empty line,
shows the change and writes it only after `yes`, through `setup save` as `signup household` does,
so the config keeps that shape. The addresses come on stdin, never as arguments: sudo logs each
command line. Then it binds the Gateway Key to the new list as `doctor` does (send_key.ensure); a
key the sign-up made isn't bound, and the relay changes nothing for it.

`myclub` asks for each Kid in turn: an empty answer keeps the link, `none` turns MyClub off for
that Kid, a link is downloaded once to check it as the sign-up does. Any link that won't do stops
it with nothing written; otherwise it shows the change and writes it only after `yes`.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import yaml

from .. import send_key, setup_myclub, setup_save
from ..config import Config
from .invites import LANGUAGES

NONE = "none"


def _config(args: argparse.Namespace) -> Path:
    return Path(os.path.expanduser(args.config or "~/.family/config.yaml"))


def _ask(prompt: str) -> str | None:
    """One line typed on stdin, without its newline; None at its end."""
    print(prompt, end="", flush=True)
    line = sys.stdin.readline()
    return None if not line else line.rstrip("\r\n")


def _confirmed() -> bool:
    answer = _ask("Type yes to write it: ")
    return answer is not None and answer.strip().lower() == "yes"


def _load(config: Path) -> Config | None:
    try:
        return Config.load(config)
    except Exception as e:  # noqa: BLE001 - its message names the config, never its values
        print(f"❌ The Household's config at {config} can't be read ({type(e).__name__}), so "
              "nothing was changed. Its doctor says why: sudo admin households", file=sys.stderr)
        return None


def _recipient_line(cfg: Config, r: Any) -> str:
    return f"  {r.address}  {cfg.language_of(r)}"


# ---------------------------------------------------------------- show

def show(cfg: Config) -> list[str]:
    lines = ["Recipients:", *(_recipient_line(cfg, r) for r in cfg.email.to)]
    if cfg.email.weekend_to:
        lines += ["Weekend Picks go to (weekend_to):",
                  *(_recipient_line(cfg, r) for r in cfg.email.weekend_to)]
    lines.append("Kids, as Wilma gave them, with their MyClub links:" if cfg.wilma.enabled
                 else "Kids, as the family named them, with their MyClub links:")
    for kid in cfg.kids:
        about = ", ".join(x for x in (kid.school, kid.class_name) if x)
        lines.append(f"  {kid.name}" + (f" ({about})" if about else ""))
        lines.append(f"    MyClub: {kid.myclub_ical_url or NONE}")
    if not cfg.kids:
        lines.append("  (none)")
    return lines


def cmd_show(args: argparse.Namespace) -> int:
    cfg = _load(_config(args))
    if cfg is None:
        return 1
    print("\n".join(show(cfg)))
    return 0


# ---------------------------------------------------------------- recipients

def parse_recipient(given: str, default: str) -> dict[str, str] | None:
    """`ADDRESS[:LANGUAGE]` as {"address", "language"}, or None when it isn't one."""
    from .server import EMAIL
    text = given.strip()
    address, sep, language = text.rpartition(":") if ":" in text else (text, "", "")
    language = language.strip().lower() if sep else default
    address = address.strip().lower()
    if language not in LANGUAGES or not EMAIL.fullmatch(address):
        return None
    return {"address": address, "language": language}


def cmd_recipients(args: argparse.Namespace) -> int:
    config = _config(args)
    cfg = _load(config)
    if cfg is None:
        return 1
    print("The Household's Recipients now:")
    print("\n".join(_recipient_line(cfg, r) for r in cfg.email.to))
    print(f"Type the new list, one ADDRESS[:LANGUAGE] a line, LANGUAGE one of "
          f"{', '.join(LANGUAGES)} (default {cfg.summary_language}), the parent first; an empty "
          "line ends it.")
    new: list[dict[str, str]] = []
    while True:
        line = _ask("> ")
        if line is None or not line.strip():
            break
        r = parse_recipient(line, cfg.summary_language)
        if r is None:
            print(f"❌ Not a Recipient: give it as ADDRESS or ADDRESS:LANGUAGE, with LANGUAGE one "
                  f"of {', '.join(LANGUAGES)}. Nothing was changed.", file=sys.stderr)
            return 1
        if any(r["address"] == other["address"] for other in new):
            print(f"❌ {r['address']} is given twice. Nothing was changed.", file=sys.stderr)
            return 1
        new.append(r)
    if not new:
        print("No Recipient given, so nothing was changed: a Household needs at least one.",
              file=sys.stderr)
        return 1
    before = {r.address.lower(): cfg.language_of(r) for r in cfg.email.to}
    after = {r["address"]: r["language"] for r in new}
    if list(before.items()) == list(after.items()):
        print("That is the list it has: nothing to change.")
        return 0
    print("The change:")
    for address, language in before.items():
        if after.get(address) != language:
            print(f"  - {address}  {language}")
    for address, language in after.items():
        print(f"  {'+' if before.get(address) != language else ' '} {address}  {language}")
    if before == after:  # the same Recipients in another order
        print("  (only the order changes: the first is the parent)")
    if cfg.email.weekend_to:
        print("Weekend Picks keep going to weekend_to, which this doesn't change.")
    if not _confirmed():
        print("Not written: nothing was changed.")
        return 1
    saved = setup_save.save(config, {"recipients": new})
    if saved["result"] != "saved":
        print(f"❌ Not written ({saved['result']}): " + "; ".join(saved.get("errors") or []),
              file=sys.stderr)
        return 1
    print("✅ Written: the Household's next Brief goes to the new list.")
    _bind(config)
    return 0


def _bind(config: Path) -> None:
    """Binds the Gateway Key to the Recipients as they are now, as doctor does after `manage`."""
    again = ("Doctor binds the key to the Recipients again whenever it runs (sudo admin "
             "households runs it).")
    try:
        status = send_key.ensure(Config.load(config), ask=False)
    except Exception as e:  # noqa: BLE001 - the list is written; its message names no address
        print(f"⚠️ The gateway couldn't be asked to bind the new list ({type(e).__name__}). "
              + again)
        return
    if status.result == send_key.CONFIRMED:
        print("✅ The Gateway Key's binding follows the new list (a key the sign-up made is "
              "bound to nobody, and stays so).")
    else:
        print(f"⚠️ The gateway: {send_key.describe(status)}. {again}")


# ---------------------------------------------------------------- myclub

def cmd_myclub(args: argparse.Namespace) -> int:
    config = _config(args)
    cfg = _load(config)
    if cfg is None:
        return 1
    if not cfg.kids:
        print("The Household has no Kids, so there is no MyClub link to change.", file=sys.stderr)
        return 1
    print("For each Kid, paste their MyClub calendar link; an empty line keeps what they have, "
          f"`{NONE}` turns MyClub off for them.")
    links = {kid.name: kid.myclub_ical_url for kid in cfg.kids}
    new = dict(links)
    for kid in cfg.kids:
        answer = _ask(f"{kid.name} (now: {links[kid.name] or NONE}): ")
        if answer is None:
            print("Not written: nothing was changed.")
            return 1
        given = "".join(answer.split())
        if not given:
            continue
        if given.lower() == NONE:
            new[kid.name] = None
            continue
        if any(other and setup_myclub.same_link(given, other)
               for name, other in new.items() if name != kid.name):
            # One link for two Kids would write every event twice, under both names (#209).
            result = "same-link"
        else:
            result, _ = setup_myclub.check_link(given)
        if result != "checked":
            print(f"❌ {kid.name}'s link won't do ({result}). Nothing was changed.",
                  file=sys.stderr)
            return 1
        new[kid.name] = given
    changed = {name: url for name, url in new.items() if url != links[name]}
    if not changed:
        print("Every Kid keeps their link: nothing to change.")
        return 0
    print("The change:")
    for name, url in changed.items():
        print(f"  {name}: {links[name] or NONE} -> {url or NONE}")
    if not _confirmed():
        print("Not written: nothing was changed.")
        return 1
    try:
        _write_links(config, changed)
    except (OSError, ValueError, yaml.YAMLError) as e:  # names the config, never a link
        print(f"❌ Not written: {type(e).__name__}", file=sys.stderr)
        return 1
    print("✅ Written: the Household's next Brief reads the new links.")
    return 0


def _write_links(config: Path, changed: dict[str, str | None]) -> None:
    """Sets or removes each Kid's myclub_ical_url, written as `setup save` writes the config."""
    data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    for kid in data.get("kids") or []:
        if isinstance(kid, dict) and kid.get("name") in changed:
            url = changed[kid["name"]]
            if url is None:
                kid.pop("myclub_ical_url", None)
            else:
                kid["myclub_ical_url"] = url
    Config.model_validate(data)
    setup_save._write_private(config, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))


def register(steps) -> None:
    p = steps.add_parser("show", help="Print this user's Household's Recipients, Kids and MyClub "
                         "links (sudo admin household runs it)")
    p.set_defaults(func=cmd_show)
    p = steps.add_parser("recipients", help="Change this user's Household's Recipients, asked "
                         "for on stdin (sudo admin household … recipients runs it)")
    p.set_defaults(func=cmd_recipients)
    p = steps.add_parser("myclub", help="Change this user's Household's MyClub links, asked for "
                         "on stdin (sudo admin household … myclub runs it)")
    p.set_defaults(func=cmd_myclub)
