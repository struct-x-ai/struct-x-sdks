"""Test the tool-dispatch layer directly (skip the MCP transport).

`_dispatch(name, arguments, sdk)` is pure — feed it a tool name + args
+ a (mocked-HTTP) SDK, get back the dict payload that would be
serialized to the agent. Tests at this layer prove the wire-up between
MCP tools and SDK methods without needing stdio plumbing.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from structx_mcp.server import _dispatch


@respx.mock
async def test_structx_extract_with_schema(sdk, base_url):
    respx.post(f"{base_url}/v1/extract").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {"price_cents": 9900, "title": "Widget"},
                "model_used": "claude-haiku-4-5",
                "latency_ms": 100,
                "tokens": {"input": 50, "output": 20},
                "credits_used": 2,
            },
        )
    )
    out = await _dispatch(
        "structx_extract",
        {"content": "<p>$99 Widget</p>", "schema": {"type": "object"}},
        sdk,
    )
    assert out["data"] == {"price_cents": 9900, "title": "Widget"}
    assert out["credits_used"] == 2


@respx.mock
async def test_structx_extract_with_template_slug(sdk, base_url):
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
    await _dispatch(
        "structx_extract",
        {"content": "...", "template_slug": "logs.stripe.event@1.0.0", "tier": "extended"},
        sdk,
    )
    body = route.calls.last.request.read().decode()
    assert "logs.stripe.event@1.0.0" in body
    assert '"tier":"extended"' in body or '"tier": "extended"' in body


@respx.mock
async def test_structx_infer_schema(sdk, base_url):
    respx.post(f"{base_url}/v1/schemas/infer").mock(
        return_value=httpx.Response(
            200,
            json={
                "event_id": "rec_evt_1",
                "inferred": {
                    "json_schema": {"type": "object"},
                    "fields": [],
                    "overall_confidence": 0.8,
                    "needs_review": False,
                },
                "recommendations": [],
                "credits_used": 5,
            },
        )
    )
    out = await _dispatch("structx_infer_schema", {"content": "A" * 300}, sdk)
    assert out["event_id"] == "rec_evt_1"
    assert out["inferred"]["overall_confidence"] == 0.8


@respx.mock
async def test_structx_list_templates(sdk, base_url):
    """One page from GET /v1/templates, never the whole /v1/schemas catalog."""
    whole = respx.get(f"{base_url}/v1/schemas").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{base_url}/v1/templates").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [{"slug": "logs.stripe.event", "name": "Stripe Event"}],
                "total": 1,
                "limit": 25,
                "offset": 0,
            },
        )
    )
    out = await _dispatch("structx_list_templates", {}, sdk)
    assert whole.call_count == 0
    assert len(out["templates"]) == 1
    assert out["templates"][0]["slug"] == "logs.stripe.event"
    # List items carry no schema; structx_get_template returns one.
    assert "schema" not in out["templates"][0]
    assert out["total"] == 1
    assert out["next_offset"] is None


@respx.mock
async def test_structx_usage(sdk, base_url):
    respx.get(f"{base_url}/billing/usage").mock(
        return_value=httpx.Response(
            200,
            json={"credits_used_today": 42, "credits_limit_today": 2000, "tier": "pro"},
        )
    )
    out = await _dispatch("structx_usage", {}, sdk)
    assert out["credits_used_today"] == 42
    assert out["tier"] == "pro"


async def test_unknown_tool_raises(sdk):
    with pytest.raises(ValueError, match="Unknown tool"):
        await _dispatch("structx_nonexistent", {}, sdk)


# ── structx_extract_from_url — URL safety checks ────────────


async def test_url_safety_blocks_http(sdk):
    """http:// is rejected — https only."""
    from structx_mcp._url_safety import UrlSafetyError

    with pytest.raises(UrlSafetyError, match="https"):
        await _dispatch(
            "structx_extract_from_url",
            {"url": "http://example.com", "schema": {"type": "object"}},
            sdk,
        )


async def test_url_safety_blocks_metadata_ip(sdk):
    """169.254.169.254 is the cloud metadata IP — block it."""
    from structx_mcp._url_safety import UrlSafetyError

    with pytest.raises(UrlSafetyError, match="blocked"):
        await _dispatch(
            "structx_extract_from_url",
            {"url": "https://169.254.169.254/latest/meta-data/", "schema": {"type": "object"}},
            sdk,
        )


async def test_url_safety_blocks_internal_suffix(sdk):
    """*.internal is a common cloud-internal naming pattern — block it."""
    from structx_mcp._url_safety import UrlSafetyError

    with pytest.raises(UrlSafetyError, match="internal"):
        await _dispatch(
            "structx_extract_from_url",
            {"url": "https://metadata.google.internal/", "schema": {"type": "object"}},
            sdk,
        )


# ── _fetch_url — overall-deadline (slow-loris) protection (Phase 5.5 / φ) ──


async def test_fetch_url_happy_path(monkeypatch):
    """Sanity: wrapping in asyncio.wait_for didn't break the success path.

    Catches a regression where the deadline wrapper accidentally swallows
    or mis-types the body returned from streaming reads.
    """
    from structx_mcp import server as server_mod

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        async def aiter_bytes(self):
            yield b"<html>hello"
            yield b" world</html>"

    class _FakeStreamCtx:
        async def __aenter__(self):
            return _FakeResponse()

        async def __aexit__(self, *exc):
            return False

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, method, url):
            return _FakeStreamCtx()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    out = await server_mod._fetch_url("https://example.com/")
    assert out == "<html>hello world</html>"


async def test_fetch_url_slow_loris_triggers_overall_timeout(monkeypatch):
    """A server that yields one chunk then stalls forever must hit the
    asyncio.wait_for deadline and surface as UrlSafetyError. Without the
    wrapper, the per-chunk timeout would never fire (each chunk arrived
    fine, the wait between chunks is what's unbounded) and the call
    would hang the agent indefinitely.
    """
    from structx_mcp import server as server_mod
    from structx_mcp._url_safety import UrlSafetyError

    # Tighten the deadline so the test runs in <1s instead of waiting 60s.
    monkeypatch.setattr(server_mod, "_URL_FETCH_TOTAL_TIMEOUT_S", 0.1)

    class _SlowResponse:
        def raise_for_status(self) -> None:
            return None

        async def aiter_bytes(self):
            yield b"x"
            # Stall forever — the per-chunk httpx timeout doesn't fire
            # (we're just waiting for the next chunk, not blocked in a
            # single read op), but asyncio.wait_for should.
            await asyncio.sleep(3600)
            yield b"never reached"

    class _SlowStreamCtx:
        async def __aenter__(self):
            return _SlowResponse()

        async def __aexit__(self, *exc):
            return False

    class _SlowClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, method, url):
            return _SlowStreamCtx()

    monkeypatch.setattr(httpx, "AsyncClient", _SlowClient)

    with pytest.raises(UrlSafetyError, match="slow-loris"):
        await server_mod._fetch_url("https://evil.example.com/slow")
