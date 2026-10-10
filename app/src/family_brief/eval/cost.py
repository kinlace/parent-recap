"""What one night's Brief call would cost on the Anthropic API, from the dated price table in
`prices.yaml`. A model the table doesn't list, or a backend that reports no tokens (codex), has
no cost rather than a wrong one."""
from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from typing import Any

import yaml

PRICES = Path(__file__).parent / "prices.yaml"
KINDS = ("input", "cache_write", "cached_input", "output")


@cache
def prices(path: Path = PRICES) -> dict[str, Any]:
    return yaml.safe_load(path.read_text())


def read_on(path: Path = PRICES) -> str:
    """The date the prices were read."""
    return str(prices(path)["read"])


def rates(model: str | None, path: Path = PRICES) -> dict[str, Any] | None:
    """The model's prices, by its full name or a --model alias. A name the claude CLI reports may
    carry a context-window suffix, such as claude-sonnet-5-5[1m]."""
    if not model:
        return None
    table = prices(path)
    name = re.sub(r"\[.*\]$", "", model.strip())
    name = table.get("aliases", {}).get(name, name)
    return table["models"].get(name)


def night_usd(model: str | None, usage: dict[str, int] | None, path: Path = PRICES) -> float | None:
    """The dollars one call's `usage` (tokens by kind) costs on `model`."""
    price = rates(model, path)
    if price is None or not usage:
        return None
    long = price.get("long_prompt")
    if long and sum(usage.get(k, 0) for k in ("input", "cache_write", "cached_input")) > long["over"]:
        price = long
    return sum(usage.get(kind, 0) * price[kind] for kind in KINDS) / 1_000_000
