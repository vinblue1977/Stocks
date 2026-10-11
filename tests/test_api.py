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
