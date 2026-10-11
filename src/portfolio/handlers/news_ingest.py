"""Fetches recent headlines per holding, scores and summarizes them with Claude on Bedrock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from portfolio import llm
from portfolio.config import get_config
from portfolio.db import Repository
from portfolio.logs import get_logger, log
from portfolio.providers.news import FinnhubNewsProvider, NewsProvider
from portfolio.secrets import get_secret

logger = get_logger(__name__)


def ingest_ticker(
    ticker: str,
    repo: Repository,
    provider: NewsProvider,
    now: datetime,
    summarizer: Any = llm,
) -> dict[str, Any] | None:
    cfg = get_config()
    from_date = (now - timedelta(days=cfg.news_lookback_days)).date().isoformat()
    articles = provider.company_news(ticker, from_date, now.date().isoformat())
    if not articles:
        return None
    brief = summarizer.brief_for_ticker(ticker, articles)
    scores = {s.article_id: s.score for s in brief.article_scores}
    for article in articles:
        article["score"] = scores.get(article["article_id"], 0.0)
    repo.put_news(ticker, articles)

    week_ago = (now - timedelta(days=7)).isoformat(timespec="seconds")
    recent = repo.recent_news(ticker, week_ago, limit=100)
    week_scores = [float(a["score"]) for a in recent if a.get("score") is not None]
    sentiment_7d = round(sum(week_scores) / len(week_scores), 3) if week_scores else None
    record = {
        "summary": brief.summary,
        "sentiment_7d": sentiment_7d,
        "label": llm.sentiment_label(sentiment_7d),
        "headline_count": len(recent),
        "generated_at": now.isoformat(timespec="seconds"),
    }
    repo.put_brief(ticker, now.date().isoformat(), record)
    return record


def handler(
    event: dict[str, Any] | None,
    context: Any,
    repo: Repository | None = None,
    provider: NewsProvider | None = None,
    summarizer: Any = llm,
) -> dict[str, Any]:
    repo = repo or Repository()
    provider = provider or FinnhubNewsProvider(get_secret(get_config().news_api_key_param))
    now = datetime.now(UTC)
    tickers = sorted({h["ticker"] for h in repo.all_holdings()})

    briefs: dict[str, str] = {}
    scores: list[float] = []
    failed: list[str] = []
    for ticker in tickers:
        try:
            record = ingest_ticker(ticker, repo, provider, now, summarizer)
        except Exception:
            logger.exception("news ingest failed")
            failed.append(ticker)
            continue
        if record:
            briefs[ticker] = record["summary"]
            if record["sentiment_7d"] is not None:
                scores.append(record["sentiment_7d"])

    if briefs:
        active = [r["detail"] for r in _active_rule_details(repo)]
        overall = round(sum(scores) / len(scores), 3) if scores else None
        repo.put_brief(
            "ALL",
            now.date().isoformat(),
            {
                "summary": summarizer.brief_for_portfolio(briefs, active),
                "sentiment_7d": overall,
                "label": llm.sentiment_label(overall),
                "headline_count": len(briefs),
                "generated_at": now.isoformat(timespec="seconds"),
            },
        )
    log(logger, "news_ingest_complete", tickers=tickers, failed=failed)
    return {"tickers": tickers, "summarized": sorted(briefs), "failed": failed}


def _active_rule_details(repo: Repository) -> list[dict[str, Any]]:
    active = []
    for rule in repo.all_rules():
        state = repo.get_rule_state(rule["user_id"], rule["rule_id"])
        if state and state.get("triggered"):
            active.append(state)
    return active
