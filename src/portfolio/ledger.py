"""Transactions for a Stock Holdings position: replayed first-in, first-out into open lots and realized sales.

A transaction is {txn_id, type: opening|buy|sell, date (YYYY-MM-DD or None), shares, price}. An "opening"
entry stands for shares held before tracking began (from the old totals); its date may be unknown.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

EPSILON = 1e-9


class LedgerError(ValueError):
    """The transactions don't add up, e.g. a sale of more shares than were held at that date."""


def _sort_key(txn: Mapping[str, Any]) -> tuple[str, int, str]:
    # Undated openings first; on the same day, buys before sells so a same-day round trip works.
    order = {"opening": 0, "buy": 1, "sell": 2}[txn["type"]]
    return (txn.get("date") or "0000-00-00", order, str(txn.get("txn_id", "")))


def _long_term(bought: str | None, sold: str) -> bool | None:
    """Held more than one year (IRS: the day after the one-year anniversary). None when the buy date is unknown."""
    if not bought:
        return None
    start = date.fromisoformat(bought)
    try:
        anniversary = start.replace(year=start.year + 1)
    except ValueError:  # Feb 29
        anniversary = start.replace(year=start.year + 1, day=28)
    return date.fromisoformat(sold) > anniversary


def replay(txns: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Open lots, current shares and average cost of the open lots, first lot date, and realized sales."""
    lots: list[dict[str, Any]] = []
    realized: list[dict[str, Any]] = []
    for txn in sorted(txns, key=_sort_key):
        shares, price = float(txn["shares"]), float(txn["price"])
        if txn["type"] != "sell":
            lots.append({"date": txn.get("date"), "shares": shares, "price": price, "txn_id": txn.get("txn_id")})
            continue
        held = sum(lot["shares"] for lot in lots)
        if shares > held + EPSILON:
            raise LedgerError(f"Sale on {txn['date']} of {shares:g} shares is more than the {held:g} held then")
        left = shares
        while left > EPSILON:
            lot = lots[0]
            used = min(lot["shares"], left)
            realized.append(
                {
                    "sold": txn["date"],
                    "bought": lot["date"],
                    "shares": round(used, 6),
                    "proceeds": round(used * price, 2),
                    "cost": round(used * lot["price"], 2),
                    "gain": round(used * (price - lot["price"]), 2),
                    "long_term": _long_term(lot["date"], txn["date"]),
                    "txn_id": txn.get("txn_id"),
                }
            )
            lot["shares"] -= used
            left -= used
            if lot["shares"] <= EPSILON:
                lots.pop(0)
    shares = sum(lot["shares"] for lot in lots)
    cost = sum(lot["shares"] * lot["price"] for lot in lots)
    dates = [lot["date"] for lot in lots if lot["date"]]
    return {
        "lots": [{**lot, "shares": round(lot["shares"], 6)} for lot in lots],
        "shares": round(shares, 6),
        "avg_cost": round(cost / shares, 8) if shares > EPSILON else 0.0,
        "first_date": min(dates) if dates else None,
        "realized": realized,
    }
