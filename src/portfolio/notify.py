"""Email notifications through the SNS alerts topic."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import boto3

from portfolio.config import get_config

DISCLAIMER = (
    "\n\n--\nThis is an automated alert based on rules you set in Portfolio Tracker. "
    "It describes market data and is not investment advice."
)


@lru_cache(maxsize=1)
def _sns() -> Any:
    return boto3.client("sns")


def send_email(subject: str, body: str) -> None:
    topic_arn = get_config().topic_arn
    if not topic_arn:
        return
    _sns().publish(TopicArn=topic_arn, Subject=subject[:100], Message=body + DISCLAIMER)
