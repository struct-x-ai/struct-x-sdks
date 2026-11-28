"""`usage()` and the personal-schema methods work with an API key alone, and
`augment_schema()` calls `POST /v1/schemas/recommendations/{event_id}/augment`.

respx-mocked httpx transport, no live network.
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from structx_sdk import AsyncStructX, InferenceResult, RetryPolicy, ServerError, StructX

_EVENT_ID = "8a3f7c2d-1b9e-4d6a-b2c3-1f8a7e6c5d4b"
_INFER_RESPONSE = {
    "event_id": "0b5e2a91-77c4-4f0e-9d3a-5c1e2f8b6a70",
    "inferred": {
        "json_schema": {"type": "object", "properties": {"id": {"type": "string"}}},
        "fields": [],
        "overall_confidence": 0.9,
        "needs_review": False,
    },
    "recommendations": [],
    "credits_used": 4,
    "partial": False,
    "cached": False,
}

# (method, args, kwargs, HTTP method, path, status, response body)
_API_KEY_CALLS = [
    ("usage", (), {}, "GET", "/billing/usage", 200,
     {"credits_used_today": 3, "credits_limit_today": 334}),
    ("list_my_schemas", (), {}, "GET", "/v1/schemas/mine", 200, [{"slug": "a"}]),
    ("get_my_schema", ("a",), {}, "GET", "/v1/schemas/mine/a", 200, {"slug": "a"}),
    ("save_my_schema", (), {"name": "A", "json_schema": {"type": "object"}},
     "POST", "/v1/schemas/mine", 201, {"slug": "a"}),
    ("update_my_schema", ("a",), {"description": "v2"},
     "PATCH", "/v1/schemas/mine/a", 200, {"slug": "a"}),
    ("delete_my_schema", ("a",), {}, "DELETE", "/v1/schemas/mine/a", 204, None),
]
_IDS = [c[0] for c in _API_KEY_CALLS]


def _retry() -> RetryPolicy:
    return RetryPolicy(max_attempts=2, initial_backoff=0.0, max_backoff=0.0)


@pytest.fixture
def key_only_client(monkeypatch, api_key, base_url):
    """A client with an API key and no bearer token, even if the shell has
    STRUCTX_BEARER_TOKEN set."""
    monkeypatch.delenv("STRUCTX_BEARER_TOKEN", raising=False)
    return StructX(api_key=api_key, base_url=base_url, retry=_retry())


@pytest.fixture
async def akey_only_client(monkeypatch, api_key, base_url):
    monkeypatch.delenv("STRUCTX_BEARER_TOKEN", raising=False)
    c = AsyncStructX(api_key=api_key, base_url=base_url, retry=_retry())
    yield c
    await c.aclose()


def _mock(base_url, http_method, path, status, body):
    response = httpx.Response(status) if body is None else httpx.Response(status, json=body)
    return respx.route(method=http_method, url=f"{base_url}{path}").mock(return_value=response)


def _assert_api_key_only(request, api_key):
    assert request.headers["x-api-key"] == api_key
    assert "authorization" not in request.headers


# ── usage + personal schemas with an API key only ────────────


@respx.mock
@pytest.mark.parametrize("method,args,kwargs,http_method,path,status,body", _API_KEY_CALLS, ids=_IDS)
def test_api_key_only_client_calls_route(
    key_only_client, api_key, base_url, method, args, kwargs, http_method, path, status, body,
):
    route = _mock(base_url, http_method, path, status, body)
    getattr(key_only_client, method)(*args, **kwargs)
    assert route.call_count == 1
    _assert_api_key_only(route.calls.last.request, api_key)


@respx.mock
@pytest.mark.parametrize("method,args,kwargs,http_method,path,status,body", _API_KEY_CALLS, ids=_IDS)
async def test_async_api_key_only_client_calls_route(
    akey_only_client, api_key, base_url, method, args, kwargs, http_method, path, status, body,
):
    route = _mock(base_url, http_method, path, status, body)
    await getattr(akey_only_client, method)(*args, **kwargs)
    assert route.call_count == 1
    _assert_api_key_only(route.calls.last.request, api_key)


# ── augment_schema ───────────────────────────────────────────


@respx.mock
def test_augment_schema_posts_to_augment(key_only_client, base_url):
    route = respx.post(f"{base_url}/v1/schemas/recommendations/{_EVENT_ID}/augment").mock(
        return_value=httpx.Response(200, json=_INFER_RESPONSE)
    )
    result = key_only_client.augment_schema(
        _EVENT_ID, "rename event_id to id", "<p>sample</p>", content_type="html",
    )
    assert isinstance(result, InferenceResult)
    assert result.event_id == _INFER_RESPONSE["event_id"]
    assert result.inferred.json_schema["properties"]["id"]["type"] == "string"
    assert json.loads(route.calls.last.request.content) == {
        "guidance": "rename event_id to id",
        "content": "<p>sample</p>",
        "content_type": "html",
    }


@respx.mock
def test_augment_schema_omits_content_type_when_none(key_only_client, base_url):
    route = respx.post(f"{base_url}/v1/schemas/recommendations/{_EVENT_ID}/augment").mock(
        return_value=httpx.Response(200, json=_INFER_RESPONSE)
    )
    key_only_client.augment_schema(_EVENT_ID, "drop trace_id", "sample text")
    assert json.loads(route.calls.last.request.content) == {
        "guidance": "drop trace_id",
        "content": "sample text",
    }


@respx.mock
def test_augment_schema_does_not_retry_5xx(key_only_client, base_url):
    """Augment bills like infer, so a 5xx is not retried."""
    route = respx.post(f"{base_url}/v1/schemas/recommendations/{_EVENT_ID}/augment").mock(
        return_value=httpx.Response(503, json={"error": "down", "code": "unavailable"})
    )
    with pytest.raises(ServerError):
        key_only_client.augment_schema(_EVENT_ID, "drop trace_id", "sample text")
    assert route.call_count == 1


@respx.mock
async def test_async_augment_schema(akey_only_client, base_url):
    route = respx.post(f"{base_url}/v1/schemas/recommendations/{_EVENT_ID}/augment").mock(
        return_value=httpx.Response(200, json=_INFER_RESPONSE)
    )
    result = await akey_only_client.augment_schema(_EVENT_ID, "drop trace_id", "sample text")
    assert isinstance(result, InferenceResult)
    assert json.loads(route.calls.last.request.content) == {
        "guidance": "drop trace_id",
        "content": "sample text",
    }
