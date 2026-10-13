"""HTTP API (API Gateway v2) handler.

Every route sits behind a Cognito JWT authorizer. The user id is always taken from the
verified token's `sub` claim, never from the request, so users can only read and change
their own data.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import boto3

from portfolio import analytics, rules
from portfolio.config import get_config
from portfolio.db import GROUPS, KINDS, PEOPLE, ConcurrentUpdateError, Repository, kind_of, person_of
from portfolio.llm import sentiment_label
from portfolio.logs import get_logger, log
from portfolio.market import trading_day
from portfolio.news_policy import news_off

logger = get_logger(__name__)

MAX_BODY_BYTES = 10_000
MAX_HOLDINGS = 40  # unique tickers across all groups; keeps the price sync under Tiingo's free 50 requests/hour
MAX_RULES = 50
HISTORY_DAYS = 400  # calendar days of history for the performance chart (about 1 trading year + margin)

JSON_HEADERS = {
    "Content-Type": "application/json",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _response(status: int, body: Any) -> dict[str, Any]:
    payload = "" if status == 204 else json.dumps(body, default=str)
    return {"statusCode": status, "headers": JSON_HEADERS, "body": payload}


def _user_id(event: dict[str, Any]) -> str:
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    sub = claims.get("sub")
    if not sub:
        raise ApiError(401, "Unauthorized")
    return str(sub)


def _body(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raise ApiError(415, "Send JSON as text")
    if len(raw.encode("utf-8")) > MAX_BODY_BYTES:
        raise ApiError(413, "Request body too large")
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ApiError(400, "Body must be valid JSON") from exc
    if not isinstance(body, dict):
        raise ApiError(400, "Body must be a JSON object")
    return body


def _ticker(value: str | None) -> str:
    ticker = (value or "").upper().strip()
    if not rules.TICKER_PATTERN.match(ticker):
        raise ApiError(400, "Invalid ticker")
    return ticker


def _number(body: dict[str, Any], name: str, low: float, high: float) -> float:
    try:
        value = float(body[name])
    except (KeyError, TypeError, ValueError) as exc:
        raise ApiError(400, f"{name} must be a number") from exc
    if not low <= value <= high or value != value:  # also rejects NaN
        raise ApiError(400, f"{name} must be between {low} and {high}")
    return value


# ---------------------------------------------------------------- holdings
# Holdings live in a group: Stock Holdings or Automatic Investment, for the owner or the partner
# (see db.GROUPS). The same ticker can be in several; the 40-ticker cap counts unique tickers across all.
def _group(event: dict[str, Any]) -> str:
    group = (event.get("pathParameters") or {}).get("group", "")
    if group not in GROUPS:
        raise ApiError(404, "Unknown group")
    return group


def _admit_ticker(repo: Repository, user_id: str, ticker: str) -> bool:
    """Enforce the unique-ticker cap; return True if the ticker is new to the whole portfolio."""
    held = {h["ticker"] for h in repo.list_holdings(user_id)}
    if ticker in held:
        return False
    if len(held) >= MAX_HOLDINGS:
        raise ApiError(409, f"You can track up to {MAX_HOLDINGS} different tickers")
    return True


def list_holdings(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    group = (event.get("queryStringParameters") or {}).get("group")
    if group is not None and group not in GROUPS:
        raise ApiError(400, f"group must be one of {', '.join(GROUPS)}")
    return _response(200, {"holdings": repo.list_holdings(_user_id(event), group)})


def put_holding(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    user_id = _user_id(event)
    group = _group(event)
    ticker = _ticker(event.get("pathParameters", {}).get("ticker"))
    body = _body(event)
    if "cost_basis" in body:  # the field's old name, still accepted
        if "avg_cost" in body:
            raise ApiError(400, "Send avg_cost only (cost_basis is its old name)")
        body["avg_cost"] = body.pop("cost_basis")
    shares = _number(body, "shares", 0.000001, 1_000_000_000)
    holding: dict[str, Any] = {
        "ticker": ticker,
        "group": group,
        "shares": shares,
        "avg_cost": _per_share(body, shares, per_share="avg_cost", total="total_cost"),
    }
    purchased_on = _purchase_date(body)
    if purchased_on:
        holding["purchased_on"] = purchased_on
    new_ticker = repo.get_holding(user_id, group, ticker) is None and _admit_ticker(repo, user_id, ticker)
    saved = repo.put_holding(user_id, holding)
    if new_ticker:
        _request_backfill(ticker)
    return _response(200, saved)


def _per_share(body: dict[str, Any], shares: float, per_share: str, total: str) -> float:
    """Accept either a per-share value or a dollar total (e.g. "amount invested") and return per share."""
    has_per_share, has_total = body.get(per_share) is not None, body.get(total) is not None
    if has_per_share == has_total:
        raise ApiError(400, f"Send either {per_share} (per share) or {total} (total dollars), not both")
    if has_per_share:
        return _number(body, per_share, 0, 10_000_000)
    return round(_number(body, total, 0, 10_000_000_000) / shares, 8)


def _purchase_date(body: dict[str, Any]) -> str | None:
    if not body.get("purchased_on"):
        return None
    try:
        purchased = date.fromisoformat(str(body["purchased_on"]))
    except ValueError as exc:
        raise ApiError(400, "purchased_on must be YYYY-MM-DD") from exc
    if purchased > datetime.now(UTC).date():
        raise ApiError(400, "purchased_on cannot be in the future")
    return purchased.isoformat()


def record_purchase(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    """Add shares bought at a price; updates the group's total shares and weighted average cost.

    new average = (old shares x old average + new shares x price) / total shares
    """
    user_id = _user_id(event)
    group = _group(event)
    ticker = _ticker(event.get("pathParameters", {}).get("ticker"))
    body = _body(event)
    shares = _number(body, "shares", 0.000001, 1_000_000_000)
    price = _per_share(body, shares, per_share="price", total="amount")
    purchased_on = _purchase_date(body)

    new_ticker = False
    for _ in range(3):  # retry if another request changed the holding between read and write
        current = repo.get_holding(user_id, group, ticker)
        if current is None:
            new_ticker = _admit_ticker(repo, user_id, ticker)
            updated: dict[str, Any] = {"ticker": ticker, "group": group, "shares": shares, "avg_cost": price}
            if purchased_on:
                updated["purchased_on"] = purchased_on
        else:
            old_shares = float(current["shares"])
            total = old_shares + shares
            average = (old_shares * float(current["avg_cost"]) + shares * price) / total
            updated = {"ticker": ticker, "group": group, "shares": round(total, 6), "avg_cost": round(average, 8)}
            dates = [d for d in (current.get("purchased_on"), purchased_on) if d]
            if dates:
                updated["purchased_on"] = min(dates)  # keep the first purchase date
        try:
            saved = repo.replace_holding_if_unchanged(user_id, updated, current)
            break
        except ConcurrentUpdateError:
            continue
    else:
        raise ApiError(409, "The holding changed while saving. Please try again.")

    if new_ticker:
        _request_backfill(ticker)
    return _response(201 if current is None else 200, {"holding": saved, "previous": current})


def delete_holding(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    group = _group(event)
    ticker = _ticker(event.get("pathParameters", {}).get("ticker"))
    repo.delete_holding(_user_id(event), group, ticker)
    return _response(204, {})


def _request_backfill(ticker: str) -> None:
    """Ask the price-sync function to load history for a newly added ticker (fire and forget)."""
    function_name = os.environ.get("PRICE_SYNC_FUNCTION_NAME")
    if not function_name:
        return
    boto3.client("lambda").invoke(
        FunctionName=function_name, InvocationType="Event", Payload=json.dumps({"tickers": [ticker]}).encode()
    )


# ---------------------------------------------------------------- rules
def list_rule_types(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    _user_id(event)
    types = {
        name: {"description": spec.description, "params": {p: {"min": lo, "max": hi} for p, (lo, hi) in spec.params.items()}}
        for name, spec in rules.RULE_TYPES.items()
    }
    return _response(200, {"rule_types": types})


def list_rules(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    user_id = _user_id(event)
    items = repo.list_rules(user_id)
    for rule in items:
        state = repo.get_rule_state(user_id, rule["rule_id"]) or {}
        rule["triggered"] = bool(state.get("triggered", False))
        rule["detail"] = state.get("detail")
    return _response(200, {"rules": items})


def create_rule(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    user_id = _user_id(event)
    try:
        rule = rules.validate_rule(_body(event))
    except rules.RuleValidationError as exc:
        raise ApiError(400, str(exc)) from exc
    if len(repo.list_rules(user_id)) >= MAX_RULES:
        raise ApiError(409, f"You can create up to {MAX_RULES} rules")
    return _response(201, repo.create_rule(user_id, rule))


def delete_rule(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    rule_id = event.get("pathParameters", {}).get("ruleId", "")
    if not re.fullmatch(r"[a-f0-9]{12}", rule_id):
        raise ApiError(400, "Invalid rule id")
    repo.delete_rule(_user_id(event), rule_id)
    return _response(204, {})


def list_alerts(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    return _response(200, {"alerts": repo.list_alerts(_user_id(event))})


# ---------------------------------------------------------------- portfolio, signals, news
def get_portfolio(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    """Summaries per person (household = everyone, mine, partner) and kind (overall, stocks, auto)."""
    user_id = _user_id(event)
    holdings = repo.list_holdings(user_id)
    since = (datetime.now(UTC).date() - timedelta(days=HISTORY_DAYS)).isoformat()
    histories = {t: repo.price_history(t, since) for t in {h["ticker"] for h in holdings}}
    latest = {t: h[-1][1] for t, h in histories.items() if h}
    previous = {t: h[-2][1] for t, h in histories.items() if len(h) > 1}
    # During market hours today's intraday quote replaces the last close, and "today" is measured against
    # that close. Tickers without a quote today count as unchanged, so the day change never mixes in
    # yesterday's move. Once the evening sync stores today's close, the quote is no longer newer.
    today = trading_day(datetime.now(UTC)).isoformat()
    live = {
        t: q
        for t, q in repo.latest_quotes().items()
        if q.get("date") == today and histories.get(t) and histories[t][-1][0] < today
    }
    if live:
        for ticker, history in histories.items():
            if history and history[-1][0] < today:
                previous[ticker] = latest[ticker]
                if ticker in live:
                    latest[ticker] = live[ticker]["last"]
    benchmark_ticker = get_config().benchmark_ticker
    benchmark = repo.price_history(benchmark_ticker, since)

    built: dict[tuple[str, ...], dict[str, Any]] = {}

    def build(subset: list[dict[str, Any]]) -> dict[str, Any]:
        # Views with the same holdings (e.g. household = mine when there's no partner) are built once.
        key = tuple(sorted(f"{h['group']}#{h['ticker']}" for h in subset))
        if key not in built:
            built[key] = _summary(subset)
        return built[key]

    def _summary(subset: list[dict[str, Any]]) -> dict[str, Any]:
        summary = analytics.summarize(subset, latest, previous)
        series = analytics.value_history(subset, histories)
        summary["benchmark"] = {"ticker": benchmark_ticker, **analytics.compare_to_benchmark(series, benchmark)}
        summary["history"] = [{"date": d, "value": v} for d, v in series]
        return summary

    def pick(person: str, kind: str) -> list[dict[str, Any]]:
        return [
            h for h in holdings if person in ("household", person_of(h["group"])) and kind in ("overall", kind_of(h["group"]))
        ]

    portfolios = {p: {k: build(pick(p, k)) for k in ("overall", *KINDS)} for p in ("household", *PEOPLE)}
    return _response(
        200,
        {
            "as_of": max((h[-1][0] for h in histories.values() if h), default=None),
            "live_as_of": max((q["timestamp"] for q in live.values()), default=None),
            "benchmark_ticker": benchmark_ticker,
            "benchmark_history": [{"date": d, "close": c} for d, c in benchmark],
            "portfolios": portfolios,
        },
    )


def get_profile(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    """Display names for the dashboard. Served behind sign-in (not in the public config.json).

    The partner page shows when a partner name is configured or the partner already has holdings,
    so a deploy without the name can't hide holdings that still count in household totals.
    """
    owner, partner = os.environ.get("OWNER_NAME", ""), os.environ.get("PARTNER_NAME", "")
    has_partner = bool(partner) or any(person_of(h["group"]) == "partner" for h in repo.list_holdings(_user_id(event)))
    return _response(200, {"owner_name": owner, "partner_name": partner, "has_partner": has_partner})


def get_signals(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    tickers = sorted({h["ticker"] for h in repo.list_holdings(_user_id(event))})
    return _response(200, {"signals": [s for t in tickers if (s := repo.get_signal(t))]})


def get_news(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    user_id = _user_id(event)
    params = event.get("queryStringParameters") or {}
    cfg = get_config()
    holdings = repo.list_holdings(user_id)
    excluded = news_off(holdings, cfg.news_excluded, cfg.partner_news)
    held = sorted({h["ticker"] for h in holdings})
    requested = params.get("ticker")
    tickers = [_ticker(requested)] if requested else [t for t in held if t not in excluded]
    if requested and tickers[0] not in held:
        raise ApiError(404, "Ticker is not in your portfolio")
    if requested and tickers[0] in excluded:  # don't show old headlines or briefs once news is off
        return _response(200, {"brief": None, "articles": [], "news_excluded": sorted(excluded)})
    since = (datetime.now(UTC) - timedelta(days=7)).isoformat(timespec="seconds")
    articles = []
    for ticker in tickers:
        for article in repo.recent_news(ticker, since, limit=20):
            article["ticker"] = ticker
            article["sentiment"] = sentiment_label(article.get("score"))
            articles.append(article)
    articles.sort(key=lambda a: a["published_at"], reverse=True)
    brief = repo.latest_brief(tickers[0] if requested else "ALL")
    return _response(200, {"brief": brief, "articles": articles[:60], "news_excluded": sorted(excluded)})


Route = Callable[[dict[str, Any], Repository], dict[str, Any]]

ROUTES: dict[str, Route] = {
    "GET /holdings": list_holdings,
    "PUT /groups/{group}/holdings/{ticker}": put_holding,
    "POST /groups/{group}/holdings/{ticker}/purchases": record_purchase,
    "DELETE /groups/{group}/holdings/{ticker}": delete_holding,
    "GET /rule-types": list_rule_types,
    "GET /rules": list_rules,
    "POST /rules": create_rule,
    "DELETE /rules/{ruleId}": delete_rule,
    "GET /alerts": list_alerts,
    "GET /portfolio": get_portfolio,
    "GET /profile": get_profile,
    "GET /signals": get_signals,
    "GET /news": get_news,
}


def handler(event: dict[str, Any], context: Any, repo: Repository | None = None) -> dict[str, Any]:
    route_key = event.get("routeKey", "")
    route = ROUTES.get(route_key)
    if route is None:
        return _response(404, {"message": "Not found"})
    try:
        return route(event, repo or Repository())
    except ApiError as exc:
        return _response(exc.status, {"message": exc.message})
    except Exception:
        logger.exception("Unhandled error")
        log(logger, "request_failed", route=route_key)
        return _response(500, {"message": "Internal error"})
