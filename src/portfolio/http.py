"""Minimal JSON-over-HTTPS client with retries, built on the standard library."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


RETRYABLE = {429, 500, 502, 503, 504}


def get_json(
    url: str,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    retries: int = 3,
    backoff_seconds: float = 2.0,
    timeout: float = 15.0,
) -> Any:
    if not url.startswith("https://"):
        raise ValueError("Only https URLs are allowed")
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    # Scheme is checked above, so the S310 (file:// or custom scheme) concern does not apply.
    request = urllib.request.Request(url, headers={"Accept": "application/json", **(headers or {})})  # noqa: S310
    attempt = 0
    while True:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https hosts
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in RETRYABLE and attempt < retries:
                attempt += 1
                time.sleep(backoff_seconds * attempt)
                continue
            raise HttpError(exc.code, exc.reason) from exc
        except urllib.error.URLError:
            if attempt < retries:
                attempt += 1
                time.sleep(backoff_seconds * attempt)
                continue
            raise
