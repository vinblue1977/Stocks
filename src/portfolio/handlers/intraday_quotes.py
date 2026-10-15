"""Stores intraday prices for every tracked ticker while the US market is open.

Scheduled every 15 minutes on weekdays across both EDT and EST market hours; runs outside
9:30-16:00 New York time return without calling Tiingo. One request covers all tickers.
The dashboard shows these until the evening pipeline stores the day's official close.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from portfolio.config import get_config
from portfolio.db import Repository
from portfolio.logs import get_logger, log
from portfolio.market import market_open, trading_day
from portfolio.providers.prices import TiingoPriceProvider
from portfolio.secrets import get_secret

logger = get_logger(__name__)


def handler(
    event: dict[str, Any] | None,
    context: Any,
    repo: Repository | None = None,
    provider: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    # On market holidays the quotes are the previous session's, so the dashboard ignores them.
    # Decide by the run's scheduled time (EventBridge "time"): runs start ~40 s late, which would
    # otherwise push the 4:00 PM run past the close.
    now = now or _scheduled_time(event) or datetime.now(UTC)
    if not market_open(now):
        return {"skipped": "market closed"}
    repo = repo or Repository()
    tickers = repo.tracked_tickers()
    if not tickers:
        return {"tickers": 0}
    provider = provider or TiingoPriceProvider(get_secret(get_config().price_api_key_param))
    quotes = provider.quotes(tickers)
    # Keep today's earlier quote for a ticker this response skipped, rather than dropping it mid-session.
    today = trading_day(now).isoformat()
    kept = {t: q for t, q in repo.latest_quotes().items() if t in tickers and t not in quotes and q.get("date") == today}
    if quotes or kept:
        repo.put_quotes({**kept, **quotes})
    missing = sorted(set(tickers) - set(quotes))
    log(logger, "intraday_quotes_complete", quoted=len(quotes), missing=missing)
    return {"tickers": len(tickers), "quoted": len(quotes), "missing": missing}


def _scheduled_time(event: dict[str, Any] | None) -> datetime | None:
    try:
        return datetime.fromisoformat(str((event or {})["time"]).replace("Z", "+00:00"))
    except KeyError, ValueError:
        return None
