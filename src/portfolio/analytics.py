"""Portfolio math: position values, gains, weights and performance against a benchmark."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from portfolio import indicators


def summarize(
    holdings: Sequence[Mapping[str, Any]],
    latest: Mapping[str, float],
    previous: Mapping[str, float],
) -> dict[str, Any]:
    """Current value, gain/loss and allocation for each holding plus portfolio totals.

    `latest` and `previous` map ticker -> close for the last and prior trading day.
    Holdings without a price yet are reported with value None and excluded from totals.
    """
    positions = []
    total_value = 0.0
    total_cost = 0.0
    total_prev_value = 0.0
    for holding in holdings:
        ticker = holding["ticker"]
        shares = float(holding["shares"])
        avg_cost = float(holding["avg_cost"])
        price = latest.get(ticker)
        cost = shares * avg_cost
        group = holding.get("group")
        if price is None:
            positions.append(
                {"ticker": ticker, "group": group, "shares": shares, "avg_cost": avg_cost, "price": None, "value": None}
            )
            continue
        value = shares * price
        prev_price = previous.get(ticker, price)  # no earlier close yet: counts as unchanged
        total_value += value
        total_cost += cost
        total_prev_value += shares * prev_price
        positions.append(
            {
                "ticker": ticker,
                "group": group,
                "shares": shares,
                "avg_cost": avg_cost,
                "price": price,
                "value": round(value, 2),
                "gain": round(value - cost, 2),
                "gain_pct": round(value / cost - 1.0, 4) if cost else None,
                "day_change": round(shares * (price - prev_price), 2),
                "day_change_pct": round(price / prev_price - 1.0, 4) if prev_price else None,
            }
        )
    for position in positions:
        if position["value"] is not None:
            position["weight"] = round(position["value"] / total_value, 4) if total_value else 0.0
    positions.sort(key=lambda p: p["value"] or 0.0, reverse=True)
    return {
        "positions": positions,
        "total_value": round(total_value, 2),
        "total_cost": round(total_cost, 2),
        "total_gain": round(total_value - total_cost, 2),
        "total_gain_pct": round(total_value / total_cost - 1.0, 4) if total_cost else None,
        "day_change": round(total_value - total_prev_value, 2),
        "day_change_pct": round(total_value / total_prev_value - 1.0, 4) if total_prev_value else None,
    }


def value_history(
    holdings: Sequence[Mapping[str, Any]],
    histories: Mapping[str, Sequence[tuple[str, float]]],
) -> list[tuple[str, float]]:
    """Daily value of the current holdings over the dates every holding has a price for.

    This answers "how would today's portfolio have moved", which is what the performance
    chart shows. It ignores past buys and sells; transaction-level history is a later feature.
    """
    by_ticker = {t: dict(h) for t, h in histories.items()}
    tickers = sorted({h["ticker"] for h in holdings if h["ticker"] in by_ticker})
    if not tickers:
        return []
    common_dates = sorted(set.intersection(*(set(by_ticker[t]) for t in tickers)))
    shares: dict[str, float] = {}
    for h in holdings:  # a ticker held in two groups counts once, with both groups' shares
        shares[h["ticker"]] = shares.get(h["ticker"], 0.0) + float(h["shares"])
    return [(d, round(sum(shares[t] * by_ticker[t][d] for t in tickers), 2)) for d in common_dates]


def compare_to_benchmark(
    portfolio_series: Sequence[tuple[str, float]],
    benchmark_series: Sequence[tuple[str, float]],
    days: int = 252,
) -> dict[str, float | None]:
    """Trailing return of the portfolio vs. the benchmark over the same window."""
    bench = dict(benchmark_series)
    aligned = [(d, v, bench[d]) for d, v in portfolio_series if d in bench]
    if len(aligned) <= 1:
        return {"portfolio_return": None, "benchmark_return": None, "difference": None}
    window = aligned[-(days + 1) :]
    portfolio_return = indicators.pct_change(window[-1][1], window[0][1])
    benchmark_return = indicators.pct_change(window[-1][2], window[0][2])
    difference = None
    if portfolio_return is not None and benchmark_return is not None:
        difference = round(portfolio_return - benchmark_return, 4)
    return {
        "portfolio_return": None if portfolio_return is None else round(portfolio_return, 4),
        "benchmark_return": None if benchmark_return is None else round(benchmark_return, 4),
        "difference": difference,
    }


def combined_weights(holdings: Sequence[Mapping[str, Any]], latest: Mapping[str, float]) -> dict[str, float]:
    """Share of the whole portfolio (all groups) per ticker, combining a ticker held in several groups."""
    values: dict[str, float] = {}
    for h in holdings:
        price = latest.get(h["ticker"])
        if price is not None:
            values[h["ticker"]] = values.get(h["ticker"], 0.0) + float(h["shares"]) * price
    total = sum(values.values())
    return {t: v / total for t, v in values.items()} if total else {}
