import pytest

from portfolio import indicators


def test_sma_basic_and_offset():
    closes = [1, 2, 3, 4, 5]
    assert indicators.sma(closes, 3) == 4
    assert indicators.sma(closes, 3, offset=1) == 3
    assert indicators.sma(closes, 6) is None


def test_rsi_all_gains_is_100():
    assert indicators.rsi(list(range(1, 30)), 14) == 100.0


def test_rsi_all_losses_is_0():
    assert indicators.rsi(list(range(30, 1, -1)), 14) == pytest.approx(0.0)


def test_rsi_needs_enough_data():
    assert indicators.rsi([1, 2, 3], 14) is None


def test_rsi_known_value():
    # Classic Wilder example series; RSI after 14 periods is about 70.5
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
    assert indicators.rsi(closes, 14) == pytest.approx(70.5, abs=0.6)


def test_drawdown_and_returns():
    closes = [100, 120, 90]
    assert indicators.drawdown_from_high(closes) == pytest.approx(-0.25)
    assert indicators.trailing_return(closes, 2) == pytest.approx(-0.10)
    assert indicators.trailing_return(closes, 3) is None
    assert indicators.pct_change(None, 1) is None
    assert indicators.pct_change(1, 0) is None
