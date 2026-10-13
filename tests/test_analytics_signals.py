import pytest

from portfolio import analytics, signals

HOLDINGS = [
    {"ticker": "VTI", "shares": 40, "avg_cost": 230},
    {"ticker": "SCHD", "shares": 120, "avg_cost": 29.5},
    {"ticker": "NEW", "shares": 1, "avg_cost": 10},
]


def test_summarize_values_gains_weights():
    result = analytics.summarize(HOLDINGS, {"VTI": 312.4, "SCHD": 27.9}, {"VTI": 310.0, "SCHD": 28.0})
    vti = next(p for p in result["positions"] if p["ticker"] == "VTI")
    schd = next(p for p in result["positions"] if p["ticker"] == "SCHD")
    unpriced = next(p for p in result["positions"] if p["ticker"] == "NEW")
    assert vti["value"] == 12496.0
    assert vti["gain"] == 3296.0
    assert schd["gain"] == pytest.approx(-192.0)
    assert unpriced["value"] is None
    assert vti["weight"] + schd["weight"] == pytest.approx(1.0, abs=1e-3)
    assert result["total_value"] == 15844.0
    assert result["day_change"] == pytest.approx(40 * 2.4 - 120 * 0.1)
    # Per position too, for the Overview's movers
    assert vti["day_change"] == pytest.approx(96.0) and vti["day_change_pct"] == pytest.approx(0.0077, abs=1e-4)
    assert schd["day_change"] == pytest.approx(-12.0) and schd["day_change_pct"] == pytest.approx(-0.0036, abs=1e-4)
    assert "day_change" not in unpriced


def test_position_without_an_earlier_close_is_unchanged():
    result = analytics.summarize(HOLDINGS[:1], {"VTI": 312.4}, {})
    assert result["positions"][0]["day_change"] == 0 and result["positions"][0]["day_change_pct"] == 0


def test_value_history_and_trailing_return():
    histories = {
        "VTI": [("2025-01-01", 100.0), ("2025-01-02", 110.0), ("2025-01-03", 121.0)],
        "SCHD": [("2025-01-02", 10.0), ("2025-01-03", 10.0)],
    }
    holdings = [{"ticker": "VTI", "shares": 1, "avg_cost": 0}, {"ticker": "SCHD", "shares": 1, "avg_cost": 0}]
    series = analytics.value_history(holdings, histories)
    assert series == [("2025-01-02", 120.0), ("2025-01-03", 131.0)]
    assert analytics.trailing_return(series, days=1) == pytest.approx(0.0917, abs=1e-4)
    assert analytics.trailing_return(series + [("2025-01-06", 144.1)], days=1) == pytest.approx(0.1, abs=1e-4)
    assert analytics.trailing_return(series) is None  # less than a year of history: no "1Y" figure


def test_compute_signals_shapes():
    history = [(f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}", 100.0 + i) for i in range(260)]
    snap = signals.compute_signals("VTI", history, sentiment_7d=0.123456, extra_sma_windows=[20])
    assert snap["price"] == 359.0
    assert snap["sma50"] == pytest.approx(334.5)
    assert snap["vs_sma50"] > 0
    assert snap["rsi14"] == 100.0
    assert snap["drawdown_from_high"] == 0.0
    assert snap["sentiment_7d"] == 0.123
    assert "20" in snap["smas"]
    assert signals.compute_signals("X", []) is None
