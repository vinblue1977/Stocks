"""Checkup math: the portfolio's mix by area vs. the user's own targets, and how it behaves vs. the market.

Pure functions over holdings and price histories, used by the API (Checkup page) and the monthly email.
Everything here describes data and the user's targets; nothing recommends buying or selling.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from portfolio import analytics
from portfolio.buckets import AREAS, UNASSIGNED, area_of

TRADING_DAYS = 252
SMALL_POSITION = 500.0  # dollars


def exposure(positions: Sequence[Mapping[str, Any]], overrides: Mapping[str, str] | None = None) -> dict[str, float]:
    """Dollar value per area (priced positions only)."""
    out: dict[str, float] = {}
    for p in positions:
        if p.get("value") is not None:
            area = area_of(p["ticker"], overrides)
            out[area] = out.get(area, 0.0) + float(p["value"])
    return out


def drift(by_area: Mapping[str, float], total: float, targets: Mapping[str, float]) -> list[dict[str, Any]]:
    """Each area's share now vs. its target (both 0-1), and the gap in dollars (positive = above target)."""
    rows = []
    for key, label in AREAS.items():
        now = by_area.get(key, 0.0) / total if total else 0.0
        target = float(targets.get(key, 0.0))
        rows.append(
            {"area": key, "label": label, "now": round(now, 4), "target": target, "gap": round((now - target) * total, 2)}
        )
    if by_area.get(UNASSIGNED):
        now = by_area[UNASSIGNED] / total if total else 0.0
        rows.append(
            {"area": UNASSIGNED, "label": "Unassigned", "now": round(now, 4), "target": 0.0, "gap": round(by_area[UNASSIGNED], 2)}
        )
    return rows


def split(by_area: Mapping[str, float], total: float, targets: Mapping[str, float], amount: float) -> list[tuple[str, float]]:
    """Where `amount` of new money would go: areas below target after adding it, in proportion to the shortfall."""
    after = total + amount
    shortfall = {k: (float(targets.get(k, 0.0)) * after) - by_area.get(k, 0.0) for k in AREAS}
    shortfall = {k: v for k, v in shortfall.items() if v > 0}
    gap = sum(shortfall.values())
    if amount <= 0 or not gap:
        return []
    parts = [(k, round(v / gap * amount, 2)) for k, v in shortfall.items()]
    return sorted((p for p in parts if p[1] >= 1), key=lambda p: -p[1])


def effective_holdings(values: Mapping[str, float]) -> float | None:
    """How many equal-sized holdings this spread is worth (1 / sum of squared weights)."""
    total = sum(values.values())
    if not total:
        return None
    return round(1 / sum((v / total) ** 2 for v in values.values()), 1)


def _returns(series: Sequence[tuple[str, float]]) -> dict[str, float]:
    return {series[i][0]: series[i][1] / series[i - 1][1] - 1 for i in range(1, len(series)) if series[i - 1][1]}


def _worst_drop(series: Sequence[tuple[str, float]]) -> float | None:
    peak, worst = 0.0, 0.0
    for _, v in series:
        peak = max(peak, v)
        if peak:
            worst = min(worst, v / peak - 1)
    return round(worst, 4) if series else None


def _year(series: Sequence[tuple[str, float]], year: str) -> list[tuple[str, float]]:
    return [p for p in series if p[0].startswith(year)]


def risk(series: Sequence[tuple[str, float]], market: Sequence[tuple[str, float]] | None) -> dict[str, Any]:
    """Returns, swings and drops of a value series, and the same for the market where given."""
    last_year = list(series[-(TRADING_DAYS + 1) :])
    out: dict[str, Any] = {
        "return_1y": analytics.trailing_return(series),
        "worst_drop_1y": _worst_drop(last_year),
        "worst_drop_5y": _worst_drop(series),
        "volatility": None,
        "beta": None,
    }
    y2022 = _year(series, "2022")
    out["return_2022"] = round(y2022[-1][1] / y2022[0][1] - 1, 4) if len(y2022) > 1 else None
    out["worst_drop_2022"] = _worst_drop(y2022) if len(y2022) > 1 else None
    mine = _returns(last_year)
    if len(mine) >= 20:
        out["volatility"] = round(statistics.pstdev(mine.values()) * math.sqrt(TRADING_DAYS), 4)
    if market:
        theirs = _returns(market[-(TRADING_DAYS + 1) :])
        common = [d for d in mine if d in theirs]
        if len(common) >= 20:
            a, b = [mine[d] for d in common], [theirs[d] for d in common]
            variance = statistics.variance(b)
            out["beta"] = round(statistics.covariance(a, b) / variance, 2) if variance else None
        m2022 = _year(market, "2022")
        out["market"] = {
            "return_1y": analytics.trailing_return(market),
            "volatility": round(statistics.pstdev(theirs.values()) * math.sqrt(TRADING_DAYS), 4) if len(theirs) >= 20 else None,
            "return_2022": round(m2022[-1][1] / m2022[0][1] - 1, 4) if len(m2022) > 1 else None,
            "worst_drop_2022": _worst_drop(m2022) if len(m2022) > 1 else None,
        }
    return out


def view(
    holdings: Sequence[Mapping[str, Any]],
    histories: Mapping[str, Sequence[tuple[str, float]]],
    market: Sequence[tuple[str, float]] | None,
    targets: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """The Checkup for one set of holdings (household or one person)."""
    latest = {t: h[-1][1] for t, h in histories.items() if h}
    summary = analytics.summarize(holdings, latest, latest)
    overrides = (targets or {}).get("overrides") or {}
    by_area = exposure(summary["positions"], overrides)
    total = summary["total_value"]
    per_ticker: dict[str, float] = {}
    for p in summary["positions"]:
        if p["value"] is not None:
            per_ticker[p["ticker"]] = per_ticker.get(p["ticker"], 0.0) + p["value"]
    area_pct = {k: v["pct"] / 100 for k, v in ((targets or {}).get("areas") or {}).items()}
    # Tickers with no prices yet (new, still loading) would empty the shared-date history, so leave them out.
    series = analytics.value_history([h for h in holdings if histories.get(h["ticker"])], histories)
    groups: dict[str, list[str]] = {}
    for ticker in sorted(per_ticker, key=lambda t: -per_ticker[t]):
        groups.setdefault(area_of(ticker, overrides), []).append(ticker)
    ranked = [
        {"ticker": t, "value": round(v, 2), "area": area_of(t, overrides), "return_1y": analytics.trailing_return(histories[t])}
        for t, v in sorted(per_ticker.items(), key=lambda kv: -kv[1])
    ]
    return {
        "total_value": total,
        "holdings": ranked,
        "top": ranked[:5],
        "by_area": {k: round(v, 2) for k, v in by_area.items()},
        "drift": drift(by_area, total, area_pct) if targets else None,
        "effective_holdings": effective_holdings(per_ticker),
        "holdings_count": len(per_ticker),
        "overlap": {k: [{"ticker": t, "value": round(per_ticker[t], 2)} for t in ts] for k, ts in groups.items() if len(ts) > 1},
        "small_positions": sorted(
            ({"ticker": t, "value": round(v, 2)} for t, v in per_ticker.items() if v < SMALL_POSITION), key=lambda x: x["value"]
        ),
        "history_1y": _with_market(series[-(TRADING_DAYS + 1) :], market),
        "risk": risk(series, market),
    }


def _with_market(series: Sequence[tuple[str, float]], market: Sequence[tuple[str, float]] | None) -> list[dict[str, Any]]:
    """The last year of value, with the market's close on the same days (for the report's chart)."""
    closes = dict(market or [])
    return [{"date": d, "value": v, "market": closes.get(d)} for d, v in series]


MARKET_TICKER = "VTI"  # "the market": the total US stock market


def build(repo: Any, holdings: Sequence[Mapping[str, Any]], targets: Mapping[str, Any] | None) -> dict[str, Any]:
    """Loads prices and returns the Checkup for the household and each person who has holdings."""
    from portfolio.db import PEOPLE, person_of  # local import keeps this module free of AWS clients

    tickers = sorted({h["ticker"] for h in holdings})
    histories = repo.price_histories(tickers)
    as_of = max((h[-1][0] for h in histories.values() if h), default=None)
    market = histories.get(MARKET_TICKER) or repo.price_history(MARKET_TICKER) or None
    if market and as_of and market[-1][0] < (date.fromisoformat(as_of) - timedelta(days=7)).isoformat():
        market = None  # VTI is no longer synced (not held or watched): no stale comparison
    views = {"household": view(holdings, histories, market, targets)}
    for person in PEOPLE:
        subset = [h for h in holdings if person_of(h.get("group", "stocks")) == person]
        if subset:
            views[person] = view(subset, histories, market, targets)
    return {"as_of": as_of, "views": views, "tickers": tickers}
