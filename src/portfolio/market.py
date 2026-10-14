"""US market clock (New York time), for intraday quotes."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
MARKET_OPEN, MARKET_CLOSE = time(9, 30), time(16, 0)  # inclusive, so the 4:00 PM run still fetches
EARLY_CLOSE = time(13, 0)


def trading_day(now: datetime) -> date:
    return now.astimezone(NEW_YORK).date()


def early_close(day: date) -> bool:
    """NYSE 1:00 PM closes: the day after Thanksgiving, Christmas Eve (Mon-Thu) and July 3 (Mon-Thu)."""
    if day.month == 11:
        first_thursday = 1 + (3 - date(day.year, 11, 1).weekday()) % 7
        return day == date(day.year, 11, first_thursday + 21) + timedelta(days=1)
    return (day.month, day.day) in ((12, 24), (7, 3)) and day.weekday() < 4


def market_open(now: datetime) -> bool:
    local = now.astimezone(NEW_YORK)
    close = EARLY_CLOSE if early_close(local.date()) else MARKET_CLOSE
    return local.weekday() < 5 and MARKET_OPEN <= local.time() <= close
