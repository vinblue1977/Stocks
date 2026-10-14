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

from portfolio import analytics, buckets, checkup, ledger, rules, signals
from portfolio.config import get_config
from portfolio.db import GROUPS, KINDS, PEOPLE, ConcurrentUpdateError, Repository, kind_of, person_of
from portfolio.llm import sentiment_label
from portfolio.logs import get_logger, log
from portfolio.market import market_status, trading_day
from portfolio.news_policy import news_off

logger = get_logger(__name__)

MAX_BODY_BYTES = 10_000
MAX_HOLDINGS = 40  # unique tickers across all groups; keeps the price sync under Tiingo's free 50 requests/hour
MAX_RULES = 50
MAX_WATCHLIST = 10
MAX_TRACKED = 48  # unique tickers across holdings and the watchlist; stays under Tiingo's free 50 requests/hour
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
    """Enforce the ticker caps; return True if the ticker is new to the tracked set (needs a price backfill)."""
    held = repo.held_tickers(user_id)
    if ticker in held:
        return False
    if len(held) >= MAX_HOLDINGS:
        raise ApiError(409, f"You can track up to {MAX_HOLDINGS} different tickers")
    watched = repo.watched_tickers(user_id)
    if ticker in watched:
        return False
    if len(held | watched) >= MAX_TRACKED:
        raise ApiError(409, f"You can follow up to {MAX_TRACKED} different tickers across holdings and the watchlist")
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
    if kind_of(group) == "stocks" and repo.list_txns(user_id, group, ticker):
        raise ApiError(409, f"{ticker} has recorded transactions here; record a buy or sale instead of setting totals")
    body = _body(event)
    if body.get("cost_basis") is not None:  # the field's old name, still accepted
        if body.get("avg_cost") is not None:
            raise ApiError(400, "Send avg_cost only (cost_basis is its old name)")
        body["avg_cost"] = body["cost_basis"]
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
    if kind_of(group) == "stocks":  # Stock Holdings keep each buy as a transaction
        previous = repo.get_holding(user_id, group, ticker)
        txn = {"type": "buy", "date": purchased_on or _today(), "shares": shares, "price": price}
        saved = _record_txn(repo, user_id, group, ticker, txn)
        return _response(201 if previous is None else 200, {"holding": saved, "previous": previous})

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
    user_id = _user_id(event)
    group = _group(event)
    ticker = _ticker(event.get("pathParameters", {}).get("ticker"))
    if kind_of(group) == "stocks":
        txns = repo.list_txns(user_id, group, ticker)
        if any(t["type"] == "sell" for t in txns):
            raise ApiError(409, "This holding has recorded sales; delete its transactions instead so realized gains stay correct")
        for txn in txns:
            repo.delete_txn(user_id, group, ticker, txn)
    repo.delete_holding(user_id, group, ticker)
    _prune_rules(repo, user_id, ticker)
    return _response(204, {})


# ---------------------------------------------------------------- transactions (Stock Holdings)
# Each buy and sale is kept; the holding item is the replayed summary (shares, FIFO average cost, first date),
# so everything that reads holdings keeps working. A position with no transactions yet is one implicit
# "opening" entry built from its totals, saved on the first change.
OPENING_ID = "opening"


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _stock_group(event: dict[str, Any]) -> str:
    group = _group(event)
    if kind_of(group) != "stocks":
        raise ApiError(400, "Transactions are tracked for Stock Holdings only")
    return group


def _ledger_txns(repo: Repository, user_id: str, group: str, ticker: str) -> tuple[list[dict[str, Any]], bool]:
    """(transactions, whether the only one is the implicit opening entry from the holding's totals)."""
    txns = repo.list_txns(user_id, group, ticker)
    if txns:
        return txns, False
    holding = repo.get_holding(user_id, group, ticker)
    if not holding:
        return [], False
    opening = {
        "txn_id": OPENING_ID,
        "type": "opening",
        "date": holding.get("purchased_on"),
        "shares": holding["shares"],
        "price": holding["avg_cost"],
    }
    return [opening], True


def _replay(txns: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        return ledger.replay(txns)
    except ledger.LedgerError as exc:
        raise ApiError(400, str(exc)) from exc


def _write_summary(repo: Repository, user_id: str, group: str, ticker: str, result: dict[str, Any]) -> dict[str, Any] | None:
    """Rewrite the holding from the replayed ledger; selling everything removes it (its transactions stay)."""
    if result["shares"] <= ledger.EPSILON:
        repo.delete_holding(user_id, group, ticker)
        _prune_rules(repo, user_id, ticker)
        return None
    holding: dict[str, Any] = {"ticker": ticker, "group": group, "shares": result["shares"], "avg_cost": result["avg_cost"]}
    if result["first_date"]:
        holding["purchased_on"] = result["first_date"]
    return repo.put_holding(user_id, holding)


def _admit_if_returning(repo: Repository, user_id: str, group: str, ticker: str, result: dict[str, Any]) -> bool:
    """A position coming back (first buy, re-buy after selling out, or an undone sale) counts against the caps."""
    if result["shares"] <= ledger.EPSILON or repo.get_holding(user_id, group, ticker):
        return False
    return _admit_ticker(repo, user_id, ticker)


def _resync(repo: Repository, user_id: str, group: str, ticker: str) -> dict[str, Any] | None:
    """Re-read the saved transactions and rewrite the summary, so trades saved at the same moment both count."""
    return _write_summary(repo, user_id, group, ticker, _replay(repo.list_txns(user_id, group, ticker)))


def _record_txn(repo: Repository, user_id: str, group: str, ticker: str, txn: dict[str, Any]) -> dict[str, Any] | None:
    txns, implicit = _ledger_txns(repo, user_id, group, ticker)
    txn = {"txn_id": repo.new_txn_id(), **txn}
    if txn["type"] == "sell" and not txns:
        raise ApiError(400, f"You don't hold {ticker} in this group")
    result = _replay([*txns, txn])  # validates before anything is written
    new_ticker = _admit_if_returning(repo, user_id, group, ticker, result)
    if implicit:
        repo.put_txn(user_id, group, ticker, txns[0])
    repo.put_txn(user_id, group, ticker, txn)
    saved = _resync(repo, user_id, group, ticker)
    if new_ticker:
        _request_backfill(ticker)
    return saved


def list_transactions(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    user_id = _user_id(event)
    group = _stock_group(event)
    ticker = _ticker((event.get("pathParameters") or {}).get("ticker"))
    txns, implicit = _ledger_txns(repo, user_id, group, ticker)
    result = _replay(txns)
    ordered = sorted(txns, key=lambda t: (t.get("date") or "", t["txn_id"]), reverse=True)
    return _response(
        200, {"transactions": ordered, "lots": result["lots"], "realized": result["realized"], "implicit_opening": implicit}
    )


def add_transaction(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    """{type: buy|sell, date, shares, price | amount}"""
    user_id = _user_id(event)
    group = _stock_group(event)
    ticker = _ticker((event.get("pathParameters") or {}).get("ticker"))
    body = _body(event)
    kind = body.get("type")
    if kind not in ("buy", "sell"):
        raise ApiError(400, "type must be buy or sell")
    shares = _number(body, "shares", 0.000001, 1_000_000_000)
    price = _per_share(body, shares, per_share="price", total="amount")
    when = _purchase_date({"purchased_on": body.get("date") or _today()})
    saved = _record_txn(repo, user_id, group, ticker, {"type": kind, "date": when, "shares": shares, "price": price})
    return _response(201, {"holding": saved})


def _find_txn(repo: Repository, event: dict[str, Any]) -> tuple[str, str, str, list[dict[str, Any]], bool, dict[str, Any]]:
    user_id = _user_id(event)
    group = _stock_group(event)
    params = event.get("pathParameters") or {}
    ticker = _ticker(params.get("ticker"))
    txn_id = str(params.get("txnId", ""))
    txns, implicit = _ledger_txns(repo, user_id, group, ticker)
    match = next((t for t in txns if t["txn_id"] == txn_id), None)
    if match is None:
        raise ApiError(404, "Transaction not found")
    return user_id, group, ticker, txns, implicit, match


def update_transaction(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    """Change a transaction's date, e.g. set when an opening position was bought (for long/short term)."""
    user_id, group, ticker, txns, implicit, match = _find_txn(repo, event)
    when = _purchase_date({"purchased_on": _body(event).get("date")})
    if not when:
        raise ApiError(400, "date is required (YYYY-MM-DD)")
    changed = {**match, "date": when}
    _replay([changed if t is match else t for t in txns])  # validates, e.g. a sale can't come before its shares
    repo.put_txn(user_id, group, ticker, changed)  # write the new one first: a failure leaves a duplicate, not a gap
    if not implicit and changed["date"] != match.get("date"):
        repo.delete_txn(user_id, group, ticker, match)  # the date is part of the key
    return _response(200, {"holding": _resync(repo, user_id, group, ticker)})


def delete_transaction(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    user_id, group, ticker, txns, implicit, match = _find_txn(repo, event)
    if implicit:
        raise ApiError(400, "This opening position comes from your totals; record a sale or remove the holding instead")
    result = _replay([t for t in txns if t is not match])  # e.g. a later sale would need these shares
    returning = _admit_if_returning(repo, user_id, group, ticker, result)  # undoing a full sale brings it back
    repo.delete_txn(user_id, group, ticker, match)
    _resync(repo, user_id, group, ticker)
    if returning:
        _request_backfill(ticker)
    return _response(204, {})


def get_realized(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    """Realized gains from sales in Stock Holdings (both people): each sale piece, plus totals per year."""
    user_id = _user_id(event)
    by_position: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for txn in repo.list_txns(user_id):
        by_position.setdefault((txn["group"], txn["ticker"]), []).append(txn)
    sales, unreadable = [], []
    for (group, ticker), txns in by_position.items():
        try:
            pieces = ledger.replay(txns)["realized"]
        except ledger.LedgerError:
            unreadable.append(ticker)  # e.g. a sale larger than the shares then; shown, not a page failure
            continue
        sales += [{**piece, "ticker": ticker, "group": group} for piece in pieces]
    sales.sort(key=lambda s: s["sold"], reverse=True)
    years: dict[str, dict[str, float]] = {}
    for s in sales:
        year = years.setdefault(s["sold"][:4], {"total": 0.0, "long": 0.0, "short": 0.0, "unknown": 0.0})
        term = "unknown" if s["long_term"] is None else "long" if s["long_term"] else "short"
        year["total"] = round(year["total"] + s["gain"], 2)
        year[term] = round(year[term] + s["gain"], 2)
    return _response(200, {"sales": sales, "years": years, "unreadable": sorted(unreadable)})


def _prune_rules(repo: Repository, user_id: str, ticker: str) -> None:
    """Delete rules that can no longer be checked: any rule once the ticker is neither held nor watched,
    and portfolio-share rules once it isn't held."""
    if ticker in repo.held_tickers(user_id):
        return
    watched = ticker in repo.watched_tickers(user_id)
    for rule in repo.list_rules(user_id):
        if rule["ticker"] == ticker and (not watched or rule["type"] == "weight_above"):
            repo.delete_rule(user_id, rule["rule_id"])


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
    held = repo.held_tickers(user_id)
    if rule["ticker"] not in held | repo.watched_tickers(user_id):
        raise ApiError(400, f"Add {rule['ticker']} to your holdings or watchlist first")
    if rule["type"] == "weight_above" and rule["ticker"] not in held:
        raise ApiError(400, "A portfolio-share rule needs a ticker you hold")
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
        summary["return_1y"] = analytics.trailing_return(series)
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
            "market": market_status(datetime.now(UTC)),
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
    user_id = _user_id(event)
    tickers = sorted(repo.held_tickers(user_id) | repo.watched_tickers(user_id))
    return _response(200, {"signals": [s for t in tickers if (s := repo.get_signal(t))]})


def get_news(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    user_id = _user_id(event)
    params = event.get("queryStringParameters") or {}
    cfg = get_config()
    holdings = repo.list_holdings(user_id)
    watched = repo.watched_tickers(user_id)
    excluded = news_off(holdings, cfg.news_excluded, cfg.partner_news, watched)
    held = sorted({h["ticker"] for h in holdings})
    requested = params.get("ticker")
    tickers = [_ticker(requested)] if requested else [t for t in held if t not in excluded]
    if requested and tickers[0] not in held and tickers[0] not in watched:
        raise ApiError(404, "Ticker is not in your portfolio or watchlist")
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


# ---------------------------------------------------------------- earnings
EARNINGS_WINDOW_DAYS = 60


def _upcoming(record: dict[str, Any] | None, today: str) -> dict[str, Any] | None:
    """A stored report date only while it's today or later (a failed nightly refresh keeps the old one)."""
    return record if record and record.get("date", "") >= today else None


def get_earnings(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    """Upcoming earnings for held and watched tickers (funds have none), soonest first, with where each is held."""
    user_id = _user_id(event)
    where: dict[str, list[str]] = {}
    for h in repo.list_holdings(user_id):
        where.setdefault(h["ticker"], []).append(h["group"])
    for ticker in repo.watched_tickers(user_id):
        where.setdefault(ticker, []).append("watchlist")
    today = trading_day(datetime.now(UTC))
    end = (today + timedelta(days=EARNINGS_WINDOW_DAYS)).isoformat()
    upcoming = []
    for ticker, places in where.items():
        record = repo.get_earnings(ticker)
        if record and today.isoformat() <= record["date"] <= end:
            upcoming.append(
                {"ticker": ticker, "where": sorted(set(places)), **{k: record.get(k) for k in ("date", "hour", "eps_estimate")}}
            )
    return _response(200, {"earnings": sorted(upcoming, key=lambda e: (e["date"], e["ticker"]))})


# ---------------------------------------------------------------- watchlist
def list_watchlist(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    """Watched tickers with everything the page shows: signals, today's live price and the news summary."""
    user_id = _user_id(event)
    holdings = repo.list_holdings(user_id)
    held = {h["ticker"] for h in holdings}
    watchlist = sorted(repo.list_watchlist(user_id), key=lambda w: w["ticker"])
    cfg = get_config()
    no_news = news_off(holdings, cfg.news_excluded, cfg.partner_news, {w["ticker"] for w in watchlist})
    now = datetime.now(UTC)
    today = trading_day(now).isoformat()
    quotes = repo.latest_quotes()
    since = (now.date() - timedelta(days=signals.HISTORY_DAYS)).isoformat()
    items = []
    for watch in watchlist:
        ticker = watch["ticker"]
        # The stored snapshot can be missing (new ticker) or stale (re-added after a while unwatched);
        # then compute one from the prices until the evening run stores a fresh one.
        signal = repo.get_signal(ticker)
        latest_date = repo.latest_price_date(ticker)
        if latest_date and (signal is None or signal["as_of"] < latest_date):
            signal = signals.compute_signals(ticker, repo.price_history(ticker, since))
        quote = quotes.get(ticker)
        live = quote and signal and quote.get("date") == today and signal["as_of"] < today
        brief = None if ticker in no_news else repo.latest_brief(ticker)
        items.append(
            {
                **watch,
                "held": ticker in held,
                "signal": signal,
                "live": {"last": quote["last"], "timestamp": quote.get("timestamp")} if live else None,
                "brief": {k: brief.get(k) for k in ("summary", "sentiment_7d", "label", "headline_count", "generated_at")}
                if brief
                else None,
                "news_off": ticker in no_news,
                "earnings": _upcoming(repo.get_earnings(ticker), today),
            }
        )
    return _response(200, {"watchlist": items, "max": MAX_WATCHLIST})


def put_watch(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    user_id = _user_id(event)
    ticker = _ticker((event.get("pathParameters") or {}).get("ticker"))
    watched = repo.watched_tickers(user_id)
    if ticker in watched:
        return _response(200, {"ticker": ticker})
    if len(watched) >= MAX_WATCHLIST:
        raise ApiError(409, f"You can watch up to {MAX_WATCHLIST} tickers")
    held = repo.held_tickers(user_id)
    new_ticker = ticker not in held
    if new_ticker and len(held | watched) >= MAX_TRACKED:
        raise ApiError(409, f"You can follow up to {MAX_TRACKED} different tickers across holdings and the watchlist")
    saved = repo.put_watch(user_id, ticker)
    if new_ticker:
        _request_backfill(ticker)
    return _response(201, saved)


def delete_watch(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    """Stop watching; a ticker that isn't held also loses its alert rules, which would have no data."""
    user_id = _user_id(event)
    ticker = _ticker((event.get("pathParameters") or {}).get("ticker"))
    repo.delete_watch(user_id, ticker)
    _prune_rules(repo, user_id, ticker)
    return _response(204, {})


# ---------------------------------------------------------------- checkup
def get_checkup(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    """Mix by area vs. the user's targets, and risk vs. the market, for the household and each person."""
    user_id = _user_id(event)
    targets = repo.get_targets(user_id)
    built = checkup.build(repo, repo.list_holdings(user_id), targets)
    overrides = (targets or {}).get("overrides")
    return _response(
        200,
        {
            "as_of": built["as_of"],
            "areas": buckets.AREAS,
            "shortlist": buckets.SHORTLIST,
            "assigned": {t: buckets.area_of(t, overrides) for t in built["tickers"]},
            "targets": targets,
            # The 1-year history is for the PDF report; the page doesn't draw it.
            "views": {k: {f: v for f, v in view.items() if f != "history_1y"} for k, view in built["views"].items()},
            "summary": repo.latest_checkup_summary(user_id),
        },
    )


def get_targets(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    return _response(200, {"targets": repo.get_targets(_user_id(event)), "areas": buckets.AREAS, "shortlist": buckets.SHORTLIST})


def put_targets(event: dict[str, Any], repo: Repository) -> dict[str, Any]:
    """{areas: {us: {pct, fund}, ...}, sector_ceiling, overrides: {ticker: area}}; percentages add up to 100."""
    body = _body(event)
    raw_areas = body.get("areas")
    if not isinstance(raw_areas, dict) or set(raw_areas) - set(buckets.AREAS):
        raise ApiError(400, f"areas must use: {', '.join(buckets.AREAS)}")
    areas: dict[str, dict[str, Any]] = {}
    for key in buckets.AREAS:
        entry = raw_areas.get(key) or {}
        if not isinstance(entry, dict):
            raise ApiError(400, f"areas.{key} must be an object")
        pct = _number({"pct": entry.get("pct", 0)}, "pct", 0, 100)
        if pct != int(pct):
            raise ApiError(400, "Target percentages must be whole numbers")
        fund = str(entry.get("fund") or "").upper().strip()
        if fund and not rules.TICKER_PATTERN.match(fund):
            raise ApiError(400, f"areas.{key}.fund must look like VTI or BRK.B")
        areas[key] = {"pct": int(pct), "fund": fund}
    if sum(a["pct"] for a in areas.values()) != 100:
        raise ApiError(400, "Target percentages must add up to 100")
    ceiling = _number({"sector_ceiling": body.get("sector_ceiling", 100)}, "sector_ceiling", 0, 100)
    if ceiling != int(ceiling):
        raise ApiError(400, "sector_ceiling must be a whole number")
    ceiling = int(ceiling)
    raw_overrides = body.get("overrides") or {}
    if not isinstance(raw_overrides, dict) or len(raw_overrides) > MAX_HOLDINGS + MAX_WATCHLIST:
        raise ApiError(400, "overrides must be an object of ticker: area")
    overrides = {}
    for ticker, area in raw_overrides.items():
        if area not in buckets.AREAS:
            raise ApiError(400, f"overrides.{ticker} must be one of: {', '.join(buckets.AREAS)}")
        overrides[_ticker(ticker)] = area
    saved = repo.put_targets(_user_id(event), {"areas": areas, "sector_ceiling": ceiling, "overrides": overrides})
    return _response(200, {"targets": saved})


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
    "GET /watchlist": list_watchlist,
    "PUT /watchlist/{ticker}": put_watch,
    "DELETE /watchlist/{ticker}": delete_watch,
    "GET /groups/{group}/holdings/{ticker}/transactions": list_transactions,
    "POST /groups/{group}/holdings/{ticker}/transactions": add_transaction,
    "PUT /groups/{group}/holdings/{ticker}/transactions/{txnId}": update_transaction,
    "DELETE /groups/{group}/holdings/{ticker}/transactions/{txnId}": delete_transaction,
    "GET /realized": get_realized,
    "GET /earnings": get_earnings,
    "GET /checkup": get_checkup,
    "GET /targets": get_targets,
    "PUT /targets": put_targets,
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
