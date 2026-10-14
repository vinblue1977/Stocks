"""Computes signal snapshots for every ticker and evaluates each user's alert rules.

Alerts are edge-triggered: an email goes out when a rule changes from not-met to met,
not every day the condition stays true.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from portfolio import analytics, notify, rules, signals
from portfolio.config import get_config
from portfolio.db import Repository
from portfolio.logs import get_logger, log
from portfolio.news_policy import news_off

logger = get_logger(__name__)

HISTORY_DAYS = signals.HISTORY_DAYS


def handler(event: dict[str, Any] | None, context: Any, repo: Repository | None = None) -> dict[str, Any]:
    repo = repo or Repository()
    now = datetime.now(UTC)
    since = (now.date() - timedelta(days=HISTORY_DAYS)).isoformat()
    all_rules = [r for r in repo.all_rules() if r.get("enabled", True)]
    extra_windows = rules.needed_sma_windows(all_rules)

    snapshots: dict[str, dict[str, Any]] = {}
    latest: dict[str, float] = {}
    cfg = get_config()
    holdings = repo.all_holdings()
    no_news = news_off(holdings, cfg.news_excluded, cfg.partner_news, {w["ticker"] for w in repo.all_watched()})
    for ticker in repo.tracked_tickers():
        history = repo.price_history(ticker, since)
        brief = None if ticker in no_news else repo.latest_brief(ticker)  # no stale sentiment once news is off
        sentiment = brief.get("sentiment_7d") if brief else None
        snapshot = signals.compute_signals(ticker, history, sentiment, extra_windows)
        if snapshot:
            repo.put_signal(ticker, snapshot)
            snapshots[ticker] = snapshot
            latest[ticker] = snapshot["price"]

    weights = _weights_by_user(holdings, latest)
    fired = 0
    for rule in all_rules:
        signal = snapshots.get(rule["ticker"])
        if signal is None:
            continue
        user_id = rule["user_id"]
        met, detail = rules.evaluate(rule, signal, weights.get(user_id, {}).get(rule["ticker"]))
        state = repo.get_rule_state(user_id, rule["rule_id"]) or {}
        was_met = bool(state.get("triggered", False))
        repo.put_rule_state(user_id, rule["rule_id"], met, detail)
        if met and not was_met:
            fired += 1
            repo.record_alert(user_id, rule["rule_id"], detail)
            description = rules.RULE_TYPES[rule["type"]].description
            notify.send_email(f"Alert: {rule['ticker']} rule triggered", f"{detail}.\n\nRule: {description}.")
            notify.push_to_user(repo, user_id, f"Alert: {rule['ticker']}", f"{detail}.")
    log(logger, "signals_complete", tickers=sorted(snapshots), rules=len(all_rules), fired=fired)
    return {"tickers": sorted(snapshots), "rules_evaluated": len(all_rules), "alerts_sent": fired}


def _weights_by_user(holdings: list[dict[str, Any]], latest: dict[str, float]) -> dict[str, dict[str, float]]:
    """Per user, each ticker's share of the whole portfolio (all groups combined)."""
    by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for holding in holdings:
        by_user[holding["user_id"]].append(holding)
    return {user_id: analytics.combined_weights(holdings, latest) for user_id, holdings in by_user.items()}
