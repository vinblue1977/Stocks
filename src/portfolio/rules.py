"""User-defined alert rules: validation and evaluation against a signal snapshot.

Rules describe conditions the user cares about. They never produce buy/sell advice;
an alert only says "the condition you set is now true".
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


class RuleValidationError(ValueError):
    pass


@dataclass(frozen=True)
class RuleSpec:
    params: dict[str, tuple[float, float]]  # name -> (min, max)
    description: str


RULE_TYPES: dict[str, RuleSpec] = {
    "price_below": RuleSpec({"price": (0.01, 1_000_000)}, "Price closes below $N"),
    "price_above": RuleSpec({"price": (0.01, 1_000_000)}, "Price closes above $N"),
    "price_below_sma": RuleSpec({"window": (5, 250)}, "Price closes below its N-day average"),
    "price_above_sma": RuleSpec({"window": (5, 250)}, "Price closes above its N-day average"),
    "golden_cross": RuleSpec({}, "50-day average crosses above the 200-day average"),
    "death_cross": RuleSpec({}, "50-day average crosses below the 200-day average"),
    "drawdown_below": RuleSpec({"pct": (1, 90)}, "Price falls N% or more below its 1-year high"),
    "rsi_below": RuleSpec({"threshold": (1, 99)}, "14-day RSI falls below N"),
    "rsi_above": RuleSpec({"threshold": (1, 99)}, "14-day RSI rises above N"),
    "weight_above": RuleSpec({"pct": (1, 100)}, "Holding grows past N% of the portfolio"),
    "sentiment_below": RuleSpec({"threshold": (-1, 1)}, "7-day news sentiment falls below N (-1 to +1)"),
}


def validate_rule(body: Mapping[str, Any]) -> dict[str, Any]:
    """Return a normalized rule dict or raise RuleValidationError."""
    rule_type = body.get("type")
    if rule_type not in RULE_TYPES:
        raise RuleValidationError(f"type must be one of: {', '.join(sorted(RULE_TYPES))}")
    ticker = str(body.get("ticker", "")).upper().strip()
    if not TICKER_PATTERN.match(ticker):
        raise RuleValidationError("ticker must look like VTI or BRK.B")
    spec = RULE_TYPES[rule_type]
    raw_params = body.get("params") or {}
    if not isinstance(raw_params, Mapping):
        raise RuleValidationError("params must be an object")
    params: dict[str, float] = {}
    for name, (low, high) in spec.params.items():
        if name not in raw_params:
            raise RuleValidationError(f"params.{name} is required for {rule_type}")
        try:
            value = float(raw_params[name])
        except (TypeError, ValueError) as exc:
            raise RuleValidationError(f"params.{name} must be a number") from exc
        if not low <= value <= high:
            raise RuleValidationError(f"params.{name} must be between {low} and {high}")
        params[name] = value
    return {"type": rule_type, "ticker": ticker, "params": params, "enabled": bool(body.get("enabled", True))}


def _sma_for_window(signal: Mapping[str, Any], window: float) -> float | None:
    """Rules support any window, but the snapshot stores 50/200; other windows come from `smas`."""
    key = int(window)
    if key == 50:
        return signal.get("sma50")
    if key == 200:
        return signal.get("sma200")
    smas = signal.get("smas") or {}
    return smas.get(str(key))


def evaluate(rule: Mapping[str, Any], signal: Mapping[str, Any], weight: float | None) -> tuple[bool, str]:
    """Return (condition_is_true, human readable explanation)."""
    t = rule["type"]
    p = rule.get("params", {})
    ticker = rule["ticker"]
    price = signal.get("price")

    def fmt_pct(x: float) -> str:
        return f"{x * 100:.1f}%"

    if t in ("price_below", "price_above"):
        level = float(p["price"])
        if price is None:
            return False, "No price yet"
        hit = price < level if t == "price_below" else price > level
        side = "below" if price < level else "above" if price > level else "at"
        return hit, f"{ticker} closed at ${price:,.2f}, {side} your ${level:,.2f} level"

    if t in ("price_below_sma", "price_above_sma"):
        window = float(p["window"])
        avg = _sma_for_window(signal, window)
        if price is None or avg is None:
            return False, f"Not enough history for a {int(window)}-day average"
        below = price < avg
        hit = below if t == "price_below_sma" else price > avg
        side = "below" if below else "above"
        return hit, f"{ticker} closed at {price:.2f}, {side} its {int(window)}-day average of {avg:.2f}"

    if t in ("golden_cross", "death_cross"):
        f, s = signal.get("sma50"), signal.get("sma200")
        fp, sp = signal.get("sma50_prev"), signal.get("sma200_prev")
        if None in (f, s, fp, sp):
            return False, "Not enough history for 50/200-day averages"
        if t == "golden_cross":
            return (fp <= sp and f > s), f"{ticker} 50-day average {f:.2f} vs 200-day {s:.2f}"
        return (fp >= sp and f < s), f"{ticker} 50-day average {f:.2f} vs 200-day {s:.2f}"

    if t == "drawdown_below":
        dd = signal.get("drawdown_from_high")
        if dd is None:
            return False, "No price history"
        return dd <= -float(p["pct"]) / 100, f"{ticker} is {fmt_pct(-dd)} below its 1-year high"

    if t in ("rsi_below", "rsi_above"):
        value = signal.get("rsi14")
        if value is None:
            return False, "Not enough history for RSI"
        threshold = float(p["threshold"])
        hit = value < threshold if t == "rsi_below" else value > threshold
        return hit, f"{ticker} 14-day RSI is {value:.1f} (your threshold: {threshold:g})"

    if t == "weight_above":
        if weight is None:
            return False, f"{ticker} is not in the portfolio"
        limit = float(p["pct"]) / 100
        return weight > limit, f"{ticker} is {fmt_pct(weight)} of the portfolio (your limit: {fmt_pct(limit)})"

    if t == "sentiment_below":
        score = signal.get("sentiment_7d")
        if score is None:
            return False, "No recent news scored"
        threshold = float(p["threshold"])
        return score < threshold, f"{ticker} 7-day news sentiment is {score:+.2f} (your threshold: {threshold:+.2f})"

    raise RuleValidationError(f"Unknown rule type {t}")


def needed_sma_windows(rules: list[Mapping[str, Any]]) -> set[int]:
    windows = set()
    for rule in rules:
        if rule["type"] in ("price_below_sma", "price_above_sma"):
            windows.add(int(float(rule["params"]["window"])))
    return windows - {50, 200}
