"""US market clock (New York time), for intraday quotes."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
MARKET_OPEN, MARKET_CLOSE = time(9, 30), time(16, 0)
# Scheduled runs start up to a minute or so late (e.g. 4:00:42 PM), so the closing run gets a few minutes' grace.
CLOSE_GRACE = time(16, 5)


def trading_day(now: datetime) -> date:
    return now.astimezone(NEW_YORK).date()


def market_open(now: datetime) -> bool:
    local = now.astimezone(NEW_YORK)
    return local.weekday() < 5 and MARKET_OPEN <= local.time() <= CLOSE_GRACE
