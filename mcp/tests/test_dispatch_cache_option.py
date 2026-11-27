"""The extract tools send the API's `cache` option name.

Tool descriptions up to 0.4.0 told agents to send `options.use_cache`, which
the API ignored, so an agent could never skip the extraction cache. The server
now renames `use_cache` to `cache` on structx_extract, structx_extract_from_url
and structx_extract_batch (the batch tool posts directly, not through the SDK).
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx
from structx_sdk import StructXError

from structx_mcp import server as server_mod
from structx_mcp._tools import EXTRACT_TOOL
from structx_mcp.server import _dispatch, _extract_options

_EXTRACTION = {
    "data": {},
    "model_used": "fast",
    "latency_ms": 1,
    "tokens": {"input": 1, "output": 1},
    "credits_used": 1,
}
_BATCH = {"results": [], "total_latency_ms": 0, "total_credits_used": 0}


def _sent_options(route):
    return json.loads(route.calls.last.request.content).get("options")


@pytest.mark.parametrize(
    "options,expected",
    [
        (None, None),
        ({}, {}),
        ({"cache": False}, {"cache": False}),
        ({"use_cache": False}, {"cache": False}),
        ({"use_cache": True}, {"cache": True}),
        ({"cache": True, "use_cache": True}, {"cache": True}),
        ({"use_cache": None}, {}),
        ({"use_cache": False, "include_citations": True}, {"cache": False, "include_citations": True}),
    ],
)
def test_extract_options_mapping(options, expected):
    original = None if options is None else dict(options)
    assert _extract_options(options) == expected
    assert options == original  # the agent's arguments are not changed


@pytest.mark.parametrize("cache,use_cache", [(True, False), (False, True)])
def test_extract_options_conflict_raises(cache, use_cache):
    with pytest.raises(StructXError, match="use_cache"):
        _extract_options({"cache": cache, "use_cache": use_cache})


@respx.mock
async def test_structx_extract_sends_cache(sdk, base_url):
    route = respx.post(f"{base_url}/v1/extract").mock(
        return_value=httpx.Response(200, json=_EXTRACTION)
    )
    await _dispatch(
        "structx_extract",
        {"content": "x", "schema": {"type": "object"}, "options": {"use_cache": False}},
        sdk,
    )
    assert _sent_options(route) == {"cache": False}


@respx.mock
async def test_structx_extract_conflict_sends_nothing(sdk, base_url):
    route = respx.post(f"{base_url}/v1/extract").mock(
        return_value=httpx.Response(200, json=_EXTRACTION)
    )
    with pytest.raises(StructXError):
        await _dispatch(
            "structx_extract",
            {
                "content": "x",
                "schema": {"type": "object"},
                "options": {"cache": True, "use_cache": False},
            },
            sdk,
        )
    assert route.call_count == 0


@respx.mock
async def test_structx_extract_batch_sends_cache(sdk, base_url):
    route = respx.post(f"{base_url}/v1/extract/batch").mock(
        return_value=httpx.Response(200, json=_BATCH)
    )
    await _dispatch(
        "structx_extract_batch",
        {
            "items": [{"content": "x", "schema": {"type": "object"}}],
            "options": {"use_cache": False},
        },
        sdk,
    )
    assert _sent_options(route) == {"cache": False}


@respx.mock
async def test_structx_extract_from_url_sends_cache(sdk, base_url, monkeypatch):
    async def _fake_fetch(url):
        return "<p>page</p>"

    monkeypatch.setattr(server_mod, "validate_fetch_url", lambda url: None)
    monkeypatch.setattr(server_mod, "_fetch_url", _fake_fetch)
    route = respx.post(f"{base_url}/v1/extract").mock(
        return_value=httpx.Response(200, json=_EXTRACTION)
    )
    await _dispatch(
        "structx_extract_from_url",
        {
            "url": "https://example.com/p",
            "schema": {"type": "object"},
            "options": {"use_cache": False},
        },
        sdk,
    )
    assert _sent_options(route) == {"cache": False}


async def test_structx_extract_from_url_conflict_fetches_nothing(sdk, monkeypatch):
    fetched: list[str] = []

    async def _fake_fetch(url):
        fetched.append(url)
        return "<p>page</p>"

    monkeypatch.setattr(server_mod, "validate_fetch_url", lambda url: None)
    monkeypatch.setattr(server_mod, "_fetch_url", _fake_fetch)
    with pytest.raises(StructXError):
        await _dispatch(
            "structx_extract_from_url",
            {"url": "https://example.com/p", "options": {"cache": False, "use_cache": True}},
            sdk,
        )
    assert fetched == []


def test_extract_tool_documents_cache():
    desc = EXTRACT_TOOL.inputSchema["properties"]["options"]["description"]
    assert "`cache` (boolean" in desc
    assert "`use_cache` (boolean" not in desc
