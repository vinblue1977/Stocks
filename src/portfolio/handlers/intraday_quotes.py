"""Stores intraday prices for every tracked ticker while the US market is open.

Scheduled every 15 minutes on weekdays across both EDT and EST market hours; runs outside
9:30-16:05 New York time return without calling Tiingo. One request covers all tickers.
The dashboard shows these until the evening pipeline stores the day's official close.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from portfolio.config import get_config
from portfolio.db import Repository
from portfolio.logs import get_logger, log
from portfolio.providers.prices import TiingoPriceProvider
from portfolio.rules import TICKER_PATTERN
from portfolio.secrets import get_secret

logger = get_logger(__name__)

NEW_YORK = ZoneInfo("America/New_York")
MARKET_OPEN, MARKET_CLOSE = time(9, 30), time(16, 5)  # a few minutes past the close to catch the final print


def market_open(now: datetime) -> bool:
    local = now.astimezone(NEW_YORK)
    return local.weekday() < 5 and MARKET_OPEN <= local.time() <= MARKET_CLOSE


def handler(
    event: dict[str, Any] | None,
    context: Any,
    repo: Repository | None = None,
    provider: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    # On market holidays the quotes are the previous session's, so the dashboard ignores them.
    if not market_open(now or datetime.now(UTC)):
        return {"skipped": "market closed"}
    repo = repo or Repository()
    tickers = [t for t in repo.tracked_tickers() if TICKER_PATTERN.match(t)]
    if not tickers:
        return {"tickers": 0}
    provider = provider or TiingoPriceProvider(get_secret(get_config().price_api_key_param))
    quotes = provider.quotes(tickers)
    if quotes:
        repo.put_quotes(quotes)
    missing = sorted(set(tickers) - set(quotes))
    log(logger, "intraday_quotes_complete", quoted=len(quotes), missing=missing)
    return {"tickers": len(tickers), "quoted": len(quotes), "missing": missing}
