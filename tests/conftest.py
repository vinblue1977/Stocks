from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ["TABLE_NAME"] = "PortfolioTable"
os.environ["BENCHMARK_TICKER"] = "SPY"


@pytest.fixture
def table() -> Iterator[Any]:
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        tbl = dynamodb.create_table(
            TableName="PortfolioTable",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
                {"AttributeName": "gsi1pk", "AttributeType": "S"},
                {"AttributeName": "gsi1sk", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "gsi1",
                    "KeySchema": [
                        {"AttributeName": "gsi1pk", "KeyType": "HASH"},
                        {"AttributeName": "gsi1sk", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield tbl


@pytest.fixture
def repo(table: Any) -> Any:
    from portfolio.db import Repository

    return Repository(table)


def api_event(
    route: str, user: str = "user-1", body: str | None = None, path: dict | None = None, query: dict | None = None
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "routeKey": route,
        "requestContext": {"authorizer": {"jwt": {"claims": {"sub": user}}}},
        "pathParameters": path or {},
        "queryStringParameters": query,
    }
    if body is not None:
        event["body"] = body
    return event


def price_series(start: float, daily_step: float, days: int, start_date: str = "2025-01-01") -> list[dict[str, Any]]:
    from datetime import date, timedelta

    d0 = date.fromisoformat(start_date)
    return [
        {"date": (d0 + timedelta(days=i)).isoformat(), "close": round(start + daily_step * i, 4), "volume": 1000}
        for i in range(days)
    ]
