"""Shared fixtures for structx-mcp tests."""
from __future__ import annotations

import pytest
from structx_sdk import AsyncStructX, RetryPolicy


@pytest.fixture
def api_key() -> str:
    return "sx_test_abcdef"


@pytest.fixture
def base_url() -> str:
    return "https://api.test.struct-x.ai"


@pytest.fixture
async def sdk(monkeypatch, api_key, base_url):
    """An AsyncStructX pointed at a fake base_url. Tests mock the HTTP
    layer with respx so this never actually dials out.

    Carries an API key only, like a real MCP install (`_make_client` passes
    STRUCTX_API_KEY). STRUCTX_BEARER_TOKEN is cleared so a value in the
    developer's shell can't add an Authorization header."""
    monkeypatch.delenv("STRUCTX_BEARER_TOKEN", raising=False)
    c = AsyncStructX(
        api_key=api_key,
        base_url=base_url,
        retry=RetryPolicy(max_attempts=1, initial_backoff=0.0, max_backoff=0.0),
    )
    yield c
    await c.aclose()
