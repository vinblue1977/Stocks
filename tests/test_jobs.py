from datetime import UTC, datetime, timedelta

import pytest
from conftest import price_series

from portfolio import alerting, llm
from portfolio.handlers import evaluate_signals, intraday_quotes, news_ingest, price_sync, weekly_report
from portfolio.llm import ArticleScore, TickerBrief
from portfolio.market import market_open


class FakePrices:
    def __init__(self):
        self.calls = []

    def daily_prices(self, ticker, start_date):
        self.calls.append((ticker, start_date))
        if ticker == "BAD":
            raise RuntimeError("provider down")
        return price_series(100, 0.5, 5, start_date)


def test_price_sync_backfills_then_increments(repo):
    repo.put_holding("u1", {"ticker": "VTI", "shares": 1, "avg_cost": 1})
    provider = FakePrices()
    result = price_sync.handler({}, None, repo=repo, provider=provider)
    assert result["tickers"] == ["VTI"]
    assert result["rows_written"] == 5
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
    repo.put_holding("u1", {"ticker": "VTI", "shares": 1, "avg_cost": 1})
    result = news_ingest.handler({}, None, repo=repo, provider=FakeNews(), summarizer=FakeSummarizer)
    assert result["summarized"] == ["VTI"]
    brief = repo.latest_brief("VTI")
    assert brief["sentiment_7d"] == pytest.approx(0.3)
    assert brief["label"] == "Positive"
    assert repo.latest_brief("ALL")["summary"] == "Portfolio summary"


def test_evaluate_signals_alerts_once(repo, monkeypatch):
    sent = []
    pushed = []
    monkeypatch.setattr(alerting.notify, "send_email", lambda s, b: sent.append(s))
    monkeypatch.setattr(alerting.notify, "push_to_user", lambda repo, user, title, body: pushed.append((user, title)))
    start = (datetime.now(UTC).date() - timedelta(days=300)).isoformat()
    repo.put_holding("u1", {"ticker": "VTI", "shares": 10, "avg_cost": 100})
    repo.put_holding("u1", {"ticker": "BND", "shares": 1, "avg_cost": 70})
    repo.put_prices("VTI", price_series(100, 1, 300, start))
    repo.put_prices("BND", price_series(70, 0, 300, start))
    repo.put_prices("SPY", price_series(400, 1, 300, start))
    repo.create_rule("u1", {"type": "weight_above", "ticker": "VTI", "params": {"pct": 50}, "enabled": True})
    repo.create_rule("u1", {"type": "rsi_below", "ticker": "VTI", "params": {"threshold": 30}, "enabled": True})

    first = evaluate_signals.handler({}, None, repo=repo)
    assert first["alerts_sent"] == 1 and len(sent) == 1
    assert pushed == [("u1", "Alert: VTI")]  # the same alert goes to phones with notifications on
    assert repo.get_signal("VTI")["rsi14"] == 100.0
    second = evaluate_signals.handler({}, None, repo=repo)
    assert second["alerts_sent"] == 0  # still true, but no repeat email
    assert len(repo.list_alerts("u1")) == 1


def test_weekly_report(repo, monkeypatch):
    sent = []
    monkeypatch.setattr(weekly_report.notify, "send_email", lambda s, b: sent.append(b))
    start = (datetime.now(UTC).date() - timedelta(days=10)).isoformat()
    repo.put_holding("u1", {"ticker": "VTI", "shares": 10, "avg_cost": 100})
    repo.put_prices("VTI", price_series(100, 1, 10, start))
    assert weekly_report.handler({}, None, repo=repo) == {"reports_sent": 1}
    assert "Total value: $1,090.00" in sent[0]
    assert "No alerts fired this week." in sent[0]
    assert "By person" not in sent[0] and "By group" not in sent[0]  # only one group in use


def test_weekly_report_flags_a_stock_over_the_limit(repo, monkeypatch):
    sent = []
    monkeypatch.setattr(weekly_report.notify, "send_email", lambda s, b: sent.append(b))
    start = (datetime.now(UTC).date() - timedelta(days=10)).isoformat()
    for ticker, shares in (("VTI", 1), ("AAPL", 1)):
        repo.put_holding("u1", {"ticker": ticker, "shares": shares, "avg_cost": 100})
        repo.put_prices(ticker, price_series(100, 0, 10, start))
    weekly_report.handler({}, None, repo=repo)
    assert "single-holding limit" not in sent[0]  # not set
    repo.put_targets("u1", {"areas": {}, "single_holding_limit": 40})
    weekly_report.handler({}, None, repo=repo)
    assert "Over your 40% single-holding limit: AAPL" in sent[1]  # 50% each; VTI is a fund


def test_weekly_report_by_person(repo, monkeypatch):
    sent = []
    monkeypatch.setattr(weekly_report.notify, "send_email", lambda s, b: sent.append(b))
    monkeypatch.setenv("OWNER_NAME", "Ana")
    monkeypatch.setenv("PARTNER_NAME", "Ben")
    weekly_report.get_config.cache_clear()
    start = (datetime.now(UTC).date() - timedelta(days=10)).isoformat()
    repo.put_prices("VTI", price_series(100, 0, 10, start))
    repo.put_holding("u1", {"ticker": "VTI", "group": "stocks", "shares": 1, "avg_cost": 100})
    repo.put_holding("u1", {"ticker": "VTI", "group": "partner_auto", "shares": 3, "avg_cost": 100})
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
            "avg_cost": 200,
        }
    )
    repo.put_holding("u1", {"ticker": "BND", "group": "auto", "shares": 1, "avg_cost": 70})
    assert repo.migrate_legacy_holdings() == ["u1:VTI"]
    assert repo.migrate_legacy_holdings() == []  # idempotent
    holdings = {(h["group"], h["ticker"]): h for h in repo.list_holdings("u1")}
    assert set(holdings) == {("stocks", "VTI"), ("auto", "BND")}
    assert holdings[("stocks", "VTI")]["shares"] == 3 and holdings[("stocks", "VTI")]["avg_cost"] == 200
    assert table.get_item(Key={"pk": "USER#u1", "sk": "HOLDING#VTI"}).get("Item") is None


def test_weight_rule_uses_combined_portfolio(repo, monkeypatch):
    sent = []
    monkeypatch.setattr(alerting.notify, "send_email", lambda s, b: sent.append(s))
    start = (datetime.now(UTC).date() - timedelta(days=60)).isoformat()
    for t, p in (("VOO", 100), ("BND", 100), ("SPY", 400)):
        repo.put_prices(t, price_series(p, 0, 60, start))
    # Combined across groups: 6 VOO ($600) vs 4 BND ($400) => VOO is 60% of the whole portfolio
    repo.put_holding("u1", {"ticker": "VOO", "group": "stocks", "shares": 2, "avg_cost": 100})
    repo.put_holding("u1", {"ticker": "VOO", "group": "auto", "shares": 4, "avg_cost": 100})
    repo.put_holding("u1", {"ticker": "BND", "group": "auto", "shares": 4, "avg_cost": 100})
    repo.create_rule("u1", {"type": "weight_above", "ticker": "VOO", "params": {"pct": 55}, "enabled": True})
    assert evaluate_signals.handler({}, None, repo=repo)["alerts_sent"] == 1


def test_news_covers_watched_tickers_but_not_in_the_portfolio_brief(repo):
    class Recorder(FakeSummarizer):
        portfolio_inputs = None

        @staticmethod
        def brief_for_portfolio(briefs, alerts):
            Recorder.portfolio_inputs = sorted(briefs)
            return "Portfolio summary"

    repo.put_holding("u1", {"ticker": "VTI", "shares": 1, "avg_cost": 1})
    repo.put_watch("u1", "NVDA")
    result = news_ingest.handler({}, None, repo=repo, provider=FakeNews(), summarizer=Recorder)
    assert result["summarized"] == ["NVDA", "VTI"]
    assert repo.latest_brief("NVDA")["summary"] == "NVDA summary"
    assert Recorder.portfolio_inputs == ["VTI"]


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

    repo.put_holding("u1", {"ticker": "ITOT", "group": "auto", "shares": 1, "avg_cost": 1})
    repo.put_holding("u1", {"ticker": "VTI", "group": "auto", "shares": 1, "avg_cost": 1})
    spy = Spy()
    result = news_ingest.handler({}, None, repo=repo, provider=spy, summarizer=FakeSummarizer)
    assert spy.asked == ["VTI"] and result["tickers"] == ["VTI"]


def test_excluded_ticker_has_no_stale_sentiment(repo, monkeypatch):
    import dataclasses

    from portfolio import config

    start = (datetime.now(UTC).date() - timedelta(days=60)).isoformat()
    for t in ("DVY", "SPY"):
        repo.put_prices(t, price_series(100, 0, 60, start))
    repo.put_holding("u1", {"ticker": "DVY", "group": "auto", "shares": 1, "avg_cost": 1})
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
    repo.put_holding("u1", {"ticker": "VTI", "shares": 1, "avg_cost": 1})
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


def test_news_resummarizes_after_prompt_change(repo, monkeypatch):
    CountingSummarizer.ticker_calls = CountingSummarizer.portfolio_calls = 0
    repo.put_holding("u1", {"ticker": "VTI", "shares": 1, "avg_cost": 1})
    news = GrowingNews()
    news_ingest.handler({}, None, repo=repo, provider=news, summarizer=CountingSummarizer)
    assert repo.latest_brief("VTI")["prompt_version"] == llm.PROMPT_VERSION

    monkeypatch.setattr(llm, "PROMPT_VERSION", llm.PROMPT_VERSION + 1)  # no new headlines, but new rules
    again = news_ingest.handler({}, None, repo=repo, provider=news, summarizer=CountingSummarizer)
    assert again["summarized"] == ["VTI"] and again["claude_calls"] == 2


def test_portfolio_brief_regenerates_when_alerts_change(repo):
    CountingSummarizer.ticker_calls = CountingSummarizer.portfolio_calls = 0
    repo.put_holding("u1", {"ticker": "VTI", "shares": 1, "avg_cost": 1})
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

    repo.put_holding("u1", {"ticker": "VTI", "group": "stocks", "shares": 1, "avg_cost": 1})
    repo.put_holding("u1", {"ticker": "VTI", "group": "partner_auto", "shares": 1, "avg_cost": 1})  # shared
    repo.put_holding("u1", {"ticker": "SCHD", "group": "partner_stocks", "shares": 1, "avg_cost": 1})  # partner only
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


class FakeQuotes:
    def __init__(self):
        self.calls = []

    def quotes(self, tickers):
        self.calls.append(list(tickers))
        return {t: {"last": 10.0, "timestamp": "2026-10-12T15:00:00+00:00", "date": "2026-10-12"} for t in tickers if t != "VTI"}


@pytest.mark.parametrize(
    "moment, is_open",
    [
        ("2026-10-12T13:29:00+00:00", False),  # 9:29 AM EDT
        ("2026-10-12T13:30:00+00:00", True),
        ("2026-10-12T20:00:00+00:00", True),  # 4:00 PM EDT, last run
        ("2026-10-12T20:00:01+00:00", False),
        ("2026-10-12T20:15:00+00:00", False),
        ("2026-12-14T14:15:00+00:00", False),  # 9:15 AM EST
        ("2026-12-14T21:00:00+00:00", True),  # 4:00 PM EST
        ("2026-10-10T15:00:00+00:00", False),  # Saturday
    ],
)
def test_market_hours_follow_new_york_time(moment, is_open):
    assert market_open(datetime.fromisoformat(moment)) is is_open


def test_intraday_quotes_one_request_only_when_open(repo):
    for ticker in ("VTI", "VOO"):
        repo.put_holding("u1", {"group": "stocks", "ticker": ticker, "shares": 1, "avg_cost": 1})
    provider = FakeQuotes()
    closed = intraday_quotes.handler({}, None, repo=repo, provider=provider, now=datetime(2026, 10, 12, 12, 0, tzinfo=UTC))
    assert closed == {"skipped": "market closed"} and provider.calls == []

    result = intraday_quotes.handler({}, None, repo=repo, provider=provider, now=datetime(2026, 10, 12, 15, 0, tzinfo=UTC))
    assert provider.calls == [["VOO", "VTI"]]
    assert result == {"tickers": 2, "quoted": 1, "missing": ["VTI"]}
    assert repo.latest_quotes() == {"VOO": {"last": 10, "timestamp": "2026-10-12T15:00:00+00:00", "date": "2026-10-12"}}


def test_intraday_quotes_keep_todays_quote_when_a_run_skips_a_ticker(repo):
    for ticker in ("VTI", "VOO"):
        repo.put_holding("u1", {"group": "stocks", "ticker": ticker, "shares": 1, "avg_cost": 1})
    earlier = {"last": 9.0, "timestamp": "2026-10-12T14:00:00+00:00", "date": "2026-10-12"}
    stale = {"last": 8.0, "timestamp": "2026-10-09T19:00:00+00:00", "date": "2026-10-09"}
    repo.put_quotes({"VTI": earlier, "OLD": stale})
    intraday_quotes.handler({}, None, repo=repo, provider=FakeQuotes(), now=datetime(2026, 10, 12, 15, 0, tzinfo=UTC))
    assert set(repo.latest_quotes()) == {"VOO", "VTI"} and repo.latest_quotes()["VTI"]["last"] == 9


def test_watching_turns_news_on_for_a_partner_only_ticker(repo, monkeypatch):
    import dataclasses

    from portfolio import config
    from portfolio.news_policy import news_off

    holdings = [{"ticker": "VTI", "group": "stocks"}, {"ticker": "PQR", "group": "partner_stocks"}]
    assert news_off(holdings, {"ITOT"}, partner_news=False) == {"ITOT", "PQR"}
    assert news_off(holdings, {"ITOT"}, partner_news=False, watched={"PQR", "ITOT"}) == {"ITOT"}
    cfg = dataclasses.replace(config.get_config(), partner_news=False)
    monkeypatch.setattr(news_ingest, "get_config", lambda: cfg)
    repo.put_holding("u1", {"ticker": "PQR", "group": "partner_stocks", "shares": 1, "avg_cost": 1})
    repo.put_watch("u1", "PQR")
    assert news_ingest.handler({}, None, repo=repo, provider=FakeNews(), summarizer=FakeSummarizer)["tickers"] == ["PQR"]


def test_monthly_checkup_email_and_stored_summary(repo, monkeypatch):
    from portfolio.handlers import monthly_checkup

    sent = []
    monkeypatch.setattr(monthly_checkup.notify, "send_email", lambda subject, body: sent.append((subject, body)))

    class Summarizer:
        seen = None

        @staticmethod
        def checkup_summary(facts):
            Summarizer.seen = facts
            return "A calm month.\nInternational is 2% vs. a 20% target, about $9,100 below plan."

    start = (datetime.now(UTC).date() - timedelta(days=400)).isoformat()
    for ticker, shares in (("VTI", 6), ("SMH", 4)):
        repo.put_holding("u1", {"ticker": ticker, "shares": shares, "avg_cost": 1})
        repo.put_prices(ticker, price_series(100, 0.5, 300, start))
    assert monthly_checkup.handler({}, None, repo=repo, summarizer=Summarizer) == {"emails_sent": 1, "skipped": 0}
    assert "Set your target mix" in sent[0][1]  # no targets yet

    repo.put_targets(
        "u1", {"areas": {"us": {"pct": 80, "fund": "VTI"}, "sector": {"pct": 20, "fund": "SMH"}}, "sector_ceiling": 20}
    )
    monthly_checkup.handler({"force": True}, None, repo=repo, summarizer=Summarizer)  # same month: a manual re-run
    subject, body = sent[-1]
    assert subject.startswith("Your monthly portfolio checkup")
    assert "  • A calm month.\n  • International is 2%" in body and "YOUR MIX VS. TARGET" in body and "Single sector" in body
    assert "NEXT $1,000" in body and "(VTI)" in body
    assert Summarizer.seen["single_sector_ceiling"] == 20 and "u1" not in str(Summarizer.seen)
    assert repo.latest_checkup_summary("u1")["summary"].startswith("A calm month.\n")
    # A retry in the same month doesn't email again; a forced manual run does
    count = len(sent)
    assert monthly_checkup.handler({}, None, repo=repo, summarizer=Summarizer)["skipped"] == 1 and len(sent) == count
    monthly_checkup.handler({"force": True}, None, repo=repo, summarizer=Summarizer)
    assert len(sent) == count + 1


def test_monthly_checkup_keeps_going_when_one_user_fails(repo, monkeypatch):
    from portfolio.handlers import monthly_checkup

    sent = []
    monkeypatch.setattr(monthly_checkup.notify, "send_email", lambda subject, body: sent.append(body))

    class Flaky:
        calls = 0

        @staticmethod
        def checkup_summary(facts):
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise RuntimeError("bedrock down")
            return "ok"

    start = (datetime.now(UTC).date() - timedelta(days=30)).isoformat()
    for user in ("u1", "u2"):
        repo.put_holding(user, {"ticker": "VTI", "shares": 1, "avg_cost": 1})
    repo.put_prices("VTI", price_series(100, 1, 20, start))
    with pytest.raises(RuntimeError, match="1 user"):
        monthly_checkup.handler({}, None, repo=repo, summarizer=Flaky)
    assert len(sent) == 1
    monthly_checkup.handler({}, None, repo=repo, summarizer=Flaky)  # the retry only does the failed user
    assert len(sent) == 2


def test_unavailable_summary_is_not_a_bullet():
    from portfolio import llm
    from portfolio.handlers.monthly_checkup import summary_lines

    assert summary_lines(llm.UNAVAILABLE) == ["  " + llm.UNAVAILABLE]
    assert summary_lines("One.\n\n Two. ") == ["  • One.", "  • Two."]


def test_intraday_uses_the_scheduled_time_not_the_late_start(repo, monkeypatch):
    repo.put_holding("u1", {"group": "stocks", "ticker": "VOO", "shares": 1, "avg_cost": 1})
    provider = FakeQuotes()
    # The 4:00 PM EDT run, started 42 s late: still the closing run.
    result = intraday_quotes.handler({"time": "2026-10-12T20:00:00Z"}, None, repo=repo, provider=provider)
    assert provider.calls == [["VOO"]] and result["quoted"] == 1
    assert intraday_quotes.handler({"time": "2026-10-12T20:15:00Z"}, None, repo=repo, provider=provider) == {
        "skipped": "market closed"
    }
    assert intraday_quotes._scheduled_time({}) is None and intraday_quotes._scheduled_time({"time": "junk"}) is None


def test_dividend_day_reloads_full_history(repo):
    class Provider:
        def __init__(self):
            self.starts = []

        def daily_prices(self, ticker, start_date):
            self.starts.append(start_date)
            rows = price_series(100, 0.5, 3, start_date)
            if len(self.starts) == 2:  # the incremental fetch brings an ex-dividend day
                rows[-1]["div_cash"] = 0.25
            return rows

    repo.put_holding("u1", {"ticker": "SCHD", "shares": 1, "avg_cost": 1})
    provider = Provider()
    price_sync.handler({}, None, repo=repo, provider=provider)  # backfill
    price_sync.handler({}, None, repo=repo, provider=provider)  # new days include a dividend -> full reload
    assert len(provider.starts) == 3 and provider.starts[2] == provider.starts[0]


@pytest.mark.parametrize(
    "day, early",
    [
        ("2026-11-27", True),
        ("2026-11-26", False),
        ("2027-11-26", True),
        ("2026-12-24", True),
        ("2027-12-24", False),
        ("2025-07-03", True),
        ("2026-07-03", False),
        ("2026-10-13", False),
    ],
)
def test_early_close_days(day, early):
    from datetime import date

    from portfolio.market import early_close

    assert early_close(date.fromisoformat(day)) is early


def test_half_day_stops_quotes_at_one():
    assert market_open(datetime(2026, 11, 27, 18, 0, tzinfo=UTC)) is True  # 1:00 PM EST, the last run
    assert market_open(datetime(2026, 11, 27, 18, 15, tzinfo=UTC)) is False  # 1:15 PM EST: closed


def test_nightly_earnings_refresh(repo):
    class Earnings(FakeNews):
        def next_earnings(self, ticker, start, end):
            if ticker == "BAD":
                raise RuntimeError("finnhub down")
            return {"date": "2026-11-17", "hour": "amc", "eps_estimate": 2.53} if ticker == "NVDA" else None

    news_ingest.EARNINGS_PAUSE_SECONDS = 0
    for ticker in ("NVDA", "AMZN", "BAD", "SMH"):
        repo.put_holding("u1", {"ticker": ticker, "shares": 1, "avg_cost": 1})
    repo.put_earnings("BAD", {"date": "2026-11-01", "hour": None, "eps_estimate": None})
    result = news_ingest.handler({}, None, repo=repo, provider=Earnings(), summarizer=FakeSummarizer)
    assert repo.get_earnings("NVDA")["date"] == "2026-11-17" and repo.get_earnings("AMZN") is None
    assert repo.get_earnings("BAD")["date"] == "2026-11-01"  # a failed lookup keeps yesterday's date
    assert result is not None


def test_nightly_insights_refresh(repo):
    class Insights(FakeNews):
        def recommendations(self, ticker):
            if ticker == "BAD":
                raise RuntimeError("finnhub down")
            return [{"period": "2026-09", "buy": 65, "hold": 3, "sell": 1}]

        def insider_trades(self, ticker, since):
            self.since = since
            return {"bought": 0, "buys": 0, "sold": 1000, "sells": 1, "routine": 0, "trades": []}

    news_ingest.EARNINGS_PAUSE_SECONDS = 0
    for ticker in ("NVDA", "BAD", "VTI"):
        repo.put_holding("u1", {"ticker": ticker, "shares": 1, "avg_cost": 1})
    repo.put_insights("BAD", {"analysts": [{"period": "2026-08", "buy": 1, "hold": 0, "sell": 0}]})
    provider = Insights()
    news_ingest.handler({}, None, repo=repo, provider=provider, summarizer=FakeSummarizer)
    assert repo.get_insights("NVDA")["analysts"][0]["buy"] == 65
    assert repo.get_insights("BAD")["analysts"][0]["period"] == "2026-08"  # a failed lookup keeps yesterday's
    assert repo.get_insights("VTI") is None  # funds are skipped
    assert provider.since < datetime.now(UTC).date().isoformat()


def test_nyse_holidays():
    from datetime import date

    from portfolio.market import holidays

    assert sorted(holidays(2026)) == [
        date(2026, *md) for md in ((1, 1), (1, 19), (2, 16), (4, 3), (5, 25), (6, 19), (7, 3), (9, 7), (11, 26), (12, 25))
    ]
    observed_2027 = sorted(holidays(2027))
    assert date(2027, 6, 18) in observed_2027 and date(2027, 7, 5) in observed_2027 and date(2027, 12, 24) in observed_2027
    assert date(2027, 3, 26) in observed_2027  # Good Friday
    from portfolio.market import holiday

    assert holiday(date(2027, 12, 31)) is None and date(2028, 1, 1) not in holidays(2028)  # Jan 1, 2028 is a Saturday
    assert holiday(date(2021, 6, 18)) is None  # Juneteenth closures start in 2022


def test_market_closed_on_holidays_and_status():
    from portfolio.market import market_status

    thanksgiving = datetime(2026, 11, 26, 16, 0, tzinfo=UTC)  # 11:00 AM EST
    assert market_open(thanksgiving) is False
    assert market_status(thanksgiving) == {
        "date": "2026-11-26",
        "holiday": "Thanksgiving Day",
        "early_close": False,
        "open": False,
    }
    assert market_status(datetime(2026, 11, 27, 15, 0, tzinfo=UTC))["early_close"] is True
    assert market_status(datetime(2026, 11, 27, 17, 59, tzinfo=UTC))["open"] is True  # 12:59 PM EST, early-close day
    assert market_status(datetime(2026, 11, 27, 18, 1, tzinfo=UTC))["open"] is False
    assert market_status(datetime(2026, 10, 14, 20, 0, tzinfo=UTC))["open"] is True  # 4:00 PM EDT, the last quote
    assert market_status(datetime(2026, 10, 14, 20, 35, tzinfo=UTC))["open"] is False
    assert market_status(datetime(2027, 12, 24, 15, 0, tzinfo=UTC))["holiday"] == "Christmas Day"  # observed, not a half day


@pytest.mark.parametrize(
    "now_utc, expected",
    [
        ("2026-10-14T22:00:00", "2026-10-13"),  # Wed 6:00 PM EDT: tonight's run isn't due yet
        ("2026-10-14T23:45:00", "2026-10-14"),  # Wed 7:45 PM EDT: tonight's close is due
        ("2026-10-17T15:00:00", "2026-10-16"),  # Saturday: Friday's close
        ("2026-10-19T13:00:00", "2026-10-16"),  # Monday morning: still Friday's
        ("2026-11-27T14:00:00", "2026-11-25"),  # Friday after Thanksgiving, morning: Wednesday's
        ("2026-12-01T01:00:00", "2026-11-30"),  # Monday 8:00 PM EST (Tuesday in UTC): Monday's
    ],
)
def test_expected_close(now_utc, expected):
    from portfolio.market import expected_close

    assert expected_close(datetime.fromisoformat(now_utc).replace(tzinfo=UTC)).isoformat() == expected


def test_news_ingest_records_its_run(repo):
    repo.put_holding("u1", {"ticker": "VTI", "shares": 1, "avg_cost": 1})
    assert repo.get_job_run("news_ingest") is None
    news_ingest.handler({}, None, repo=repo, provider=FakeNews(), summarizer=FakeSummarizer)
    assert repo.get_job_run("news_ingest") is not None
    stored = repo.recent_news("VTI", "2000-01-01")
    assert stored and all(a.get("first_seen") for a in stored)


def test_news_ingest_not_recorded_when_every_ticker_fails(repo):
    class Down(FakeNews):
        def company_news(self, ticker, from_date, to_date):
            raise RuntimeError("bad key")

    repo.put_holding("u1", {"ticker": "VTI", "shares": 1, "avg_cost": 1})
    news_ingest.handler({}, None, repo=repo, provider=Down(), summarizer=FakeSummarizer)
    assert repo.get_job_run("news_ingest") is None


def test_intraday_price_rule_alerts_once_a_day(repo, monkeypatch):
    sent = []
    monkeypatch.setattr(alerting.notify, "send_email", lambda s, b: sent.append(b))
    monkeypatch.setattr(alerting.notify, "push_to_user", lambda *a, **k: 1)
    repo.put_holding("u1", {"ticker": "VTI", "shares": 1, "avg_cost": 100})
    repo.put_prices("VTI", price_series(100, 0, 5, "2026-10-05"))  # last close 100
    repo.create_rule("u1", {"type": "price_below", "ticker": "VTI", "params": {"price": 95}, "enabled": True})
    repo.create_rule("u1", {"type": "rsi_above", "ticker": "VTI", "params": {"threshold": 1}, "enabled": True})
    evaluate_signals.handler({}, None, repo=repo)  # snapshot for the intraday check
    now = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)

    def quote(last):
        return {"VTI": {"last": last, "timestamp": "2026-10-12T15:00:00+00:00", "date": "2026-10-12"}}

    assert alerting.check_intraday(repo, quote(97), now) == 0  # above the level
    assert alerting.check_intraday(repo, quote(94), now) == 1  # crosses mid-session
    assert "VTI is at $94.00, below your $95.00 level" in sent[0]
    assert alerting.check_intraday(repo, quote(94), now) == 0  # still true: no repeat
    alerting.check_intraday(repo, quote(96), now)
    assert alerting.check_intraday(repo, quote(94), now) == 0  # bounced and crossed again the same day
    assert alerting.check_intraday(repo, quote(94), now + timedelta(days=1)) == 0  # old quotes (not today) are ignored
    assert len(sent) == 1 and len(repo.list_alerts("u1")) == 1  # the RSI rule waits for the close


def test_intraday_check_failure_keeps_the_quotes(repo, monkeypatch):
    repo.put_holding("u1", {"group": "stocks", "ticker": "VOO", "shares": 1, "avg_cost": 1})

    def boom(*args):
        raise RuntimeError("db down")

    monkeypatch.setattr(intraday_quotes.alerting, "check_intraday", boom)
    intraday_quotes.handler({}, None, repo=repo, provider=FakeQuotes(), now=datetime(2026, 10, 12, 15, 0, tzinfo=UTC))
    assert "VOO" in repo.latest_quotes()


def test_intraday_weight_rule_uses_live_prices(repo, monkeypatch):
    monkeypatch.setattr(alerting.notify, "send_email", lambda s, b: None)
    monkeypatch.setattr(alerting.notify, "push_to_user", lambda *a, **k: 1)
    for ticker in ("VTI", "BND"):
        repo.put_holding("u1", {"ticker": ticker, "shares": 1, "avg_cost": 100})
        repo.put_prices(ticker, price_series(100, 0, 5, "2026-10-05"))
    repo.create_rule("u1", {"type": "weight_above", "ticker": "VTI", "params": {"pct": 55}, "enabled": True})
    evaluate_signals.handler({}, None, repo=repo)  # 50/50 at the close
    now = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)
    live = {"VTI": {"last": 130, "timestamp": "2026-10-12T15:00:00+00:00", "date": "2026-10-12"}}
    assert alerting.check_intraday(repo, live, now) == 1  # 130 of 230 is 57%


def test_failed_alert_email_is_retried_and_stale_snapshots_ignored(repo, monkeypatch):
    sent = []

    def flaky(subject, body):
        if not sent:
            sent.append("failed")
            raise RuntimeError("sns down")
        sent.append(subject)

    monkeypatch.setattr(alerting.notify, "send_email", flaky)
    monkeypatch.setattr(alerting.notify, "push_to_user", lambda *a, **k: 1)
    repo.put_holding("u1", {"ticker": "VTI", "shares": 1, "avg_cost": 100})
    repo.put_prices("VTI", price_series(100, 0, 5, "2026-10-05"))
    repo.create_rule("u1", {"type": "price_below", "ticker": "VTI", "params": {"price": 95}, "enabled": True})
    evaluate_signals.handler({}, None, repo=repo)
    now = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)
    live = {"VTI": {"last": 94, "timestamp": "2026-10-12T15:00:00+00:00", "date": "2026-10-12"}}
    assert alerting.check_intraday(repo, live, now) == 0  # the email failed: nothing stored
    assert alerting.check_intraday(repo, live, now) == 1  # the next run delivers it
    assert len(repo.list_alerts("u1")) == 1
    later = now + timedelta(days=30)
    repo.put_rule_state("u1", repo.all_rules()[0]["rule_id"], False, "x")
    stale = {"VTI": {"last": 94, "timestamp": "2026-11-11T15:00:00+00:00", "date": "2026-11-11"}}
    assert alerting.check_intraday(repo, stale, later) == 0  # the last close is a month old
