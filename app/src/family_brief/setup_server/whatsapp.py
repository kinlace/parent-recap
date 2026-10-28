"""WhatsApp's connect step: pairing the Linked Device (ADR 0025, ADR 0027) with a QR code, on
macOS and Windows alike.

The server runs the Node program's `pair` mode in the background, and the page asks every few
seconds what it last said: the QR string to draw, which WhatsApp changes now and then, until the
phone has scanned it. Once paired, the groups it lists are kept in setup's progress for the check
page, each with its id and Kid hint, WhatsApp is done, and the always-running job is installed so
that `follow` keeps the picked groups' messages from then on (ADR 0026).

A link that isn't fresh is paired the same way: a page opened with picked groups and no pairing,
since WhatsApp ended the link or the groups were picked when WhatsApp for Mac was read, goes to
the step, and the pairing keeps the groups, fills in the ids of those picked by name only, and
returns the family to where they were. Turning WhatsApp off removes the job, the pairing and the
messages kept."""
from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

from .. import failure, linked_device, setup_whatsapp
from ..config import Config
from ..setup_connect import Nothing, Reply, parse
from ..system import jobs
from .context import Action, Phase

# What the page is told about a pairing: `starting` until the first QR string, `qr` with the one
# to draw, `linking` once scanned while the groups are listed, then `paired`. One that ended
# without pairing: `timed-out` when the phone didn't scan in time, which the page offers a new
# code for, or `failed` with why. `no-library` without the pinned Node or the Baileys tree, and
# `no-pairing` when none was started or the last was stopped.
PAIR_RESULTS = ("starting", "qr", "linking", "paired", "timed-out", "failed", "no-library",
                "no-pairing")
UNDER_WAY = ("starting", "qr", "linking")
NOT_SCANNED = ("starting", "qr")  # what moving on from the step still ends
# What turning WhatsApp off says: `off`, or `failed` with why.
OFF_RESULTS = ("off", "failed")
# The phases past Connect a page opened again is taken back from to pair again, and returned to.
AFTER_CONNECT = ("check", "first-brief", "finish")


@dataclass
class _Run:
    """One run of `pair`. `said` is replaced whole, never changed in place, so a check reads
    either the last word or the one before."""
    proc: subprocess.Popen
    said: dict[str, Any]
    stopped: bool = False
    off: bool = False  # ended as WhatsApp was turned off: nothing is installed after it


class WhatsApp(Phase):
    def __init__(self, context) -> None:
        super().__init__(context)
        self._lock = threading.Lock()  # one pairing at a time
        self._run: _Run | None = None
        # The phase a page opened again was at before it was taken to pair again, which pairing
        # returns it to. None when the family got to the step in setup's order.
        self._back_to: str | None = None

    def routes(self) -> dict[str, Action]:
        return {"api/whatsapp/pair": self.pair, "api/whatsapp/check": self.check_pairing,
                "api/whatsapp/stop": self.stop_pairing, "api/whatsapp/off": self.turn_off}

    def opening(self) -> None:
        """Takes a page opened with picked groups that need a new pairing to the step: the link
        WhatsApp ended, or groups picked when WhatsApp for Mac was read, which have none. From a
        phase past Connect, pairing then returns the family there; in Connect, a step done is
        to do again, and one to do comes in its turn."""
        if not needs_pairing(self.context.model()):
            return
        with self.context.connect.lock:
            progress = self.context.progress()
            phase = progress["phase"]
            if phase in AFTER_CONNECT:
                self._back_to = phase
            elif not (phase == "connect" and progress["sources"]["whatsapp"] == "done"):
                return
            self.context.connect.save({"progress": {"phase": "connect", "source": "whatsapp",
                                                    "sources": {"whatsapp": "to-do"}}})

    def state(self, progress: dict[str, Any]) -> dict[str, Any]:
        """How many groups are picked already, which a new pairing keeps."""
        return {"whatsapp": {"picked": len(self.context.model().whatsapp.chats)}}

    def pair(self, raw: Any) -> Reply:
        """Starts pairing, unless a pairing is under way, which the page then goes on with. A
        running always-running job is stopped first, so it doesn't write the pairing it was
        started with over the new one."""
        parse(Nothing, raw)
        with self._lock:
            if self._run is not None and self._run.said["result"] in UNDER_WAY:
                return HTTPStatus.OK, self._run.said
            command = linked_device.command("pair", self.context.model(),
                                            timeout=self.context.timeouts.whatsapp_pair)
            if command is None:
                self._run = None
                return HTTPStatus.OK, {"result": "no-library"}
            following = jobs.always_status().installed
            if following:
                jobs.stop_always()
            proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                    errors="replace")
            run = _Run(proc, {"result": "starting"})
            self._run = run
        threading.Thread(target=self._pairing, args=(run, following), daemon=True).start()
        return HTTPStatus.OK, run.said

    def check_pairing(self, raw: Any) -> Reply:
        """What the pairing last said, with setup's progress once paired."""
        parse(Nothing, raw)
        run = self._run
        said = run.said if run is not None else {"result": "no-pairing"}
        if said["result"] == "paired":
            return HTTPStatus.OK, {**said, "progress": self.context.progress()}
        return HTTPStatus.OK, said

    def stop_pairing(self, raw: Any) -> Reply:
        """Ends a pairing the phone hasn't scanned yet, as the family moves on from the step."""
        parse(Nothing, raw)
        with self._lock:
            if self._run is not None and self._run.said["result"] in NOT_SCANNED:
                _end(self._run)
        return HTTPStatus.OK, {"result": "stopped"}

    def turn_off(self, raw: Any) -> Reply:
        """Turns WhatsApp off: ends a pairing under way, stops and removes the always-running
        job, logs out as best it can, deletes the Linked Device's folder, and saves WhatsApp as
        off and skipped, keeping the picked groups for turning it on again. The page then tells
        the family to remove the device on the phone."""
        parse(Nothing, raw)
        with self._lock:
            if self._run is not None and self._run.proc.poll() is None:
                self._run.off = True
                _end(self._run)
            self._back_to = None
            try:
                linked_device.forget()
            except Exception as e:  # such as a file Windows keeps open: the page says why
                return HTTPStatus.OK, {"result": "failed", "reason": failure.logged(
                    "WhatsApp wasn't turned off", failure.raised(e, "Turning it off"))}
        status, out = self.context.connect.skip("whatsapp")
        if status != HTTPStatus.OK:
            return status, out
        return HTTPStatus.OK, {"result": "off", "progress": out["progress"]}

    def stop(self) -> None:
        if self._run is not None and self._run.proc.poll() is None:
            _end(self._run)

    def _pairing(self, run: _Run, following: bool) -> None:
        """Reads what `pair` prints until it exits, then says how it ended. Never leaves the page
        waiting."""
        paired, groups = False, None
        try:
            assert run.proc.stdout is not None and run.proc.stderr is not None
            for line in run.proc.stdout:
                said = _said(line)
                if isinstance(said.get("qr"), str):
                    run.said = {"result": "qr", "qr": said["qr"]}
                elif said.get("paired") is True:
                    paired = True
                    run.said = {"result": "linking"}
                elif isinstance(said.get("groups"), list):
                    groups = said["groups"]
            code = run.proc.wait()
            errors = run.proc.stderr.read()
            if run.off:
                done = {"result": "no-pairing"}
            elif paired and groups is not None:
                done = self._paired(groups)
            elif run.stopped:
                done = {"result": "no-pairing"}
            elif code == linked_device.TIMED_OUT and not paired:
                done = {"result": "timed-out"}
            else:  # its QR strings aren't passed on, only what it said went wrong
                done = {"result": "failed", "reason": failure.logged(
                    "WhatsApp wasn't paired", failure.exited("The pairing", code, errors, None))}
        except Exception as e:
            run.proc.kill()
            done = {"result": "failed", "reason": failure.logged(
                "WhatsApp wasn't paired", failure.raised(e, "The pairing"))}
        if done["result"] != "paired" and following and not run.off:
            self._follow_again()
        run.said = done

    def _paired(self, groups: list[Any]) -> dict[str, Any]:
        """Keeps the groups for the check page, each with its Kid hint, and WhatsApp is done,
        unless the family skipped it meanwhile: their choice stands. Groups picked already stay
        picked, those picked by name only given their ids, and a page taken to the step to pair
        again goes back to where it was. Then marks the link paired and installs the
        always-running job."""
        context = self.context
        found = [{"name": g.get("subject") or g["id"], "id": g["id"]} for g in groups
                 if isinstance(g, dict) and isinstance(g.get("id"), str)]
        cfg = context.model()
        back, self._back_to = self._back_to, None
        answers: dict[str, Any] = {"progress": {
            "whatsapp_chats": setup_whatsapp.with_hints(found, cfg.kids),
            **({"phase": back, "source": None} if back else {})}}
        if cfg.whatsapp.chats:
            kept = setup_whatsapp.with_ids(cfg.whatsapp.chats, found)
            answers["sources"] = {"whatsapp": {
                "enabled": True, "chats": [c.model_dump(exclude_none=True) for c in kept]}}
        status, out = context.connect.complete(
            "whatsapp", answers, keep_skipped=True,
            move_on=lambda progress: back is None and progress["source"] == "whatsapp")
        if status != HTTPStatus.OK:
            return {"result": "failed", "reason": failure.logged(
                "WhatsApp's groups weren't saved", out.get("reason") or
                f"Saving the groups said {out['result']}.")}
        # Only once saved: a link marked paired whose groups weren't would never be offered again.
        linked_device.mark_paired()
        if out["progress"]["sources"]["whatsapp"] == "done":
            self._follow_again()
        return {"result": "paired"}

    def _follow_again(self) -> None:
        """Installs the always-running job on the pairing there is. A job that doesn't install
        leaves the pairing as it is: doctor says the job isn't there."""
        try:
            linked_device.install_follow(self.context.model())
        except Exception as e:
            failure.logged("The Linked Device's job wasn't installed",
                           failure.raised(e, "Installing the job"))


def needs_pairing(cfg: Config) -> bool:
    """Whether WhatsApp has groups picked but no pairing: WhatsApp ended the link, or there never
    was one. A pairing none of whose groups had a picked name has groups to pick on the check page
    instead."""
    return cfg.whatsapp.enabled and bool(cfg.whatsapp.chats) and not linked_device.link().paired


def _end(run: _Run) -> None:
    run.stopped = True
    run.proc.terminate()


def _said(line: str) -> dict[str, Any]:
    try:
        said = json.loads(line)
    except ValueError:
        return {}
    return said if isinstance(said, dict) else {}
