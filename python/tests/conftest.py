"""Shared pytest fixtures for the structx-sdk SDK test suite.

All tests run against `respx`-mocked httpx transports — no live network.
"""
from __future__ import annotations

import pytest

from structx_sdk import AsyncStructX, RetryPolicy, StructX


# Disable backoff sleeps in tests by default — keeps the suite under
# one second. Tests that specifically exercise backoff override this.
@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    import asyncio
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)

    async def _async_noop(*_, **__):
        return None

    monkeypatch.setattr(asyncio, "sleep", _async_noop)


@pytest.fixture
def api_key() -> str:
    return "sx_test_abcdef0123456789"


@pytest.fixture
def base_url() -> str:
    return "https://api.test.struct-x.ai"


@pytest.fixture
def bearer_token() -> str:
    return "jwt_test_token_0123456789"


@pytest.fixture
def client(api_key, base_url) -> StructX:
    return StructX(
        api_key=api_key,
        base_url=base_url,
        retry=RetryPolicy(max_attempts=2, initial_backoff=0.0, max_backoff=0.0),
    )


@pytest.fixture
def bearer_client(api_key, base_url, bearer_token) -> StructX:
    """A client carrying BOTH an API key and a JWT bearer token. Both
    headers are sent on every request."""
    return StructX(
        api_key=api_key,
        base_url=base_url,
        bearer_token=bearer_token,
        retry=RetryPolicy(max_attempts=2, initial_backoff=0.0, max_backoff=0.0),
    )


@pytest.fixture
async def aclient(api_key, base_url):
    c = AsyncStructX(
        api_key=api_key,
        base_url=base_url,
        retry=RetryPolicy(max_attempts=2, initial_backoff=0.0, max_backoff=0.0),
    )
    yield c
    await c.aclose()


@pytest.fixture
async def abearer_client(api_key, base_url, bearer_token):
    """Async client carrying both credentials."""
    c = AsyncStructX(
        api_key=api_key,
        base_url=base_url,
        bearer_token=bearer_token,
        retry=RetryPolicy(max_attempts=2, initial_backoff=0.0, max_backoff=0.0),
    )
    yield c
    await c.aclose()
