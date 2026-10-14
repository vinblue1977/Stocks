"""Prices from Tiingo: daily end-of-day closes, plus intraday IEX quotes during market hours.

Free tier is plenty: one end-of-day request per ticker each weekday, and one quote request
(all tickers at once) every 15 minutes while the market is open.

Docs: https://www.tiingo.com/documentation/end-of-day, https://www.tiingo.com/documentation/iex
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Protocol

from portfolio.http import get_json


class PriceProvider(Protocol):
    def daily_prices(self, ticker: str, start_date: str) -> list[dict[str, Any]]: ...


class TiingoPriceProvider:
    BASE_URL = "https://api.tiingo.com/tiingo/daily"
    IEX_URL = "https://api.tiingo.com/iex/"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def daily_prices(self, ticker: str, start_date: str) -> list[dict[str, Any]]:
        """Rows of {date, close, volume} oldest first. `close` is split/dividend adjusted."""
        raw = get_json(
            f"{self.BASE_URL}/{ticker.lower()}/prices",
            params={"startDate": start_date, "resampleFreq": "daily"},
            headers={"Authorization": f"Token {self._api_key}"},
        )
        return parse_tiingo(raw)

    def quotes(self, tickers: list[str]) -> dict[str, dict[str, Any]]:
        """Latest intraday price per ticker, from one request for all tickers."""
        raw = get_json(
            self.IEX_URL,
            params={"tickers": ",".join(t.lower() for t in tickers)},
            headers={"Authorization": f"Token {self._api_key}"},
        )
        return parse_iex(raw)


def parse_iex(raw: Any) -> dict[str, dict[str, Any]]:
    """{TICKER: {last, timestamp, date}}. `date` is the exchange-local trading day of the quote."""
    quotes = {}
    for entry in raw or []:
        price = entry.get("tngoLast") or entry.get("last")
        ticker, timestamp = entry.get("ticker"), entry.get("timestamp")
        if not price or not ticker or not timestamp:
            continue
        # Tiingo timestamps carry the New York offset, so the first 10 chars are the trading day.
        # Fractional seconds (nanoseconds) are dropped so every client can parse the stored UTC time.
        try:
            moment = datetime.fromisoformat(re.sub(r"\.\d+", "", str(timestamp)))
        except ValueError:
            continue
        if moment.tzinfo is None:
            continue  # without an offset the trading day and time are ambiguous
        moment = moment.astimezone(UTC)
        quotes[str(ticker).upper()] = {
            "last": float(price),
            "timestamp": moment.isoformat(timespec="seconds"),
            "date": timestamp[:10],
        }
    return quotes


def parse_tiingo(raw: Any) -> list[dict[str, Any]]:
    rows = []
    for entry in raw or []:
        close = entry.get("adjClose", entry.get("close"))
        if close is None or not entry.get("date"):
            continue
        rows.append(
            {
                "date": entry["date"][:10],
                "close": float(close),
                "raw_close": float(entry.get("close", close)),
                "volume": int(entry.get("adjVolume", entry.get("volume", 0)) or 0),
                "div_cash": float(entry.get("divCash") or 0),  # dividend per share paid that day (ex-date)
                "split_factor": float(entry.get("splitFactor") or 1),
            }
        )
    rows.sort(key=lambda r: r["date"])
    return rows
