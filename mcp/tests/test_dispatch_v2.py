"""Tests for the 10 new tools added in v0.2.0:

  Catalog browsing:
    structx_list_categories
    structx_get_template

  Personal schemas:
    structx_list_my_schemas
    structx_get_my_schema
    structx_save_my_schema
    structx_update_my_schema
    structx_delete_my_schema

  Schema lifecycle:
    structx_recommend_templates
    structx_augment_schema

  Community:
    structx_request_withdrawal

Same pattern as test_dispatch.py — respx-mock the HTTP layer, call
_dispatch directly with the tool name + args + an AsyncStructX from
the conftest fixture, assert the returned payload and (where it
matters) the body sent to the backend.
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from structx_mcp._tools import (
    AUGMENT_SCHEMA_TOOL,
    DELETE_MY_SCHEMA_TOOL,
    SAVE_MY_SCHEMA_TOOL,
    UPDATE_MY_SCHEMA_TOOL,
)
from structx_mcp.server import _dispatch


# ── Catalog browsing ─────────────────────────────────────────


@respx.mock
async def test_list_categories(sdk, base_url):
    respx.get(f"{base_url}/v1/templates/categories").mock(
        return_value=httpx.Response(
            200,
            json={
                "categories": [
                    {"id": "CLOUD_INFRA", "label": "Cloud infra"},
                    {"id": "PAYMENTS", "label": "Payments"},
                ],
            },
        )
    )
    out = await _dispatch("structx_list_categories", {}, sdk)
    assert out["categories"][0]["id"] == "CLOUD_INFRA"


@respx.mock
async def test_get_template(sdk, base_url):
    slug = "logs.stripe.event"
    respx.get(f"{base_url}/v1/templates/{slug}").mock(
        return_value=httpx.Response(
            200,
            json={
                "slug": slug,
                "name": "Stripe Webhook Event",
                "json_schema": {"type": "object"},
                "category": "PAYMENTS",
            },
        )
    )
    out = await _dispatch("structx_get_template", {"slug": slug}, sdk)
    assert out["slug"] == slug
    assert out["name"] == "Stripe Webhook Event"


# ── Personal schemas (user_schemas) ─────────────────────────


@respx.mock
async def test_list_my_schemas_dict_response(sdk, base_url):
    """Backend may return {schemas: [...]} OR a raw list. The MCP tool
    always normalizes to {schemas: [...]} for the agent."""
    respx.get(f"{base_url}/v1/schemas/mine").mock(
        return_value=httpx.Response(
            200,
            json={"schemas": [{"slug": "my-invoice", "name": "My Invoice"}]},
        )
    )
    out = await _dispatch("structx_list_my_schemas", {}, sdk)
    assert "schemas" in out
    assert out["schemas"][0]["slug"] == "my-invoice"


@respx.mock
async def test_list_my_schemas_list_response(sdk, base_url):
    """SDK helper handles bare-list responses too."""
    respx.get(f"{base_url}/v1/schemas/mine").mock(
        return_value=httpx.Response(
            200,
            json=[{"slug": "my-invoice"}, {"slug": "my-order"}],
        )
    )
    out = await _dispatch("structx_list_my_schemas", {}, sdk)
    assert len(out["schemas"]) == 2


@respx.mock
async def test_get_my_schema(sdk, base_url):
    slug = "my-invoice"
    respx.get(f"{base_url}/v1/schemas/mine/{slug}").mock(
        return_value=httpx.Response(
            200,
            json={
                "slug": slug,
                "name": "My invoice",
                "json_schema": {"type": "object"},
                "publishable_consent": False,
            },
        )
    )
    out = await _dispatch("structx_get_my_schema", {"slug": slug}, sdk)
    assert out["slug"] == slug
    assert out["publishable_consent"] is False


@respx.mock
async def test_save_my_schema_minimal(sdk, base_url):
    """Required-only call — name + json_schema. Optional fields omitted."""
    route = respx.post(f"{base_url}/v1/schemas/mine").mock(
        return_value=httpx.Response(
            201,
            json={
                "slug": "my-new-schema",
                "name": "My New Schema",
                "json_schema": {"type": "object"},
                "publishable_consent": False,
            },
        )
    )
    out = await _dispatch(
        "structx_save_my_schema",
        {"name": "My New Schema", "json_schema": {"type": "object"}},
        sdk,
    )
    body = route.calls.last.request.read().decode()
    assert '"name":"My New Schema"' in body or '"name": "My New Schema"' in body
    assert '"publishable_consent":false' in body or '"publishable_consent": false' in body
    assert out["slug"] == "my-new-schema"


@respx.mock
async def test_save_my_schema_with_consent(sdk, base_url):
    """publishable_consent=true signals opt-in to community promotion."""
    route = respx.post(f"{base_url}/v1/schemas/mine").mock(
        return_value=httpx.Response(
            201,
            json={"slug": "shareable", "publishable_consent": True},
        )
    )
    await _dispatch(
        "structx_save_my_schema",
        {
            "name": "Shareable",
            "json_schema": {"type": "object"},
            "publishable_consent": True,
            "category": "PAYMENTS",
            "description": "A useful schema.",
        },
        sdk,
    )
    body = route.calls.last.request.read().decode()
    assert '"publishable_consent":true' in body or '"publishable_consent": true' in body
    assert "PAYMENTS" in body


@respx.mock
async def test_update_my_schema_partial(sdk, base_url):
    """PATCH only the fields the agent passes — slug isn't in the body."""
    slug = "my-invoice"
    route = respx.patch(f"{base_url}/v1/schemas/mine/{slug}").mock(
        return_value=httpx.Response(
            200,
            json={"slug": slug, "publishable_consent": True},
        )
    )
    out = await _dispatch(
        "structx_update_my_schema",
        {"slug": slug, "publishable_consent": True},
        sdk,
    )
    body = route.calls.last.request.read().decode()
    # slug should NOT appear in the body — it's the path identifier.
    assert '"slug"' not in body
    assert '"publishable_consent":true' in body or '"publishable_consent": true' in body
    assert out["publishable_consent"] is True


@respx.mock
async def test_delete_my_schema(sdk, base_url):
    slug = "my-invoice"
    respx.delete(f"{base_url}/v1/schemas/mine/{slug}").mock(
        return_value=httpx.Response(204),
    )
    out = await _dispatch("structx_delete_my_schema", {"slug": slug}, sdk)
    assert out["deleted"] is True
    assert out["slug"] == slug


# ── Schema lifecycle (recommend / augment) ──────────────────


@respx.mock
async def test_recommend_templates_with_content_only(sdk, base_url):
    route = respx.post(f"{base_url}/v1/schemas/recommend").mock(
        return_value=httpx.Response(
            200,
            json={
                "event_id": "rec_evt_42",
                "recommendations": [
                    {"slug": "logs.stripe.event", "score": 0.91},
                ],
                "credits_used": 1,
            },
        )
    )
    out = await _dispatch(
        "structx_recommend_templates",
        {"content": "...stripe webhook json..."},
        sdk,
    )
    body = route.calls.last.request.read().decode()
    assert '"content"' in body
    assert out["event_id"] == "rec_evt_42"
    assert out["recommendations"][0]["slug"] == "logs.stripe.event"


@respx.mock
async def test_recommend_templates_with_draft_schema(sdk, base_url):
    """Recommend can take a draft_schema instead of content — useful
    for "find templates similar to this shape" flows."""
    route = respx.post(f"{base_url}/v1/schemas/recommend").mock(
        return_value=httpx.Response(
            200,
            json={"event_id": "rec_evt_2", "recommendations": []},
        )
    )
    await _dispatch(
        "structx_recommend_templates",
        {"draft_schema": {"type": "object", "properties": {"foo": {"type": "string"}}}, "k": 10},
        sdk,
    )
    body = route.calls.last.request.read().decode()
    assert "draft_schema" in body
    assert '"k":10' in body or '"k": 10' in body


_AUGMENT_RESPONSE = {
    "event_id": "rec_evt_43",
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


@respx.mock
async def test_augment_schema(sdk, base_url):
    """Posts guidance + content + content_type to /augment and returns the
    infer-shaped result as a plain dict."""
    event_id = "rec_evt_42"
    route = respx.post(
        f"{base_url}/v1/schemas/recommendations/{event_id}/augment"
    ).mock(return_value=httpx.Response(200, json=_AUGMENT_RESPONSE))
    out = await _dispatch(
        "structx_augment_schema",
        {
            "event_id": event_id,
            "guidance": "rename event_id to id",
            "content": "<p>sample</p>",
            "content_type": "html",
        },
        sdk,
    )
    assert json.loads(route.calls.last.request.read()) == {
        "guidance": "rename event_id to id",
        "content": "<p>sample</p>",
        "content_type": "html",
    }
    assert out["event_id"] == "rec_evt_43"
    assert out["inferred"]["json_schema"]["properties"]["id"]["type"] == "string"
    json.dumps(out)  # the MCP layer serializes the payload as JSON


@respx.mock
async def test_augment_schema_omits_content_type_when_not_given(sdk, base_url):
    event_id = "rec_evt_42"
    route = respx.post(
        f"{base_url}/v1/schemas/recommendations/{event_id}/augment"
    ).mock(return_value=httpx.Response(200, json=_AUGMENT_RESPONSE))
    await _dispatch(
        "structx_augment_schema",
        {"event_id": event_id, "guidance": "drop trace_id", "content": "sample text"},
        sdk,
    )
    assert json.loads(route.calls.last.request.read()) == {
        "guidance": "drop trace_id",
        "content": "sample text",
    }


def test_augment_tool_requires_content():
    props = AUGMENT_SCHEMA_TOOL.inputSchema["properties"]
    assert AUGMENT_SCHEMA_TOOL.inputSchema["required"] == ["event_id", "guidance", "content"]
    assert "same sample content" in props["content"]["description"]
    assert props["content_type"]["enum"] == ["html", "json", "markdown", "text"]
    assert "content_type" not in AUGMENT_SCHEMA_TOOL.inputSchema["required"]


# ── usage + personal schemas with an API key only ───────────


@respx.mock
@pytest.mark.parametrize(
    "tool,arguments,method,path,status,body",
    [
        ("structx_usage", {}, "GET", "/billing/usage", 200,
         {"credits_used_today": 1, "credits_limit_daily": 17}),
        ("structx_list_my_schemas", {}, "GET", "/v1/schemas/mine", 200, []),
        ("structx_get_my_schema", {"slug": "a"}, "GET", "/v1/schemas/mine/a", 200, {"slug": "a"}),
        ("structx_save_my_schema", {"name": "A", "json_schema": {"type": "object"}},
         "POST", "/v1/schemas/mine", 201, {"slug": "a"}),
        ("structx_update_my_schema", {"slug": "a", "description": "v2"},
         "PATCH", "/v1/schemas/mine/a", 200, {"slug": "a"}),
        ("structx_delete_my_schema", {"slug": "a"}, "DELETE", "/v1/schemas/mine/a", 204, None),
    ],
)
async def test_tool_works_with_api_key_only(sdk, api_key, base_url, tool, arguments, method, path, status, body):
    response = httpx.Response(status) if body is None else httpx.Response(status, json=body)
    route = respx.route(method=method, url=f"{base_url}{path}").mock(return_value=response)
    await _dispatch(tool, arguments, sdk)
    assert route.call_count == 1
    request = route.calls.last.request
    assert request.headers["x-api-key"] == api_key
    assert "authorization" not in request.headers


@pytest.mark.parametrize(
    "tool", [SAVE_MY_SCHEMA_TOOL, UPDATE_MY_SCHEMA_TOOL, DELETE_MY_SCHEMA_TOOL],
    ids=lambda t: t.name,
)
def test_schema_write_tools_say_api_key_is_personal_only(tool):
    desc = tool.description
    assert "With an API key" in desc and "personal" in desc
    assert "organization" in desc and "dashboard" in desc
    assert "JWT" not in desc and "bearer" not in desc.lower()


# ── Community ────────────────────────────────────────────────


@respx.mock
async def test_request_withdrawal_fork_detach(sdk, base_url):
    slug = "ecommerce.acme.product"
    route = respx.post(f"{base_url}/v1/templates/{slug}/withdraw").mock(
        return_value=httpx.Response(
            201,
            json={
                "id": "wd_1",
                "status": "pending",
                "mode": "fork_detach",
            },
        )
    )
    out = await _dispatch(
        "structx_request_withdrawal",
        {
            "slug": slug,
            "mode": "fork_detach",
            "justification": "Product is being deprecated.",
        },
        sdk,
    )
    body = route.calls.last.request.read().decode()
    assert '"mode":"fork_detach"' in body or '"mode": "fork_detach"' in body
    assert "Product is being deprecated" in body
    assert out["status"] == "pending"


@respx.mock
async def test_request_withdrawal_legal_erasure(sdk, base_url):
    """legal_erasure mode deletes forks too — heavier action, used for
    GDPR / DSR-style removal requests."""
    slug = "contact.acme.client-list"
    respx.post(f"{base_url}/v1/templates/{slug}/withdraw").mock(
        return_value=httpx.Response(
            201,
            json={"status": "pending", "mode": "legal_erasure"},
        )
    )
    out = await _dispatch(
        "structx_request_withdrawal",
        {
            "slug": slug,
            "mode": "legal_erasure",
            "justification": "Contains PII inadvertently published.",
        },
        sdk,
    )
    assert out["mode"] == "legal_erasure"
