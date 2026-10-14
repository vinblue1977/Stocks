"""Company and fund news from Finnhub (free tier, 60 calls/minute).

Docs: https://finnhub.io/docs/api/company-news
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any, Protocol

from portfolio.http import get_json


class NewsProvider(Protocol):
    def company_news(self, ticker: str, from_date: str, to_date: str) -> list[dict[str, Any]]: ...


class FinnhubNewsProvider:
    BASE_URL = "https://finnhub.io/api/v1/company-news"
    EARNINGS_URL = "https://finnhub.io/api/v1/calendar/earnings"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def company_news(self, ticker: str, from_date: str, to_date: str) -> list[dict[str, Any]]:
        raw = get_json(
            self.BASE_URL,
            params={"symbol": ticker, "from": from_date, "to": to_date},
            headers={"X-Finnhub-Token": self._api_key},
        )
        return parse_finnhub(raw)

    def next_earnings(self, ticker: str, from_date: str, to_date: str) -> dict[str, Any] | None:
        """The next scheduled earnings report in the window, or None (funds don't report)."""
        raw = get_json(
            self.EARNINGS_URL,
            params={"symbol": ticker, "from": from_date, "to": to_date},
            headers={"X-Finnhub-Token": self._api_key},
        )
        return parse_earnings(raw, from_date)


def parse_earnings(raw: Any, from_date: str) -> dict[str, Any] | None:
    upcoming = [e for e in (raw or {}).get("earningsCalendar") or [] if str(e.get("date") or "") >= from_date]
    if not upcoming:
        return None
    nxt = min(upcoming, key=lambda e: e["date"])
    hour = nxt.get("hour") if nxt.get("hour") in ("bmo", "amc", "dmh") else None
    estimate = nxt.get("epsEstimate")
    return {"date": nxt["date"], "hour": hour, "eps_estimate": None if estimate is None else round(float(estimate), 2)}


def parse_finnhub(raw: Any, max_items: int = 25) -> list[dict[str, Any]]:
    """Normalize, drop items without a headline or https URL, dedupe by headline."""
    seen: set[str] = set()
    articles = []
    for entry in raw or []:
        headline = (entry.get("headline") or "").strip()
        url = entry.get("url") or ""
        if not headline or not url.startswith("https://"):
            continue
        key = headline.lower()
        if key in seen:
            continue
        seen.add(key)
        ts = entry.get("datetime") or 0
        published = datetime.fromtimestamp(int(ts), tz=UTC).isoformat(timespec="seconds")
        articles.append(
            {
                "article_id": str(entry.get("id") or hashlib.sha1(url.encode()).hexdigest()[:16]),  # noqa: S324 - id, not security
                "headline": headline[:300],
                "summary": (entry.get("summary") or "")[:1000],
                "source": (entry.get("source") or "Unknown")[:80],
                "url": url[:1000],
                "published_at": published,
            }
        )
    articles.sort(key=lambda a: a["published_at"], reverse=True)
    return articles[:max_items]
