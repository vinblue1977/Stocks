"""Loads daily closing prices for every tracked ticker.

Runs as the first step of the weekday pipeline, and is also invoked asynchronously by the
API with {"tickers": ["XYZ"]} to backfill history when a new holding is added.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from portfolio.config import get_config
from portfolio.db import Repository
from portfolio.logs import get_logger, log
from portfolio.providers.prices import PriceProvider, TiingoPriceProvider
from portfolio.rules import TICKER_PATTERN
from portfolio.secrets import get_secret

logger = get_logger(__name__)


def sync_ticker(ticker: str, repo: Repository, provider: PriceProvider, today: date) -> int:
    """Load the full history once (until marked complete), then only the days since the last stored close.

    If a backfill is interrupted, the marker is missing and the next run reloads the full history,
    so a partial load can't leave a permanent gap.

    Stored closes are dividend- and split-adjusted as of when they were loaded. When a new day brings a
    dividend or split, the whole history is reloaded so earlier closes reflect it and returns stay total returns.
    """
    last = repo.latest_price_date(ticker)
    if last and repo.backfill_complete(ticker):
        start = date.fromisoformat(last) + timedelta(days=1)
        if start > today:
            return 0
        rows = provider.daily_prices(ticker, start.isoformat())
        if not any(r.get("div_cash") or r.get("split_factor", 1) != 1 for r in rows):
            return repo.put_prices(ticker, rows)
        log(logger, "price_history_readjusted", ticker=ticker)

    rows = provider.daily_prices(ticker, (today - timedelta(days=365 * get_config().history_years)).isoformat())
    written = repo.put_prices(ticker, rows)
    repo.mark_backfill_complete(ticker, min((r["date"] for r in rows), default=None))
    return written


def handler(
    event: dict[str, Any] | None,
    context: Any,
    repo: Repository | None = None,
    provider: PriceProvider | None = None,
) -> dict[str, Any]:
    repo = repo or Repository()
    provider = provider or TiingoPriceProvider(get_secret(get_config().price_api_key_param))
    requested = (event or {}).get("tickers")
    tickers = [t for t in requested if TICKER_PATTERN.match(str(t))] if requested else repo.tracked_tickers()
    today = datetime.now(UTC).date()

    updated: dict[str, int] = {}
    failed: list[str] = []
    for ticker in tickers:
        try:
            updated[ticker] = sync_ticker(ticker, repo, provider, today)
        except Exception:
            logger.exception("price sync failed")
            failed.append(ticker)
    log(logger, "price_sync_complete", updated=updated, failed=failed)
    if tickers and len(failed) == len(tickers):
        raise RuntimeError(f"Price sync failed for every ticker: {failed}")
    return {"tickers": tickers, "rows_written": sum(updated.values()), "failed": failed}
