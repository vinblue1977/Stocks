"""US market clock (New York time): trading hours, NYSE holidays and 1:00 PM early closes."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
MARKET_OPEN, MARKET_CLOSE = time(9, 30), time(16, 0)  # inclusive, so the 4:00 PM run still fetches
EARLY_CLOSE = time(13, 0)


def trading_day(now: datetime) -> date:
    return now.astimezone(NEW_YORK).date()


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th given weekday of a month (n = -1 for the last one). Monday = 0."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741 - the algorithm's own name
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _observed(day: date) -> date:
    """A holiday on Saturday is observed Friday, on Sunday the following Monday."""
    return day - timedelta(days=1) if day.weekday() == 5 else day + timedelta(days=1) if day.weekday() == 6 else day


@lru_cache(maxsize=8)
def holidays(year: int) -> dict[date, str]:
    """NYSE full-day closures for a year."""
    days = {
        _nth_weekday(year, 1, 0, 3): "Martin Luther King Jr. Day",
        _nth_weekday(year, 2, 0, 3): "Washington's Birthday",
        _easter(year) - timedelta(days=2): "Good Friday",
        _nth_weekday(year, 5, 0, -1): "Memorial Day",
        _observed(date(year, 7, 4)): "Independence Day",
        _nth_weekday(year, 9, 0, 1): "Labor Day",
        _nth_weekday(year, 11, 3, 4): "Thanksgiving Day",
        _observed(date(year, 12, 25)): "Christmas Day",
    }
    if year >= 2022:  # NYSE first closed for Juneteenth in 2022
        days[_observed(date(year, 6, 19))] = "Juneteenth"
    new_year = date(year, 1, 1)
    if new_year.weekday() != 5:  # on a Saturday NYSE doesn't close the Friday before (it's the prior year-end)
        days[_observed(new_year)] = "New Year's Day"
    return days


def holiday(day: date) -> str | None:
    """The holiday's name when NYSE is closed all day, else None."""
    return holidays(day.year).get(day)


def early_close(day: date) -> bool:
    """NYSE 1:00 PM closes: the day after Thanksgiving, Christmas Eve (Mon-Thu) and July 3 (Mon-Thu)."""
    if day.month == 11:
        return day == _nth_weekday(day.year, 11, 3, 4) + timedelta(days=1)  # day after Thanksgiving
    # On a Friday these dates are the observed holiday instead (July 4 or Christmas on a Saturday).
    return (day.month, day.day) in ((12, 24), (7, 3)) and day.weekday() < 4


def market_open(now: datetime) -> bool:
    local = now.astimezone(NEW_YORK)
    if local.weekday() >= 5 or holiday(local.date()):
        return False
    close = EARLY_CLOSE if early_close(local.date()) else MARKET_CLOSE
    return MARKET_OPEN <= local.time() <= close


def market_status(now: datetime) -> dict[str, object]:
    """Today's session for the dashboard: a holiday's name, or whether it closes early."""
    day = trading_day(now)
    return {"date": day.isoformat(), "holiday": holiday(day), "early_close": early_close(day)}
