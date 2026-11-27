"""Happy-path tests for the v0.4.0 core agent surface — batch extract,
canonical projection, synth dataset management, and the template extras
(cost-estimate / versions). Mirrors test_client.py: respx-mocked httpx
transport, no live network.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from structx_sdk import (
    BatchExtraction,
    CanonicalProjection,
    Extraction,
    ExtractionErrorItem,
    Framework,
)


# ── batch_extract ────────────────────────────────────────────


@respx.mock
def test_batch_extract_success(client, base_url):
    route = respx.post(f"{base_url}/v1/extract/batch").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "data": {"title": "Widget"},
                        "model_used": "claude-haiku-4-5",
                        "latency_ms": 120,
                        "tokens": {"input": 40, "output": 12},
                        "credits_used": 1,
                        "credits_remaining": 2999,
                    },
                    {"error": "schema invalid", "code": "schema_invalid"},
                ],
                "total_latency_ms": 250,
                "total_credits_used": 1,
            },
        )
    )
    result = client.batch_extract(
        items=[
            {"content": "<div>Widget</div>", "schema": {"type": "object"}},
            {"content": "bad", "schema": {"type": "object"}},
        ],
        output_format="json",
    )
    assert isinstance(result, BatchExtraction)
    assert result.total_credits_used == 1
    assert isinstance(result.results[0], Extraction)
    assert result.results[0].credits_remaining == 2999
    assert isinstance(result.results[1], ExtractionErrorItem)
    assert result.results[1].code == "schema_invalid"
    body = route.calls.last.request.read().decode()
    assert '"items"' in body
    assert '"output_format":"json"' in body.replace(" ", "")


@respx.mock
def test_batch_extract_no_5xx_retry(client, base_url):
    """Batch is a WRITE path — a 5xx must NOT be retried (it may have
    partially processed). One call, then the typed error surfaces."""
    from structx_sdk import ServerError

    route = respx.post(f"{base_url}/v1/extract/batch").mock(
        return_value=httpx.Response(503, json={"error": "down", "code": "unavailable"})
    )
    with pytest.raises(ServerError):
        client.batch_extract(items=[{"content": "x", "schema": {"type": "object"}}])
    assert route.call_count == 1


# ── canonical projection ─────────────────────────────────────


@respx.mock
def test_list_canonical_frameworks(client, base_url):
    respx.get(f"{base_url}/v1/canonical/frameworks").mock(
        return_value=httpx.Response(
            200,
            json={
                "frameworks": [
                    {"framework": "otel", "version": "1.0", "kind": "event"},
                    {"framework": "ocsf", "version": "1.1", "kind": "event"},
                ]
            },
        )
    )
    fws = client.list_canonical_frameworks()
    assert len(fws) == 2
    assert all(isinstance(f, Framework) for f in fws)
    assert fws[0].framework == "otel"


@respx.mock
def test_project_canonical(client, base_url):
    route = respx.post(f"{base_url}/v1/canonical/project").mock(
        return_value=httpx.Response(
            200,
            json={
                "projections": {
                    "ocsf": {
                        "record": {"class_uid": 1001},
                        "coverage": {"mapped": 3, "unmapped": 1},
                        "valid": True,
                    }
                }
            },
        )
    )
    result = client.project_canonical(
        extracted={"amount": 100},
        frameworks=["ocsf"],
        template_slug="logs.stripe.event",
    )
    assert isinstance(result, CanonicalProjection)
    assert result.projections["ocsf"].valid is True
    assert result.projections["ocsf"].record == {"class_uid": 1001}
    body = route.calls.last.request.read().decode()
    assert "logs.stripe.event" in body


@respx.mock
def test_get_template_canonical(client, base_url):
    route = respx.get(f"{base_url}/v1/templates/logs.stripe.event/canonical").mock(
        return_value=httpx.Response(
            200,
            json={
                "projections": {
                    "ecs": {"record": {}, "coverage": {}, "valid": False}
                }
            },
        )
    )
    result = client.get_template_canonical("logs.stripe.event", framework="ecs")
    assert isinstance(result, CanonicalProjection)
    assert result.projections["ecs"].valid is False
    assert route.calls.last.request.url.params["framework"] == "ecs"


# ── templates extras ─────────────────────────────────────────


@respx.mock
def test_get_template_cost_estimate(client, base_url):
    route = respx.get(f"{base_url}/v1/templates/logs.stripe.event/cost-estimate").mock(
        return_value=httpx.Response(
            200,
            json={
                "family_slug": "logs.stripe.event",
                "version": "1.0.0",
                "tier": "extended",
                "estimated_field_count": 12,
                "estimated_credits": 1,
            },
        )
    )
    out = client.get_template_cost_estimate("logs.stripe.event", tier="extended")
    assert out["estimated_field_count"] == 12
    assert route.calls.last.request.url.params["tier"] == "extended"


@respx.mock
def test_list_template_versions(client, base_url):
    respx.get(f"{base_url}/v1/templates/logs.stripe.event/versions").mock(
        return_value=httpx.Response(
            200,
            json={
                "family_slug": "logs.stripe.event",
                "versions": [{"slug": "logs.stripe.event@1.0.0", "version": "1.0.0"}],
            },
        )
    )
    out = client.list_template_versions("logs.stripe.event")
    assert out["family_slug"] == "logs.stripe.event"
    assert len(out["versions"]) == 1


# ── synth dataset management ─────────────────────────────────


@respx.mock
def test_list_synth_datasets(client, base_url):
    route = respx.get(f"{base_url}/v1/synth/datasets").mock(
        return_value=httpx.Response(
            200,
            json={
                "datasets": [
                    {"id": "ds_1", "name": "set one", "record_count": 10},
                ]
            },
        )
    )
    out = client.list_synth_datasets(limit=50)
    assert out["datasets"][0]["id"] == "ds_1"
    assert route.calls.last.request.url.params["limit"] == "50"


@respx.mock
def test_get_synth_dataset(client, base_url):
    respx.get(f"{base_url}/v1/synth/datasets/ds_1").mock(
        return_value=httpx.Response(
            200,
            json={"id": "ds_1", "records": [{"x": 1}], "manifest": {"produced": 1}},
        )
    )
    out = client.get_synth_dataset("ds_1")
    assert out["records"] == [{"x": 1}]


@respx.mock
def test_export_synth_dataset_returns_bytes(client, base_url):
    route = respx.get(f"{base_url}/v1/synth/datasets/ds_1/export").mock(
        return_value=httpx.Response(
            200,
            content=b'{"x":1}\n{"x":2}\n',
            headers={"content-disposition": 'attachment; filename="ds.jsonl"'},
        )
    )
    body = client.export_synth_dataset("ds_1", format="jsonl")
    assert isinstance(body, bytes)
    assert body == b'{"x":1}\n{"x":2}\n'
    assert route.calls.last.request.url.params["format"] == "jsonl"


@respx.mock
def test_export_synth_dataset_error_raises_typed(client, base_url):
    from structx_sdk import ValidationError

    respx.get(f"{base_url}/v1/synth/datasets/ds_1/export").mock(
        return_value=httpx.Response(
            422, json={"error": "bad format", "code": "unsupported_format"}
        )
    )
    with pytest.raises(ValidationError):
        client.export_synth_dataset("ds_1", format="xlsx")


@respx.mock
def test_delete_synth_dataset(client, base_url):
    respx.delete(f"{base_url}/v1/synth/datasets/ds_1").mock(
        return_value=httpx.Response(200, json={"deleted": True, "id": "ds_1"})
    )
    out = client.delete_synth_dataset("ds_1")
    assert out["deleted"] is True


# ── async parity ─────────────────────────────────────────────


@respx.mock
async def test_async_batch_extract(aclient, base_url):
    respx.post(f"{base_url}/v1/extract/batch").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "data": {"x": 1},
                        "model_used": "x",
                        "latency_ms": 1,
                        "tokens": {"input": 1, "output": 1},
                        "credits_used": 1,
                    }
                ],
                "total_latency_ms": 1,
                "total_credits_used": 1,
            },
        )
    )
    result = await aclient.batch_extract(
        items=[{"content": "x", "schema": {"type": "object"}}]
    )
    assert isinstance(result, BatchExtraction)
    assert isinstance(result.results[0], Extraction)


@respx.mock
async def test_async_list_canonical_frameworks(aclient, base_url):
    respx.get(f"{base_url}/v1/canonical/frameworks").mock(
        return_value=httpx.Response(
            200, json={"frameworks": [{"framework": "otel", "version": "1.0"}]}
        )
    )
    fws = await aclient.list_canonical_frameworks()
    assert fws[0].framework == "otel"


@respx.mock
async def test_async_project_canonical(aclient, base_url):
    respx.post(f"{base_url}/v1/canonical/project").mock(
        return_value=httpx.Response(
            200,
            json={"projections": {"otel": {"record": {}, "coverage": {}, "valid": True}}},
        )
    )
    result = await aclient.project_canonical(
        extracted={"a": 1}, frameworks=["otel"], schema={"type": "object"}
    )
    assert result.projections["otel"].valid is True


@respx.mock
async def test_async_export_synth_dataset(aclient, base_url):
    respx.get(f"{base_url}/v1/synth/datasets/ds_1/export").mock(
        return_value=httpx.Response(200, content=b"csv,data\n1,2\n")
    )
    body = await aclient.export_synth_dataset("ds_1", format="csv")
    assert body == b"csv,data\n1,2\n"


@respx.mock
async def test_async_list_synth_datasets(aclient, base_url):
    respx.get(f"{base_url}/v1/synth/datasets").mock(
        return_value=httpx.Response(200, json={"datasets": []})
    )
    out = await aclient.list_synth_datasets()
    assert out["datasets"] == []


@respx.mock
async def test_async_template_extras(aclient, base_url):
    respx.get(f"{base_url}/v1/templates/t/cost-estimate").mock(
        return_value=httpx.Response(200, json={"estimated_credits": 1})
    )
    respx.get(f"{base_url}/v1/templates/t/versions").mock(
        return_value=httpx.Response(200, json={"family_slug": "t", "versions": []})
    )
    ce = await aclient.get_template_cost_estimate("t")
    ver = await aclient.list_template_versions("t")
    assert ce["estimated_credits"] == 1
    assert ver["family_slug"] == "t"
