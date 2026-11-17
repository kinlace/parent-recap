"""The Problem Report (ADR 0048, #925): what a parent whose Parent Recap isn't working sends the
maintainers from Parent Recap Check, without typing a command. Check runs `parent-recap doctor
--offer-problem-report`; after doctor's results the window shows the rest of the report (the
version and system, and the last lines of Parent Recap's own logs) and asks one yes/no question in
the family's language. Only a yes sends: no, an empty answer, any other answer, the end of the
input and closing the window send nothing, and nothing is kept to send later. After a yes it asks
for one line on what went wrong, which Return skips, sends the report and says whether it went.

`doctor` run any other way, in a chat, by the setup page or a script, has no such flag, so it
asks nothing and sends nothing.

The log lines are the part that can hold free text, so only the program's own dated log lines go
(`OWN_LINE`): never pip's output, an installer's or a traceback. The window says in plain words
that what it shows goes to the Parent Recap team and may be read by a person.

It goes to the gateway's `POST /v1/problem`, the address the Brief already goes to, from inside
the program (no shell, PowerShell or `curl`, #591), with the Gateway Key when there is one and
under the Run Report's random id when there is none, as on a computer whose install never
finished. The gateway limits how many a caller sends and how large each is, and keeps them 30
days (ops/gateway/README.md, "Problem Reports")."""
from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import gateway, last_run, own_node, run_report
from .config import ArchiveConfig, Config, LLMConfig

# The logs a Problem Report holds the last lines of, by the name the gateway knows them by: the
# evening run's, the update's, and the newest install's.
LOGS = ("run", "update", "install")
LINES = 30          # at most, of each log
MAX_LINE = 300      # characters of a line; the rest is cut
MAX_NOTE = 300      # characters of the parent's line, as the gateway allows
TAIL_BYTES = 256 * 1024  # of a log's end read: a log is never read whole
TIMEOUT = 30

# A line the program itself wrote, with its time first: the evening run's logging
# (`21:00:01 INFO family_brief.run | …`, the setup preview's with its date before), or the
# update's log (`2026-11-15T23:54:44+02:00 install: …`). Anything else is left out.
OWN_LINE = re.compile(
    r"(\d{4}-\d\d-\d\d )?\d\d:\d\d:\d\d (DEBUG|INFO|WARNING|ERROR|CRITICAL) family_brief[\w.]* \| "
    r"|\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:?\d\d) [a-z][a-z-]*: ",
    re.ASCII)

SYSTEM_NAMES = {"macos": "macOS", "windows": "Windows", "linux": "Linux"}


# ── What the window says, in each language the program has reviewed text in

@dataclass(frozen=True)
class Words:
    heading: str
    goes: str
    version: str           # {version}, {system}
    logs: str
    log_names: dict[str, str]
    no_lines: str
    question: str
    yes: frozenset[str]    # what counts as yes, casefolded; nothing else does
    note: str
    sent: str
    not_sent: str          # {why}
    offline: str
    too_many: str
    refused: str           # {status}
    nothing_sent: str


WORDS: dict[str, Words] = {
    "en": Words(
        heading="Problem Report",
        goes="If you say yes, the checks above and the lines below go to the Parent Recap team, "
             "and a person on the team may read them. Nothing is sent unless you type yes.",
        version="Version {version} on {system}",
        logs="The last lines of Parent Recap's own logs:",
        log_names={"run": "Evening run", "update": "Updates", "install": "Install"},
        no_lines="(no lines)",
        question="Send this Problem Report to the Parent Recap team? Type yes and press Return "
                 "to send it; any other answer sends nothing: ",
        yes=frozenset({"yes", "y"}),
        note="In one line, what went wrong? (Return skips this): ",
        sent="✅ Sent. The Parent Recap team has your Problem Report.",
        not_sent="❌ It wasn't sent: {why}. Nothing was kept to send later.",
        offline="this computer couldn't reach Parent Recap's server",
        too_many="the server has taken as many as it takes from this computer today; try "
                 "again tomorrow",
        refused="the server didn't take it (HTTP {status})",
        nothing_sent="Nothing was sent."),
    "fi": Words(
        heading="Vikailmoitus",
        goes="Jos vastaat kyllä, yllä olevat tarkistukset ja alla olevat rivit lähetetään Parent "
             "Recapin tiimille, ja joku tiimistä voi lukea ne. Mitään ei lähetetä, ellet "
             "kirjoita kyllä.",
        version="Versio {version}, {system}",
        logs="Parent Recapin omien lokien viimeiset rivit:",
        log_names={"run": "Iltaajo", "update": "Päivitykset", "install": "Asennus"},
        no_lines="(ei rivejä)",
        question="Lähetetäänkö tämä vikailmoitus Parent Recapin tiimille? Kirjoita kyllä ja "
                 "paina Enter lähettääksesi; mikä tahansa muu vastaus ei lähetä mitään: ",
        yes=frozenset({"kyllä", "kylla", "k", "yes", "y"}),
        note="Kerro yhdellä rivillä, mikä meni vikaan (Enter ohittaa): ",
        sent="✅ Lähetetty. Parent Recapin tiimi sai vikailmoituksesi.",
        not_sent="❌ Vikailmoitusta ei lähetetty: {why}. Mitään ei jäänyt odottamaan "
                 "myöhempää lähetystä.",
        offline="tietokone ei saanut yhteyttä Parent Recapin palvelimeen",
        too_many="palvelin on tänään ottanut tältä tietokoneelta niin monta kuin se ottaa; "
                 "yritä huomenna uudelleen",
        refused="palvelin ei ottanut sitä vastaan (HTTP {status})",
        nothing_sent="Mitään ei lähetetty."),
    "zh": Words(
        heading="问题报告",
        goes="如果你回答“是”，上面的检查结果和下面这些行会发送给 Parent Recap 团队，团队里的人可能会"
             "阅读它们。除非你输入“是”，否则不会发送任何内容。",
        version="版本 {version}，{system}",
        logs="Parent Recap 自己日志的最后几行：",
        log_names={"run": "晚间运行", "update": "更新", "install": "安装"},
        no_lines="（没有）",
        question="要把这份问题报告发送给 Parent Recap 团队吗？输入“是”并按回车键发送；其他任何回答都"
                 "不会发送：",
        yes=frozenset({"是", "是的", "yes", "y"}),
        note="用一行写下出了什么问题（直接按回车键跳过）：",
        sent="✅ 已发送。Parent Recap 团队收到了你的问题报告。",
        not_sent="❌ 没有发送：{why}。也没有留下任何内容稍后再发。",
        offline="这台电脑连不上 Parent Recap 的服务器",
        too_many="服务器今天从这台电脑收到的报告已经够多了；请明天再试",
        refused="服务器没有接收（HTTP {status}）",
        nothing_sent="没有发送任何内容。"),
}


def words(language: str) -> Words:
    return WORDS.get(language, WORDS["en"])


# ── What the report holds

@dataclass(frozen=True)
class Report:
    about: dict[str, str]          # version, system, system_version
    doctor: str                    # doctor's output, as the window showed it
    logs: dict[str, list[str]]     # by LOGS' names

    def body(self, note: str = "") -> bytes:
        data = {**self.about, "doctor": self.doctor, "logs": self.logs}
        if note:
            data["note"] = note
        return json.dumps(data, ensure_ascii=False).encode("utf-8")


def own_lines(text: str, limit: int = LINES) -> list[str]:
    """The last `limit` lines of `text` the program itself wrote (OWN_LINE), each cut to
    MAX_LINE characters, with any character a terminal would act on as a space."""
    kept = [line for line in text.splitlines() if OWN_LINE.match(line)]
    return ["".join(c if c.isprintable() else " " for c in line[:MAX_LINE]) for line in kept[-limit:]]


def _tail(path: Path) -> str:
    """The end of the file at `path` as text, or "" when it can't be read."""
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            raw = f.read()
    except OSError:
        return ""
    text = raw.decode("utf-8", errors="replace")
    # A cut line at the start isn't the program's whole line: it is dropped.
    return text if size <= TAIL_BYTES else text.partition("\n")[2]


def log_files(cfg: Config | None) -> dict[str, Path | None]:
    """Where each of LOGS is: the evening run's in the archive's logs folder, the update's and
    the newest install's in Parent Recap's own folder. None for an install log there is none of."""
    archive = (cfg.archive if cfg is not None else ArchiveConfig()).resolved_dir()
    own = own_node.home() / "logs"
    installs = sorted(own.glob("install-*.log")) if own.is_dir() else []
    return {"run": archive / "logs" / "run-stderr.log", "update": own / "update.log",
            "install": installs[-1] if installs else None}


def gather(cfg: Config | None, doctor: str) -> Report:
    """The Problem Report of this computer: `doctor`'s output, the version and system, and the
    last lines of each log the program wrote itself."""
    logs = {name: own_lines(_tail(path)) if path is not None else []
            for name, path in log_files(cfg).items()}
    return Report(last_run.about(), doctor, logs)


def shown(report: Report, language: str) -> str:
    """What the window shows of the report after doctor's output, which it has shown already."""
    w = words(language)
    about = report.about
    system = " ".join(x for x in (SYSTEM_NAMES.get(about["system"], about["system"]),
                                  about.get("system_version", "")) if x and x != last_run.UNKNOWN)
    lines = [f"── {w.heading} ──", w.goes, "", w.version.format(version=about["version"], system=system),
             "", w.logs]
    for name in LOGS:
        lines.append(f"[{w.log_names[name]}]")
        lines += [f"  {line}" for line in report.logs.get(name, [])] or [f"  {w.no_lines}"]
    return "\n".join(lines)


# ── Asking, and sending

def _answer(ask: Callable[[str], str], prompt: str) -> str | None:
    """What the parent typed, or None when there is nothing to read: the input ended, or the
    window is being closed."""
    try:
        return ask(prompt)
    except (EOFError, KeyboardInterrupt, OSError):
        return None


def is_yes(answer: str | None, language: str) -> bool:
    return answer is not None and answer.strip().casefold() in words(language).yes


def note_of(answer: str | None) -> str:
    """The parent's line as it goes: one line, without control characters, at most MAX_NOTE."""
    if not answer:
        return ""
    return " ".join("".join(c if c.isprintable() else " " for c in answer).split())[:MAX_NOTE]


def _who(cfg: Config | None) -> dict[str, str]:
    """The Gateway Key when one is stored and can be read, else the Run Report's report id, or a
    random id made for this report alone when there is no config to keep one beside."""
    try:
        return {run_report.KEY_HEADER: gateway.key()}
    except gateway.NoKey:
        return {run_report.REPORT_ID_HEADER: _report_id(cfg)}


def _report_id(cfg: Config | None) -> str:
    if cfg is not None:
        try:
            return run_report.report_id(cfg)
        except OSError:
            pass
    return uuid.uuid4().hex


def send(cfg: Config | None, body: bytes, language: str) -> str:
    """Sends the report once, and says in `language` whether it went. Nothing is kept to send
    again: a parent who wants to tries again from Check."""
    w = words(language)
    url = cfg.llm.gateway_url if cfg is not None else LLMConfig().gateway_url
    headers = _who(cfg)
    try:
        status = gateway.post_problem(url, headers, body, TIMEOUT).status_code
        if status == gateway.UNKNOWN_KEY and run_report.KEY_HEADER in headers:
            # A key the gateway no longer knows: under the report id instead, as a Run Report.
            status = gateway.post_problem(
                url, {run_report.REPORT_ID_HEADER: _report_id(cfg)}, body, TIMEOUT).status_code
    except (TimeoutError, gateway.GatewayError):
        return w.not_sent.format(why=w.offline)
    if status == 200:
        return w.sent
    why = w.too_many if status == 429 else w.refused.format(status=status)
    return w.not_sent.format(why=why)


def offer(config: str | None, doctor: str, ask: Callable[[str], str] = input,
          say: Callable[[str], None] = print) -> None:
    """After doctor's results in Parent Recap Check: shows the rest of the Problem Report, asks
    whether to send it, and sends it only on a yes, with the parent's line when they write one.
    Never raises: doctor's results stand whatever happens here."""
    try:
        cfg: Config | None = Config.load(config)
    except Exception:  # noqa: BLE001 - no config, as after an install that never finished
        cfg = None
    language = cfg.summary_language if cfg is not None else "en"
    w = words(language)
    try:
        report = gather(cfg, doctor)
    except Exception as e:  # noqa: BLE001 - a report that can't be made is just not offered
        say(f"\nThe Problem Report couldn't be made ({type(e).__name__}).")
        return
    say("\n" + shown(report, language) + "\n")
    if not is_yes(_answer(ask, w.question), language):
        say(w.nothing_sent)
        return
    note = _answer(ask, w.note)
    if note is None:  # the window was closed while it asked: the parent didn't finish the yes
        say(w.nothing_sent)
        return
    try:
        said = send(cfg, report.body(note_of(note)), language)
    except Exception as e:  # noqa: BLE001 - doctor's results stand
        said = w.not_sent.format(why=type(e).__name__)
    say(said)
