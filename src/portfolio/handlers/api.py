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
from portfolio.db import Repository
from portfolio.llm import sentiment_label
from portfolio.logs import get_logger, log

logger = get_logger(__name__)

MAX_BODY_BYTES = 10_000
MAX_HOLDINGS = 40  # keeps the price sync under Tiingo's free limit of 50 requests/hour
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
def list_holdings(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    return _response(200, {"holdings": repo.list_holdings(_user_id(event))})


def put_holding(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    user_id = _user_id(event)
    ticker = _ticker(event.get("pathParameters", {}).get("ticker"))
    body = _body(event)
    holding: dict[str, Any] = {
        "ticker": ticker,
        "shares": _number(body, "shares", 0.000001, 1_000_000_000),
        "cost_basis": _number(body, "cost_basis", 0, 10_000_000),
    }
    if body.get("purchased_on"):
        try:
            purchased = date.fromisoformat(str(body["purchased_on"]))
        except ValueError as exc:
            raise ApiError(400, "purchased_on must be YYYY-MM-DD") from exc
        if purchased > datetime.now(UTC).date():
            raise ApiError(400, "purchased_on cannot be in the future")
        holding["purchased_on"] = purchased.isoformat()
    existing = {h["ticker"] for h in repo.list_holdings(user_id)}
    if ticker not in existing and len(existing) >= MAX_HOLDINGS:
        raise ApiError(409, f"You can track up to {MAX_HOLDINGS} holdings")
    saved = repo.put_holding(user_id, holding)
    if ticker not in existing:
        _request_backfill(ticker)
    return _response(200, saved)


def delete_holding(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    ticker = _ticker(event.get("pathParameters", {}).get("ticker"))
    repo.delete_holding(_user_id(event), ticker)
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
    user_id = _user_id(event)
    holdings = repo.list_holdings(user_id)
    since = (datetime.now(UTC).date() - timedelta(days=HISTORY_DAYS)).isoformat()
    histories = {h["ticker"]: repo.price_history(h["ticker"], since) for h in holdings}
    latest = {t: h[-1][1] for t, h in histories.items() if h}
    previous = {t: h[-2][1] for t, h in histories.items() if len(h) > 1}
    summary = analytics.summarize(holdings, latest, previous)
    series = analytics.value_history(holdings, histories)
    benchmark_ticker = get_config().benchmark_ticker
    benchmark = repo.price_history(benchmark_ticker, since)
    summary["benchmark"] = {"ticker": benchmark_ticker, **analytics.compare_to_benchmark(series, benchmark)}
    summary["history"] = [{"date": d, "value": v} for d, v in series]
    summary["benchmark_history"] = [{"date": d, "close": c} for d, c in benchmark]
    summary["as_of"] = max((h[-1][0] for h in histories.values() if h), default=None)
    return _response(200, summary)


def get_signals(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    tickers = sorted({h["ticker"] for h in repo.list_holdings(_user_id(event))})
    return _response(200, {"signals": [s for t in tickers if (s := repo.get_signal(t))]})


def get_news(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    user_id = _user_id(event)
    params = event.get("queryStringParameters") or {}
    held = sorted({h["ticker"] for h in repo.list_holdings(user_id)})
    requested = params.get("ticker")
    tickers = [_ticker(requested)] if requested else held
    if requested and tickers[0] not in held:
        raise ApiError(404, "Ticker is not in your portfolio")
    since = (datetime.now(UTC) - timedelta(days=7)).isoformat(timespec="seconds")
    articles = []
    for ticker in tickers:
        for article in repo.recent_news(ticker, since, limit=20):
            article["ticker"] = ticker
            article["sentiment"] = sentiment_label(article.get("score"))
            articles.append(article)
    articles.sort(key=lambda a: a["published_at"], reverse=True)
    brief = repo.latest_brief(tickers[0] if requested else "ALL")
    return _response(200, {"brief": brief, "articles": articles[:60]})


Route = Callable[[dict[str, Any], Repository], dict[str, Any]]

ROUTES: dict[str, Route] = {
    "GET /holdings": list_holdings,
    "PUT /holdings/{ticker}": put_holding,
    "DELETE /holdings/{ticker}": delete_holding,
    "GET /rule-types": list_rule_types,
    "GET /rules": list_rules,
    "POST /rules": create_rule,
    "DELETE /rules/{ruleId}": delete_rule,
    "GET /alerts": list_alerts,
    "GET /portfolio": get_portfolio,
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
