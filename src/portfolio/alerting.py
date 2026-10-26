"""Alert delivery shared by the evening signal job and the 15-minute intraday check.

Alerts are edge-triggered (not-met to met), and a rule alerts at most once per trading day, so a price
hovering around a level during market hours does not send a message every 15 minutes.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from portfolio import analytics, notify, rules
from portfolio.db import Repository
from portfolio.market import trading_day


def weights_by_user(holdings: list[dict[str, Any]], latest: Mapping[str, float]) -> dict[str, dict[str, float]]:
    """Per user, each ticker's share of the whole portfolio (all groups combined)."""
    by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for holding in holdings:
        by_user[holding["user_id"]].append(holding)
    return {user_id: analytics.combined_weights(held, latest) for user_id, held in by_user.items()}


def apply_rule(repo: Repository, rule: Mapping[str, Any], met: bool, detail: str, today: str) -> bool:
    """Store the rule's state; email and push when it newly became met and has not alerted today."""
    user_id = rule["user_id"]
    state = repo.get_rule_state(user_id, rule["rule_id"]) or {}
    fire = met and not state.get("triggered", False) and state.get("alerted_on") != today
    repo.put_rule_state(user_id, rule["rule_id"], met, detail, today if fire else state.get("alerted_on"))
    if fire:
        repo.record_alert(user_id, rule["rule_id"], detail)
        description = rules.RULE_TYPES[rule["type"]].description
        notify.send_email(f"Alert: {rule['ticker']} rule triggered", f"{detail}.\n\nRule: {description}.")
        notify.push_to_user(repo, user_id, f"Alert: {rule['ticker']}", f"{detail}.")
    return fire


def check_intraday(repo: Repository, quotes: Mapping[str, Mapping[str, Any]], now: datetime) -> int:
    """Evaluate the price-level rules against today's quotes, using the last close's averages and 1-year high."""
    todo = [r for r in repo.all_rules() if r.get("enabled", True) and r["type"] in rules.INTRADAY_TYPES]
    if not todo:
        return 0
    today = trading_day(now).isoformat()
    live = {t: float(q["last"]) for t, q in quotes.items() if q.get("date") == today}
    snapshots = {t: repo.get_signal(t) for t in {r["ticker"] for r in todo}}
    weights: dict[str, dict[str, float]] = {}
    if any(r["type"] == "weight_above" for r in todo):
        holdings = repo.all_holdings()
        latest: dict[str, float] = {}
        for ticker in {h["ticker"] for h in holdings}:
            price = live.get(ticker) or (repo.get_signal(ticker) or {}).get("price")
            if price is not None:
                latest[ticker] = price
        weights = weights_by_user(holdings, latest)
    fired = 0
    for rule in todo:
        snapshot, price = snapshots.get(rule["ticker"]), live.get(rule["ticker"])
        if snapshot is None or price is None:
            continue
        signal = {**snapshot, "price": price, "intraday": True}
        drawdown = snapshot.get("drawdown_from_high")
        if drawdown is not None and snapshot.get("price"):
            high = snapshot["price"] / (1 + drawdown)  # the 1-year high behind the stored drawdown
            signal["drawdown_from_high"] = min(0.0, price / high - 1)
        met, detail = rules.evaluate(rule, signal, weights.get(rule["user_id"], {}).get(rule["ticker"]))
        fired += apply_rule(repo, rule, met, detail, today)
    return fired
