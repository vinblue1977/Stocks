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
    assert analytics.trailing_return(series) is None  # less than a year of history: no "1Y" figure
    year = [("2024-01-02", 100.0), ("2024-06-03", 105.0), ("2025-01-02", 120.0)]
    assert analytics.trailing_return(year) == pytest.approx(0.2)  # same calendar date a year earlier


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


def test_period_returns_by_calendar_date():
    from portfolio import indicators

    history = [
        ("2022-10-12", 50.0),
        ("2023-10-12", 80.0),
        ("2025-10-10", 100.0),
        ("2025-12-31", 110.0),
        ("2026-04-10", 115.0),
        ("2026-07-10", 118.0),
        ("2026-10-12", 132.0),
    ]
    r = indicators.period_returns(history)
    assert r["return_3m"] == pytest.approx(132 / 118 - 1)  # Jul 12 falls on a weekend: the prior close counts
    assert r["return_6m"] == pytest.approx(132 / 115 - 1)
    assert r["return_ytd"] == pytest.approx(132 / 110 - 1)  # vs. the last close of the previous year
    assert r["return_1y"] == pytest.approx(132 / 100 - 1)
    assert r["return_3y_ann"] == pytest.approx((132 / 80) ** (1 / 3) - 1)  # from Oct 12, 2023
    assert indicators.period_returns(history[-2:])["return_1y"] is None  # not enough history
    assert indicators.months_before(__import__("datetime").date(2026, 5, 31), 3).isoformat() == "2026-02-28"


def test_signal_snapshot_has_period_returns():
    s = signals.compute_signals("VTI", [("2025-10-13", 100.0), ("2026-10-13", 110.0)])
    assert s["return_1y"] == pytest.approx(0.1) and {"return_3m", "return_6m", "return_ytd", "return_3y_ann"} <= set(s)
