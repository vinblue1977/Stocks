"""One-time migration: copy the stored holding attribute cost_basis into avg_cost (cost_basis is kept).

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
    needed = repo.migrate_avg_cost(dry_run=args.dry_run)
    action = "Would add" if args.dry_run else "Added"
    print(f"{action} avg_cost to {len(needed)} holdings")


if __name__ == "__main__":
    main()
