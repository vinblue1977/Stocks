"""One-time migration: rename the stored holding attribute cost_basis to avg_cost.

Usage (from the project root, with AWS credentials for the account):
    PYTHONPATH=src .venv/bin/python scripts/migrate_avg_cost.py <TableName> [--region us-east-2] [--dry-run]

Run after deploying the code that reads both names. Safe to run more than once.
"""

from __future__ import annotations

import argparse

import boto3

from portfolio.db import Repository


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("table")
    parser.add_argument("--region", default="us-east-2")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    repo = Repository(boto3.resource("dynamodb", region_name=args.region).Table(args.table))
    before = repo.all_holdings()
    if args.dry_run:
        print(f"Holdings: {len(before)}; dry run, nothing changed")
        return
    changed = repo.migrate_avg_cost()
    after = repo.all_holdings()
    print(f"Renamed cost_basis -> avg_cost on {len(changed)} of {len(after)} holdings")
    if [(h["ticker"], h["group"], h["avg_cost"]) for h in after] != [(h["ticker"], h["group"], h["avg_cost"]) for h in before]:
        raise SystemExit("Values changed: stop and investigate (point-in-time recovery is available).")


if __name__ == "__main__":
    main()
