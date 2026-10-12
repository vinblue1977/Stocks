from datetime import UTC, datetime, timedelta

import pytest
from conftest import price_series

from portfolio.handlers import evaluate_signals, news_ingest, price_sync, weekly_report
from portfolio.llm import ArticleScore, TickerBrief


class FakePrices:
    def __init__(self):
        self.calls = []

    def daily_prices(self, ticker, start_date):
        self.calls.append((ticker, start_date))
        if ticker == "BAD":
            raise RuntimeError("provider down")
        return price_series(100, 0.5, 5, start_date)


def test_price_sync_backfills_then_increments(repo):
    repo.put_holding("u1", {"ticker": "VTI", "shares": 1, "cost_basis": 1})
    provider = FakePrices()
    result = price_sync.handler({}, None, repo=repo, provider=provider)
    assert result["tickers"] == ["SPY", "VTI"]  # benchmark always included
    assert result["rows_written"] == 10
    first_start = dict(provider.calls)["VTI"]
    assert first_start < (datetime.now(UTC).date() - timedelta(days=365 * 4)).isoformat()

    provider.calls.clear()
    price_sync.handler({"tickers": ["VTI"]}, None, repo=repo, provider=provider)
    last = repo.latest_price_date("VTI")
    assert provider.calls[0][1] > first_start and last is not None


def test_price_sync_tolerates_partial_failure_but_not_total(repo):
    result = price_sync.handler({"tickers": ["VTI", "BAD"]}, None, repo=repo, provider=FakePrices())
    assert result["failed"] == ["BAD"]
    with pytest.raises(RuntimeError):
        price_sync.handler({"tickers": ["BAD"]}, None, repo=repo, provider=FakePrices())


def test_price_sync_ignores_invalid_requested_tickers(repo):
    result = price_sync.handler({"tickers": ["VTI", "../x"]}, None, repo=repo, provider=FakePrices())
    assert result["tickers"] == ["VTI"]


class FakeNews:
    def company_news(self, ticker, from_date, to_date):
        now = datetime.now(UTC).isoformat(timespec="seconds")
        return [
            {
                "article_id": f"{ticker}-1",
                "headline": "Up",
                "summary": "",
                "source": "Wire",
                "url": "https://x/1",
                "published_at": now,
            },
            {
                "article_id": f"{ticker}-2",
                "headline": "Down",
                "summary": "",
                "source": "Wire",
                "url": "https://x/2",
                "published_at": now,
            },
        ]


class FakeSummarizer:
    @staticmethod
    def brief_for_ticker(ticker, articles):
        return TickerBrief(
            summary=f"{ticker} summary",
            article_scores=[
                ArticleScore(article_id=f"{ticker}-1", score=0.8),
                ArticleScore(article_id=f"{ticker}-2", score=-0.2),
            ],
        )

    @staticmethod
    def brief_for_portfolio(briefs, alerts):
        return "Portfolio summary"


def test_news_ingest_scores_and_briefs(repo):
    repo.put_holding("u1", {"ticker": "VTI", "shares": 1, "cost_basis": 1})
    result = news_ingest.handler({}, None, repo=repo, provider=FakeNews(), summarizer=FakeSummarizer)
    assert result["summarized"] == ["VTI"]
    brief = repo.latest_brief("VTI")
    assert brief["sentiment_7d"] == pytest.approx(0.3)
    assert brief["label"] == "Positive"
    assert repo.latest_brief("ALL")["summary"] == "Portfolio summary"


def test_evaluate_signals_alerts_once(repo, monkeypatch):
    sent = []
    monkeypatch.setattr(evaluate_signals.notify, "send_email", lambda s, b: sent.append(s))
    start = (datetime.now(UTC).date() - timedelta(days=300)).isoformat()
    repo.put_holding("u1", {"ticker": "VTI", "shares": 10, "cost_basis": 100})
    repo.put_holding("u1", {"ticker": "BND", "shares": 1, "cost_basis": 70})
    repo.put_prices("VTI", price_series(100, 1, 300, start))
    repo.put_prices("BND", price_series(70, 0, 300, start))
    repo.put_prices("SPY", price_series(400, 1, 300, start))
    repo.create_rule("u1", {"type": "weight_above", "ticker": "VTI", "params": {"pct": 50}, "enabled": True})
    repo.create_rule("u1", {"type": "rsi_below", "ticker": "VTI", "params": {"threshold": 30}, "enabled": True})

    first = evaluate_signals.handler({}, None, repo=repo)
    assert first["alerts_sent"] == 1 and len(sent) == 1
    assert repo.get_signal("VTI")["rsi14"] == 100.0
    second = evaluate_signals.handler({}, None, repo=repo)
    assert second["alerts_sent"] == 0  # still true, but no repeat email
    assert len(repo.list_alerts("u1")) == 1


def test_weekly_report(repo, monkeypatch):
    sent = []
    monkeypatch.setattr(weekly_report.notify, "send_email", lambda s, b: sent.append(b))
    start = (datetime.now(UTC).date() - timedelta(days=10)).isoformat()
    repo.put_holding("u1", {"ticker": "VTI", "shares": 10, "cost_basis": 100})
    repo.put_prices("VTI", price_series(100, 1, 10, start))
    assert weekly_report.handler({}, None, repo=repo) == {"reports_sent": 1}
    assert "Total value: $1,090.00" in sent[0]
    assert "No alerts fired this week." in sent[0]
    assert "By person" not in sent[0] and "By group" not in sent[0]  # only one group in use


def test_weekly_report_by_person(repo, monkeypatch):
    sent = []
    monkeypatch.setattr(weekly_report.notify, "send_email", lambda s, b: sent.append(b))
    monkeypatch.setenv("OWNER_NAME", "Ana")
    monkeypatch.setenv("PARTNER_NAME", "Ben")
    weekly_report.get_config.cache_clear()
    start = (datetime.now(UTC).date() - timedelta(days=10)).isoformat()
    repo.put_prices("VTI", price_series(100, 0, 10, start))
    repo.put_holding("u1", {"ticker": "VTI", "group": "stocks", "shares": 1, "cost_basis": 100})
    repo.put_holding("u1", {"ticker": "VTI", "group": "partner_auto", "shares": 3, "cost_basis": 100})
    weekly_report.handler({}, None, repo=repo)
    weekly_report.get_config.cache_clear()
    assert "Total value: $400.00" in sent[0]
    assert "  Ana: $100.00" in sent[0] and "  Ben: $300.00" in sent[0]
    assert "  Ana · Stock Holdings: $100.00" in sent[0] and "  Ben · Automatic Investment: $300.00" in sent[0]


def test_prices_written_newest_first(repo):
    order = []
    real_batch = repo.table.batch_writer

    class Spy:
        def __init__(self, **kw):
            self.inner = real_batch(**kw)

        def __enter__(self):
            self.ctx = self.inner.__enter__()
            return self

        def __exit__(self, *a):
            return self.inner.__exit__(*a)

        def put_item(self, Item):  # noqa: N803 - boto3 argument name
            order.append(Item["sk"])
            self.ctx.put_item(Item=Item)

    repo.table.batch_writer = lambda **kw: Spy(**kw)
    repo.put_prices("AVGO", price_series(100, 1, 5, "2026-01-01"))
    assert order == sorted(order, reverse=True)


def test_interrupted_backfill_is_redone(repo):
    provider = FakePrices()
    today = datetime.now(UTC).date()
    # Simulate a partial earlier load: recent prices exist but no completion marker
    repo.put_prices("AVGO", price_series(300, 1, 3, (today - timedelta(days=3)).isoformat()))
    price_sync.sync_ticker("AVGO", repo, provider, today)
    assert provider.calls[-1][1] <= (today - timedelta(days=365 * 4)).isoformat()  # full reload
    assert repo.backfill_complete("AVGO")
    # Afterwards only new days are requested
    price_sync.sync_ticker("AVGO", repo, provider, today)
    assert provider.calls[-1][1] > (today - timedelta(days=30)).isoformat()


def test_migration_moves_legacy_holdings_once(repo, table):
    table.put_item(
        Item={
            "pk": "USER#u1",
            "sk": "HOLDING#VTI",
            "gsi1pk": "HOLDINGS",
            "gsi1sk": "VTI#u1",
            "ticker": "VTI",
            "shares": 3,
            "cost_basis": 200,
        }
    )
    repo.put_holding("u1", {"ticker": "BND", "group": "auto", "shares": 1, "cost_basis": 70})
    assert repo.migrate_legacy_holdings() == ["u1:VTI"]
    assert repo.migrate_legacy_holdings() == []  # idempotent
    holdings = {(h["group"], h["ticker"]): h for h in repo.list_holdings("u1")}
    assert set(holdings) == {("stocks", "VTI"), ("auto", "BND")}
    assert holdings[("stocks", "VTI")]["shares"] == 3 and holdings[("stocks", "VTI")]["cost_basis"] == 200
    assert table.get_item(Key={"pk": "USER#u1", "sk": "HOLDING#VTI"}).get("Item") is None


def test_weight_rule_uses_combined_portfolio(repo, monkeypatch):
    sent = []
    monkeypatch.setattr(evaluate_signals.notify, "send_email", lambda s, b: sent.append(s))
    start = (datetime.now(UTC).date() - timedelta(days=60)).isoformat()
    for t, p in (("VOO", 100), ("BND", 100), ("SPY", 400)):
        repo.put_prices(t, price_series(p, 0, 60, start))
    # Combined across groups: 6 VOO ($600) vs 4 BND ($400) => VOO is 60% of the whole portfolio
    repo.put_holding("u1", {"ticker": "VOO", "group": "stocks", "shares": 2, "cost_basis": 100})
    repo.put_holding("u1", {"ticker": "VOO", "group": "auto", "shares": 4, "cost_basis": 100})
    repo.put_holding("u1", {"ticker": "BND", "group": "auto", "shares": 4, "cost_basis": 100})
    repo.create_rule("u1", {"type": "weight_above", "ticker": "VOO", "params": {"pct": 55}, "enabled": True})
    assert evaluate_signals.handler({}, None, repo=repo)["alerts_sent"] == 1


def test_news_skips_excluded_tickers(repo, monkeypatch):
    import dataclasses

    from portfolio import config

    cfg = dataclasses.replace(config.get_config(), news_excluded=frozenset({"ITOT"}))
    monkeypatch.setattr(news_ingest, "get_config", lambda: cfg)

    class Spy(FakeNews):
        def __init__(self):
            self.asked = []

        def company_news(self, ticker, from_date, to_date):
            self.asked.append(ticker)
            return super().company_news(ticker, from_date, to_date)

    repo.put_holding("u1", {"ticker": "ITOT", "group": "auto", "shares": 1, "cost_basis": 1})
    repo.put_holding("u1", {"ticker": "VTI", "group": "auto", "shares": 1, "cost_basis": 1})
    spy = Spy()
    result = news_ingest.handler({}, None, repo=repo, provider=spy, summarizer=FakeSummarizer)
    assert spy.asked == ["VTI"] and result["tickers"] == ["VTI"]


def test_excluded_ticker_has_no_stale_sentiment(repo, monkeypatch):
    import dataclasses

    from portfolio import config

    start = (datetime.now(UTC).date() - timedelta(days=60)).isoformat()
    for t in ("DVY", "SPY"):
        repo.put_prices(t, price_series(100, 0, 60, start))
    repo.put_holding("u1", {"ticker": "DVY", "group": "auto", "shares": 1, "cost_basis": 1})
    repo.put_brief("DVY", "2026-10-09", {"summary": "old", "sentiment_7d": 0.6})
    evaluate_signals.handler({}, None, repo=repo)
    assert repo.get_signal("DVY")["sentiment_7d"] == 0.6  # news on: uses the brief

    cfg = dataclasses.replace(config.get_config(), news_excluded=frozenset({"DVY"}))
    monkeypatch.setattr(evaluate_signals, "get_config", lambda: cfg)
    evaluate_signals.handler({}, None, repo=repo)
    assert repo.get_signal("DVY").get("sentiment_7d") is None  # news off: no stale score


class CountingSummarizer(FakeSummarizer):
    ticker_calls = 0
    portfolio_calls = 0

    @classmethod
    def brief_for_ticker(cls, ticker, articles):
        cls.ticker_calls += 1
        brief = FakeSummarizer.brief_for_ticker(ticker, articles)
        if len(articles) > 1:  # new coverage changes the summary text, as it would with Claude
            brief.summary = f"{ticker} summary ({len(articles)} headlines)"
        return brief

    @classmethod
    def brief_for_portfolio(cls, briefs, alerts):
        cls.portfolio_calls += 1
        return f"Portfolio summary {cls.portfolio_calls}"


class GrowingNews:
    """Returns the same headlines until told to add a new one."""

    def __init__(self):
        self.extra = False

    def company_news(self, ticker, from_date, to_date):
        now = datetime.now(UTC).isoformat(timespec="seconds")
        items = [
            {
                "article_id": f"{ticker}-1",
                "headline": "Up",
                "summary": "",
                "source": "Wire",
                "url": "https://x/1",
                "published_at": now,
            }
        ]
        if self.extra:
            items.append(
                {
                    "article_id": f"{ticker}-9",
                    "headline": "New",
                    "summary": "",
                    "source": "Wire",
                    "url": "https://x/9",
                    "published_at": now,
                }
            )
        return items


def test_news_reuses_summaries_when_nothing_is_new(repo):
    CountingSummarizer.ticker_calls = CountingSummarizer.portfolio_calls = 0
    repo.put_holding("u1", {"ticker": "VTI", "shares": 1, "cost_basis": 1})
    news = GrowingNews()

    first = news_ingest.handler({}, None, repo=repo, provider=news, summarizer=CountingSummarizer)
    assert first["summarized"] == ["VTI"] and first["claude_calls"] == 2

    second = news_ingest.handler({}, None, repo=repo, provider=news, summarizer=CountingSummarizer)
    assert second["reused"] == ["VTI"] and second["claude_calls"] == 0
    assert CountingSummarizer.ticker_calls == 1 and CountingSummarizer.portfolio_calls == 1
    brief = repo.latest_brief("VTI")
    assert brief["summary"] == "VTI summary" and brief["reused"] is True and brief["sentiment_7d"] == pytest.approx(0.8)
    assert repo.latest_brief("ALL")["summary"] == "Portfolio summary 1"

    news.extra = True  # a new headline arrives
    third = news_ingest.handler({}, None, repo=repo, provider=news, summarizer=CountingSummarizer)
    assert third["summarized"] == ["VTI"] and third["claude_calls"] == 2


def test_portfolio_brief_regenerates_when_alerts_change(repo):
    CountingSummarizer.ticker_calls = CountingSummarizer.portfolio_calls = 0
    repo.put_holding("u1", {"ticker": "VTI", "shares": 1, "cost_basis": 1})
    news = GrowingNews()
    news_ingest.handler({}, None, repo=repo, provider=news, summarizer=CountingSummarizer)
    rule = repo.create_rule("u1", {"type": "rsi_below", "ticker": "VTI", "params": {"threshold": 30}, "enabled": True})
    repo.put_rule_state("u1", rule["rule_id"], True, "VTI 14-day RSI is 25.0")
    result = news_ingest.handler({}, None, repo=repo, provider=news, summarizer=CountingSummarizer)
    assert result["reused"] == ["VTI"] and result["claude_calls"] == 1  # only the portfolio briefing


def test_partner_only_tickers_skip_news_when_partner_news_off(repo, monkeypatch):
    import dataclasses

    from portfolio import config

    class Spy(FakeNews):
        def __init__(self):
            self.asked = []

        def company_news(self, ticker, from_date, to_date):
            self.asked.append(ticker)
            return super().company_news(ticker, from_date, to_date)

    repo.put_holding("u1", {"ticker": "VTI", "group": "stocks", "shares": 1, "cost_basis": 1})
    repo.put_holding("u1", {"ticker": "VTI", "group": "partner_auto", "shares": 1, "cost_basis": 1})  # shared
    repo.put_holding("u1", {"ticker": "SCHD", "group": "partner_stocks", "shares": 1, "cost_basis": 1})  # partner only
    spy = Spy()
    news_ingest.handler({}, None, repo=repo, provider=spy, summarizer=FakeSummarizer)
    assert sorted(spy.asked) == ["SCHD", "VTI"]  # default: partner news on

    cfg = dataclasses.replace(config.get_config(), partner_news=False)
    monkeypatch.setattr(news_ingest, "get_config", lambda: cfg)
    spy = Spy()
    assert news_ingest.handler({}, None, repo=repo, provider=spy, summarizer=FakeSummarizer)["tickers"] == ["VTI"]
    assert spy.asked == ["VTI"]

    # Signals ignore any earlier SCHD brief once its news is off
    start = (datetime.now(UTC).date() - timedelta(days=60)).isoformat()
    for t in ("SCHD", "VTI", "SPY"):
        repo.put_prices(t, price_series(100, 0, 60, start))
    repo.put_brief("SCHD", "2026-10-09", {"summary": "old", "sentiment_7d": 0.6})
    monkeypatch.setattr(evaluate_signals, "get_config", lambda: cfg)
    evaluate_signals.handler({}, None, repo=repo)
    assert repo.get_signal("SCHD").get("sentiment_7d") is None
