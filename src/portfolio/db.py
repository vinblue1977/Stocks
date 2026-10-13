"""DynamoDB single-table repository.

Key design (pk / sk):
    USER#<uid>      HOLDING#<group>#<ticker>     a position: shares + avg_cost (per share); group is
                                                 one of GROUPS (gsi1: HOLDINGS / <ticker>#<uid>#<group>)
    USER#<uid>      RULE#<rule_id>               an alert rule (gsi1: RULES / <uid>#<rule_id>)
    USER#<uid>      RULESTATE#<rule_id>          whether the rule is currently triggered
    USER#<uid>      ALERT#<iso_ts>#<rule_id>     alert history (expires after 180 days)
    USER#<uid>      WATCH#<ticker>               a watched ticker, not held (gsi1: WATCHLIST / <ticker>#<uid>)
    USER#<uid>      TARGETS                      the user's target mix by area (Checkup)
    USER#<uid>      TXN#<group>#<ticker>#<date>#<id>  a buy, sell or opening entry (Stock Holdings only)
    USER#<uid>      CHECKUP#<yyyy-mm>            the monthly checkup summary
    PRICE#<ticker>  <yyyy-mm-dd>                 daily adjusted close
    PRICEMETA#<ticker> BACKFILL                  set once the full history has loaded
    NEWS#<ticker>   <iso_ts>#<article_id>        scored headline (expires after 30 days)
    BRIEF#<ticker>  <yyyy-mm-dd>                 AI news summary; BRIEF#ALL is the portfolio brief
    SIGNAL#<ticker> LATEST                       latest signal snapshot
    QUOTES          LATEST                       intraday prices for all tickers (market hours only)
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
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
        item = {**self._holding_keys(user_id, group, holding["ticker"]), "updated_at": _now_iso(), **holding, "group": group}
        # Also stored under the old name for one release, so a rollback still reads it (ROADMAP: drop later).
        item["cost_basis"] = item["avg_cost"]
        return item

    def _all_holding_items(self) -> list[dict[str, Any]]:
        return self._query_all(IndexName="gsi1", KeyConditionExpression=Key("gsi1pk").eq("HOLDINGS"))

    @staticmethod
    def _holding_out(item: Mapping[str, Any]) -> dict[str, Any]:
        clean = _clean(item)
        clean.setdefault("group", DEFAULT_GROUP)  # items written before groups existed
        if "cost_basis" in clean:  # old name; items written before the rename have only this one
            clean.setdefault("avg_cost", clean["cost_basis"])
            del clean["cost_basis"]
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
            # Items written before the rename have only the old attribute name.
            condition = Attr("shares").eq(to_dynamo(float(previous["shares"]))) & (
                Attr("avg_cost").eq(avg_cost) | (Attr("avg_cost").not_exists() & Attr("cost_basis").eq(avg_cost))
            )
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

    def migrate_avg_cost(self, dry_run: bool = False) -> list[str]:
        """Copy cost_basis into avg_cost on holdings written before the rename. The old attribute is kept.

        Idempotent: items that already have avg_cost are skipped. Returns the sort key of each item needing it.
        """
        changed = []
        for item in self._all_holding_items():
            if "avg_cost" in item or "cost_basis" not in item:
                continue
            if not dry_run:
                try:
                    self.table.update_item(
                        Key={"pk": item["pk"], "sk": item["sk"]},
                        UpdateExpression="SET avg_cost = cost_basis",
                        ConditionExpression=Attr("avg_cost").not_exists() & Attr("cost_basis").exists(),
                    )
                except self.table.meta.client.exceptions.ConditionalCheckFailedException:
                    continue  # rewritten by the app in the meantime
            changed.append(f"{item['pk']}:{item['sk']}")
        return changed

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
        items = self._query_all(KeyConditionExpression=Key("pk").eq(f"USER#{user_id}") & Key("sk").begins_with(prefix))
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

    def price_history(self, ticker: str, since: str | None = None) -> list[tuple[str, float]]:
        condition = Key("pk").eq(f"PRICE#{ticker}")
        if since:
            condition = condition & Key("sk").gte(since)
        items = self._query_all(
            KeyConditionExpression=condition, ProjectionExpression="sk, #c", ExpressionAttributeNames={"#c": "close"}
        )
        return [(i["sk"], float(i["close"])) for i in items]

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
