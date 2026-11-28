"""Tool input schemas are valid and self-consistent, `publishable_consent`
is never offered to an agent, and `structx_list_templates` pages
`GET /v1/templates` (25 per page by default, at most 50).

respx-mocked HTTP; `_dispatch` is called directly.
"""
from __future__ import annotations

from typing import Any, Iterator

import httpx
import jsonschema
import pytest
import respx
from structx_sdk import StructXError

from structx_mcp._tools import ALL_TOOLS, LIST_TEMPLATES_TOOL
from structx_mcp.server import _dispatch


def _subschemas(schema: Any) -> Iterator[dict[str, Any]]:
    """Every (sub)schema dict inside a JSON Schema, depth-first."""
    if isinstance(schema, dict):
        yield schema
        for key in ("properties",):
            for sub in (schema.get(key) or {}).values():
                yield from _subschemas(sub)
        if "items" in schema:
            yield from _subschemas(schema["items"])
    elif isinstance(schema, list):
        for sub in schema:
            yield from _subschemas(sub)


def _property_names(schema: Any) -> Iterator[str]:
    for sub in _subschemas(schema):
        yield from (sub.get("properties") or {}).keys()


# ── every tool ───────────────────────────────────────────────


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_input_schema_is_a_valid_json_schema(tool):
    jsonschema.Draft202012Validator.check_schema(tool.inputSchema)
    assert tool.inputSchema["type"] == "object"


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_defaults_are_valid_values(tool):
    """A default must satisfy its own property schema: inside the enum, within
    minimum / maximum. (The MCP server validates arguments against these
    schemas, so an out-of-range default would be rejected.)"""
    for sub in _subschemas(tool.inputSchema):
        if "default" in sub:
            jsonschema.validate(sub["default"], {k: v for k, v in sub.items() if k != "default"})


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_required_names_are_declared_properties(tool):
    for sub in _subschemas(tool.inputSchema):
        for name in sub.get("required", []):
            assert name in sub.get("properties", {}), (tool.name, name)


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_no_tool_offers_publishable_consent(tool):
    """Community-catalog consent is set in the dashboard only."""
    assert "publishable_consent" not in set(_property_names(tool.inputSchema))


def test_tool_count_and_names_unchanged():
    """The hosted MCP server keeps a parity test against these names."""
    assert sorted(t.name for t in ALL_TOOLS) == sorted([
        "structx_extract", "structx_extract_batch", "structx_infer_schema",
        "structx_list_templates", "structx_get_template",
        "structx_template_cost_estimate", "structx_template_versions",
        "structx_list_categories", "structx_recommend_templates",
        "structx_augment_schema", "structx_canonical_frameworks",
        "structx_canonical_project", "structx_list_my_schemas",
        "structx_get_my_schema", "structx_save_my_schema",
        "structx_update_my_schema", "structx_delete_my_schema",
        "structx_extract_from_url", "structx_extract_document",
        "structx_request_withdrawal", "structx_usage",
        "structx_synthesize_dataset", "structx_list_datasets",
        "structx_get_dataset", "structx_export_dataset", "structx_delete_dataset",
    ])


# ── structx_list_templates schema ────────────────────────────


def test_list_templates_schema_pages_25_to_50():
    props = LIST_TEMPLATES_TOOL.inputSchema["properties"]
    assert set(props) == {"q", "category", "limit", "offset"}
    assert props["limit"]["default"] == 25
    assert props["limit"]["minimum"] == 1
    assert props["limit"]["maximum"] == 50
    assert props["offset"]["minimum"] == 0
    assert "required" not in LIST_TEMPLATES_TOOL.inputSchema
    jsonschema.validate({}, LIST_TEMPLATES_TOOL.inputSchema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"limit": 51}, LIST_TEMPLATES_TOOL.inputSchema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"offset": -1}, LIST_TEMPLATES_TOOL.inputSchema)


def test_list_templates_description_says_items_have_no_schema():
    desc = LIST_TEMPLATES_TOOL.description
    assert "no JSON Schema" in desc
    assert "structx_get_template" in desc
    assert "25 per page" in desc and "at most 50" in desc
    assert "next_offset" in desc
    assert "every public template" not in desc.lower()


# ── structx_list_templates dispatch ─────────────────────────


def _page(items: list[dict[str, Any]], total: int, limit: int = 25, offset: int = 0) -> dict[str, Any]:
    return {"items": items, "total": total, "limit": limit, "offset": offset}


_ITEM = {
    "id": "5f0c1e2a-0000-4000-8000-000000000001",
    "slug": "logs.stripe.event",
    "name": "Stripe event",
    "category": "PAYMENTS",
    "vendor": "stripe",
    "tags": ["stripe", "payments"],
    "field_counts": {"required": 9, "recommended": 5, "optional": 2, "extended": 0, "total": 16},
    "published_at": "2026-06-01T00:00:00Z",
}


@respx.mock
async def test_list_templates_default_page(sdk, api_key, base_url):
    route = respx.get(f"{base_url}/v1/templates").mock(
        return_value=httpx.Response(200, json=_page([_ITEM] * 25, total=60))
    )
    out = await _dispatch("structx_list_templates", {}, sdk)
    request = route.calls.last.request
    assert dict(request.url.params) == {"limit": "25", "offset": "0"}
    assert request.headers["x-api-key"] == api_key
    assert len(out["templates"]) == 25
    assert out["total"] == 60
    assert out["limit"] == 25
    assert out["offset"] == 0
    assert out["next_offset"] == 25
    assert out["templates"][0]["field_counts"]["required"] == 9


@respx.mock
async def test_list_templates_filters_and_next_page(sdk, base_url):
    route = respx.get(f"{base_url}/v1/templates").mock(
        return_value=httpx.Response(200, json=_page([_ITEM] * 10, total=60, limit=50, offset=50))
    )
    out = await _dispatch(
        "structx_list_templates",
        {"q": "  Stripe   Payments ", "category": "payments", "limit": 50, "offset": 50},
        sdk,
    )
    assert dict(route.calls.last.request.url.params) == {
        "q": "stripe payments",
        "category": "PAYMENTS",
        "limit": "50",
        "offset": "50",
    }
    assert out["next_offset"] is None  # 50 + 10 == total


@respx.mock
@pytest.mark.parametrize("sent,expected", [(500, "50"), (0, "1"), (-3, "1"), (None, "25")])
async def test_list_templates_clamps_limit(sdk, base_url, sent, expected):
    """The MCP server validates limit against the schema; dispatch clamps as
    well, so a direct call can't pull more than 50 per page."""
    route = respx.get(f"{base_url}/v1/templates").mock(
        return_value=httpx.Response(200, json=_page([], total=0))
    )
    args = {} if sent is None else {"limit": sent}
    await _dispatch("structx_list_templates", args, sdk)
    assert route.calls.last.request.url.params["limit"] == expected


@respx.mock
async def test_list_templates_drops_empty_filters(sdk, base_url):
    route = respx.get(f"{base_url}/v1/templates").mock(
        return_value=httpx.Response(200, json=_page([], total=0))
    )
    await _dispatch("structx_list_templates", {"q": "   ", "category": "", "offset": None}, sdk)
    assert dict(route.calls.last.request.url.params) == {"limit": "25", "offset": "0"}


@pytest.mark.parametrize(
    "arguments,match",
    [
        ({"q": "stripe; drop"}, "q may contain only"),
        ({"q": "a" * 101}, "q may contain only"),
        ({"category": "pay-ments"}, "category must be a code"),
    ],
)
async def test_list_templates_rejects_bad_filters_before_any_request(sdk, arguments, match):
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(StructXError, match=match):
            await _dispatch("structx_list_templates", arguments, sdk)
        assert route.call_count == 0


@respx.mock
async def test_list_templates_result_is_json_serializable(sdk, base_url):
    """call_tool serializes the payload with json.dumps(default=str)."""
    import json

    respx.get(f"{base_url}/v1/templates").mock(
        return_value=httpx.Response(200, json=_page([_ITEM], total=1))
    )
    out = await _dispatch("structx_list_templates", {}, sdk)
    text = json.dumps(out, default=str)
    assert "logs.stripe.event" in text
    assert '"schema"' not in text
