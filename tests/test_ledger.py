import pytest

from portfolio import ledger


def t(kind, date, shares, price, txn_id="x"):
    return {"txn_id": txn_id, "type": kind, "date": date, "shares": shares, "price": price}


def test_fifo_sells_oldest_lots_first_and_splits_terms():
    r = ledger.replay(
        [t("opening", "2024-06-14", 20, 120, "a"), t("buy", "2025-11-03", 10, 250, "b"), t("sell", "2026-10-13", 25, 226, "c")]
    )
    long_piece, short_piece = r["realized"]
    assert long_piece["shares"] == 20 and long_piece["long_term"] is True and long_piece["gain"] == pytest.approx(20 * 106)
    assert short_piece["shares"] == 5 and short_piece["long_term"] is False and short_piece["gain"] == pytest.approx(5 * -24)
    assert r["shares"] == 5 and r["avg_cost"] == 250 and r["first_date"] == "2025-11-03"


def test_average_cost_is_of_the_open_lots():
    r = ledger.replay(
        [t("buy", "2026-01-02", 10, 100, "a"), t("buy", "2026-02-02", 10, 200, "b"), t("sell", "2026-03-02", 5, 300, "c")]
    )
    assert r["shares"] == 15 and r["avg_cost"] == pytest.approx((5 * 100 + 10 * 200) / 15)


def test_cannot_sell_more_than_held_at_that_date():
    with pytest.raises(ledger.LedgerError, match="more than"):
        ledger.replay([t("buy", "2026-05-01", 5, 10, "a"), t("sell", "2026-04-01", 1, 10, "b")])  # sold before it was bought


def test_undated_opening_has_unknown_term_and_comes_first():
    r = ledger.replay([t("buy", "2026-01-05", 1, 50, "b"), t("opening", None, 2, 10, "a"), t("sell", "2026-06-01", 2, 20, "c")])
    assert [p["long_term"] for p in r["realized"]] == [None]  # the opening lot was sold first
    assert r["first_date"] == "2026-01-05"


def test_long_term_needs_more_than_a_year():
    sold_on_anniversary = ledger.replay([t("buy", "2025-03-01", 1, 1, "a"), t("sell", "2026-03-01", 1, 2, "b")])
    assert sold_on_anniversary["realized"][0]["long_term"] is False
    assert (
        ledger.replay([t("buy", "2024-02-29", 1, 1, "a"), t("sell", "2025-03-01", 1, 2, "b")])["realized"][0]["long_term"] is True
    )
