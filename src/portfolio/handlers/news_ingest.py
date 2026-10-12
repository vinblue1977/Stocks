"""Fetches recent headlines per holding, scores and summarizes them with Claude on Bedrock.

To keep Bedrock costs down, Claude is only called when there is something new:
- a ticker is re-summarized only if Finnhub returned a headline we haven't already scored;
  otherwise yesterday's summary is reused and only the 7-day sentiment is recalculated
- the portfolio briefing is regenerated only if a ticker summary changed or the set of
  triggered alerts changed
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from portfolio import llm
from portfolio.config import get_config
from portfolio.db import Repository
from portfolio.logs import get_logger, log
from portfolio.news_policy import news_off
from portfolio.providers.news import FinnhubNewsProvider, NewsProvider
from portfolio.secrets import get_secret

logger = get_logger(__name__)


def _sentiment_7d(repo: Repository, ticker: str, now: datetime) -> tuple[float | None, int]:
    week_ago = (now - timedelta(days=7)).isoformat(timespec="seconds")
    recent = repo.recent_news(ticker, week_ago, limit=100)
    scores = [float(a["score"]) for a in recent if a.get("score") is not None]
    return (round(sum(scores) / len(scores), 3) if scores else None), len(recent)


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

    already_scored = {
        a["article_id"] for a in repo.recent_news(ticker, (now - timedelta(days=8)).isoformat(timespec="seconds"), limit=200)
    }
    previous = repo.latest_brief(ticker)
    fresh = [a for a in articles if a["article_id"] not in already_scored]

    if not fresh and previous and previous.get("summary") and previous["summary"] != llm.UNAVAILABLE:
        # Nothing new: keep the summary, refresh only the 7-day score (older headlines age out).
        sentiment, count = _sentiment_7d(repo, ticker, now)
        record = {
            **{k: previous[k] for k in ("summary", "summarized_at") if k in previous},
            "sentiment_7d": sentiment,
            "label": llm.sentiment_label(sentiment),
            "headline_count": count,
            "generated_at": now.isoformat(timespec="seconds"),
            "reused": True,
        }
        record.setdefault("summarized_at", previous.get("generated_at"))
        repo.put_brief(ticker, now.date().isoformat(), record)
        return record

    brief = summarizer.brief_for_ticker(ticker, articles)
    scores = {s.article_id: s.score for s in brief.article_scores}
    for article in articles:
        article["score"] = scores.get(article["article_id"], 0.0)
    repo.put_news(ticker, articles)

    sentiment, count = _sentiment_7d(repo, ticker, now)
    stamp = now.isoformat(timespec="seconds")
    record = {
        "summary": brief.summary,
        "sentiment_7d": sentiment,
        "label": llm.sentiment_label(sentiment),
        "headline_count": count,
        "generated_at": stamp,
        "summarized_at": stamp,
        "reused": False,
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
    cfg = get_config()
    holdings = repo.all_holdings()
    # Skips e.g. broad index funds that rarely get headlines, and partner-only tickers if partner news is off.
    tickers = sorted({h["ticker"] for h in holdings} - news_off(holdings, cfg.news_excluded, cfg.partner_news))

    briefs: dict[str, str] = {}
    scores: list[float] = []
    failed: list[str] = []
    summarized: list[str] = []
    reused: list[str] = []
    for ticker in tickers:
        try:
            record = ingest_ticker(ticker, repo, provider, now, summarizer)
        except Exception:
            logger.exception("news ingest failed")
            failed.append(ticker)
            continue
        if record:
            briefs[ticker] = record["summary"]
            (reused if record.get("reused") else summarized).append(ticker)
            if record["sentiment_7d"] is not None:
                scores.append(record["sentiment_7d"])

    portfolio_reused = False
    if briefs:
        active = sorted(r["detail"] for r in _active_rule_details(repo) if r.get("detail"))
        overall = round(sum(scores) / len(scores), 3) if scores else None
        # Fingerprint of everything the portfolio briefing is based on.
        inputs_key = hashlib.sha256(json.dumps({"briefs": briefs, "alerts": active}, sort_keys=True).encode()).hexdigest()
        previous = repo.latest_brief("ALL")
        if previous and previous.get("inputs_key") == inputs_key and previous.get("summary") != llm.UNAVAILABLE:
            summary = previous["summary"]
            portfolio_reused = True
        else:
            summary = summarizer.brief_for_portfolio(briefs, active)
        repo.put_brief(
            "ALL",
            now.date().isoformat(),
            {
                "summary": summary,
                "sentiment_7d": overall,
                "label": llm.sentiment_label(overall),
                "headline_count": len(briefs),
                "generated_at": now.isoformat(timespec="seconds"),
                "inputs_key": inputs_key,
                "reused": portfolio_reused,
            },
        )
    claude_calls = len(summarized) + (1 if briefs and not portfolio_reused else 0)
    log(
        logger,
        "news_ingest_complete",
        tickers=tickers,
        summarized=summarized,
        reused=reused,
        failed=failed,
        claude_calls=claude_calls,
    )
    return {"tickers": tickers, "summarized": summarized, "reused": reused, "failed": failed, "claude_calls": claude_calls}


def _active_rule_details(repo: Repository) -> list[dict[str, Any]]:
    active = []
    for rule in repo.all_rules():
        state = repo.get_rule_state(rule["user_id"], rule["rule_id"])
        if state and state.get("triggered"):
            active.append(state)
    return active
