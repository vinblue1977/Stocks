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
    last = repo.latest_price_date(ticker)
    if last:
        start = date.fromisoformat(last) + timedelta(days=1)
    else:
        start = today - timedelta(days=365 * get_config().history_years)
    if start > today:
        return 0
    rows = provider.daily_prices(ticker, start.isoformat())
    return repo.put_prices(ticker, rows)


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
