"""Error mapping tests — every status code → the right exception class,
with the right attributes populated.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from structx_sdk import (
    ApiError,
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    StructX,
    TransportError,
    ValidationError,
)
from structx_sdk._client import RetryPolicy


@respx.mock
def test_401_authentication_error(client, base_url):
    respx.post(f"{base_url}/v1/extract").mock(
        return_value=httpx.Response(
            401, json={"error": "Invalid API key", "code": "invalid_api_key"}
        )
    )
    with pytest.raises(AuthenticationError) as exc:
        client.extract(content="x", schema={"type": "object"})
    assert exc.value.status_code == 401
    assert exc.value.code == "invalid_api_key"
    # Inherits from ApiError → StructXError
    assert isinstance(exc.value, ApiError)


@respx.mock
def test_403_permission_denied(bearer_client, base_url):
    # usage() is JWT-only and mounts at `/billing/usage` (no `/v1`).
    respx.get(f"{base_url}/billing/usage").mock(
        return_value=httpx.Response(
            403, json={"error": "Admin only", "code": "admin_required"}
        )
    )
    with pytest.raises(PermissionDeniedError):
        bearer_client.usage()


@respx.mock
def test_404_not_found(client, base_url):
    respx.post(f"{base_url}/v1/extract").mock(
        return_value=httpx.Response(
            404, json={"error": "Template not found", "code": "template_not_found"}
        )
    )
    with pytest.raises(NotFoundError):
        client.extract(content="x", template_slug="does.not.exist")


@respx.mock
def test_422_validation_error(client, base_url):
    respx.post(f"{base_url}/v1/extract").mock(
        return_value=httpx.Response(
            422, json={"error": "schema must include 'type'", "code": "invalid_schema"}
        )
    )
    with pytest.raises(ValidationError) as exc:
        client.extract(content="x", schema={"properties": {}})
    assert exc.value.code == "invalid_schema"


@respx.mock
def test_429_rate_limit_with_retry_after(client, base_url):
    respx.post(f"{base_url}/v1/extract").mock(
        return_value=httpx.Response(
            429,
            json={
                "error": "Daily credit limit exhausted",
                "code": "credits_exhausted",
                "credits_used": 2000,
                "credits_remaining": 0,
            },
            headers={"retry-after": "3600"},
        )
    )
    with pytest.raises(RateLimitError) as exc:
        client.extract(content="x", schema={"type": "object"})
    assert exc.value.retry_after == 3600.0
    assert exc.value.credits_used == 2000
    assert exc.value.credits_remaining == 0
    assert exc.value.code == "credits_exhausted"


@respx.mock
def test_500_server_error(client, base_url):
    """500 should NOT be retried (writes don't auto-retry on 5xx),
    and should raise ServerError with request_id when present."""
    respx.post(f"{base_url}/v1/extract").mock(
        return_value=httpx.Response(
            500,
            json={"error": "Internal", "code": "internal_error"},
            headers={"x-request-id": "req_abc123"},
        )
    )
    with pytest.raises(ServerError) as exc:
        client.extract(content="x", schema={"type": "object"})
    assert exc.value.status_code == 500
    assert exc.value.request_id == "req_abc123"


@respx.mock
def test_transport_error_raised_after_retries(client, base_url):
    respx.post(f"{base_url}/v1/extract").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    with pytest.raises(TransportError) as exc:
        client.extract(content="x", schema={"type": "object"})
    assert "ConnectError" in str(exc.value)


@respx.mock
def test_error_response_without_json_body(client, base_url):
    """Some upstream proxies return plain-text 502s. The SDK shouldn't
    crash trying to .json() — it should still raise ServerError."""
    respx.post(f"{base_url}/v1/extract").mock(
        return_value=httpx.Response(502, text="Bad Gateway")
    )
    with pytest.raises(ServerError) as exc:
        client.extract(content="x", schema={"type": "object"})
    assert exc.value.status_code == 502


@respx.mock
def test_unknown_4xx_falls_back_to_apierror(client, base_url):
    """Status codes not in the explicit map should still raise SOMETHING
    a caller can pattern-match — the base ApiError class."""
    respx.post(f"{base_url}/v1/extract").mock(
        return_value=httpx.Response(418, json={"error": "I'm a teapot", "code": "teapot"})
    )
    with pytest.raises(ApiError) as exc:
        client.extract(content="x", schema={"type": "object"})
    # NOT a Server/Validation/Auth — just bare ApiError
    assert type(exc.value) is ApiError
    assert exc.value.status_code == 418
