"""Technical indicators as pure functions over a list of closing prices (oldest first)."""

from __future__ import annotations

from collections.abc import Sequence


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
