"""Runtime configuration, read once from environment variables set by the CDK stack."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Config:
    table_name: str
    topic_arn: str
    benchmark_ticker: str
    price_api_key_param: str
    news_api_key_param: str
    bedrock_model_id: str
    bedrock_region: str
    history_years: int
    news_lookback_days: int


@lru_cache(maxsize=1)
def get_config() -> Config:
    return Config(
        table_name=os.environ.get("TABLE_NAME", "PortfolioTable"),
        topic_arn=os.environ.get("ALERTS_TOPIC_ARN", ""),
        benchmark_ticker=os.environ.get("BENCHMARK_TICKER", "SPY"),
        price_api_key_param=os.environ.get("PRICE_API_KEY_PARAM", "/portfolio-tracker/tiingo-api-key"),
        news_api_key_param=os.environ.get("NEWS_API_KEY_PARAM", "/portfolio-tracker/finnhub-api-key"),
        bedrock_model_id=os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-haiku-5-5"),
        bedrock_region=os.environ.get("BEDROCK_REGION", os.environ.get("AWS_REGION", "us-east-1")),
        history_years=int(os.environ.get("HISTORY_YEARS", "5")),
        news_lookback_days=int(os.environ.get("NEWS_LOOKBACK_DAYS", "3")),
    )
