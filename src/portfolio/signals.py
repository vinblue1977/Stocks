"""Builds the per-ticker signal snapshot shown on the News & signals screen and used by rules."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from portfolio import indicators

TRADING_DAYS_PER_YEAR = 252


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def compute_signals(
    ticker: str,
    history: Sequence[tuple[str, float]],
    sentiment_7d: float | None = None,
    extra_sma_windows: Sequence[int] = (),
) -> dict[str, Any] | None:
    """`history` is [(iso_date, close), ...] sorted oldest first."""
    if not history:
        return None
    closes = [close for _, close in history]
    price = closes[-1]
    sma50 = indicators.sma(closes, 50)
    sma200 = indicators.sma(closes, 200)
    return {
        "ticker": ticker,
        "as_of": history[-1][0],
        "price": price,
        "change_1d": _round(indicators.trailing_return(closes, 1)),
        "rsi14": _round(indicators.rsi(closes, 14), 2),
        "sma50": _round(sma50),
        "sma200": _round(sma200),
        "sma50_prev": _round(indicators.sma(closes, 50, offset=1)),
        "sma200_prev": _round(indicators.sma(closes, 200, offset=1)),
        "vs_sma50": _round(indicators.pct_change(price, sma50)),
        "vs_sma200": _round(indicators.pct_change(price, sma200)),
        "drawdown_from_high": _round(indicators.drawdown_from_high(closes, TRADING_DAYS_PER_YEAR)),
        "return_1y": _round(indicators.trailing_return(closes, TRADING_DAYS_PER_YEAR)),
        "sentiment_7d": _round(sentiment_7d, 3),
        "smas": {str(w): _round(indicators.sma(closes, w)) for w in sorted(set(extra_sma_windows))},
    }
