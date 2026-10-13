import json
from datetime import date, timedelta

import pytest
from conftest import api_event, price_series

from portfolio.handlers import api


@pytest.fixture(autouse=True)
def no_backfill(monkeypatch):
    calls = []
    monkeypatch.setattr(api, "_request_backfill", calls.append)
    return calls


def call(repo, route, **kwargs):
    response = api.handler(api_event(route, **kwargs), None, repo=repo)
    body = json.loads(response["body"]) if response["body"] else None
    return response["statusCode"], body


def test_requires_authenticated_user(repo):
    event = api_event("GET /holdings")
    event["requestContext"] = {}
    response = api.handler(event, None, repo=repo)
    assert response["statusCode"] == 401


def test_unknown_route(repo):
    assert call(repo, "GET /admin")[0] == 404


def test_holding_crud_and_backfill(repo, no_backfill):
    status, body = call(
        repo,
        "PUT /groups/{group}/holdings/{ticker}",
        path={"group": "stocks", "ticker": "vti"},
        body=json.dumps({"shares": 40, "avg_cost": 230, "purchased_on": "2024-03-01"}),
    )
    assert status == 200 and body["ticker"] == "VTI"
    assert no_backfill == ["VTI"]
    # Updating an existing holding does not trigger another backfill
    call(
        repo,
        "PUT /groups/{group}/holdings/{ticker}",
        path={"group": "stocks", "ticker": "VTI"},
        body=json.dumps({"shares": 41, "avg_cost": 230}),
    )
    assert no_backfill == ["VTI"]
    status, body = call(repo, "GET /holdings")
    assert [h["shares"] for h in body["holdings"]] == [41]
    assert call(repo, "DELETE /groups/{group}/holdings/{ticker}", path={"group": "stocks", "ticker": "VTI"})[0] == 204
    assert call(repo, "GET /holdings")[1]["holdings"] == []


def test_users_are_isolated(repo):
    call(
        repo,
        "PUT /groups/{group}/holdings/{ticker}",
        user="alice",
        path={"group": "stocks", "ticker": "VTI"},
        body=json.dumps({"shares": 1, "avg_cost": 1}),
    )
    assert call(repo, "GET /holdings", user="bob")[1]["holdings"] == []
    # Bob deleting "VTI" only touches his own (empty) data
    call(repo, "DELETE /groups/{group}/holdings/{ticker}", user="bob", path={"group": "stocks", "ticker": "VTI"})
    assert len(call(repo, "GET /holdings", user="alice")[1]["holdings"]) == 1


@pytest.mark.parametrize(
    "path, body, status",
    [
        ({"group": "stocks", "ticker": "VTI;DROP"}, {"shares": 1, "avg_cost": 1}, 400),
        ({"group": "stocks", "ticker": "VTI"}, {"shares": -1, "avg_cost": 1}, 400),
        ({"group": "stocks", "ticker": "VTI"}, {"shares": "NaN", "avg_cost": 1}, 400),
        ({"group": "stocks", "ticker": "VTI"}, {"shares": 1}, 400),
        ({"group": "stocks", "ticker": "VTI"}, {"shares": 1, "avg_cost": 1, "purchased_on": "2999-01-01"}, 400),
        ({"group": "stocks", "ticker": "VTI"}, {"shares": 1, "avg_cost": 1, "purchased_on": "yesterday"}, 400),
    ],
)
def test_holding_validation(repo, path, body, status):
    assert call(repo, "PUT /groups/{group}/holdings/{ticker}", path=path, body=json.dumps(body))[0] == status


def test_rejects_bad_bodies(repo):
    assert call(repo, "POST /rules", body="not json")[0] == 400
    assert call(repo, "POST /rules", body="[1, 2]")[0] == 400
    assert call(repo, "POST /rules", body=json.dumps({"pad": "x" * 20_000}))[0] == 413


def test_rules_lifecycle(repo):
    repo.put_holding("user-1", {"ticker": "VTI", "shares": 1, "avg_cost": 1})
    status, created = call(
        repo, "POST /rules", body=json.dumps({"type": "rsi_below", "ticker": "VTI", "params": {"threshold": 30}})
    )
    assert status == 201
    rules_list = call(repo, "GET /rules")[1]["rules"]
    assert rules_list[0]["rule_id"] == created["rule_id"] and rules_list[0]["triggered"] is False
    assert call(repo, "DELETE /rules/{ruleId}", path={"ruleId": "../../etc"})[0] == 400
    assert call(repo, "DELETE /rules/{ruleId}", path={"ruleId": created["rule_id"]})[0] == 204
    assert call(repo, "GET /rules")[1]["rules"] == []
    assert "rsi_below" in call(repo, "GET /rule-types")[1]["rule_types"]
    assert call(repo, "POST /rules", body=json.dumps({"type": "bogus", "ticker": "VTI"}))[0] == 400


def test_portfolio_summary(repo):
    call(
        repo,
        "PUT /groups/{group}/holdings/{ticker}",
        path={"group": "stocks", "ticker": "VTI"},
        body=json.dumps({"shares": 10, "avg_cost": 100}),
    )
    from datetime import UTC, datetime, timedelta

    start = (datetime.now(UTC).date() - timedelta(days=30)).isoformat()
    repo.put_prices("VTI", price_series(100, 1, 30, start))
    status, body = call(repo, "GET /portfolio")
    assert status == 200
    overall = body["portfolios"]["household"]["overall"]
    assert overall["total_value"] == pytest.approx(10 * 129)
    assert body["portfolios"]["mine"]["stocks"]["total_value"] == pytest.approx(10 * 129)
    assert body["portfolios"]["mine"]["auto"]["total_value"] == 0
    assert body["portfolios"]["partner"]["overall"]["total_value"] == 0
    assert overall["return_1y"] is None  # only 30 days of history, not a year
    assert len(overall["history"]) == 30 and "benchmark_history" not in body


def test_portfolio_uses_intraday_quote_until_the_close_is_stored(repo):
    from datetime import UTC, datetime, timedelta

    from portfolio.market import trading_day

    for ticker in ("VTI", "FUND"):
        call(
            repo,
            "PUT /groups/{group}/holdings/{ticker}",
            path={"group": "stocks", "ticker": ticker},
            body=json.dumps({"shares": 10, "avg_cost": 100}),
        )
    today = trading_day(datetime.now(UTC))
    start = (today - timedelta(days=30)).isoformat()
    for ticker in ("VTI", "FUND"):
        repo.put_prices(ticker, price_series(100, 1, 30, start))  # closes through yesterday: 128, then 129
    quote = {"last": 131.0, "timestamp": f"{today}T15:00:00+00:00", "date": today.isoformat()}

    repo.put_quotes({"VTI": quote})
    _, body = call(repo, "GET /portfolio")
    overall = body["portfolios"]["household"]["overall"]
    assert overall["total_value"] == pytest.approx(1310 + 1290)
    # Today: VTI +2 vs. its close; FUND has no quote, so it counts as unchanged (not yesterday's +1).
    assert overall["day_change"] == pytest.approx(20)
    moves = {p["ticker"]: (p["day_change"], p["day_change_pct"]) for p in body["portfolios"]["mine"]["stocks"]["positions"]}
    assert moves == {"VTI": (20.0, pytest.approx(0.0155, abs=1e-4)), "FUND": (0.0, 0.0)}
    assert body["live_as_of"] == quote["timestamp"]
    assert len(overall["history"]) == 30  # the chart stays on closing prices

    # A quote from an earlier day (e.g. the evening sync failed) is not shown as live.
    repo.put_quotes({"VTI": {**quote, "date": (today - timedelta(days=1)).isoformat()}})
    _, body = call(repo, "GET /portfolio")
    assert body["live_as_of"] is None
    assert body["portfolios"]["household"]["overall"]["total_value"] == pytest.approx(2580)

    # Once today's close is stored, the quote is ignored.
    repo.put_quotes({"VTI": quote})
    repo.put_prices("VTI", [{"date": today.isoformat(), "close": 132.0, "volume": 1}])
    _, body = call(repo, "GET /portfolio")
    assert body["live_as_of"] is None
    assert body["portfolios"]["household"]["overall"]["total_value"] == pytest.approx(1320 + 1290)


def test_news_only_for_held_tickers(repo):
    assert call(repo, "GET /news", query={"ticker": "TSLA"})[0] == 404
    status, body = call(repo, "GET /news")
    assert status == 200 and body["articles"] == []


def test_holdings_cap(repo, monkeypatch):
    monkeypatch.setattr(api, "MAX_HOLDINGS", 2)
    for ticker in ("VTI", "BND"):
        call(
            repo,
            "PUT /groups/{group}/holdings/{ticker}",
            path={"group": "stocks", "ticker": ticker},
            body=json.dumps({"shares": 1, "avg_cost": 1}),
        )
    status, body = call(
        repo,
        "PUT /groups/{group}/holdings/{ticker}",
        path={"group": "stocks", "ticker": "QQQ"},
        body=json.dumps({"shares": 1, "avg_cost": 1}),
    )
    assert status == 409 and "up to 2 different tickers" in body["message"]
    # Updating an existing holding is still allowed at the cap
    assert (
        call(
            repo,
            "PUT /groups/{group}/holdings/{ticker}",
            path={"group": "stocks", "ticker": "VTI"},
            body=json.dumps({"shares": 2, "avg_cost": 1}),
        )[0]
        == 200
    )


def buy(repo, ticker, shares, price, group="stocks", **extra):
    return call(
        repo,
        "POST /groups/{group}/holdings/{ticker}/purchases",
        path={"group": group, "ticker": ticker},
        body=json.dumps({"shares": shares, **({"price": price} if price is not None else {}), **extra}),
    )


def test_purchase_creates_then_averages(repo, no_backfill):
    status, body = buy(repo, "VTI", 10, 250, purchased_on="2026-03-02")
    assert status == 201 and body["previous"] is None
    assert body["holding"]["shares"] == 10 and body["holding"]["avg_cost"] == 250
    assert no_backfill == ["VTI"]

    status, body = buy(repo, "vti", 5, 280, purchased_on="2026-08-03")
    assert status == 200
    assert body["holding"]["shares"] == 15
    assert body["holding"]["avg_cost"] == pytest.approx(260.0)  # (10*250 + 5*280) / 15
    assert body["holding"]["purchased_on"] == "2026-03-02"  # earliest purchase kept
    assert body["previous"]["shares"] == 10
    assert no_backfill == ["VTI"]  # no second backfill for an existing ticker


def test_purchase_fractional_shares(repo):
    buy(repo, "QQQ", 0.5, 400)
    _, body = buy(repo, "QQQ", 0.25, 520)
    assert body["holding"]["shares"] == 0.75
    assert body["holding"]["avg_cost"] == pytest.approx(440.0)


@pytest.mark.parametrize(
    "payload",
    [
        {"shares": 0, "price": 10},
        {"shares": 1},
        {"shares": 1, "price": -1},
        {"shares": 1, "price": 1, "purchased_on": "2999-01-01"},
    ],
)
def test_purchase_validation(repo, payload):
    status, _ = call(
        repo,
        "POST /groups/{group}/holdings/{ticker}/purchases",
        path={"group": "stocks", "ticker": "VTI"},
        body=json.dumps(payload),
    )
    assert status == 400


def test_purchase_retries_on_concurrent_change(repo, monkeypatch):
    from portfolio.db import ConcurrentUpdateError

    buy(repo, "VTI", 10, 100, group="auto")  # Automatic Investment keeps totals (Stock Holdings use transactions)
    real = repo.replace_holding_if_unchanged
    calls = {"n": 0}

    def flaky(user_id, holding, previous):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConcurrentUpdateError("VTI")
        return real(user_id, holding, previous)

    monkeypatch.setattr(repo, "replace_holding_if_unchanged", flaky)
    status, body = buy(repo, "VTI", 10, 200, group="auto")
    assert status == 200 and body["holding"]["avg_cost"] == pytest.approx(150.0)
    assert calls["n"] == 2


def test_conditional_write_detects_stale_read(repo):
    from portfolio.db import ConcurrentUpdateError

    buy(repo, "VTI", 10, 100)
    stale = repo.get_holding("user-1", "stocks", "VTI")
    buy(repo, "VTI", 1, 100)  # someone else changes it
    with pytest.raises(ConcurrentUpdateError):
        repo.replace_holding_if_unchanged("user-1", {"ticker": "VTI", "shares": 99, "avg_cost": 1}, stale)


def test_purchase_respects_holdings_cap(repo, monkeypatch):
    monkeypatch.setattr(api, "MAX_HOLDINGS", 1)
    buy(repo, "VTI", 1, 1)
    assert buy(repo, "BND", 1, 1)[0] == 409
    assert buy(repo, "VTI", 1, 1)[0] == 200


# ---------------------------------------------------------------- groups
def put(repo, group, ticker, shares, cost, user="user-1"):
    return call(
        repo,
        "PUT /groups/{group}/holdings/{ticker}",
        user=user,
        path={"group": group, "ticker": ticker},
        body=json.dumps({"shares": shares, "avg_cost": cost}),
    )


def test_unknown_group_is_rejected(repo):
    assert put(repo, "crypto", "VTI", 1, 1)[0] == 404
    assert call(repo, "GET /holdings", query={"group": "crypto"})[0] == 400


def test_same_ticker_tracked_separately_per_group(repo, no_backfill):
    put(repo, "stocks", "VOO", 2, 600)
    put(repo, "auto", "VOO", 6, 700)
    assert no_backfill == ["VOO"]  # prices load once, not per group
    buy(repo, "VOO", 2, 800, group="auto")
    stocks = call(repo, "GET /holdings", query={"group": "stocks"})[1]["holdings"]
    auto = call(repo, "GET /holdings", query={"group": "auto"})[1]["holdings"]
    assert [(h["shares"], h["avg_cost"]) for h in stocks] == [(2, 600)]  # untouched
    assert auto[0]["shares"] == 8 and auto[0]["avg_cost"] == pytest.approx(725.0)  # (6*700 + 2*800) / 8
    assert len(call(repo, "GET /holdings")[1]["holdings"]) == 2
    # Deleting from one group leaves the other
    call(repo, "DELETE /groups/{group}/holdings/{ticker}", path={"group": "auto", "ticker": "VOO"})
    assert [h["group"] for h in call(repo, "GET /holdings")[1]["holdings"]] == ["stocks"]


def test_cap_counts_unique_tickers_across_groups(repo, monkeypatch):
    monkeypatch.setattr(api, "MAX_HOLDINGS", 2)
    put(repo, "stocks", "VTI", 1, 1)
    put(repo, "auto", "BND", 1, 1)
    assert put(repo, "auto", "VTI", 1, 1)[0] == 200  # VTI already counted
    assert put(repo, "stocks", "QQQ", 1, 1)[0] == 409


def test_portfolio_splits_groups_and_combines_overall(repo):
    from datetime import UTC, datetime, timedelta

    start = (datetime.now(UTC).date() - timedelta(days=10)).isoformat()
    repo.put_prices("VOO", price_series(100, 0, 10, start))
    repo.put_prices("BND", price_series(50, 0, 10, start))
    repo.put_prices("SPY", price_series(400, 0, 10, start))
    put(repo, "stocks", "VOO", 2, 90)
    put(repo, "auto", "VOO", 3, 80)
    put(repo, "auto", "BND", 4, 50)
    mine = call(repo, "GET /portfolio")[1]["portfolios"]["mine"]
    assert mine["stocks"]["total_value"] == 200 and mine["stocks"]["total_gain"] == 20
    assert mine["auto"]["total_value"] == 500 and mine["auto"]["total_gain"] == 60
    assert mine["overall"]["total_value"] == 700 and mine["overall"]["total_gain"] == 80
    assert mine["overall"]["history"][-1]["value"] == 700  # VOO shares from both groups
    assert {(p["ticker"], p["group"]) for p in mine["overall"]["positions"]} == {
        ("VOO", "stocks"),
        ("VOO", "auto"),
        ("BND", "auto"),
    }


def test_partner_groups_are_separate_and_add_up_to_household(repo, no_backfill):
    from datetime import UTC, datetime, timedelta

    start = (datetime.now(UTC).date() - timedelta(days=10)).isoformat()
    for ticker, price in (("VOO", 100), ("BND", 50), ("SPY", 400)):
        repo.put_prices(ticker, price_series(price, 0, 10, start))
    put(repo, "stocks", "VOO", 2, 90)
    put(repo, "partner_stocks", "VOO", 1, 95)
    put(repo, "partner_auto", "BND", 4, 40)
    assert no_backfill == ["VOO", "BND"]
    # Each group keeps its own holding
    assert [h["shares"] for h in call(repo, "GET /holdings", query={"group": "partner_stocks"})[1]["holdings"]] == [1]
    p = call(repo, "GET /portfolio")[1]["portfolios"]
    assert p["mine"]["overall"]["total_value"] == 200
    assert p["partner"]["stocks"]["total_value"] == 100 and p["partner"]["auto"]["total_value"] == 200
    assert p["partner"]["overall"]["total_value"] == 300
    assert p["household"]["stocks"]["total_value"] == 300  # both people's Stock Holdings
    assert p["household"]["overall"]["total_value"] == 500 and p["household"]["overall"]["total_gain"] == 500 - 180 - 95 - 160


def test_partner_auto_uses_dollar_totals(repo):
    status, body = buy(repo, "VOO", 0.5, None, group="partner_auto", amount=300)
    assert status == 201 and body["holding"]["avg_cost"] == 600


def test_cap_counts_partner_tickers_too(repo, monkeypatch):
    monkeypatch.setattr(api, "MAX_HOLDINGS", 1)
    put(repo, "stocks", "VTI", 1, 1)
    assert put(repo, "partner_stocks", "VTI", 1, 1)[0] == 200
    assert put(repo, "partner_auto", "BND", 1, 1)[0] == 409


def test_dollar_totals_for_automatic_investment(repo):
    # Set totals with a total cost basis instead of a per-share average
    status, body = call(
        repo,
        "PUT /groups/{group}/holdings/{ticker}",
        path={"group": "auto", "ticker": "VOO"},
        body=json.dumps({"shares": 4, "total_cost": 2400}),
    )
    assert status == 200 and body["avg_cost"] == 600
    # Record a purchase by amount invested instead of price per share
    status, body = buy(repo, "VOO", 0.5, None, group="auto", amount=350)
    assert status == 200
    assert body["holding"]["shares"] == 4.5
    assert body["holding"]["shares"] * body["holding"]["avg_cost"] == pytest.approx(2750, abs=0.01)  # total invested


@pytest.mark.parametrize(
    "payload",
    [{"shares": 1}, {"shares": 1, "price": 10, "amount": 10}, {"shares": 1, "amount": -5}],
)
def test_purchase_needs_exactly_one_of_price_or_amount(repo, payload):
    status, body = call(
        repo,
        "POST /groups/{group}/holdings/{ticker}/purchases",
        path={"group": "auto", "ticker": "VOO"},
        body=json.dumps(payload),
    )
    assert status == 400


def test_news_lists_excluded_tickers(repo, monkeypatch):
    import dataclasses

    from portfolio import config

    cfg = dataclasses.replace(config.get_config(), news_excluded=frozenset({"MGV"}))
    monkeypatch.setattr(api, "get_config", lambda: cfg)
    put(repo, "auto", "MGV", 1, 1)
    status, body = call(repo, "GET /news")
    assert status == 200 and body["news_excluded"] == ["MGV"]


def test_profile_names_and_partner_visibility(repo, monkeypatch):
    monkeypatch.delenv("OWNER_NAME", raising=False)
    monkeypatch.delenv("PARTNER_NAME", raising=False)
    assert call(repo, "GET /profile")[1] == {"owner_name": "", "partner_name": "", "has_partner": False}
    # Partner holdings keep the partner page visible even if a deploy dropped the name
    put(repo, "partner_auto", "BND", 1, 1)
    assert call(repo, "GET /profile")[1]["has_partner"] is True
    monkeypatch.setenv("OWNER_NAME", "Alex")
    monkeypatch.setenv("PARTNER_NAME", "Sam")
    assert call(repo, "GET /profile")[1] == {"owner_name": "Alex", "partner_name": "Sam", "has_partner": True}


def test_news_lists_partner_only_tickers_as_off(repo, monkeypatch):
    import dataclasses

    from portfolio import config

    cfg = dataclasses.replace(config.get_config(), news_excluded=frozenset(), partner_news=False)
    monkeypatch.setattr(api, "get_config", lambda: cfg)
    put(repo, "stocks", "VTI", 1, 1)
    put(repo, "partner_stocks", "VTI", 1, 1)
    put(repo, "partner_auto", "SCHD", 1, 1)
    assert call(repo, "GET /news")[1]["news_excluded"] == ["SCHD"]
    # Asking for that ticker directly returns nothing stale
    repo.put_brief("SCHD", "2026-10-09", {"summary": "old", "sentiment_7d": 0.6})
    body = call(repo, "GET /news", query={"ticker": "SCHD"})[1]
    assert body["brief"] is None and body["articles"] == []


def _put_legacy_holding(repo, ticker, shares, cost):
    """A holding as stored before the cost_basis -> avg_cost rename."""
    keys = repo._holding_keys("user-1", "stocks", ticker)
    repo.table.put_item(Item={**keys, "ticker": ticker, "group": "stocks", "shares": shares, "cost_basis": cost})


def test_legacy_cost_basis_is_read_and_replaced(repo, no_backfill):
    _put_legacy_holding(repo, "VTI", 10, 250)
    assert call(repo, "GET /holdings")[1]["holdings"][0]["avg_cost"] == 250
    assert repo.all_holdings()[0]["avg_cost"] == 250 and "cost_basis" not in repo.all_holdings()[0]

    status, body = buy(repo, "VTI", 5, 280)  # conditional write matches the old attribute name
    assert status == 200 and body["holding"]["avg_cost"] == pytest.approx(260.0)
    stored = repo.table.get_item(Key={"pk": "USER#user-1", "sk": "HOLDING#stocks#VTI"})["Item"]
    assert float(stored["avg_cost"]) == pytest.approx(260.0) == float(stored["cost_basis"])  # old name kept for rollback


def test_old_cost_basis_input_still_accepted(repo, no_backfill):
    route, path = "PUT /groups/{group}/holdings/{ticker}", {"group": "stocks", "ticker": "VTI"}
    status, body = call(repo, route, path=path, body=json.dumps({"shares": 4, "cost_basis": 25}))
    assert status == 200 and body["avg_cost"] == 25 and "cost_basis" not in body
    status, _ = call(repo, route, path=path, body=json.dumps({"shares": 4, "cost_basis": 25, "avg_cost": 25}))
    assert status == 400
    status, body = call(repo, route, path=path, body=json.dumps({"shares": 4, "cost_basis": None, "avg_cost": 30}))
    assert status == 200 and body["avg_cost"] == 30


def test_migrate_avg_cost(repo):
    _put_legacy_holding(repo, "VTI", 10, 250)
    buy(repo, "VOO", 1, 500)  # already new-format
    assert repo.migrate_avg_cost(dry_run=True) == ["USER#user-1:HOLDING#stocks#VTI"]
    assert repo.migrate_avg_cost() == ["USER#user-1:HOLDING#stocks#VTI"]
    assert repo.migrate_avg_cost() == []  # idempotent
    items = {i["ticker"]: i for i in repo.table.scan()["Items"] if i["sk"].startswith("HOLDING#")}
    assert float(items["VTI"]["avg_cost"]) == 250 == float(items["VTI"]["cost_basis"])  # old name kept


# ---------------------------------------------------------------- watchlist
def test_watchlist_add_list_remove(repo, no_backfill):
    repo.put_holding("user-1", {"ticker": "VTI", "shares": 1, "avg_cost": 1})
    repo.put_prices("NVDA", price_series(100, 1, 30, (date.today() - timedelta(days=40)).isoformat()))
    assert call(repo, "PUT /watchlist/{ticker}", path={"ticker": "nvda"})[0] == 201
    assert call(repo, "PUT /watchlist/{ticker}", path={"ticker": "NVDA"})[0] == 200  # already watched
    call(repo, "PUT /watchlist/{ticker}", path={"ticker": "VTI"})
    assert no_backfill == ["NVDA"]  # VTI is held, so it already has prices
    _, body = call(repo, "GET /watchlist")
    items = {w["ticker"]: w for w in body["watchlist"]}
    assert items["NVDA"]["held"] is False and items["VTI"]["held"] is True
    assert items["NVDA"]["signal"]["price"] == pytest.approx(129)  # computed before the evening run stores one
    assert repo.tracked_tickers() == ["NVDA", "VTI"]
    # Watched tickers never count in holdings or totals
    assert [h["ticker"] for h in call(repo, "GET /holdings")[1]["holdings"]] == ["VTI"]
    assert call(repo, "DELETE /watchlist/{ticker}", path={"ticker": "NVDA"})[0] == 204
    assert [w["ticker"] for w in call(repo, "GET /watchlist")[1]["watchlist"]] == ["VTI"]
    assert call(repo, "PUT /watchlist/{ticker}", path={"ticker": "../x"})[0] == 400


def test_watchlist_caps(repo, monkeypatch):
    monkeypatch.setattr(api, "MAX_WATCHLIST", 2)
    monkeypatch.setattr(api, "MAX_TRACKED", 3)
    repo.put_holding("user-1", {"ticker": "VTI", "shares": 1, "avg_cost": 1})
    call(repo, "PUT /watchlist/{ticker}", path={"ticker": "NVDA"})
    call(repo, "PUT /watchlist/{ticker}", path={"ticker": "AAPL"})
    status, body = call(repo, "PUT /watchlist/{ticker}", path={"ticker": "MSFT"})
    assert status == 409 and "up to 2 tickers" in body["message"]
    # Holdings share the combined cap with the watchlist
    status, body = call(
        repo,
        "PUT /groups/{group}/holdings/{ticker}",
        path={"group": "stocks", "ticker": "BND"},
        body=json.dumps({"shares": 1, "avg_cost": 1}),
    )
    assert status == 409 and "across holdings and the watchlist" in body["message"]
    # Buying a watched ticker adds no new tracked ticker, so it is allowed
    status, _ = call(
        repo,
        "PUT /groups/{group}/holdings/{ticker}",
        path={"group": "stocks", "ticker": "NVDA"},
        body=json.dumps({"shares": 1, "avg_cost": 1}),
    )
    assert status == 200


def test_rules_need_a_held_or_watched_ticker(repo):
    def create(rule_type, ticker, params):
        return call(repo, "POST /rules", body=json.dumps({"type": rule_type, "ticker": ticker, "params": params}))[0]

    assert create("rsi_below", "NVDA", {"threshold": 30}) == 400
    call(repo, "PUT /watchlist/{ticker}", path={"ticker": "NVDA"})
    assert create("rsi_below", "NVDA", {"threshold": 30}) == 201
    assert create("weight_above", "NVDA", {"pct": 10}) == 400  # not held, so no portfolio share


def test_unwatching_deletes_rules_unless_held(repo):
    repo.put_holding("user-1", {"ticker": "VTI", "shares": 1, "avg_cost": 1})
    for ticker in ("NVDA", "VTI"):
        call(repo, "PUT /watchlist/{ticker}", path={"ticker": ticker})
        call(repo, "POST /rules", body=json.dumps({"type": "rsi_below", "ticker": ticker, "params": {"threshold": 30}}))
        call(repo, "DELETE /watchlist/{ticker}", path={"ticker": ticker})
    assert [r["ticker"] for r in call(repo, "GET /rules")[1]["rules"]] == ["VTI"]


def test_news_for_a_watched_ticker(repo):
    assert call(repo, "GET /news", query={"ticker": "NVDA"})[0] == 404
    call(repo, "PUT /watchlist/{ticker}", path={"ticker": "NVDA"})
    assert call(repo, "GET /news", query={"ticker": "NVDA"})[0] == 200


def test_deleting_a_holding_prunes_rules_that_cannot_run(repo):
    for ticker in ("MSFT", "AAPL"):
        call(
            repo,
            "PUT /groups/{group}/holdings/{ticker}",
            path={"group": "stocks", "ticker": ticker},
            body=json.dumps({"shares": 1, "avg_cost": 1}),
        )
        for rule_type, params in (("rsi_below", {"threshold": 30}), ("weight_above", {"pct": 50})):
            call(repo, "POST /rules", body=json.dumps({"type": rule_type, "ticker": ticker, "params": params}))
    call(repo, "PUT /watchlist/{ticker}", path={"ticker": "AAPL"})
    for ticker in ("MSFT", "AAPL"):
        call(repo, "DELETE /groups/{group}/holdings/{ticker}", path={"group": "stocks", "ticker": ticker})
    # MSFT is gone entirely; AAPL is still watched, so only its portfolio-share rule goes
    assert [(r["ticker"], r["type"]) for r in call(repo, "GET /rules")[1]["rules"]] == [("AAPL", "rsi_below")]


def test_watchlist_recomputes_a_stale_signal(repo):
    start = (date.today() - timedelta(days=40)).isoformat()
    repo.put_prices("NVDA", price_series(100, 1, 30, start))
    repo.put_signal("NVDA", {"ticker": "NVDA", "as_of": start, "price": 1.0})  # left from an earlier watch
    call(repo, "PUT /watchlist/{ticker}", path={"ticker": "NVDA"})
    signal = call(repo, "GET /watchlist")[1]["watchlist"][0]["signal"]
    assert signal["price"] == pytest.approx(129)


def test_signals_include_watched_tickers(repo):
    repo.put_signal("NVDA", {"ticker": "NVDA", "as_of": "2026-01-02", "price": 1.0})
    call(repo, "PUT /watchlist/{ticker}", path={"ticker": "NVDA"})
    assert [s["ticker"] for s in call(repo, "GET /signals")[1]["signals"]] == ["NVDA"]


# ---------------------------------------------------------------- checkup
TARGETS = {
    "areas": {"us": {"pct": 60, "fund": "vti"}, "intl": {"pct": 25, "fund": "VXUS"}, "sector": {"pct": 15, "fund": "SMH"}},
    "sector_ceiling": 15,
    "overrides": {"xyz": "intl"},
}


def test_targets_round_trip_and_checkup(repo):
    start = (date.today() - timedelta(days=40)).isoformat()
    for ticker, shares in (("VTI", 6), ("SMH", 4)):
        repo.put_holding("user-1", {"ticker": ticker, "shares": shares, "avg_cost": 1})
        repo.put_prices(ticker, price_series(100, 1, 30, start))
    assert call(repo, "GET /targets")[1]["targets"] is None
    status, body = call(repo, "PUT /targets", body=json.dumps(TARGETS))
    assert status == 200 and body["targets"]["areas"]["us"] == {"pct": 60, "fund": "VTI"}
    assert body["targets"]["overrides"] == {"XYZ": "intl"} and body["targets"]["areas"]["bonds"]["pct"] == 0
    status, body = call(repo, "GET /checkup")
    home = body["views"]["household"]
    assert status == 200 and set(body["views"]) == {"household", "mine"}
    assert home["by_area"] == {"us": 774.0, "sector": 516.0}
    assert next(d for d in home["drift"] if d["area"] == "sector")["now"] == 0.4
    assert body["assigned"] == {"SMH": "sector", "VTI": "us"} and body["summary"] is None


@pytest.mark.parametrize(
    "change, message",
    [
        ({"areas": {"us": {"pct": 50}}}, "add up to 100"),
        ({"areas": {"crypto": {"pct": 100}}}, "areas must use"),
        ({"areas": {"us": {"pct": 99.5}, "intl": {"pct": 0.5}}}, "whole numbers"),
        ({"areas": {"us": {"pct": 100, "fund": "../x"}}}, "fund must look like"),
        ({"areas": {"us": {"pct": 100}}, "overrides": {"VTI": "moon"}}, "overrides.VTI"),
        ({"areas": {"us": {"pct": 100}}, "sector_ceiling": 150}, "between"),
        ({"areas": {"us": {"pct": 100}}, "sector_ceiling": 12.5}, "whole number"),
    ],
)
def test_targets_validation(repo, change, message):
    status, body = call(repo, "PUT /targets", body=json.dumps({**TARGETS, **change}))
    assert status == 400 and message in body["message"]


# ---------------------------------------------------------------- transactions
TXNS = "/groups/{group}/holdings/{ticker}/transactions"


def trade(repo, ticker, kind, shares, price, date="2026-03-02", group="stocks"):
    return call(
        repo,
        f"POST {TXNS}",
        path={"group": group, "ticker": ticker},
        body=json.dumps({"type": kind, "shares": shares, "price": price, "date": date}),
    )


def test_first_trade_saves_the_opening_from_totals(repo, no_backfill):
    repo.put_holding("user-1", {"ticker": "NVDA", "group": "stocks", "shares": 20, "avg_cost": 120, "purchased_on": "2024-06-14"})
    status, body = call(repo, f"GET {TXNS}", path={"group": "stocks", "ticker": "NVDA"})
    assert status == 200 and body["implicit_opening"] is True and body["transactions"][0]["type"] == "opening"
    status, body = trade(repo, "NVDA", "buy", 10, 250, "2025-11-03")
    assert status == 201 and body["holding"]["shares"] == 30 and body["holding"]["purchased_on"] == "2024-06-14"
    assert no_backfill == []  # already held
    status, body = trade(repo, "NVDA", "sell", 25, 226, "2026-10-01")
    assert body["holding"]["shares"] == 5 and body["holding"]["avg_cost"] == 250
    txns = call(repo, f"GET {TXNS}", path={"group": "stocks", "ticker": "NVDA"})[1]
    assert txns["implicit_opening"] is False and [t["type"] for t in txns["transactions"]] == ["sell", "buy", "opening"]
    years = call(repo, "GET /realized")[1]["years"]
    assert years["2026"]["long"] == pytest.approx(2120.0) and years["2026"]["short"] == pytest.approx(-120.0)


def test_selling_everything_removes_the_holding_but_keeps_gains(repo):
    trade(repo, "AMD", "buy", 2, 100, "2026-01-05")
    call(repo, "POST /rules", body=json.dumps({"type": "rsi_below", "ticker": "AMD", "params": {"threshold": 30}}))
    trade(repo, "AMD", "sell", 2, 150, "2026-02-05")
    assert call(repo, "GET /holdings")[1]["holdings"] == []
    assert call(repo, "GET /rules")[1]["rules"] == []  # nothing left to check
    assert call(repo, "GET /realized")[1]["sales"][0]["gain"] == 100.0


def test_trade_validation(repo):
    assert trade(repo, "AMD", "sell", 1, 10)[1]["message"].startswith("You don't hold")
    trade(repo, "AMD", "buy", 1, 10, "2026-01-05")
    assert "more than" in trade(repo, "AMD", "sell", 5, 10)[1]["message"]
    assert trade(repo, "AMD", "buy", 1, 10, "2999-01-01")[0] == 400  # future date
    assert trade(repo, "VTI", "buy", 1, 10, group="auto")[1]["message"] == "Transactions are tracked for Stock Holdings only"
    body = {"type": "hold", "shares": 1, "price": 1}
    assert call(repo, f"POST {TXNS}", path={"group": "stocks", "ticker": "AMD"}, body=json.dumps(body))[0] == 400


def test_delete_and_redate_transactions(repo):
    trade(repo, "AMD", "buy", 2, 100, "2026-01-05")
    trade(repo, "AMD", "sell", 2, 150, "2026-02-05")
    txns = call(repo, f"GET {TXNS}", path={"group": "stocks", "ticker": "AMD"})[1]["transactions"]
    sell, buy_ = txns
    path = {"group": "stocks", "ticker": "AMD"}
    assert call(repo, f"DELETE {TXNS}/{{txnId}}", path={**path, "txnId": buy_["txn_id"]})[0] == 400  # the sale needs it
    status, body = call(
        repo, f"PUT {TXNS}/{{txnId}}", path={**path, "txnId": buy_["txn_id"]}, body=json.dumps({"date": "2024-01-02"})
    )
    assert status == 200 and call(repo, "GET /realized")[1]["sales"][0]["long_term"] is True
    assert call(repo, f"DELETE {TXNS}/{{txnId}}", path={**path, "txnId": sell["txn_id"]})[0] == 204
    assert call(repo, "GET /holdings")[1]["holdings"][0]["shares"] == 2  # back after the sale is undone


def test_stock_holdings_with_trades_keep_totals_locked(repo):
    trade(repo, "AMD", "buy", 2, 100, "2026-01-05")
    status, _ = call(
        repo,
        "PUT /groups/{group}/holdings/{ticker}",
        path={"group": "stocks", "ticker": "AMD"},
        body=json.dumps({"shares": 9, "avg_cost": 1}),
    )
    assert status == 409
    trade(repo, "AMD", "sell", 1, 150, "2026-02-05")
    assert call(repo, "DELETE /groups/{group}/holdings/{ticker}", path={"group": "stocks", "ticker": "AMD"})[0] == 409
