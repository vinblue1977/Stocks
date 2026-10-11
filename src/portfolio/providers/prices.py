"""Daily end-of-day prices from Tiingo (free tier: plenty for one sync per weekday).

Docs: https://www.tiingo.com/documentation/end-of-day
"""

from __future__ import annotations

from typing import Any, Protocol

from portfolio.http import get_json


class PriceProvider(Protocol):
    def daily_prices(self, ticker: str, start_date: str) -> list[dict[str, Any]]: ...


class TiingoPriceProvider:
    BASE_URL = "https://api.tiingo.com/tiingo/daily"

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
            }
        )
    rows.sort(key=lambda r: r["date"])
    return rows
