"""WhatsApp's connect step: pairing the Linked Device (ADR 0025, ADR 0027) with a QR code, on
macOS and Windows alike.

The server runs the Node program's `pair` mode in the background, and the page asks every few
seconds what it last said: the QR string to draw, which WhatsApp changes now and then, until the
phone has scanned it. Once paired, the groups it lists are kept in setup's progress for the check
page, each with its id and Kid hint, WhatsApp is done, and the always-running job is installed so
that `follow` keeps the picked groups' messages from then on (ADR 0026)."""
from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

from .. import failure, linked_device, setup_whatsapp
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


@dataclass
class _Run:
    """One run of `pair`. `said` is replaced whole, never changed in place, so a check reads
    either the last word or the one before."""
    proc: subprocess.Popen
    said: dict[str, Any]
    stopped: bool = False


class WhatsApp(Phase):
    def __init__(self, context) -> None:
        super().__init__(context)
        self._lock = threading.Lock()  # one pairing at a time
        self._run: _Run | None = None

    def routes(self) -> dict[str, Action]:
        return {"api/whatsapp/pair": self.pair, "api/whatsapp/check": self.check_pairing,
                "api/whatsapp/stop": self.stop_pairing}

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
            if paired and groups is not None:
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
        if done["result"] != "paired" and following:
            self._follow_again()
        run.said = done

    def _paired(self, groups: list[Any]) -> dict[str, Any]:
        """Keeps the groups for the check page, each with its Kid hint, and WhatsApp is done,
        unless the family skipped it meanwhile: their choice stands. Then installs the
        always-running job."""
        context = self.context
        found = [{"name": g.get("subject") or g["id"], "id": g["id"]} for g in groups
                 if isinstance(g, dict) and isinstance(g.get("id"), str)]
        chats = setup_whatsapp.with_hints(found, context.model().kids)
        status, out = context.connect.complete(
            "whatsapp", {"progress": {"whatsapp_chats": chats}}, keep_skipped=True,
            move_on=lambda progress: progress["source"] == "whatsapp")
        if status != HTTPStatus.OK:
            return {"result": "failed", "reason": failure.logged(
                "WhatsApp's groups weren't saved", out.get("reason") or
                f"Saving the groups said {out['result']}.")}
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


def _end(run: _Run) -> None:
    run.stopped = True
    run.proc.terminate()


def _said(line: str) -> dict[str, Any]:
    try:
        said = json.loads(line)
    except ValueError:
        return {}
    return said if isinstance(said, dict) else {}
