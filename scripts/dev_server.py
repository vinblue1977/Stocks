"""Local preview of the dashboard: `make dev`, then open http://localhost:8765.

Serves web/ with sign-in stubbed out, and answers /api/* with the real API handler (src/portfolio/handlers/api.py)
on an in-memory DynamoDB table (moto) filled with sample data. Nothing reaches AWS: fake credentials are set
before boto3 loads, and every AWS call goes to moto.

Options:
    --port N    listen on N (default 8765, or $PORT)
    --slow      delay /portfolio by 3 s, to see the saved Overview with "Updating…"
    --late      leave out the last two closes and the news run, to see the late-update warning
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
import time
from datetime import UTC, date, datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "build" / "devsite"
USER = "local-user"

# Sample tickers: (ticker, last price, how far below today it started a year+ ago, wobble)
PRICES = [
    ("VTI", 312.4, 0.14, 0.02),
    ("AMZN", 221.3, 0.20, 0.04),
    ("GOOGL", 245.1, 0.30, 0.04),
    ("NVDA", 182.4, 0.35, 0.05),
    ("ITOT", 139.2, 0.14, 0.02),
    ("DGRO", 66.75, 0.09, 0.015),
    ("AAPL", 238.1, 0.10, 0.03),
    ("MSFT", 455.3, 0.12, 0.03),
    ("SCHD", 26.81, 0.05, 0.01),
    ("VXUS", 70.15, 0.12, 0.02),
    ("AMD", 162.4, 0.30, 0.04),
]
HOLDINGS = [
    ("stocks", "AMZN", 20, 182.4),
    ("stocks", "GOOGL", 15, 168.9),
    ("stocks", "NVDA", 40, 118.25),
    ("stocks", "VTI", 40, 230),
    ("auto", "ITOT", 85.2213, 107.95),
    ("auto", "VTI", 12.5032, 271.93),
    ("auto", "DGRO", 70.418, 58.22),
    ("partner_stocks", "AAPL", 12, 214.17),
    ("partner_stocks", "MSFT", 8, 401.5),
    ("partner_stocks", "SCHD", 95, 26.1),
    ("partner_auto", "SCHD", 182.30411, 25.23),
    ("partner_auto", "VXUS", 60.1182, 63.21),
]
WATCHED = ["AMD", "TSLA"]  # TSLA has no prices: shows the "loading price history" row


def isolate_from_aws() -> None:
    """Fake credentials and a local table name, set before boto3 is imported anywhere."""
    fake = "testing"  # moto accepts any credentials; these can't reach a real account
    os.environ.update(
        {
            "AWS_DEFAULT_REGION": "us-east-1",
            "AWS_ACCESS_KEY_ID": fake,
            "AWS_SECRET_ACCESS_KEY": fake,
            "AWS_SESSION_TOKEN": fake,
            "TABLE_NAME": "PortfolioTable",
            "OWNER_NAME": "Me",
            "PARTNER_NAME": "Partner",
        }
    )
    for key in ("AWS_PROFILE", "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE"):
        os.environ.pop(key, None)


def _series(end_price: float, drift: float, wobble: float, end: date) -> list[dict[str, Any]]:
    rows, day, i = [], end - timedelta(days=400), 0
    while day <= end:
        if day.weekday() < 5:
            close = end_price * (1 - drift * (1 - i / 280)) * (1 + wobble * math.sin(i / 9))
            rows.append({"date": day.isoformat(), "close": round(close, 2), "volume": 1})
            i += 1
        day += timedelta(days=1)
    return rows


def seed(repo: Any, *, late: bool = False, today: date | None = None) -> None:
    """Sample holdings, prices, signals, news, rules, alerts, earnings and analyst data."""
    from portfolio import signals

    today = today or date.today()
    last_close = today - timedelta(days=3 if late else 0)
    for ticker, price, drift, wobble in PRICES:
        repo.put_prices(ticker, _series(price, drift, wobble, last_close))
    for group, ticker, shares, cost in HOLDINGS:
        repo.put_holding(USER, {"ticker": ticker, "group": group, "shares": shares, "avg_cost": cost})
    for ticker in WATCHED:
        repo.put_watch(USER, ticker)
    for ticker in repo.tracked_tickers():
        signal = signals.compute_signals(ticker, repo.price_history(ticker))
        if signal:
            repo.put_signal(ticker, signal)

    now = datetime.now(UTC)
    stamp = now.isoformat(timespec="seconds")
    for ticker in ("NVDA", "AMZN", "AMD"):
        repo.put_news(
            ticker,
            [
                {
                    "article_id": f"{ticker}-{n}",
                    "headline": f"Sample headline {n} about {ticker}",
                    "summary": "Sample article text for the local preview.",
                    "source": "Sample Wire",
                    "url": "https://example.com/",
                    "published_at": (now - timedelta(hours=6 * n)).isoformat(timespec="seconds"),
                    "first_seen": (now - timedelta(hours=6 * n)).isoformat(timespec="seconds"),
                    "score": 0.4 - 0.2 * n,
                }
                for n in range(3)
            ],
        )
        repo.put_brief(
            ticker,
            today.isoformat(),
            {
                "summary": f"Sample summary: headlines about {ticker} this week were mostly about demand and new products.",
                "sentiment_7d": 0.21,
                "label": "Positive",
                "headline_count": 3,
                "generated_at": stamp,
            },
        )
    repo.put_brief(
        "ALL", today.isoformat(), {"summary": "Sample portfolio briefing for the local preview.", "generated_at": stamp}
    )

    rule = repo.create_rule(USER, {"type": "price_below", "ticker": "NVDA", "params": {"price": 200}, "enabled": True})
    repo.create_rule(USER, {"type": "rsi_above", "ticker": "AMD", "params": {"threshold": 70}, "enabled": True})
    repo.record_alert(USER, rule["rule_id"], "NVDA closed at $182.40, below your $200.00 level")

    report = (today + timedelta(days=12)).isoformat()
    repo.put_earnings("NVDA", {"date": report, "hour": "amc", "eps_estimate": 1.18})
    months = [(today.replace(day=1) - timedelta(days=31 * k)).strftime("%Y-%m") for k in range(4)]
    for ticker, buy, hold, sell in (("NVDA", 65, 3, 1), ("AMZN", 70, 4, 0), ("AMD", 41, 12, 1)):
        repo.put_insights(
            ticker,
            {
                "analysts": [{"period": m, "buy": buy - k, "hold": hold + k % 2, "sell": sell} for k, m in enumerate(months)],
                "insiders": {
                    "bought": 0,
                    "buys": 0,
                    "sold": 1006651,
                    "sells": 1,
                    "routine": 1,
                    "unpriced_buys": 0,
                    "unpriced_sells": 0,
                    "trades": [
                        {
                            "date": (today - timedelta(days=8)).isoformat(),
                            "name": "Sample Insider A",
                            "code": "G",
                            "shares": 500000,
                            "price": None,
                        },
                        {
                            "date": (today - timedelta(days=24)).isoformat(),
                            "name": "Sample Insider B",
                            "code": "S",
                            "shares": 4499,
                            "price": 223.75,
                        },
                    ],
                },
            },
        )
    if not late:
        repo.put_job_run("news_ingest")


def build_site() -> None:
    """Copy web/ to build/devsite, with a local config.json and a sign-in stub in place of auth.js."""
    shutil.rmtree(SITE, ignore_errors=True)
    shutil.copytree(ROOT / "web", SITE)
    (SITE / "config.json").write_text(json.dumps({"apiUrl": "/api"}))
    (SITE / "js" / "auth.js").write_text(auth_stub())


def auth_stub() -> str:
    """Same exports as web/js/auth.js, signed in as a local user (tests check the export list matches)."""
    real = (ROOT / "web" / "js" / "auth.js").read_text()
    constants = "\n".join(re.findall(r"^export const .+;.*$", real, flags=re.MULTILINE))
    return (
        "// Local preview only: sign-in is skipped. Generated by scripts/dev_server.py.\n"
        f"{constants}\n"
        "export async function login() {}\n"
        "export function logout() { location.reload(); }\n"
        "export async function initAuth() { return true; }\n"
        "export async function getAccessToken() { return 'local'; }\n"
        "export function currentEmail() { return 'you@example.com'; }\n"
        "export function hasSession() { return true; }\n"
        "export function addPasskeyUrl() { return '#'; }\n"
    )


def make_handler(repo: Any, slow: bool) -> type[SimpleHTTPRequestHandler]:
    from portfolio.handlers import api

    routes = [
        (method, path, re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", path) + "$"))
        for method, path in (key.split(" ", 1) for key in api.ROUTES)
    ]

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(SITE), **kwargs)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - matches the base signature
            pass

        def _api(self, method: str) -> None:
            path, _, query = self.path[len("/api") :].partition("?")
            params = dict(p.split("=", 1) for p in query.split("&") if "=" in p) or None
            if params:
                from urllib.parse import unquote

                params = {k: unquote(v) for k, v in params.items()}
            for route_method, route, pattern in routes:
                if route_method == method and (match := pattern.match(path)):
                    if slow and route == "/portfolio":
                        time.sleep(3)
                    length = int(self.headers.get("Content-Length") or 0)
                    event = {
                        "routeKey": f"{route_method} {route}",
                        "requestContext": {"authorizer": {"jwt": {"claims": {"sub": USER}}}},
                        "pathParameters": match.groupdict(),
                        "queryStringParameters": params,
                        "body": self.rfile.read(length).decode() if length else None,
                    }
                    result = api.handler(event, None, repo=repo)
                    break
            else:
                result = {"statusCode": 404, "body": json.dumps({"message": "Not found"})}
            self.send_response(result["statusCode"])
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write((result.get("body") or "").encode())

        def do_GET(self) -> None:  # noqa: N802 - http.server naming
            self._api("GET") if self.path.startswith("/api/") else super().do_GET()

        def do_POST(self) -> None:  # noqa: N802
            self._api("POST")

        def do_PUT(self) -> None:  # noqa: N802
            self._api("PUT")

        def do_DELETE(self) -> None:  # noqa: N802
            self._api("DELETE")

    return Handler


def create_table() -> Any:
    import boto3

    resource: Any = boto3.resource("dynamodb")
    keys = ("pk", "sk", "gsi1pk", "gsi1sk")
    return resource.create_table(
        TableName=os.environ["TABLE_NAME"],
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": k, "AttributeType": "S"} for k in keys],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "gsi1",
                "Projection": {"ProjectionType": "ALL"},
                "KeySchema": [{"AttributeName": "gsi1pk", "KeyType": "HASH"}, {"AttributeName": "gsi1sk", "KeyType": "RANGE"}],
            }
        ],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    parser.add_argument("--slow", action="store_true")
    parser.add_argument("--late", action="store_true")
    args = parser.parse_args()

    isolate_from_aws()
    sys.path.insert(0, str(ROOT / "src"))
    from moto import mock_aws

    mock_aws().start()
    from portfolio.db import Repository

    repo = Repository(create_table())
    seed(repo, late=args.late)
    build_site()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(repo, args.slow))
    print(f"Local preview (sample data, no AWS): http://localhost:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
