"""Tests for the 9 core-agent-surface tools added in v0.3.0:

  Extraction (write path):
    structx_extract_batch

  Canonical projection:
    structx_canonical_frameworks
    structx_canonical_project

  Template introspection:
    structx_template_cost_estimate
    structx_template_versions

  Synthetic-dataset lifecycle:
    structx_list_datasets
    structx_get_dataset
    structx_export_dataset
    structx_delete_dataset

Same pattern as test_dispatch_v2.py — respx-mock the HTTP layer, call
_dispatch directly with the tool name + args + the AsyncStructX from the
conftest fixture, assert the returned payload and (where it matters) the
body / params sent to the backend. These tools call the backend through
the SDK's HTTP layer directly (the dedicated SDK methods land in
structx-sdk 0.4.0), so the wiring stays SDK-version-independent.
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from structx_mcp._tools import ALL_TOOLS
from structx_mcp.server import _dispatch


# ── Surface size + registration ─────────────────────────────


def test_all_tools_count():
    """Guards the documented surface size (RELEASE.md smoke test asserts
    the same number). Bump both together when adding/removing tools."""
    assert len(ALL_TOOLS) == 26, len(ALL_TOOLS)


def test_tool_names_unique():
    names = [t.name for t in ALL_TOOLS]
    assert len(names) == len(set(names)), "duplicate tool name in ALL_TOOLS"


@pytest.mark.parametrize(
    "name",
    [
        "structx_extract_batch",
        "structx_canonical_frameworks",
        "structx_canonical_project",
        "structx_template_cost_estimate",
        "structx_template_versions",
        "structx_list_datasets",
        "structx_get_dataset",
        "structx_export_dataset",
        "structx_delete_dataset",
    ],
)
def test_core_tools_registered(name):
    assert name in {t.name for t in ALL_TOOLS}


# ── Batch (write path) ──────────────────────────────────────


@respx.mock
async def test_extract_batch_forwards_items_and_surfaces_credits(sdk, base_url):
    route = respx.post(f"{base_url}/v1/extract/batch").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"data": {"a": 1}, "credits_used": 2},
                    {"error": "boom", "code": "extraction_failed"},
                ],
                "total_latency_ms": 120,
                "total_credits_used": 2,
            },
        )
    )
    out = await _dispatch(
        "structx_extract_batch",
        {
            "items": [
                {"content": "<p>one</p>", "schema": {"type": "object"}},
                {"content": "two", "template_slug": "logs.stripe.event", "tier": "extended"},
            ],
            "options": {"include_citations": True},
        },
        sdk,
    )
    sent = json.loads(route.calls.last.request.content)
    assert len(sent["items"]) == 2
    assert sent["items"][0]["schema"] == {"type": "object"}
    assert sent["items"][1]["template_slug"] == "logs.stripe.event"
    assert sent["items"][1]["tier"] == "extended"
    assert sent["options"]["include_citations"] is True
    # Thin wrapper: surfaces server-side credit/latency totals verbatim.
    assert out["total_credits_used"] == 2
    assert out["total_latency_ms"] == 120
    assert len(out["results"]) == 2


@respx.mock
async def test_extract_batch_drops_none_item_fields(sdk, base_url):
    """Items only carry the keys the caller set — no null schema/template
    leaks through to the backend (which enforces exactly-one-of)."""
    route = respx.post(f"{base_url}/v1/extract/batch").mock(
        return_value=httpx.Response(200, json={"results": [], "total_latency_ms": 0})
    )
    await _dispatch(
        "structx_extract_batch",
        {"items": [{"content": "x", "schema": {"type": "object"}, "template_slug": None}]},
        sdk,
    )
    sent = json.loads(route.calls.last.request.content)
    assert "template_slug" not in sent["items"][0]
    assert "options" not in sent


# ── Canonical projection ────────────────────────────────────


@respx.mock
async def test_canonical_frameworks(sdk, base_url):
    respx.get(f"{base_url}/v1/canonical/frameworks").mock(
        return_value=httpx.Response(
            200,
            json={"frameworks": [
                {"framework": "otel", "version": "1.30.0", "kind": "event"},
                {"framework": "ocsf", "version": "1.3.0", "kind": "event"},
            ]},
        )
    )
    out = await _dispatch("structx_canonical_frameworks", {}, sdk)
    assert out["frameworks"][0]["framework"] == "otel"


@respx.mock
async def test_canonical_project_with_template(sdk, base_url):
    route = respx.post(f"{base_url}/v1/canonical/project").mock(
        return_value=httpx.Response(
            200,
            json={"projections": {"otel": {
                "record": {"name": "login"}, "coverage": {"mapped": 1}, "valid": True,
            }}},
        )
    )
    out = await _dispatch(
        "structx_canonical_project",
        {
            "extracted": {"event": "login"},
            "frameworks": ["otel"],
            "template_slug": "logs.auth.event",
        },
        sdk,
    )
    sent = json.loads(route.calls.last.request.content)
    assert sent["extracted"] == {"event": "login"}
    assert sent["frameworks"] == ["otel"]
    assert sent["template_slug"] == "logs.auth.event"
    assert "schema" not in sent
    assert out["projections"]["otel"]["valid"] is True


@respx.mock
async def test_canonical_project_with_inline_schema(sdk, base_url):
    route = respx.post(f"{base_url}/v1/canonical/project").mock(
        return_value=httpx.Response(200, json={"projections": {}})
    )
    await _dispatch(
        "structx_canonical_project",
        {
            "extracted": {"x": 1},
            "frameworks": ["ocsf"],
            "schema": {"type": "object", "properties": {"x": {"type": "integer"}}},
        },
        sdk,
    )
    sent = json.loads(route.calls.last.request.content)
    assert sent["schema"]["properties"]["x"]["type"] == "integer"
    assert "template_slug" not in sent


# ── Template introspection ──────────────────────────────────


@respx.mock
async def test_template_cost_estimate_passes_tier_param(sdk, base_url):
    slug = "logs.stripe.event"
    route = respx.get(f"{base_url}/v1/templates/{slug}/cost-estimate").mock(
        return_value=httpx.Response(
            200,
            json={
                "family_slug": slug, "version": "1.0.0", "tier": "extended",
                "estimated_field_count": 12, "estimated_input_tokens": 296,
                "estimated_output_tokens": 360, "estimated_credits": 1,
                "per_tier": {},
            },
        )
    )
    out = await _dispatch(
        "structx_template_cost_estimate",
        {"slug": slug, "tier": "extended"},
        sdk,
    )
    assert route.calls.last.request.url.params["tier"] == "extended"
    assert out["estimated_field_count"] == 12


@respx.mock
async def test_template_cost_estimate_defaults_tier_required(sdk, base_url):
    slug = "logs.stripe.event"
    route = respx.get(f"{base_url}/v1/templates/{slug}/cost-estimate").mock(
        return_value=httpx.Response(200, json={"family_slug": slug})
    )
    await _dispatch("structx_template_cost_estimate", {"slug": slug}, sdk)
    assert route.calls.last.request.url.params["tier"] == "required"


@respx.mock
async def test_template_versions(sdk, base_url):
    slug = "logs.stripe.event"
    respx.get(f"{base_url}/v1/templates/{slug}/versions").mock(
        return_value=httpx.Response(
            200,
            json={
                "family_slug": slug,
                "versions": [
                    {"slug": f"{slug}@1.0.0", "version": "1.0.0"},
                    {"slug": f"{slug}@1.1.0", "version": "1.1.0"},
                ],
            },
        )
    )
    out = await _dispatch("structx_template_versions", {"slug": slug}, sdk)
    assert out["family_slug"] == slug
    assert len(out["versions"]) == 2


# ── Synthetic-dataset lifecycle ─────────────────────────────


@respx.mock
async def test_list_datasets_passes_limit(sdk, base_url):
    route = respx.get(f"{base_url}/v1/synth/datasets").mock(
        return_value=httpx.Response(
            200,
            json={"datasets": [{"id": "d1", "name": "demo", "record_count": 10}]},
        )
    )
    out = await _dispatch("structx_list_datasets", {"limit": 50}, sdk)
    assert route.calls.last.request.url.params["limit"] == "50"
    assert out["datasets"][0]["id"] == "d1"


@respx.mock
async def test_list_datasets_default_limit(sdk, base_url):
    route = respx.get(f"{base_url}/v1/synth/datasets").mock(
        return_value=httpx.Response(200, json={"datasets": []})
    )
    await _dispatch("structx_list_datasets", {}, sdk)
    assert route.calls.last.request.url.params["limit"] == "100"


@respx.mock
async def test_get_dataset(sdk, base_url):
    ds_id = "11111111-1111-1111-1111-111111111111"
    respx.get(f"{base_url}/v1/synth/datasets/{ds_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": ds_id, "name": "demo", "record_count": 2,
                "records": [{"a": 1}, {"a": 2}], "manifest": {"produced": 2},
            },
        )
    )
    out = await _dispatch("structx_get_dataset", {"dataset_id": ds_id}, sdk)
    assert out["id"] == ds_id
    assert out["records"] == [{"a": 1}, {"a": 2}]


@respx.mock
async def test_export_dataset_returns_body_and_metadata(sdk, base_url):
    """Export returns a raw file body, not a JSON envelope — the tool
    wraps it with content-type + filename for the agent."""
    ds_id = "22222222-2222-2222-2222-222222222222"
    respx.get(f"{base_url}/v1/synth/datasets/{ds_id}/export").mock(
        return_value=httpx.Response(
            200,
            text='{"a": 1}\n{"a": 2}\n',
            headers={
                "content-type": "application/x-ndjson",
                "content-disposition": 'attachment; filename="demo.jsonl"',
            },
        )
    )
    out = await _dispatch(
        "structx_export_dataset",
        {"dataset_id": ds_id, "format": "jsonl"},
        sdk,
    )
    assert out["format"] == "jsonl"
    assert out["content_type"] == "application/x-ndjson"
    assert out["filename"] == "demo.jsonl"
    assert out["body"] == '{"a": 1}\n{"a": 2}\n'


@respx.mock
async def test_export_dataset_default_format_jsonl(sdk, base_url):
    ds_id = "33333333-3333-3333-3333-333333333333"
    route = respx.get(f"{base_url}/v1/synth/datasets/{ds_id}/export").mock(
        return_value=httpx.Response(200, text="[]", headers={"content-type": "application/json"})
    )
    await _dispatch("structx_export_dataset", {"dataset_id": ds_id}, sdk)
    assert route.calls.last.request.url.params["format"] == "jsonl"


@respx.mock
async def test_export_dataset_maps_errors(sdk, base_url):
    """A non-2xx (e.g. 404 owner-scope) flows through the SDK's typed
    error mapping rather than returning a bogus body."""
    from structx_sdk import NotFoundError

    ds_id = "44444444-4444-4444-4444-444444444444"
    respx.get(f"{base_url}/v1/synth/datasets/{ds_id}/export").mock(
        return_value=httpx.Response(
            404, json={"error": "Dataset not found", "code": "not_found"}
        )
    )
    with pytest.raises(NotFoundError):
        await _dispatch("structx_export_dataset", {"dataset_id": ds_id}, sdk)


@respx.mock
async def test_delete_dataset(sdk, base_url):
    ds_id = "55555555-5555-5555-5555-555555555555"
    respx.delete(f"{base_url}/v1/synth/datasets/{ds_id}").mock(
        return_value=httpx.Response(200, json={"deleted": True, "id": ds_id})
    )
    out = await _dispatch("structx_delete_dataset", {"dataset_id": ds_id}, sdk)
    assert out["deleted"] is True
    assert out["id"] == ds_id


def test_tool_descriptions_never_name_values_outside_their_enum():
    """A parameter description must not present a token as a value the enum
    rejects. Regression: the cost-estimate `tier` description said
    "`per_tier` covers all tiers", so agents sent tier="per_tier" and got an
    input-validation error (per_tier is a RESPONSE field, not a tier)."""
    import re

    from structx_mcp._tools import TEMPLATE_COST_ESTIMATE_TOOL

    tier = TEMPLATE_COST_ESTIMATE_TOOL.inputSchema["properties"]["tier"]
    assert tier["enum"] == ["required", "recommended", "optional", "extended"]
    desc = tier["description"]
    # every backticked token in the description is either an allowed value or
    # explicitly called a response field
    for tok in re.findall(r"`([^`]+)`", desc):
        assert tok in tier["enum"] or "response field" in desc, tok
    assert "not a tier value" in desc
