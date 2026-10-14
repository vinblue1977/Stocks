"""DynamoDB single-table repository.

Key design (pk / sk):
    USER#<uid>      HOLDING#<group>#<ticker>     a position: shares + avg_cost (per share); group is
                                                 one of GROUPS (gsi1: HOLDINGS / <ticker>#<uid>#<group>)
    USER#<uid>      RULE#<rule_id>               an alert rule (gsi1: RULES / <uid>#<rule_id>)
    USER#<uid>      RULESTATE#<rule_id>          whether the rule is currently triggered
    USER#<uid>      ALERT#<iso_ts>#<rule_id>     alert history (expires after 180 days)
    USER#<uid>      WATCH#<ticker>               a watched ticker, not held (gsi1: WATCHLIST / <ticker>#<uid>)
    USER#<uid>      TARGETS                      the user's target mix by area (Checkup)
    USER#<uid>      PUSH#<hash of endpoint>      a phone's Web Push subscription (endpoint + keys)
    USER#<uid>      TXN#<group>#<ticker>#<date>#<id>  a buy, sell or opening entry (Stock Holdings only)
    USER#<uid>      CHECKUP#<yyyy-mm>            the monthly checkup summary
    PRICE#<ticker>  <yyyy-mm-dd>                 daily adjusted close
    PRICEMETA#<ticker> BACKFILL                  set once the full history has loaded
    NEWS#<ticker>   <iso_ts>#<article_id>        scored headline (expires after 30 days)
    BRIEF#<ticker>  <yyyy-mm-dd>                 AI news summary; BRIEF#ALL is the portfolio brief
    SIGNAL#<ticker> LATEST                       latest signal snapshot
    EARNINGS#<ticker> NEXT                       next earnings date, from Finnhub (stocks only)
    INSIGHT#<ticker> LATEST                      analyst ratings and insider trades, from Finnhub (stocks only)
    QUOTES          LATEST                       intraday prices for all tickers (market hours only)
    JOB#<name>      LAST                         when a nightly job last finished (late-update warning)
"""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key

from portfolio.config import get_config

NEWS_TTL_SECONDS = 30 * 24 * 3600
ALERT_TTL_SECONDS = 180 * 24 * 3600


# A group is a person ("mine" = the account owner, "partner" = e.g. a spouse) plus a kind
# ("stocks" = Stock Holdings, "auto" = Automatic Investment). The owner's groups keep their
# original names, so holdings saved before partner groups existed need no migration.
PEOPLE = ("mine", "partner")
KINDS = {"stocks": "Stock Holdings", "auto": "Automatic Investment"}  # kind -> display label
DEFAULT_GROUP = "stocks"


def group_for(person: str, kind: str) -> str:
    return kind if person == "mine" else f"{person}_{kind}"


GROUPS = tuple(group_for(p, k) for p in PEOPLE for k in KINDS)  # stocks, auto, partner_stocks, partner_auto


def person_of(group: str) -> str:
    return "partner" if group.startswith("partner_") else "mine"


def kind_of(group: str) -> str:
    return group.removeprefix("partner_")


class ConcurrentUpdateError(Exception):
    """The item changed between read and write; the caller should re-read and retry."""


def to_dynamo(value: Any) -> Any:
    """Convert floats (which DynamoDB rejects) to Decimal, recursively."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, Mapping):
        return {k: to_dynamo(v) for k, v in value.items() if v is not None}
    if isinstance(value, list | tuple):
        return [to_dynamo(v) for v in value]
    return value


def from_dynamo(value: Any) -> Any:
    """Convert Decimals back to int/float so results serialize cleanly to JSON."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {k: from_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [from_dynamo(v) for v in value]
    return value


_INTERNAL_KEYS = {"pk", "sk", "gsi1pk", "gsi1sk", "expires_at"}


def _clean(item: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in from_dynamo(item).items() if k not in _INTERNAL_KEYS}


class Repository:
    def __init__(self, table: Any | None = None) -> None:
        self.table = table or _table()

    # ---------- queries ----------
    def _query_all(self, **kwargs: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        while True:
            response = self.table.query(**kwargs)
            items.extend(response.get("Items", []))
            last = response.get("LastEvaluatedKey")
            if not last:
                return items
            kwargs["ExclusiveStartKey"] = last

    # ---------- holdings ----------
    # A holding belongs to a group (see GROUPS). The same ticker can exist in several groups,
    # each with its own shares and average cost.
    @staticmethod
    def _holding_keys(user_id: str, group: str, ticker: str) -> dict[str, str]:
        return {
            "pk": f"USER#{user_id}",
            "sk": f"HOLDING#{group}#{ticker}",
            "gsi1pk": "HOLDINGS",
            "gsi1sk": f"{ticker}#{user_id}#{group}",
        }

    def _holding_item(self, user_id: str, holding: Mapping[str, Any]) -> dict[str, Any]:
        group = holding.get("group", DEFAULT_GROUP)
        return {**self._holding_keys(user_id, group, holding["ticker"]), "updated_at": _now_iso(), **holding, "group": group}

    def _all_holding_items(self) -> list[dict[str, Any]]:
        return self._query_all(IndexName="gsi1", KeyConditionExpression=Key("gsi1pk").eq("HOLDINGS"))

    @staticmethod
    def _holding_out(item: Mapping[str, Any]) -> dict[str, Any]:
        clean = _clean(item)
        clean.setdefault("group", DEFAULT_GROUP)  # items written before groups existed
        return clean

    def list_holdings(self, user_id: str, group: str | None = None) -> list[dict[str, Any]]:
        items = self._query_all(KeyConditionExpression=Key("pk").eq(f"USER#{user_id}") & Key("sk").begins_with("HOLDING#"))
        holdings = [self._holding_out(i) for i in items]
        return [h for h in holdings if group is None or h["group"] == group]

    def put_holding(self, user_id: str, holding: Mapping[str, Any]) -> dict[str, Any]:
        item = self._holding_item(user_id, holding)
        self.table.put_item(Item=to_dynamo(item))
        return self._holding_out(item)

    def get_holding(self, user_id: str, group: str, ticker: str) -> dict[str, Any] | None:
        key = {"pk": f"USER#{user_id}", "sk": f"HOLDING#{group}#{ticker}"}
        item = self.table.get_item(Key=key, ConsistentRead=True).get("Item")
        return self._holding_out(item) if item else None

    def replace_holding_if_unchanged(
        self, user_id: str, holding: Mapping[str, Any], previous: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        """Write `holding` only if the stored one still matches `previous` (None = must not exist).

        Prevents two purchases recorded at the same moment from overwriting each other's math.
        Raises ConcurrentUpdateError when someone else changed the holding first.
        """
        item = self._holding_item(user_id, holding)
        if previous is None:
            condition: Any = Attr("pk").not_exists()
        else:
            avg_cost = to_dynamo(float(previous["avg_cost"]))
            condition = Attr("shares").eq(to_dynamo(float(previous["shares"]))) & Attr("avg_cost").eq(avg_cost)
        try:
            self.table.put_item(Item=to_dynamo(item), ConditionExpression=condition)
        except self.table.meta.client.exceptions.ConditionalCheckFailedException as exc:
            raise ConcurrentUpdateError(holding["ticker"]) from exc
        return self._holding_out(item)

    def delete_holding(self, user_id: str, group: str, ticker: str) -> None:
        self.table.delete_item(Key={"pk": f"USER#{user_id}", "sk": f"HOLDING#{group}#{ticker}"})

    def migrate_legacy_holdings(self) -> list[str]:
        """Move holdings stored before groups existed (sk HOLDING#<ticker>) into Stock Holdings.

        Idempotent: new-format items are left alone. Returns "<user>:<ticker>" for each item moved.
        """
        moved = []
        for item in self._all_holding_items():
            if item["sk"].count("#") != 1:
                continue
            user_id = item["pk"].removeprefix("USER#")
            ticker = item["sk"].removeprefix("HOLDING#")
            data = {k: v for k, v in item.items() if k not in ("pk", "sk", "gsi1pk", "gsi1sk")}
            new_item = {**data, **self._holding_keys(user_id, DEFAULT_GROUP, ticker), "group": DEFAULT_GROUP}
            # Write the new item first (only if absent), then delete the old one.
            try:
                self.table.put_item(Item=new_item, ConditionExpression=Attr("pk").not_exists())
            except self.table.meta.client.exceptions.ConditionalCheckFailedException:
                pass  # already migrated earlier; just remove the old copy
            self.table.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
            moved.append(f"{user_id}:{ticker}")
        return moved

    def all_holdings(self) -> list[dict[str, Any]]:
        """Every holding across all users (used by scheduled jobs). Adds `user_id`."""
        items = self._all_holding_items()
        result = []
        for item in items:
            clean = self._holding_out(item)
            clean["user_id"] = item["pk"].removeprefix("USER#")
            result.append(clean)
        return result

    def tracked_tickers(self) -> list[str]:
        """Tickers the jobs keep prices and signals for: held and watched."""
        return sorted({h["ticker"] for h in self.all_holdings()} | {w["ticker"] for w in self.all_watched()})

    # ---------- watchlist ----------
    # Tickers followed without holding them: prices, signals, news and rules, but never in totals.
    def list_watchlist(self, user_id: str) -> list[dict[str, Any]]:
        items = self._query_all(KeyConditionExpression=Key("pk").eq(f"USER#{user_id}") & Key("sk").begins_with("WATCH#"))
        return [_clean(i) for i in items]

    def held_tickers(self, user_id: str) -> set[str]:
        return {h["ticker"] for h in self.list_holdings(user_id)}

    def watched_tickers(self, user_id: str) -> set[str]:
        return {w["ticker"] for w in self.list_watchlist(user_id)}

    def put_watch(self, user_id: str, ticker: str) -> dict[str, Any]:
        item = {
            "pk": f"USER#{user_id}",
            "sk": f"WATCH#{ticker}",
            "gsi1pk": "WATCHLIST",
            "gsi1sk": f"{ticker}#{user_id}",
            "ticker": ticker,
            "added_at": _now_iso(),
        }
        self.table.put_item(Item=item)
        return _clean(item)

    def delete_watch(self, user_id: str, ticker: str) -> None:
        self.table.delete_item(Key={"pk": f"USER#{user_id}", "sk": f"WATCH#{ticker}"})

    def all_watched(self) -> list[dict[str, Any]]:
        """Every watched ticker across all users (used by scheduled jobs). Adds `user_id`."""
        items = self._query_all(IndexName="gsi1", KeyConditionExpression=Key("gsi1pk").eq("WATCHLIST"))
        return [{**_clean(i), "user_id": i["pk"].removeprefix("USER#")} for i in items]

    # ---------- rules ----------
    def list_rules(self, user_id: str) -> list[dict[str, Any]]:
        items = self._query_all(KeyConditionExpression=Key("pk").eq(f"USER#{user_id}") & Key("sk").begins_with("RULE#"))
        return [_clean(i) for i in items]

    def create_rule(self, user_id: str, rule: Mapping[str, Any]) -> dict[str, Any]:
        rule_id = uuid.uuid4().hex[:12]
        item = {
            "pk": f"USER#{user_id}",
            "sk": f"RULE#{rule_id}",
            "gsi1pk": "RULES",
            "gsi1sk": f"{user_id}#{rule_id}",
            "rule_id": rule_id,
            "created_at": _now_iso(),
            **rule,
        }
        self.table.put_item(Item=to_dynamo(item))
        return _clean(item)

    def delete_rule(self, user_id: str, rule_id: str) -> None:
        self.table.delete_item(Key={"pk": f"USER#{user_id}", "sk": f"RULE#{rule_id}"})
        self.table.delete_item(Key={"pk": f"USER#{user_id}", "sk": f"RULESTATE#{rule_id}"})

    def all_rules(self) -> list[dict[str, Any]]:
        items = self._query_all(IndexName="gsi1", KeyConditionExpression=Key("gsi1pk").eq("RULES"))
        result = []
        for item in items:
            clean = _clean(item)
            clean["user_id"] = item["pk"].removeprefix("USER#")
            result.append(clean)
        return result

    def get_rule_state(self, user_id: str, rule_id: str) -> dict[str, Any] | None:
        item = self.table.get_item(Key={"pk": f"USER#{user_id}", "sk": f"RULESTATE#{rule_id}"}).get("Item")
        return _clean(item) if item else None

    def put_rule_state(self, user_id: str, rule_id: str, triggered: bool, detail: str) -> None:
        self.table.put_item(
            Item={
                "pk": f"USER#{user_id}",
                "sk": f"RULESTATE#{rule_id}",
                "triggered": triggered,
                "detail": detail,
                "updated_at": _now_iso(),
            }
        )

    def record_alert(self, user_id: str, rule_id: str, message: str) -> None:
        now = _now_iso()
        self.table.put_item(
            Item={
                "pk": f"USER#{user_id}",
                "sk": f"ALERT#{now}#{rule_id}",
                "rule_id": rule_id,
                "message": message,
                "created_at": now,
                "expires_at": int(time.time()) + ALERT_TTL_SECONDS,
            }
        )

    def list_alerts(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        response = self.table.query(
            KeyConditionExpression=Key("pk").eq(f"USER#{user_id}") & Key("sk").begins_with("ALERT#"),
            ScanIndexForward=False,
            Limit=limit,
        )
        return [_clean(i) for i in response.get("Items", [])]

    # ---------- transactions (Stock Holdings) ----------
    @staticmethod
    def _txn_sk(group: str, ticker: str, txn: Mapping[str, Any]) -> str:
        return f"TXN#{group}#{ticker}#{txn.get('date') or '0000-00-00'}#{txn['txn_id']}"

    def list_txns(self, user_id: str, group: str | None = None, ticker: str | None = None) -> list[dict[str, Any]]:
        prefix = "TXN#" + (f"{group}#" if group else "") + (f"{ticker}#" if group and ticker else "")
        items = self._query_all(
            KeyConditionExpression=Key("pk").eq(f"USER#{user_id}") & Key("sk").begins_with(prefix), ConsistentRead=True
        )
        return [_clean(i) for i in items]

    def put_txn(self, user_id: str, group: str, ticker: str, txn: Mapping[str, Any]) -> dict[str, Any]:
        item = {"pk": f"USER#{user_id}", "sk": self._txn_sk(group, ticker, txn), "group": group, "ticker": ticker, **txn}
        self.table.put_item(Item=to_dynamo(item))
        return _clean(item)

    def delete_txn(self, user_id: str, group: str, ticker: str, txn: Mapping[str, Any]) -> None:
        self.table.delete_item(Key={"pk": f"USER#{user_id}", "sk": self._txn_sk(group, ticker, txn)})

    @staticmethod
    def new_txn_id() -> str:
        return uuid.uuid4().hex[:12]

    # ---------- push subscriptions ----------
    @staticmethod
    def _push_sk(endpoint: str) -> str:
        return "PUSH#" + hashlib.sha256(endpoint.encode()).hexdigest()[:24]

    def put_push(self, user_id: str, subscription: Mapping[str, Any]) -> None:
        item = {"pk": f"USER#{user_id}", "sk": self._push_sk(subscription["endpoint"]), **subscription, "created_at": _now_iso()}
        self.table.put_item(Item=to_dynamo(item))

    def list_push(self, user_id: str) -> list[dict[str, Any]]:
        items = self._query_all(KeyConditionExpression=Key("pk").eq(f"USER#{user_id}") & Key("sk").begins_with("PUSH#"))
        return [_clean(i) for i in items]

    def delete_push(self, user_id: str, endpoint: str) -> None:
        self.table.delete_item(Key={"pk": f"USER#{user_id}", "sk": self._push_sk(endpoint)})

    # ---------- checkup ----------
    def get_targets(self, user_id: str) -> dict[str, Any] | None:
        item = self.table.get_item(Key={"pk": f"USER#{user_id}", "sk": "TARGETS"}).get("Item")
        return _clean(item) if item else None

    def put_targets(self, user_id: str, targets: Mapping[str, Any]) -> dict[str, Any]:
        item = {"pk": f"USER#{user_id}", "sk": "TARGETS", **targets, "updated_at": _now_iso()}
        self.table.put_item(Item=to_dynamo(item))
        return _clean(item)

    def put_checkup_summary(self, user_id: str, month: str, record: Mapping[str, Any]) -> None:
        self.table.put_item(Item=to_dynamo({"pk": f"USER#{user_id}", "sk": f"CHECKUP#{month}", **record}))

    def latest_checkup_summary(self, user_id: str) -> dict[str, Any] | None:
        response = self.table.query(
            KeyConditionExpression=Key("pk").eq(f"USER#{user_id}") & Key("sk").begins_with("CHECKUP#"),
            ScanIndexForward=False,
            Limit=1,
        )
        items = response.get("Items", [])
        return _clean(items[0]) if items else None

    # ---------- prices ----------
    def latest_price_date(self, ticker: str) -> str | None:
        response = self.table.query(KeyConditionExpression=Key("pk").eq(f"PRICE#{ticker}"), ScanIndexForward=False, Limit=1)
        items = response.get("Items", [])
        return items[0]["sk"] if items else None

    def put_prices(self, ticker: str, rows: Iterable[Mapping[str, Any]]) -> int:
        """Write newest first, so readers see the correct latest price even while a backfill is still running."""
        count = 0
        with self.table.batch_writer(overwrite_by_pkeys=["pk", "sk"]) as batch:
            for row in sorted(rows, key=lambda r: r["date"], reverse=True):
                batch.put_item(Item=to_dynamo({"pk": f"PRICE#{ticker}", "sk": row["date"], **row}))
                count += 1
        return count

    def backfill_complete(self, ticker: str) -> bool:
        item = self.table.get_item(Key={"pk": f"PRICEMETA#{ticker}", "sk": "BACKFILL"}).get("Item")
        return bool(item)

    def mark_backfill_complete(self, ticker: str, oldest: str | None) -> None:
        self.table.put_item(
            Item={"pk": f"PRICEMETA#{ticker}", "sk": "BACKFILL", "oldest_date": oldest or "", "completed_at": _now_iso()}
        )

    def price_history(self, ticker: str, since: str | None = None, query: Any = None) -> list[tuple[str, float]]:
        condition = Key("pk").eq(f"PRICE#{ticker}")
        if since:
            condition = condition & Key("sk").gte(since)
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": condition,
            "ProjectionExpression": "sk, #c",
            "ExpressionAttributeNames": {"#c": "close"},
        }
        items: list[dict[str, Any]] = []
        while True:
            response = (query or self.table.query)(**kwargs)
            items.extend(response.get("Items", []))
            if "LastEvaluatedKey" not in response:
                return [(i["sk"], float(i["close"])) for i in items]
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    def price_histories(self, tickers: Iterable[str], since: str | None = None) -> dict[str, list[tuple[str, float]]]:
        """price_history for many tickers at once, queried side by side (pages load faster).

        The threads share the Table's client, which is thread-safe (the Table resource is not). It is the
        resource's own client, so it still takes and returns plain Python values.
        """
        client = self.table.meta.client
        name = self.table.name

        def one(ticker: str) -> list[tuple[str, float]]:
            return self.price_history(ticker, since, query=lambda **kw: client.query(TableName=name, **kw))

        tickers = sorted(set(tickers))
        if len(tickers) <= 1:
            return {t: self.price_history(t, since) for t in tickers}
        with ThreadPoolExecutor(max_workers=min(8, len(tickers))) as pool:
            return dict(zip(tickers, pool.map(one, tickers), strict=True))

    # ---------- news ----------
    def put_news(self, ticker: str, articles: Iterable[Mapping[str, Any]]) -> int:
        count = 0
        expires = int(time.time()) + NEWS_TTL_SECONDS
        with self.table.batch_writer(overwrite_by_pkeys=["pk", "sk"]) as batch:
            for article in articles:
                item = {
                    "pk": f"NEWS#{ticker}",
                    "sk": f"{article['published_at']}#{article['article_id']}",
                    "expires_at": expires,
                    **article,
                }
                batch.put_item(Item=to_dynamo(item))
                count += 1
        return count

    def recent_news(self, ticker: str, since_iso: str, limit: int = 50) -> list[dict[str, Any]]:
        response = self.table.query(
            KeyConditionExpression=Key("pk").eq(f"NEWS#{ticker}") & Key("sk").gte(since_iso),
            ScanIndexForward=False,
            Limit=limit,
        )
        return [_clean(i) for i in response.get("Items", [])]

    def count_news_since(self, tickers: Iterable[str], since_iso: str) -> int:
        """Headlines first picked up since a time, across tickers (counted side by side; the Table's client is
        thread-safe). A headline can arrive days after it was published, so the key range reaches back a week."""
        client, name = self.table.meta.client, self.table.name
        published_from = (datetime.fromisoformat(since_iso) - timedelta(days=7)).isoformat(timespec="seconds")

        def one(ticker: str) -> int:
            kwargs: dict[str, Any] = {
                "TableName": name,
                "KeyConditionExpression": Key("pk").eq(f"NEWS#{ticker}") & Key("sk").gte(published_from),
                "FilterExpression": Attr("first_seen").gte(since_iso),
                "Select": "COUNT",
            }
            total = 0
            while True:
                response = client.query(**kwargs)
                total += response["Count"]
                if "LastEvaluatedKey" not in response:
                    return total
                kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

        tickers = sorted(set(tickers))
        if not tickers:
            return 0
        with ThreadPoolExecutor(max_workers=min(8, len(tickers))) as pool:
            return sum(pool.map(one, tickers))

    def put_brief(self, scope: str, day: str, brief: Mapping[str, Any]) -> None:
        self.table.put_item(Item=to_dynamo({"pk": f"BRIEF#{scope}", "sk": day, **brief}))

    def latest_brief(self, scope: str) -> dict[str, Any] | None:
        response = self.table.query(KeyConditionExpression=Key("pk").eq(f"BRIEF#{scope}"), ScanIndexForward=False, Limit=1)
        items = response.get("Items", [])
        return _clean(items[0]) if items else None

    # ---------- signals ----------
    def put_signal(self, ticker: str, signal: Mapping[str, Any]) -> None:
        self.table.put_item(Item=to_dynamo({"pk": f"SIGNAL#{ticker}", "sk": "LATEST", **signal}))

    def get_signal(self, ticker: str) -> dict[str, Any] | None:
        item = self.table.get_item(Key={"pk": f"SIGNAL#{ticker}", "sk": "LATEST"}).get("Item")
        return _clean(item) if item else None

    # ---------- earnings ----------
    def put_earnings(self, ticker: str, record: Mapping[str, Any] | None) -> None:
        key = {"pk": f"EARNINGS#{ticker}", "sk": "NEXT"}
        if record is None:
            self.table.delete_item(Key=key)
        else:
            # Expires a week after the report date, so a ticker no longer tracked doesn't leave a row forever.
            expires = int(datetime.fromisoformat(f"{record['date']}T00:00:00+00:00").timestamp()) + 7 * 86400
            self.table.put_item(Item=to_dynamo({**key, **record, "fetched_at": _now_iso(), "expires_at": expires}))

    def get_earnings(self, ticker: str) -> dict[str, Any] | None:
        item = self.table.get_item(Key={"pk": f"EARNINGS#{ticker}", "sk": "NEXT"}).get("Item")
        return _clean(item) if item else None

    # ---------- analysts and insiders ----------
    def put_insights(self, ticker: str, record: Mapping[str, Any]) -> None:
        # Expires after two weeks, so a ticker no longer tracked doesn't leave a row forever.
        expires = int(time.time()) + 14 * 86400
        item = {"pk": f"INSIGHT#{ticker}", "sk": "LATEST", **record, "fetched_at": _now_iso(), "expires_at": expires}
        self.table.put_item(Item=to_dynamo(item))

    def get_insights(self, ticker: str) -> dict[str, Any] | None:
        return self.get_insights_many([ticker]).get(ticker)

    def get_insights_many(self, tickers: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Stored insights for many tickers in one or two batch reads (pages load faster than one read each)."""
        keys = [{"pk": f"INSIGHT#{t}", "sk": "LATEST"} for t in sorted(set(tickers))]
        client, name, found = self.table.meta.client, self.table.name, {}
        for start in range(0, len(keys), 100):  # BatchGetItem's limit
            request = {name: {"Keys": keys[start : start + 100]}}
            while request:
                response = client.batch_get_item(RequestItems=request)
                for item in response["Responses"].get(name, []):
                    found[item["pk"].removeprefix("INSIGHT#")] = _clean(item)
                request = response.get("UnprocessedKeys") or None
        return found

    # ---------- job runs ----------
    def put_job_run(self, name: str) -> None:
        self.table.put_item(Item={"pk": f"JOB#{name}", "sk": "LAST", "finished_at": _now_iso()})

    def get_job_run(self, name: str) -> str | None:
        item = self.table.get_item(Key={"pk": f"JOB#{name}", "sk": "LAST"}).get("Item")
        return item["finished_at"] if item else None

    # ---------- intraday quotes ----------
    def put_quotes(self, quotes: Mapping[str, Mapping[str, Any]]) -> None:
        self.table.put_item(Item=to_dynamo({"pk": "QUOTES", "sk": "LATEST", "fetched_at": _now_iso(), "quotes": quotes}))

    def latest_quotes(self) -> dict[str, dict[str, Any]]:
        item = self.table.get_item(Key={"pk": "QUOTES", "sk": "LATEST"}).get("Item")
        return _clean(item).get("quotes", {}) if item else {}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@lru_cache(maxsize=1)
def _table() -> Any:
    return boto3.resource("dynamodb").Table(get_config().table_name)
