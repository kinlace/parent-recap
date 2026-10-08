"""Score Brief quality on the eval cases with a real model, e.g. before and after a prompt change:

    python -m family_brief.eval --backend claude,codex [--language en,zh,fi] [--model ...] [--repeat 2] [--judge]

Every case is one real model call per Brief language, so this costs time and quota and is not
part of the test suite. Each run is saved under --out and compared with the latest earlier run of
the same backend, model, language and case folder. --repeat N runs the whole set N times and reports the spread,
which tells a real change from the model's own run-to-run noise."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from ..config import LLMConfig
from ..brief_text import TEXT
from ..collectors.base import Message
from ..summarize import (_strip_code_fence, call_llm_json, digest_of, for_the_ai, summarize_reply,
                         system_prompt)
from .cases import BUNDLED, Case, load_cases
from .score import aggregate, is_clean, score_case

JUDGE_PROMPT = """You stand in for the parents who read a nightly family Brief. Below are one night's raw messages and the Digest (Markdown) written from them.
Judge only how readable the Digest is: can a parent see in 10 seconds what to know and do tonight; is it clear which kid each point is about; is anything wordy, repeated or irrelevant.
Don't judge whether the facts are right (a program checks that). Reply with one JSON object only: {"score": an integer from 1 to 5, "reason": "one sentence"}"""

# Saved runs from before the Brief had a language all scored Chinese Briefs.
LEGACY_RUN_LANGUAGE = "zh"

# Higher is better for every metric except these.
LOWER_IS_BETTER = {"forbidden_hits", "seconds_per_night", "tokens_per_night"}


def _strictly_valid(text: str) -> bool:
    """Whether the reply parses as one JSON object without the parser's repairs."""
    try:
        return isinstance(json.loads(_strip_code_fence(text.strip()), strict=False), dict)
    except json.JSONDecodeError:
        return False


def _judge(case: Case, messages: list[Message], digest: str) -> dict[str, Any] | None:
    bodies = "\n\n".join(f"[{m.source}] {m.subject or m.chat_name or ''}\n{m.body}" for m in messages)
    try:
        verdict = call_llm_json(case.household, f"## Raw messages\n\n{bodies or '(none)'}\n\n## Digest\n\n{digest}",
                                JUDGE_PROMPT)
        return {"score": int(verdict["score"]), "reason": str(verdict.get("reason", ""))}
    except Exception as e:  # a failed judge call must not sink the deterministic scores
        logging.getLogger(__name__).warning("judge failed on %s: %s", case.name, e)
        return None


def run_case(case: Case, judge: bool) -> dict[str, Any]:
    started = time.monotonic()
    summary, reply, error = None, None, None
    # A message that looks sensitive reaches neither the Brief's call nor the judge's, as on an evening.
    messages, _held_back = for_the_ai(case.household, case.messages)
    try:
        summary, reply = summarize_reply(case.household, messages, case.calendar, case.queued,
                                         case.earlier_briefs, now=case.now)
    except Exception as e:
        error = str(e)
    result = score_case(case.expect, summary, case.household.timezone)
    result.update(
        # Pausing for a busy model is the provider's time, not the night's.
        seconds=round(time.monotonic() - started - (reply.waited if reply else 0), 1),
        tokens=reply.tokens if reply else None,
        valid_json=bool(reply) and _strictly_valid(reply.text),
        error=error,
        summary=summary,
    )
    result["clean"] = is_clean(result)
    if judge and summary and (digest := digest_of(summary)):
        result["judge"] = _judge(case, messages, digest)
    return result


def run_once(cases: list[Case], judge: bool, jobs: int) -> dict[str, Any]:
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        results = dict(zip((c.name for c in cases), pool.map(lambda c: run_case(c, judge), cases)))
    metrics = aggregate(list(results.values()))
    scores = [r["judge"]["score"] for r in results.values() if r.get("judge")]
    metrics["judge_readability"] = round(mean(scores), 2) if scores else None
    return {"cases": results, "metrics": metrics}


def _spread(runs: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, list]]:
    means, spread = {}, {}
    for key in runs[0]["metrics"]:
        values = [r["metrics"][key] for r in runs if r["metrics"][key] is not None]
        means[key] = round(mean(values), 3) if values else None
        spread[key] = [min(values), max(values)] if values else None
    return means, spread


def _previous(out: Path, current: Path, meta: dict[str, Any]) -> dict[str, Any] | None:
    for path in sorted(out.glob("*.json"), reverse=True):
        if path == current:
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if all(data.get(k) == meta[k] for k in ("backend", "model", "cases_dir")) \
                and data.get("language", LEGACY_RUN_LANGUAGE) == meta["language"]:
            return data | {"_path": str(path)}
    return None


def _fmt(v: Any) -> str:
    return "—" if v is None else str(v)


def _problems(night: dict[str, Any]) -> list[str]:
    problems = []
    a, e, n = night["actions"], night["events"], night["notices"]
    for count, label in ((a["expected"] - a["matched"], "action missed"),
                         (a["predicted"] - a["matched"], "extra action"),
                         (a["matched"] - a["due_ok"], "wrong due date"),
                         (a["matched"] - a["kid_ok"], "wrong Kid"),
                         (e["expected"] - e["matched"], "event missed"),
                         (e["predicted"] - e["matched"], "extra event"),
                         (e["matched"] - e["start_ok"], "wrong start"),
                         (n["required"] - n["found"], "notice missed"),
                         (n["hits"], "forbidden text"),
                         (night["dates"]["iso"], "text with a YYYY-MM-DD date")):
        if count:
            problems.append(f"{count} {label}")
    if night["error"]:
        problems.append(f"error: {night['error'][:80]}")
    elif not night["valid_json"]:
        problems.append("JSON needed repair")
    return problems


def report(data: dict[str, Any], previous: dict[str, Any] | None) -> str:
    runs = data["runs"]
    lines = [f"Parent Recap eval · {data['backend']} ({data['model'] or 'default model'}) · "
             f"{data['language']} · {len(runs[0]['cases'])} cases × {len(runs)} run(s) · "
             f"prompt {data['prompt_sha']}"]
    if previous:
        lines.append(f"compared with {Path(previous['_path']).name} (prompt {previous.get('prompt_sha')})")
    lines.append("")
    lines.append(f"{'metric':<20}{'score':>8}{'spread':>16}{'previous':>10}{'change':>9}")
    for key, value in data["metrics"].items():
        lo_hi = data["spread"].get(key)
        spread = f"{lo_hi[0]}–{lo_hi[1]}" if lo_hi and len(runs) > 1 else ""
        before = (previous or {}).get("metrics", {}).get(key)
        change = ""
        if isinstance(value, (int, float)) and isinstance(before, (int, float)):
            delta = round(value - before, 3)
            better = delta < 0 if key in LOWER_IS_BETTER else delta > 0
            change = f"{delta:+}" + (" ↑" if better else " ↓" if delta else "")
        if key == "judge_readability":
            lines.append("")
            lines.append("LLM judge (not deterministic, compare with care):")
        lines.append(f"{key:<20}{_fmt(value):>8}{spread:>16}{_fmt(before) if previous else '':>10}{change:>9}")
    lines.append("")
    lines.append("Per case (clean = nothing missed, extra or wrong):")
    for name in runs[0]["cases"]:
        nights = [r["cases"][name] for r in runs]
        clean = sum(1 for n in nights if n["clean"])
        # Every run that wasn't clean, so a case that slipped in only one run still says why.
        notes = [(f"run {i}: " if len(runs) > 1 else "") + ", ".join(_problems(n))
                 for i, n in enumerate(nights, 1) if not n["clean"]]
        lines.append(f"  {name:<36} clean {clean}/{len(runs)}  {'; '.join(notes)}")
    return "\n".join(lines)


def evaluate(cases: list[Case], args: argparse.Namespace, backend: str, language: str) -> None:
    """Run, save and print the scorecard of one backend writing Briefs in one language."""
    llm = LLMConfig(backend=backend, model=args.model, codex_path=args.codex_path)
    for c in cases:
        c.household = c.household.model_copy(update={"llm": llm, "summary_language": language})
    runs = []
    for i in range(args.repeat):
        print(f"{backend} {language} run {i + 1}/{args.repeat}: {len(cases)} cases…", file=sys.stderr)
        runs.append(run_once(cases, args.judge, max(1, args.jobs)))
    metrics, spread = _spread(runs)

    finished = datetime.now(timezone.utc)
    data = {
        "backend": backend,
        "model": args.model,
        "language": language,
        "cases_dir": str(args.cases.resolve()),
        "prompt_sha": hashlib.sha256(system_prompt(language, masked=cases[0].household.ai_filter.enabled)
                                     .encode()).hexdigest()[:8],
        "finished": finished.isoformat(timespec="seconds"),
        "metrics": metrics,
        "spread": spread,
        "runs": runs,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / (f"{finished:%Y%m%d-%H%M%S-%f}-{backend}{'-' + args.model if args.model else ''}"
                       f"-{language}.json")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    if args.compare:
        previous = json.loads(args.compare.read_text()) | {"_path": str(args.compare)}
    else:
        previous = _previous(args.out, path, data)
    print(report(data, previous))
    print(f"\nsaved {path}\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m family_brief.eval", description=__doc__.split("\n\n")[0])
    p.add_argument("--backend", default="claude",
                   help="claude, codex, or both as claude,codex (one scorecard each)")
    p.add_argument("--model", default=None, help="model name passed to the backend (default: its own)")
    p.add_argument("--language", default=",".join(TEXT),
                   help=f"the reviewed Brief language(s) to score, one scorecard each (default: {','.join(TEXT)})")
    p.add_argument("--cases", type=Path, default=BUNDLED,
                   help="case folder (default: the synthetic cases shipped with Parent Recap)")
    p.add_argument("--only", default=None, help="comma-separated case names to run")
    p.add_argument("--repeat", type=int, default=1, help="run the whole set N times to see the spread")
    p.add_argument("--jobs", type=int, default=1,
                   help="cases in flight at once (faster; seconds_per_night then includes queueing)")
    p.add_argument("--judge", action="store_true", help="also have the model rate each Digest's readability")
    p.add_argument("--codex-path", default=None)
    p.add_argument("--out", type=Path, default=Path("~/ParentRecap/eval").expanduser(),
                   help="where runs are saved (default: ~/ParentRecap/eval)")
    p.add_argument("--compare", type=Path, default=None, help="saved run to compare with (default: latest)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    backends = args.backend.split(",")
    if not set(backends) <= {"claude", "codex"}:
        p.error(f"unknown backend in {args.backend!r}: use claude, codex or claude,codex")
    if len(backends) > 1 and (args.model or args.compare):
        p.error("--model and --compare name one backend's model or run; give a single --backend")
    languages = args.language.split(",")
    if not set(languages) <= set(TEXT):
        p.error(f"unknown language in {args.language!r}: only reviewed languages have eval cases "
                f"({', '.join(TEXT)})")
    if len(languages) > 1 and args.compare:
        p.error("--compare names one language's run; give a single --language")
    cases = [c for c in load_cases(args.cases)
             if not args.only or c.name in args.only.split(",")]
    if not cases:
        print(f"no cases in {args.cases}", file=sys.stderr)
        return 2
    for backend in backends:
        for language in languages:
            evaluate(cases, args, backend, language)
    return 0


if __name__ == "__main__":
    sys.exit(main())
