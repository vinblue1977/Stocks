from portfolio.providers.news import parse_finnhub
from portfolio.providers.prices import parse_iex, parse_tiingo


def test_parse_tiingo_uses_adjusted_close_and_sorts():
    raw = [
        {"date": "2025-01-03T00:00:00.000Z", "close": 101, "adjClose": 100.5, "adjVolume": 10},
        {"date": "2025-01-02T00:00:00.000Z", "close": 99, "adjClose": 98.5, "volume": 5},
        {"date": None, "close": 1},
    ]
    rows = parse_tiingo(raw)
    assert [r["date"] for r in rows] == ["2025-01-02", "2025-01-03"]
    assert rows[1]["close"] == 100.5 and rows[1]["raw_close"] == 101.0
    assert parse_tiingo(None) == []


def test_parse_finnhub_filters_and_dedupes():
    raw = [
        {"id": 1, "headline": "Stocks rise", "url": "https://example.com/a", "source": "Wire", "datetime": 1760000000},
        {"id": 2, "headline": "stocks rise", "url": "https://example.com/b", "datetime": 1760000100},
        {"id": 3, "headline": "Insecure link", "url": "http://example.com/c", "datetime": 1760000200},
        {"id": 4, "headline": "", "url": "https://example.com/d", "datetime": 1760000300},
        {"id": 5, "headline": "Later story", "url": "https://example.com/e", "datetime": 1760001000},
    ]
    articles = parse_finnhub(raw)
    assert [a["article_id"] for a in articles] == ["5", "1"]
    assert articles[1]["source"] == "Wire"
    assert articles[0]["published_at"].endswith("+00:00")


def test_parse_iex_keeps_priced_quotes_with_new_york_trading_day():
    raw = [
        {"ticker": "VTI", "tngoLast": 381.835, "last": None, "timestamp": "2026-10-12T11:41:04.281212031-04:00"},
        {"ticker": "spy", "tngoLast": None, "last": 778.3, "timestamp": "2026-10-12T19:59:00-04:00"},
        {"ticker": "NOPE", "tngoLast": None, "last": None, "timestamp": "2026-10-12T11:41:04-04:00"},
        {"ticker": "ODD", "tngoLast": 5.0, "timestamp": "yesterday"},
        {"ticker": "NAIVE", "tngoLast": 5.0, "timestamp": "2026-10-12T11:41:04"},
    ]
    quotes = parse_iex(raw)
    assert quotes["VTI"] == {"last": 381.835, "timestamp": "2026-10-12T15:41:04+00:00", "date": "2026-10-12"}
    # 7:59 PM New York is already the next day in UTC; the trading day stays the New York date.
    assert quotes["SPY"]["date"] == "2026-10-12" and quotes["SPY"]["timestamp"].startswith("2026-10-12T23:59")
    assert set(quotes) == {"VTI", "SPY"} and parse_iex(None) == {}


def test_parse_earnings_picks_the_next_report():
    from portfolio.providers.news import parse_earnings

    raw = {
        "earningsCalendar": [
            {"date": "2027-02-20", "hour": "bmo", "epsEstimate": 2.9},
            {"date": "2026-11-17", "hour": "amc", "epsEstimate": 2.5253},
            {"date": "2026-10-01", "hour": "amc", "epsEstimate": 2.0},  # already past
        ]
    }
    assert parse_earnings(raw, "2026-10-13") == {"date": "2026-11-17", "hour": "amc", "eps_estimate": 2.53}
    assert parse_earnings({"earningsCalendar": []}, "2026-10-13") is None
    assert (
        parse_earnings({"earningsCalendar": [{"date": "2026-11-17", "hour": "", "epsEstimate": None}]}, "2026-10-13")["hour"]
        is None
    )


def test_parse_recommendations_newest_four_months():
    from portfolio.providers.news import parse_recommendations

    raw = [
        {"period": f"2026-0{m}-01", "strongBuy": 20 + m, "buy": 40, "hold": 3, "sell": 1, "strongSell": 1} for m in range(3, 10)
    ]
    months = parse_recommendations(raw)
    assert [m["period"] for m in months] == ["2026-09", "2026-08", "2026-07", "2026-06"]
    assert months[0] == {"period": "2026-09", "buy": 69, "hold": 3, "sell": 2}
    assert parse_recommendations(None) == []


def test_parse_insiders_counts_open_market_only():
    from portfolio.providers.news import parse_insiders

    row = {"isDerivative": False, "filingDate": "2026-09-23"}
    raw = {
        "data": [
            {
                **row,
                "name": "A",
                "change": -4499,
                "transactionDate": "2026-09-21",
                "transactionCode": "S",
                "transactionPrice": 223.75,
            },
            {
                **row,
                "name": "B",
                "change": 1000,
                "transactionDate": "2026-09-01",
                "transactionCode": "P",
                "transactionPrice": 200,
            },
            {
                **row,
                "name": "C",
                "change": -500000,
                "transactionDate": "2026-10-07",
                "transactionCode": "G",
                "transactionPrice": 0,
            },
            {
                **row,
                "name": "D",
                "change": -10,
                "transactionDate": "2026-05-01",
                "transactionCode": "S",
                "transactionPrice": 100,
            },  # too old
            {**row, "name": "E", "change": 5, "transactionDate": "2026-09-02", "transactionCode": "M", "isDerivative": True},
        ]
    }
    out = parse_insiders(raw, "2026-07-16")
    assert {k: out[k] for k in ("bought", "buys", "sold", "sells", "routine", "unpriced")} == {
        "bought": 200000,
        "buys": 1,
        "sold": 1006651,
        "sells": 1,
        "routine": 1,
        "unpriced": 0,
    }
    assert [t["name"] for t in out["trades"]] == ["C", "A", "B"]  # newest first
    assert out["trades"][0] == {"date": "2026-10-07", "name": "C", "code": "G", "shares": 500000, "price": None}
