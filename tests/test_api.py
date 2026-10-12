import json

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
        body=json.dumps({"shares": 40, "cost_basis": 230, "purchased_on": "2024-03-01"}),
    )
    assert status == 200 and body["ticker"] == "VTI"
    assert no_backfill == ["VTI"]
    # Updating an existing holding does not trigger another backfill
    call(
        repo,
        "PUT /groups/{group}/holdings/{ticker}",
        path={"group": "stocks", "ticker": "VTI"},
        body=json.dumps({"shares": 41, "cost_basis": 230}),
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
        body=json.dumps({"shares": 1, "cost_basis": 1}),
    )
    assert call(repo, "GET /holdings", user="bob")[1]["holdings"] == []
    # Bob deleting "VTI" only touches his own (empty) data
    call(repo, "DELETE /groups/{group}/holdings/{ticker}", user="bob", path={"group": "stocks", "ticker": "VTI"})
    assert len(call(repo, "GET /holdings", user="alice")[1]["holdings"]) == 1


@pytest.mark.parametrize(
    "path, body, status",
    [
        ({"group": "stocks", "ticker": "VTI;DROP"}, {"shares": 1, "cost_basis": 1}, 400),
        ({"group": "stocks", "ticker": "VTI"}, {"shares": -1, "cost_basis": 1}, 400),
        ({"group": "stocks", "ticker": "VTI"}, {"shares": "NaN", "cost_basis": 1}, 400),
        ({"group": "stocks", "ticker": "VTI"}, {"shares": 1}, 400),
        ({"group": "stocks", "ticker": "VTI"}, {"shares": 1, "cost_basis": 1, "purchased_on": "2999-01-01"}, 400),
        ({"group": "stocks", "ticker": "VTI"}, {"shares": 1, "cost_basis": 1, "purchased_on": "yesterday"}, 400),
    ],
)
def test_holding_validation(repo, path, body, status):
    assert call(repo, "PUT /groups/{group}/holdings/{ticker}", path=path, body=json.dumps(body))[0] == status


def test_rejects_bad_bodies(repo):
    assert call(repo, "POST /rules", body="not json")[0] == 400
    assert call(repo, "POST /rules", body="[1, 2]")[0] == 400
    assert call(repo, "POST /rules", body=json.dumps({"pad": "x" * 20_000}))[0] == 413


def test_rules_lifecycle(repo):
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
        body=json.dumps({"shares": 10, "cost_basis": 100}),
    )
    from datetime import UTC, datetime, timedelta

    start = (datetime.now(UTC).date() - timedelta(days=30)).isoformat()
    repo.put_prices("VTI", price_series(100, 1, 30, start))
    repo.put_prices("SPY", price_series(500, 1, 30, start))
    status, body = call(repo, "GET /portfolio")
    assert status == 200
    overall = body["groups"]["overall"]
    assert overall["total_value"] == pytest.approx(10 * 129)
    assert body["groups"]["stocks"]["total_value"] == pytest.approx(10 * 129)
    assert body["groups"]["auto"]["total_value"] == 0
    assert overall["benchmark"]["ticker"] == "SPY" == body["benchmark_ticker"]
    assert overall["benchmark"]["portfolio_return"] > overall["benchmark"]["benchmark_return"]
    assert len(overall["history"]) == 30 and len(body["benchmark_history"]) == 30


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
            body=json.dumps({"shares": 1, "cost_basis": 1}),
        )
    status, body = call(
        repo,
        "PUT /groups/{group}/holdings/{ticker}",
        path={"group": "stocks", "ticker": "QQQ"},
        body=json.dumps({"shares": 1, "cost_basis": 1}),
    )
    assert status == 409 and "up to 2 different tickers" in body["message"]
    # Updating an existing holding is still allowed at the cap
    assert (
        call(
            repo,
            "PUT /groups/{group}/holdings/{ticker}",
            path={"group": "stocks", "ticker": "VTI"},
            body=json.dumps({"shares": 2, "cost_basis": 1}),
        )[0]
        == 200
    )


def buy(repo, ticker, shares, price, group="stocks", **extra):
    return call(
        repo,
        "POST /groups/{group}/holdings/{ticker}/purchases",
        path={"group": group, "ticker": ticker},
        body=json.dumps({"shares": shares, "price": price, **extra}),
    )


def test_purchase_creates_then_averages(repo, no_backfill):
    status, body = buy(repo, "VTI", 10, 250, purchased_on="2026-03-02")
    assert status == 201 and body["previous"] is None
    assert body["holding"]["shares"] == 10 and body["holding"]["cost_basis"] == 250
    assert no_backfill == ["VTI"]

    status, body = buy(repo, "vti", 5, 280, purchased_on="2026-08-03")
    assert status == 200
    assert body["holding"]["shares"] == 15
    assert body["holding"]["cost_basis"] == pytest.approx(260.0)  # (10*250 + 5*280) / 15
    assert body["holding"]["purchased_on"] == "2026-03-02"  # earliest purchase kept
    assert body["previous"]["shares"] == 10
    assert no_backfill == ["VTI"]  # no second backfill for an existing ticker


def test_purchase_fractional_shares(repo):
    buy(repo, "QQQ", 0.5, 400)
    _, body = buy(repo, "QQQ", 0.25, 520)
    assert body["holding"]["shares"] == 0.75
    assert body["holding"]["cost_basis"] == pytest.approx(440.0)


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

    buy(repo, "VTI", 10, 100)
    real = repo.replace_holding_if_unchanged
    calls = {"n": 0}

    def flaky(user_id, holding, previous):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConcurrentUpdateError("VTI")
        return real(user_id, holding, previous)

    monkeypatch.setattr(repo, "replace_holding_if_unchanged", flaky)
    status, body = buy(repo, "VTI", 10, 200)
    assert status == 200 and body["holding"]["cost_basis"] == pytest.approx(150.0)
    assert calls["n"] == 2


def test_conditional_write_detects_stale_read(repo):
    from portfolio.db import ConcurrentUpdateError

    buy(repo, "VTI", 10, 100)
    stale = repo.get_holding("user-1", "stocks", "VTI")
    buy(repo, "VTI", 1, 100)  # someone else changes it
    with pytest.raises(ConcurrentUpdateError):
        repo.replace_holding_if_unchanged("user-1", {"ticker": "VTI", "shares": 99, "cost_basis": 1}, stale)


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
        body=json.dumps({"shares": shares, "cost_basis": cost}),
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
    assert [(h["shares"], h["cost_basis"]) for h in stocks] == [(2, 600)]  # untouched
    assert auto[0]["shares"] == 8 and auto[0]["cost_basis"] == pytest.approx(725.0)  # (6*700 + 2*800) / 8
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
    groups = call(repo, "GET /portfolio")[1]["groups"]
    assert groups["stocks"]["total_value"] == 200 and groups["stocks"]["total_gain"] == 20
    assert groups["auto"]["total_value"] == 500 and groups["auto"]["total_gain"] == 60
    assert groups["overall"]["total_value"] == 700 and groups["overall"]["total_gain"] == 80
    assert groups["overall"]["history"][-1]["value"] == 700  # VOO shares from both groups
    assert {(p["ticker"], p["group"]) for p in groups["overall"]["positions"]} == {
        ("VOO", "stocks"),
        ("VOO", "auto"),
        ("BND", "auto"),
    }
