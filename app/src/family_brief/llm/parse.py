"""Reading the JSON object out of a model's reply, the same for every adapter."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def strip_code_fence(text: str) -> str:
    m = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    return m.group(1) if m else text


def _extract_json_object(text: str) -> str:
    """Find the largest balanced JSON object substring."""
    depth = 0
    start = -1
    best: tuple[int, int] | None = None
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                if best is None or (i - start) > (best[1] - best[0]):
                    best = (start, i + 1)
    return text[best[0]:best[1]] if best else text


def cli_response(stdout: str) -> tuple[dict[str, Any], bool]:
    """Claude CLI returns a JSON envelope with a 'result' field containing the model output.
    Returns the object and whether it needed json-repair, like model_json."""
    envelope = json.loads(stdout)
    result = envelope.get("result", "")
    if isinstance(result, dict):
        return result, False
    if not isinstance(result, str):
        raise ValueError(f"Claude result is a {type(result).__name__}, not text or an object")
    return model_json(result)


def _save_unparsed(result: str, reason: object) -> None:
    dbg = Path.home() / "ParentRecap" / "logs" / "summarize_failed.txt"
    dbg.parent.mkdir(parents=True, exist_ok=True)
    dbg.write_text(result, encoding="utf-8")
    log.error("JSON parse fully failed (%s). Raw saved to %s", reason, dbg)


def model_json(result: str) -> tuple[dict[str, Any], bool]:
    """Parse the model's reply as one JSON object, tolerating fences and small syntax slips, and
    say whether it needed json-repair: a cut-off reply is repaired too, minus what came after the cut.
    Anything that does not yield an object raises ValueError, so the caller falls back."""
    result = strip_code_fence(result.strip())
    # Attempt 1: direct, strict=False (allows control chars inside strings)
    try:
        parsed = json.loads(result, strict=False)
    except json.JSONDecodeError as e1:
        log.warning("First JSON parse failed (%s), trying bracket extraction", e1)
    else:
        if isinstance(parsed, dict):
            return parsed, False
        # Well-formed JSON of the wrong shape, e.g. a list of per-kid entries. Digging an
        # object out of it would silently give a wrong Brief, so treat it as a model failure.
        reason = f"reply is a JSON {type(parsed).__name__}, not an object"
        _save_unparsed(result, reason)
        raise ValueError(reason)
    # Attempt 2: extract largest balanced {...}
    extracted = _extract_json_object(result)
    try:
        parsed = json.loads(extracted, strict=False)
        if isinstance(parsed, dict):
            return parsed, False
        log.warning("Bracket extraction gave a %s, trying json-repair", type(parsed).__name__)
    except json.JSONDecodeError as e2:
        log.warning("Bracket-extracted parse failed (%s), trying json-repair", e2)
    # Attempt 3: json-repair (lenient — handles unescaped quotes, trailing commas, etc.)
    try:
        from json_repair import repair_json
        fixed = repair_json(extracted, return_objects=True)
        if isinstance(fixed, dict):
            return fixed, True
        raise ValueError(f"json-repair returned non-dict: {type(fixed)}")
    except Exception as e3:
        _save_unparsed(result, e3)
        raise
