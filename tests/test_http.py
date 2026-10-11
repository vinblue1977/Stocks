import io
import json
import urllib.error

import pytest

from portfolio import http


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_rejects_non_https():
    with pytest.raises(ValueError):
        http.get_json("file:///etc/passwd")
    with pytest.raises(ValueError):
        http.get_json("http://example.com")


def test_retries_then_succeeds(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {}, None)
        return FakeResponse(json.dumps({"ok": True}).encode())

    monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    assert http.get_json("https://api.example.com/x", params={"a": "b c"}) == {"ok": True}
    assert calls == ["https://api.example.com/x?a=b+c"] * 2


def test_non_retryable_error_raises(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(http.HttpError) as exc:
        http.get_json("https://api.example.com/x")
    assert exc.value.status == 401
