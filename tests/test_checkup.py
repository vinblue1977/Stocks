import pytest

from portfolio import buckets, checkup


def test_area_of_uses_overrides_then_known_map():
    assert buckets.area_of("VTI") == "us" and buckets.area_of("SMH") == "sector"
    assert buckets.area_of("XYZ") == buckets.UNASSIGNED
    assert buckets.area_of("XYZ", {"XYZ": "intl"}) == "intl"
    assert buckets.area_of("VTI", {"VTI": "nonsense"}) == "us"  # unknown area keys are ignored


def test_exposure_drift_and_split():
    positions = [
        {"ticker": "VTI", "value": 600.0},
        {"ticker": "SMH", "value": 400.0},
        {"ticker": "NEW", "value": None},  # no price yet
    ]
    by_area = checkup.exposure(positions)
    assert by_area == {"us": 600.0, "sector": 400.0}
    targets = {"us": 0.5, "intl": 0.3, "sector": 0.2}
    rows = {r["area"]: r for r in checkup.drift(by_area, 1000.0, targets)}
    assert rows["sector"]["now"] == 0.4 and rows["sector"]["gap"] == pytest.approx(200.0)
    assert rows["intl"]["gap"] == pytest.approx(-300.0)
    # $1,000 more: total 2,000 -> intl short 600, us short 400, sector over; split 60/40
    assert checkup.split(by_area, 1000.0, targets, 1000.0) == [("intl", 600.0), ("us", 400.0)]
    assert checkup.split(by_area, 1000.0, targets, 0) == []


def test_unassigned_shows_up_in_drift():
    rows = checkup.drift({"us": 900.0, "unassigned": 100.0}, 1000.0, {"us": 1.0})
    assert rows[-1]["area"] == "unassigned" and rows[-1]["now"] == 0.1


def test_effective_holdings():
    assert checkup.effective_holdings({"A": 1, "B": 1, "C": 1, "D": 1}) == 4.0
    assert checkup.effective_holdings({"A": 90, "B": 10}) == pytest.approx(1.2, abs=0.05)
    assert checkup.effective_holdings({}) is None


def test_risk_vs_market():
    days = [f"2022-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 22)]
    market, double = [(days[0], 100.0)], [(days[0], 100.0)]
    for i, day in enumerate(days[1:]):
        r = 0.01 if i % 3 else -0.012
        market.append((day, market[-1][1] * (1 + r)))
        double.append((day, double[-1][1] * (1 + 2 * r)))  # every day, twice the market's move
    r = checkup.risk(double, market)
    assert r["beta"] == pytest.approx(2.0, abs=0.1)
    assert r["volatility"] > r["market"]["volatility"]
    assert r["return_2022"] is not None and r["worst_drop_2022"] <= 0
    assert checkup.risk(double, None)["beta"] is None


def test_view_groups_overlap_and_small_positions():
    holdings = [
        {"ticker": "VTI", "group": "stocks", "shares": 10, "avg_cost": 1},
        {"ticker": "VOO", "group": "auto", "shares": 1, "avg_cost": 1},
        {"ticker": "SMH", "group": "stocks", "shares": 2, "avg_cost": 1},
    ]
    histories = {"VTI": [("2026-01-02", 100.0)], "VOO": [("2026-01-02", 300.0)], "SMH": [("2026-01-02", 500.0)]}
    targets = {"areas": {"us": {"pct": 80, "fund": "VTI"}, "sector": {"pct": 20, "fund": "SMH"}}}
    v = checkup.view(holdings, histories, None, targets)
    assert v["total_value"] == 2300
    assert [x["ticker"] for x in v["overlap"]["us"]] == ["VTI", "VOO"]
    assert v["small_positions"] == [{"ticker": "VOO", "value": 300.0}]
    sector = next(d for d in v["drift"] if d["area"] == "sector")
    assert sector["now"] == pytest.approx(0.4348, abs=1e-3) and sector["target"] == 0.2
    assert checkup.view(holdings, histories, None, None)["drift"] is None


def test_ticker_without_prices_does_not_erase_risk():
    holdings = [{"ticker": "VTI", "shares": 1, "avg_cost": 1}, {"ticker": "NEW", "shares": 1, "avg_cost": 1}]
    histories = {"VTI": [(f"2026-01-{d:02d}", 100.0 + d) for d in range(1, 29)], "NEW": []}
    assert checkup.view(holdings, histories, None, None)["risk"]["volatility"] is not None
