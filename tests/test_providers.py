from portfolio.providers.news import parse_finnhub
from portfolio.providers.prices import parse_tiingo


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
