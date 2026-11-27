"""Happy-path tests for StructX + AsyncStructX — extract, infer,
templates, models, usage.
"""
from __future__ import annotations

import os

import httpx
import pytest
import respx

from structx_sdk import (
    AsyncStructX,
    Extraction,
    InferenceResult,
    Model,
    StructX,
    StructXError,
    Template,
    Usage,
)


# ── Configuration ────────────────────────────────────────────


def test_api_key_required():
    """Empty api_key + no env var → StructXError at construction."""
    # Make sure the env doesn't leak a value through.
    if "STRUCTX_API_KEY" in os.environ:
        del os.environ["STRUCTX_API_KEY"]
    with pytest.raises(StructXError, match="api_key is required"):
        StructX(api_key="")


def test_from_env(monkeypatch):
    monkeypatch.setenv("STRUCTX_API_KEY", "sx_from_env")
    c = StructX.from_env()
    assert c.api_key == "sx_from_env"


def test_base_url_normalized():
    c = StructX(api_key="sx_x", base_url="https://api.test.com")  # no trailing slash
    # _url(absolute path) should still produce a valid concatenation
    assert c._url("/v1/extract") == "https://api.test.com/v1/extract"


# ── extract — happy path ─────────────────────────────────────


@respx.mock
def test_extract_success(client, base_url):
    respx.post(f"{base_url}/v1/extract").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {"price_cents": 9900, "title": "Widget"},
                "model_used": "claude-haiku-4-5",
                "latency_ms": 412,
                "tokens": {"input": 84, "output": 32},
                "credits_used": 2,
                "credits_remaining": 2998,
                "confidence": 0.91,
                "field_confidences": [
                    {"field": "price_cents", "confidence": 0.95},
                    {"field": "title", "confidence": 0.87},
                ],
                "cached": False,
            },
        )
    )
    result = client.extract(
        content="<div>$99 Widget</div>",
        schema={"type": "object", "properties": {"price_cents": {"type": "integer"}}},
    )
    assert isinstance(result, Extraction)
    assert result.data == {"price_cents": 9900, "title": "Widget"}
    assert result.tokens.input == 84
    assert result.field_confidences[0].confidence == 0.95


@respx.mock
def test_extract_with_template_slug(client, base_url):
    route = respx.post(f"{base_url}/v1/extract").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {},
                "model_used": "gpt-4o-mini",
                "latency_ms": 100,
                "tokens": {"input": 10, "output": 5},
                "credits_used": 1,
            },
        )
    )
    client.extract(content="...", template_slug="logs.stripe.event@1.0.0")
    body = route.calls.last.request.read().decode()
    assert "logs.stripe.event@1.0.0" in body
    assert '"schema"' not in body


@respx.mock
def test_extract_sets_auth_and_user_agent(client, base_url):
    route = respx.post(f"{base_url}/v1/extract").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {},
                "model_used": "x",
                "latency_ms": 1,
                "tokens": {"input": 1, "output": 1},
                "credits_used": 1,
            },
        )
    )
    client.extract(content="x", schema={"type": "object"})
    req = route.calls.last.request
    assert req.headers["x-api-key"] == client.api_key
    assert "structx-sdk/" in req.headers["user-agent"]


@respx.mock
def test_extract_extra_fields_pass_through(client, base_url):
    """Backend may add fields the SDK doesn't know about yet (forward
    compatibility). Test that they don't error out."""
    respx.post(f"{base_url}/v1/extract").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {},
                "model_used": "x",
                "latency_ms": 1,
                "tokens": {"input": 1, "output": 1},
                "credits_used": 1,
                "new_backend_field": "future_value",
                "another_one": {"nested": True},
            },
        )
    )
    result = client.extract(content="x", schema={"type": "object"})
    # Extra fields survive in __pydantic_extra__
    assert result.__pydantic_extra__ is not None
    assert result.__pydantic_extra__.get("new_backend_field") == "future_value"


# ── infer_schema ─────────────────────────────────────────────


@respx.mock
def test_infer_schema_success(client, base_url):
    respx.post(f"{base_url}/v1/schemas/infer").mock(
        return_value=httpx.Response(
            200,
            json={
                "event_id": "rec_evt_01",
                "inferred": {
                    "json_schema": {"type": "object", "properties": {"price": {"type": "number"}}},
                    "fields": [
                        {
                            "name": "price",
                            "type": "number",
                            "description": "Price in USD",
                            "required": True,
                            "rationale": "Prefixed with $",
                            "confidence": 0.88,
                        }
                    ],
                    "overall_confidence": 0.88,
                    "needs_review": False,
                },
                "recommendations": [],
                "credits_used": 5,
            },
        )
    )
    result = client.infer_schema(content="A product page with prices…")
    assert isinstance(result, InferenceResult)
    assert result.inferred.overall_confidence == 0.88
    assert result.inferred.fields[0].name == "price"
    assert result.credits_used == 5


# ── list_templates / list_models / usage ─────────────────────


@respx.mock
def test_list_templates(client, base_url):
    respx.get(f"{base_url}/v1/schemas").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"slug": "logs.stripe.event", "name": "Stripe Event",
                 "schema": {"type": "object"}},
                {"slug": "ecommerce.product", "name": "E-commerce Product",
                 "schema": {"type": "object"}, "usage_count": 142},
            ],
        )
    )
    tpls = client.list_templates()
    assert len(tpls) == 2
    assert all(isinstance(t, Template) for t in tpls)
    assert tpls[1].usage_count == 142
    # JSON-Schema field is exposed as .schema_def (Pydantic reserves
    # .schema as a method name); alias keeps the wire format unchanged.
    assert tpls[0].schema_def == {"type": "object"}


@respx.mock
def test_list_models(client, base_url):
    respx.get(f"{base_url}/v1/models").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"name": "claude-haiku-4-5", "provider": "anthropic"},
                {"name": "gpt-4o-mini", "provider": "openai"},
            ],
        )
    )
    models = client.list_models()
    assert len(models) == 2
    assert all(isinstance(m, Model) for m in models)


@respx.mock
def test_usage(bearer_client, base_url):
    # Bug 2: usage hits `/billing/usage` (no `/v1`). Mock exactly that
    # path — a request to `/v1/billing/usage` would not match and 404.
    route = respx.get(f"{base_url}/billing/usage").mock(
        return_value=httpx.Response(
            200, json={"credits_used_today": 42, "credits_limit_daily": 2000, "tier": "pro"}
        )
    )
    u = bearer_client.usage()
    assert isinstance(u, Usage)
    assert u.credits_used_today == 42
    assert u.tier == "pro"
    # A bearer token, when set, is sent next to the API key.
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer jwt_test_token_0123456789"
    assert req.headers["x-api-key"] == bearer_client.api_key


@respx.mock
def test_extract_id_field_decodes(client, base_url):
    """Bug 1: the extract response id decodes from the wire field `id`
    (the backend emits `id`, not `extraction_id`). The `extraction_id`
    property is a backward-compat alias reading the same value."""
    respx.post(f"{base_url}/v1/extract").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "8a3f7c2d-1b9e-4d6a-b2c3-1f8a7e6c5d4b",
                "data": {"x": 1},
                "model_used": "fast",
                "latency_ms": 10,
                "tokens": {"input": 1, "output": 1},
                "credits_used": 1,
            },
        )
    )
    result = client.extract(content="x", schema={"type": "object"})
    assert result.id == "8a3f7c2d-1b9e-4d6a-b2c3-1f8a7e6c5d4b"
    # Backward-compat alias reads the same value.
    assert result.extraction_id == result.id


@respx.mock
def test_list_my_schemas_sends_bearer_when_set(bearer_client, base_url):
    """With a bearer token set, the personal-schema methods still send
    Authorization: Bearer (next to X-API-Key)."""
    route = respx.get(f"{base_url}/v1/schemas/mine").mock(
        return_value=httpx.Response(200, json={"schemas": [{"slug": "a"}]})
    )
    out = bearer_client.list_my_schemas()
    assert out[0]["slug"] == "a"
    assert (
        route.calls.last.request.headers["authorization"]
        == "Bearer jwt_test_token_0123456789"
    )


# ── Context manager closes http client ───────────────────────


@respx.mock
def test_sync_context_manager_closes(api_key, base_url):
    respx.get(f"{base_url}/v1/schemas").mock(
        return_value=httpx.Response(200, json=[])
    )
    with StructX(api_key=api_key, base_url=base_url) as c:
        c.list_templates()
        assert not c._http.is_closed
    assert c._http.is_closed


# ── Async parity ─────────────────────────────────────────────


@respx.mock
async def test_async_extract(aclient, base_url):
    respx.post(f"{base_url}/v1/extract").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {"x": 1},
                "model_used": "x",
                "latency_ms": 1,
                "tokens": {"input": 1, "output": 1},
                "credits_used": 1,
            },
        )
    )
    result = await aclient.extract(content="x", schema={"type": "object"})
    assert result.data == {"x": 1}


@respx.mock
async def test_async_context_manager_closes(api_key, base_url):
    respx.get(f"{base_url}/v1/schemas").mock(
        return_value=httpx.Response(200, json=[])
    )
    async with AsyncStructX(api_key=api_key, base_url=base_url) as c:
        await c.list_templates()
        assert not c._http.is_closed
    assert c._http.is_closed


@respx.mock
async def test_async_usage_bearer_path(abearer_client, base_url):
    # Bug 2 on the async client: `/billing/usage`; the bearer token is sent when set.
    route = respx.get(f"{base_url}/billing/usage").mock(
        return_value=httpx.Response(
            200, json={"credits_used_today": 3, "credits_limit_daily": 334}
        )
    )
    u = await abearer_client.usage()
    assert u.credits_used_today == 3
    assert route.calls.last.request.headers["authorization"].startswith("Bearer ")
