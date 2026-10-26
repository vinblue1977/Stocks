"""Alert delivery shared by the evening signal job and the 15-minute intraday check.

Alerts are edge-triggered (not-met to met), and a rule alerts at most once per trading day, so a price
hovering around a level during market hours does not send a message every 15 minutes.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from portfolio import analytics, notify, rules
from portfolio.db import Repository
from portfolio.logs import get_logger
from portfolio.market import trading_day

logger = get_logger(__name__)
STALE_AFTER = timedelta(days=5)  # a close older than a long weekend means the evening job missed runs


def weights_by_user(holdings: list[dict[str, Any]], latest: Mapping[str, float]) -> dict[str, dict[str, float]]:
    """Per user, each ticker's share of the whole portfolio (all groups combined)."""
    by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for holding in holdings:
        by_user[holding["user_id"]].append(holding)
    return {user_id: analytics.combined_weights(held, latest) for user_id, held in by_user.items()}


def apply_rule(repo: Repository, rule: Mapping[str, Any], met: bool, detail: str, today: str) -> bool:
    """Store the rule's state; email and push when it newly became met and has not alerted today.

    The email goes first: if it fails the state is left alone, so the next run tries again.
    """
    user_id = rule["user_id"]
    state = repo.get_rule_state(user_id, rule["rule_id"]) or {}
    fire = met and not state.get("triggered", False) and state.get("alerted_on") != today
    if fire:
        description = rules.RULE_TYPES[rule["type"]].description
        try:
            notify.send_email(f"Alert: {rule['ticker']} rule triggered", f"{detail}.\n\nRule: {description}.")
        except Exception:  # e.g. an SNS error: keep going with the other rules
            logger.exception("alert email failed")
            return False
        repo.record_alert(user_id, rule["rule_id"], detail)
        notify.push_to_user(repo, user_id, f"Alert: {rule['ticker']}", f"{detail}.")
    repo.put_rule_state(user_id, rule["rule_id"], met, detail, today if fire else state.get("alerted_on"))
    return fire


def check_intraday(repo: Repository, quotes: Mapping[str, Mapping[str, Any]], now: datetime) -> int:
    """Evaluate the price-level rules against today's quotes, using the last close's averages and 1-year high."""
    todo = [r for r in repo.all_rules() if r.get("enabled", True) and r["type"] in rules.INTRADAY_TYPES]
    if not todo:
        return 0
    today = trading_day(now).isoformat()
    live = {t: float(q["last"]) for t, q in quotes.items() if q.get("date") == today}
    oldest = (trading_day(now) - STALE_AFTER).isoformat()
    snapshots = {t: repo.get_signal(t) for t in {r["ticker"] for r in todo}}
    weights: dict[str, dict[str, float]] = {}
    skip_weights = False
    if any(r["type"] == "weight_above" for r in todo):
        holdings = repo.all_holdings()
        latest: dict[str, float] = {}
        for ticker in {h["ticker"] for h in holdings}:
            snapshot = snapshots[ticker] if ticker in snapshots else repo.get_signal(ticker)
            price = live.get(ticker) or (snapshot or {}).get("price")
            if price is not None:
                latest[ticker] = price
        skip_weights = len(latest) < len({h["ticker"] for h in holdings})  # a missing price would skew every weight
        weights = weights_by_user(holdings, latest)
    fired = 0
    for rule in todo:
        snapshot, price = snapshots.get(rule["ticker"]), live.get(rule["ticker"])
        if snapshot is None or price is None or str(snapshot.get("as_of", "")) < oldest:
            continue
        if rule["type"] == "weight_above" and skip_weights:
            continue
        signal = {**snapshot, "price": price, "intraday": True}
        drawdown = snapshot.get("drawdown_from_high")
        if drawdown is not None and snapshot.get("price"):
            high = snapshot["price"] / (1 + drawdown)  # the 1-year high behind the stored drawdown
            signal["drawdown_from_high"] = min(0.0, price / high - 1)
        met, detail = rules.evaluate(rule, signal, weights.get(rule["user_id"], {}).get(rule["ticker"]))
        fired += apply_rule(repo, rule, met, detail, today)
    return fired
