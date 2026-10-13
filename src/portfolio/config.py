"""Runtime configuration, read once from environment variables set by the CDK stack."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Config:
    table_name: str
    topic_arn: str
    price_api_key_param: str
    news_api_key_param: str
    bedrock_model_id: str
    checkup_model_id: str
    bedrock_api: str
    bedrock_region: str
    history_years: int
    news_lookback_days: int
    news_excluded: frozenset[str]
    owner_name: str
    partner_news: bool
    partner_name: str
    site_url: str


@lru_cache(maxsize=1)
def get_config() -> Config:
    return Config(
        table_name=os.environ.get("TABLE_NAME", "PortfolioTable"),
        topic_arn=os.environ.get("ALERTS_TOPIC_ARN", ""),
        price_api_key_param=os.environ.get("PRICE_API_KEY_PARAM", "/portfolio-tracker/tiingo-api-key"),
        news_api_key_param=os.environ.get("NEWS_API_KEY_PARAM", "/portfolio-tracker/finnhub-api-key"),
        bedrock_model_id=os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
        checkup_model_id=os.environ.get("CHECKUP_MODEL_ID")
        or os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
        bedrock_api=os.environ.get("BEDROCK_API", "runtime"),
        bedrock_region=os.environ.get("BEDROCK_REGION", os.environ.get("AWS_REGION", "us-east-2")),
        history_years=int(os.environ.get("HISTORY_YEARS", "5")),
        news_lookback_days=int(os.environ.get("NEWS_LOOKBACK_DAYS", "3")),
        news_excluded=frozenset(t.strip().upper() for t in os.environ.get("NEWS_EXCLUDED_TICKERS", "").split(",") if t.strip()),
        owner_name=os.environ.get("OWNER_NAME") or "Me",
        partner_news=os.environ.get("PARTNER_NEWS", "true").lower() != "false",
        partner_name=os.environ.get("PARTNER_NAME") or "Partner",
        site_url=os.environ.get("SITE_URL", ""),
    )
