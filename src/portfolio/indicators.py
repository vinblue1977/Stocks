"""Technical indicators as pure functions over a list of closing prices (oldest first)."""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from datetime import date


def sma(closes: Sequence[float], window: int, offset: int = 0) -> float | None:
    """Simple moving average of the last `window` closes, `offset` days ago (0 = today)."""
    end = len(closes) - offset
    if window <= 0 or end < window:
        return None
    return sum(closes[end - window : end]) / window


def rsi(closes: Sequence[float], period: int = 14) -> float | None:
    """Relative Strength Index using Wilder's smoothing. Needs at least period + 1 closes."""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:], strict=True):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def drawdown_from_high(closes: Sequence[float], lookback: int | None = None) -> float | None:
    """Fractional distance of the last close from the highest close (e.g. -0.08 = 8% below)."""
    if not closes:
        return None
    window = closes[-lookback:] if lookback else closes
    high = max(window)
    return closes[-1] / high - 1.0 if high else None


def pct_change(current: float | None, reference: float | None) -> float | None:
    if current is None or reference in (None, 0):
        return None
    return current / reference - 1.0


def trailing_return(closes: Sequence[float], days: int) -> float | None:
    if len(closes) <= days:
        return None
    return pct_change(closes[-1], closes[-1 - days])


def months_before(day: date, months: int) -> date:
    """The same day `months` earlier, clamped to the month's end (e.g. May 31 -> Feb 28)."""
    index = day.year * 12 + day.month - 1 - months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def return_since(history: Sequence[tuple[str, float]], start: str) -> float | None:
    """Last close vs. the last close on or before `start` (YYYY-MM-DD); None if history doesn't reach back."""
    if not history or history[0][0] > start:
        return None
    base = next(close for day, close in reversed(history) if day <= start)
    return pct_change(history[-1][1], base)


def period_returns(history: Sequence[tuple[str, float]]) -> dict[str, float | None]:
    """Total returns by calendar period, as brokers show them: 3M, 6M, YTD, 1Y, and 3Y per year (annualized)."""
    if not history:
        return dict.fromkeys(("return_3m", "return_6m", "return_ytd", "return_1y", "return_3y_ann"))
    end = date.fromisoformat(history[-1][0])
    three_years = return_since(history, months_before(end, 36).isoformat())
    return {
        "return_3m": return_since(history, months_before(end, 3).isoformat()),
        "return_6m": return_since(history, months_before(end, 6).isoformat()),
        "return_ytd": return_since(history, f"{end.year - 1}-12-31"),
        "return_1y": return_since(history, months_before(end, 12).isoformat()),
        "return_3y_ann": None if three_years is None else (1 + three_years) ** (1 / 3) - 1,
    }
