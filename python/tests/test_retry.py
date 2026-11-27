"""Retry behavior — the write-vs-read distinction is the load-bearing
piece. Writes (extract, infer) MUST NOT auto-retry on 5xx because the
backend may have partially billed; reads SHOULD retry up to max_attempts.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from structx_sdk import RateLimitError, RetryPolicy, ServerError, StructX, TransportError


@respx.mock
def test_read_retries_on_503(api_key, base_url):
    """GET /v1/schemas returning 503 twice then 200 → SDK succeeds."""
    c = StructX(
        api_key=api_key,
        base_url=base_url,
        retry=RetryPolicy(max_attempts=3, initial_backoff=0.0, max_backoff=0.0),
    )
    responses = [
        httpx.Response(503, json={"error": "down", "code": "unavailable"}),
        httpx.Response(503, json={"error": "down", "code": "unavailable"}),
        httpx.Response(200, json=[{"slug": "x", "name": "X", "schema": {}}]),
    ]
    route = respx.get(f"{base_url}/v1/schemas").mock(side_effect=responses)
    tpls = c.list_templates()
    assert len(tpls) == 1
    assert route.call_count == 3


@respx.mock
def test_read_gives_up_after_max_attempts(api_key, base_url):
    c = StructX(
        api_key=api_key,
        base_url=base_url,
        retry=RetryPolicy(max_attempts=2, initial_backoff=0.0, max_backoff=0.0),
    )
    route = respx.get(f"{base_url}/v1/schemas").mock(
        return_value=httpx.Response(503, json={"error": "down", "code": "unavailable"})
    )
    with pytest.raises(ServerError):
        c.list_templates()
    assert route.call_count == 2


@respx.mock
def test_write_does_not_retry_on_5xx(api_key, base_url):
    """POST /v1/extract returning 503 should raise immediately without
    retrying — write paths skip 5xx retry to avoid double-billing."""
    c = StructX(
        api_key=api_key,
        base_url=base_url,
        retry=RetryPolicy(max_attempts=3, initial_backoff=0.0, max_backoff=0.0),
    )
    route = respx.post(f"{base_url}/v1/extract").mock(
        return_value=httpx.Response(503, json={"error": "down", "code": "unavailable"})
    )
    with pytest.raises(ServerError):
        c.extract(content="x", schema={"type": "object"})
    assert route.call_count == 1


@respx.mock
def test_write_retries_on_transport_error(api_key, base_url):
    """ConnectError on a write IS retried — the request never reached
    the server, so no billing risk."""
    c = StructX(
        api_key=api_key,
        base_url=base_url,
        retry=RetryPolicy(max_attempts=3, initial_backoff=0.0, max_backoff=0.0),
    )
    route = respx.post(f"{base_url}/v1/extract").mock(
        side_effect=[
            httpx.ConnectError("refused"),
            httpx.ConnectError("refused"),
            httpx.Response(
                200,
                json={
                    "data": {},
                    "model_used": "x",
                    "latency_ms": 1,
                    "tokens": {"input": 1, "output": 1},
                    "credits_used": 1,
                },
            ),
        ]
    )
    result = c.extract(content="x", schema={"type": "object"})
    assert route.call_count == 3
    assert result.credits_used == 1


@respx.mock
def test_write_transport_error_after_max_attempts(api_key, base_url):
    c = StructX(
        api_key=api_key,
        base_url=base_url,
        retry=RetryPolicy(max_attempts=2, initial_backoff=0.0, max_backoff=0.0),
    )
    respx.post(f"{base_url}/v1/extract").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(TransportError):
        c.extract(content="x", schema={"type": "object"})


@respx.mock
def test_429_does_not_auto_retry(api_key, base_url):
    """429 is the caller's decision — the SDK does NOT silently retry,
    it raises RateLimitError so the application can choose to back off,
    surface a UI, etc. The retry_after attribute tells them how long."""
    c = StructX(
        api_key=api_key,
        base_url=base_url,
        retry=RetryPolicy(max_attempts=5, initial_backoff=0.0, max_backoff=0.0),
    )
    route = respx.post(f"{base_url}/v1/extract").mock(
        return_value=httpx.Response(
            429,
            json={"error": "rate limited", "code": "rate_limited"},
            headers={"retry-after": "60"},
        )
    )
    with pytest.raises(RateLimitError):
        c.extract(content="x", schema={"type": "object"})
    assert route.call_count == 1


def test_retry_policy_backoff_curve():
    """Verify the exponential-backoff math directly (no HTTP needed).
    With initial=1.0 and jitter=0, attempts 1/2/3 should be 1, 2, 4s."""
    p = RetryPolicy(initial_backoff=1.0, max_backoff=30.0, jitter=0.0)
    assert p.backoff_for(1) == 1.0
    assert p.backoff_for(2) == 2.0
    assert p.backoff_for(3) == 4.0
    assert p.backoff_for(10) == 30.0  # capped


def test_retry_policy_respects_server_retry_after():
    p = RetryPolicy(initial_backoff=1.0, max_backoff=30.0, jitter=0.0)
    assert p.backoff_for(1, retry_after=15.0) == 15.0
    # but never longer than max_backoff
    assert p.backoff_for(1, retry_after=300.0) == 30.0
