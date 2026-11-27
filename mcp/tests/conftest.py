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
def bearer_token() -> str:
    return "jwt_test_token_0123456789"


@pytest.fixture
async def sdk(api_key, base_url, bearer_token):
    """An AsyncStructX pointed at a fake base_url. Tests mock the HTTP
    layer with respx so this never actually dials out.

    Carries BOTH an API key and a JWT bearer token. The bearer token is
    required by the JWT-only SDK methods that back several MCP tools
    (`structx_usage`, the `structx_*_my_schema` personal-schema CRUD):
    the SDK guards those with `_require_bearer` and raises if no bearer
    token is set. At runtime the MCP server picks the token up from
    `STRUCTX_BEARER_TOKEN` (the SDK reads that env var), so exercising
    the dispatch layer with a bearer-carrying client mirrors real use."""
    c = AsyncStructX(
        api_key=api_key,
        base_url=base_url,
        bearer_token=bearer_token,
        retry=RetryPolicy(max_attempts=1, initial_backoff=0.0, max_backoff=0.0),
    )
    yield c
    await c.aclose()
