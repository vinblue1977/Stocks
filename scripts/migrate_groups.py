"""One-time migration: move holdings saved before groups existed into "Stock Holdings".

Usage (from the project root, with AWS credentials for the account):
    PYTHONPATH=src .venv/bin/python scripts/migrate_groups.py <TableName> [--region us-east-2] [--dry-run]

Safe to run more than once: already-migrated holdings are left alone.
"""

from __future__ import annotations

import argparse

import boto3
from boto3.dynamodb.conditions import Key

from portfolio.db import Repository


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("table")
    parser.add_argument("--region", default="us-east-2")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    table = boto3.resource("dynamodb", region_name=args.region).Table(args.table)
    items = table.query(IndexName="gsi1", KeyConditionExpression=Key("gsi1pk").eq("HOLDINGS"))["Items"]
    legacy = [i for i in items if i["sk"].count("#") == 1]
    print(f"Holdings: {len(items)} total, {len(legacy)} in the old format")
    for item in legacy:
        avg = item.get("avg_cost", item.get("cost_basis"))
        print(f"  {item['sk'].removeprefix('HOLDING#'):<6} shares={item['shares']} avg={avg}")
    if args.dry_run or not legacy:
        return
    moved = Repository(table).migrate_legacy_holdings()
    after = table.query(IndexName="gsi1", KeyConditionExpression=Key("gsi1pk").eq("HOLDINGS"))["Items"]
    print(f"Moved {len(moved)} into Stock Holdings. Holdings now: {len(after)} (should equal {len(items)})")
    if len(after) != len(items):
        raise SystemExit("Count changed: stop and investigate (point-in-time recovery is available).")


if __name__ == "__main__":
    main()
