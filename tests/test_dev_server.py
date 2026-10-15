"""The local preview (scripts/dev_server.py): sample data loads, the API answers on it, and the sign-in stub keeps
the same exports as web/js/auth.js."""

import importlib.util
import json
import re
from pathlib import Path

import pytest
from conftest import api_event

from portfolio.handlers import api

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("dev_server", ROOT / "scripts" / "dev_server.py")
dev_server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev_server)


def exports(source):
    return set(re.findall(r"^export (?:async )?(?:function|const) (\w+)", source, flags=re.MULTILINE))


def test_auth_stub_matches_real_exports():
    real = (ROOT / "web" / "js" / "auth.js").read_text()
    assert exports(dev_server.auth_stub()) == exports(real)


@pytest.mark.parametrize("late", [False, True])
def test_sample_data_serves_every_page(repo, late):
    dev_server.seed(repo, late=late)

    def get(route, **query):
        response = api.handler(api_event(route, user=dev_server.USER, query=query or None), None, repo=repo)
        assert response["statusCode"] == 200, (route, response["body"])
        return json.loads(response["body"])

    portfolio = get("GET /portfolio")
    assert portfolio["portfolios"]["household"]["overall"]["total_value"] > 0
    assert portfolio["freshness"]["prices_late"] is late
    assert {i["ticker"] for i in get("GET /insights")["insights"]} == {"NVDA", "AMZN", "AMD"}
    assert get("GET /watchlist")["watchlist"]
    assert get("GET /alerts")["alerts"]
    assert get("GET /changes", since="2020-01-01T00:00:00Z")["headlines"] > 0
    for route in ("GET /signals", "GET /news", "GET /rules", "GET /earnings", "GET /holdings", "GET /checkup"):
        get(route)
