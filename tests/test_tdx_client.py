"""Mocked authentication, cache and quota handling; never contact TDX."""

import time

import pytest
import requests

from tools import freshness, tdx_client as client

REAL_TOKEN = client._get_token


class Response:
    def __init__(self, body=None, status=200, reset="60"):
        self.body, self.status_code = body, status
        self.headers = {"ratelimit-reset": reset}

    def json(self):
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError()


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(client, "_cache", {})
    monkeypatch.setattr(client, "_token", {"value": None, "expires_at": 0})


def test_missing_credentials_make_no_http_request(monkeypatch):
    monkeypatch.setattr(client, "_get_token", REAL_TOKEN)
    monkeypatch.delenv("TDX_CLIENT_ID", raising=False)
    monkeypatch.delenv("TDX_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(client.requests, "post", lambda *a, **k: pytest.fail("Unexpected token request"))
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: pytest.fail("Unexpected data request"))
    assert "credentials" in client.tdx_get("test", {})["error"]


def test_token_reuse_and_refresh(monkeypatch):
    monkeypatch.setenv("TDX_CLIENT_ID", "test")
    monkeypatch.setenv("TDX_CLIENT_SECRET", "test")
    calls = []
    def post(*args, **kwargs):
        calls.append(kwargs)
        return Response({"access_token": str(len(calls)), "expires_in": 3600})
    monkeypatch.setattr(client.requests, "post", post)
    assert REAL_TOKEN() == REAL_TOKEN() == "1"
    client._token["expires_at"] = time.time() + 30
    assert REAL_TOKEN() == "2" and len(calls) == 2


def test_cache_hit_avoids_token_and_data_requests(monkeypatch):
    monkeypatch.setattr(client, "_get_token", lambda: "test")
    requests_seen = []
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: requests_seen.append(k) or Response({"value": [{"name": "A"}]}))
    assert client.tdx_get("test", {"$top": 1}) == [{"name": "A"}]
    monkeypatch.setattr(client, "_get_token", lambda: pytest.fail("Token should be cached"))
    assert client.tdx_get("test", {"$top": 1}) == [{"name": "A"}]
    assert len(requests_seen) == 1


@pytest.mark.parametrize("reset,expected", [("2", 2), ("60", 1)])
def test_429_has_at_most_one_short_retry(monkeypatch, reset, expected):
    monkeypatch.setattr(client, "_get_token", lambda: "test")
    calls, waits = [], []
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: calls.append(1) or Response(status=429, reset=reset))
    monkeypatch.setattr(client.time, "sleep", waits.append)
    assert "rate limit" in client.tdx_get("test", {})["error"]
    assert len(calls) == expected
    assert waits == ([3] if expected == 2 else [])


def test_stale_fallback_reports_age(monkeypatch):
    client._cache[("test", ())] = (time.time() - client.CACHE_TTL - 1, [{"name": "A"}])
    monkeypatch.setattr(client, "_get_token", lambda: "test")
    def offline(*a, **k):
        raise requests.ConnectionError()
    monkeypatch.setattr(client.requests, "get", offline)
    token = freshness.begin()
    assert client.tdx_get("test", {}) == [{"name": "A"}]
    metadata = freshness.finish(token)
    assert metadata[0]["stale"] and metadata[0]["age_seconds"] >= client.CACHE_TTL


def test_failed_request_without_cache_returns_error(monkeypatch):
    monkeypatch.setattr(client, "_get_token", lambda: "test")
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: Response(status=503))
    assert client.tdx_get("test", {})["error"] == "TDX request failed: HTTPError"
