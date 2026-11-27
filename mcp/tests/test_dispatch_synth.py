"""Dispatch + registration tests for structx_synthesize_dataset (PR-C4).

Mirrors test_dispatch.py: drive `_dispatch` directly with a respx-mocked SDK,
proving the MCP tool ↔ SDK ↔ /v1/synth/generate wiring without MCP transport.
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from structx_mcp._tools import ALL_TOOLS
from structx_mcp.server import _dispatch


def test_synth_tool_registered():
    assert "structx_synthesize_dataset" in {t.name for t in ALL_TOOLS}


@respx.mock
async def test_synthesize_template_returns_records_and_manifest(sdk, base_url):
    respx.post(f"{base_url}/v1/synth/generate").mock(
        return_value=httpx.Response(200, json={
            "records": [{"a": 1}, {"a": 2}],
            "manifest": {"produced": 2, "anonymized": True,
                         "method_breakdown": {"llm": 1, "derived": 1, "fallback": 0}},
        })
    )
    out = await _dispatch(
        "structx_synthesize_dataset",
        {"template_slug": "logs.aws.cloudtrail", "count": 2}, sdk,
    )
    assert out["manifest"]["anonymized"] is True
    assert out["records"] == [{"a": 1}, {"a": 2}]


@respx.mock
async def test_synthesize_inline_schema_forwards_body(sdk, base_url):
    route = respx.post(f"{base_url}/v1/synth/generate").mock(
        return_value=httpx.Response(200, json={"records": [], "manifest": {}})
    )
    await _dispatch(
        "structx_synthesize_dataset",
        {"json_schema": {"type": "object"}, "count": 5,
         "directives": "mostly denies", "mode": "hybrid"}, sdk,
    )
    sent = json.loads(route.calls.last.request.content)
    assert sent["json_schema"] == {"type": "object"}
    assert sent["count"] == 5
    assert sent["directives"] == "mostly denies"
    assert sent["mode"] == "hybrid"
