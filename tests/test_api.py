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
        "PUT /holdings/{ticker}",
        path={"ticker": "vti"},
        body=json.dumps({"shares": 40, "cost_basis": 230, "purchased_on": "2024-03-01"}),
    )
    assert status == 200 and body["ticker"] == "VTI"
    assert no_backfill == ["VTI"]
    # Updating an existing holding does not trigger another backfill
    call(repo, "PUT /holdings/{ticker}", path={"ticker": "VTI"}, body=json.dumps({"shares": 41, "cost_basis": 230}))
    assert no_backfill == ["VTI"]
    status, body = call(repo, "GET /holdings")
    assert [h["shares"] for h in body["holdings"]] == [41]
    assert call(repo, "DELETE /holdings/{ticker}", path={"ticker": "VTI"})[0] == 204
    assert call(repo, "GET /holdings")[1]["holdings"] == []


def test_users_are_isolated(repo):
    call(repo, "PUT /holdings/{ticker}", user="alice", path={"ticker": "VTI"}, body=json.dumps({"shares": 1, "cost_basis": 1}))
    assert call(repo, "GET /holdings", user="bob")[1]["holdings"] == []
    # Bob deleting "VTI" only touches his own (empty) data
    call(repo, "DELETE /holdings/{ticker}", user="bob", path={"ticker": "VTI"})
    assert len(call(repo, "GET /holdings", user="alice")[1]["holdings"]) == 1


@pytest.mark.parametrize(
    "path, body, status",
    [
        ({"ticker": "VTI;DROP"}, {"shares": 1, "cost_basis": 1}, 400),
        ({"ticker": "VTI"}, {"shares": -1, "cost_basis": 1}, 400),
        ({"ticker": "VTI"}, {"shares": "NaN", "cost_basis": 1}, 400),
        ({"ticker": "VTI"}, {"shares": 1}, 400),
        ({"ticker": "VTI"}, {"shares": 1, "cost_basis": 1, "purchased_on": "2999-01-01"}, 400),
        ({"ticker": "VTI"}, {"shares": 1, "cost_basis": 1, "purchased_on": "yesterday"}, 400),
    ],
)
def test_holding_validation(repo, path, body, status):
    assert call(repo, "PUT /holdings/{ticker}", path=path, body=json.dumps(body))[0] == status


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
    call(repo, "PUT /holdings/{ticker}", path={"ticker": "VTI"}, body=json.dumps({"shares": 10, "cost_basis": 100}))
    from datetime import UTC, datetime, timedelta

    start = (datetime.now(UTC).date() - timedelta(days=30)).isoformat()
    repo.put_prices("VTI", price_series(100, 1, 30, start))
    repo.put_prices("SPY", price_series(500, 1, 30, start))
    status, body = call(repo, "GET /portfolio")
    assert status == 200
    assert body["total_value"] == pytest.approx(10 * 129)
    assert body["benchmark"]["ticker"] == "SPY"
    assert body["benchmark"]["portfolio_return"] > body["benchmark"]["benchmark_return"]
    assert len(body["history"]) == 30


def test_news_only_for_held_tickers(repo):
    assert call(repo, "GET /news", query={"ticker": "TSLA"})[0] == 404
    status, body = call(repo, "GET /news")
    assert status == 200 and body["articles"] == []


def test_holdings_cap(repo, monkeypatch):
    monkeypatch.setattr(api, "MAX_HOLDINGS", 2)
    for ticker in ("VTI", "BND"):
        call(repo, "PUT /holdings/{ticker}", path={"ticker": ticker}, body=json.dumps({"shares": 1, "cost_basis": 1}))
    status, body = call(repo, "PUT /holdings/{ticker}", path={"ticker": "QQQ"}, body=json.dumps({"shares": 1, "cost_basis": 1}))
    assert status == 409 and "up to 2" in body["message"]
    # Updating an existing holding is still allowed at the cap
    assert call(repo, "PUT /holdings/{ticker}", path={"ticker": "VTI"}, body=json.dumps({"shares": 2, "cost_basis": 1}))[0] == 200


def buy(repo, ticker, shares, price, **extra):
    return call(
        repo,
        "POST /holdings/{ticker}/purchases",
        path={"ticker": ticker},
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
    status, _ = call(repo, "POST /holdings/{ticker}/purchases", path={"ticker": "VTI"}, body=json.dumps(payload))
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
    stale = repo.get_holding("user-1", "VTI")
    buy(repo, "VTI", 1, 100)  # someone else changes it
    with pytest.raises(ConcurrentUpdateError):
        repo.replace_holding_if_unchanged("user-1", {"ticker": "VTI", "shares": 99, "cost_basis": 1}, stale)


def test_purchase_respects_holdings_cap(repo, monkeypatch):
    monkeypatch.setattr(api, "MAX_HOLDINGS", 1)
    buy(repo, "VTI", 1, 1)
    assert buy(repo, "BND", 1, 1)[0] == 409
    assert buy(repo, "VTI", 1, 1)[0] == 200
