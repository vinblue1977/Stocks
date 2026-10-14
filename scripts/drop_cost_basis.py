"""One-time cleanup: finish the cost_basis -> avg_cost rename on stored holdings (sets avg_cost if missing,
removes cost_basis).

Usage (from the project root, with AWS credentials for the account):
    PYTHONPATH=src .venv/bin/python scripts/drop_cost_basis.py <TableName> [--region us-east-2] [--dry-run]

Run after deploying the code that no longer writes cost_basis. Safe to run more than once.
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
    changed = repo.drop_cost_basis(dry_run=args.dry_run)
    action = "Would clean" if args.dry_run else "Cleaned"
    print(f"{action} {len(changed)} holdings")


if __name__ == "__main__":
    main()
