"""Which held tickers get news (headlines, Claude summaries, news sentiment)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from portfolio.db import DEFAULT_GROUP, person_of


def news_off(
    holdings: Iterable[Mapping[str, Any]], excluded: Iterable[str], partner_news: bool, watched: Iterable[str] = ()
) -> set[str]:
    """Tickers to skip: the configured exclusions, plus partner-only tickers when partner news is off.

    Watching a ticker turns its news on, unless it's in the configured exclusions.

    The app has one owner account, so jobs (all holdings) and the API (the user's holdings) agree.
    """
    holdings = list(holdings)
    off = set(excluded)
    if not partner_news:
        # all_holdings() items may predate the group field; the API normalizes it already.
        mine = {h["ticker"] for h in holdings if person_of(h.get("group", DEFAULT_GROUP)) == "mine"}
        off |= {h["ticker"] for h in holdings} - mine - set(watched)
    return off
