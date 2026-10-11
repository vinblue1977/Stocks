"""API keys live in SSM Parameter Store (SecureString). Cached per Lambda container."""

from __future__ import annotations

from functools import lru_cache

import boto3


@lru_cache(maxsize=8)
def get_secret(parameter_name: str) -> str:
    ssm = boto3.client("ssm")
    response = ssm.get_parameter(Name=parameter_name, WithDecryption=True)
    return response["Parameter"]["Value"]
