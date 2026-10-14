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
    RECOMMENDATION_URL = "https://finnhub.io/api/v1/stock/recommendation"
    INSIDER_URL = "https://finnhub.io/api/v1/stock/insider-transactions"

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

    def recommendations(self, ticker: str) -> list[dict[str, Any]]:
        """Analyst rating counts by month, newest first."""
        raw = get_json(self.RECOMMENDATION_URL, params={"symbol": ticker}, headers={"X-Finnhub-Token": self._api_key})
        return parse_recommendations(raw)

    def insider_trades(self, ticker: str, from_date: str) -> list[dict[str, Any]]:
        """Insider trades (SEC Form 4) since from_date, newest first."""
        raw = get_json(self.INSIDER_URL, params={"symbol": ticker, "from": from_date}, headers={"X-Finnhub-Token": self._api_key})
        return parse_insiders(raw, from_date)


ANALYST_MONTHS = 4
INSIDER_ROWS = 25


def parse_recommendations(raw: Any) -> list[dict[str, Any]]:
    """Strong buy + buy, hold, sell + strong sell for the latest months."""
    months = sorted((r for r in raw or [] if r.get("period")), key=lambda r: r["period"], reverse=True)
    count = lambda r, *keys: sum(int(r.get(k) or 0) for k in keys)  # noqa: E731
    return [
        {
            "period": r["period"][:7],
            "buy": count(r, "strongBuy", "buy"),
            "hold": count(r, "hold"),
            "sell": count(r, "sell", "strongSell"),
        }
        for r in months[:ANALYST_MONTHS]
    ]


def parse_insiders(raw: Any, from_date: str) -> dict[str, Any]:
    """Totals for open-market trades (P bought, S sold) and the newest trades; other codes count as routine
    (awards, gifts, option exercises, shares withheld for tax). Derivative rows are left out."""
    rows = [
        r
        for r in (raw or {}).get("data") or []
        if not r.get("isDerivative") and str(r.get("transactionDate") or "") >= from_date and r.get("change")
    ]
    rows.sort(key=lambda r: (r["transactionDate"], r.get("filingDate") or ""), reverse=True)
    summary = {"bought": 0.0, "buys": 0, "sold": 0.0, "sells": 0, "routine": 0, "unpriced_buys": 0, "unpriced_sells": 0}
    trades = []
    for r in rows:
        code, shares, price = r.get("transactionCode") or "", abs(int(r["change"])), float(r.get("transactionPrice") or 0)
        if code in ("P", "S") and not price:
            summary["unpriced_buys" if code == "P" else "unpriced_sells"] += 1  # counted, but its dollars are unknown
        if code == "P":
            summary["bought"] += shares * price
            summary["buys"] += 1
        elif code == "S":
            summary["sold"] += shares * price
            summary["sells"] += 1
        else:
            summary["routine"] += 1
        trades.append(
            {"date": r["transactionDate"], "name": r.get("name") or "", "code": code, "shares": shares, "price": price or None}
        )
    return {**summary, "bought": round(summary["bought"]), "sold": round(summary["sold"]), "trades": trades[:INSIDER_ROWS]}


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
