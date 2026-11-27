"""`options.cache` is the server's name for the cache switch.

Releases up to 0.5.0 documented `options.use_cache`, which the server ignored,
so callers could never skip the extraction cache. The SDK now sends `cache`
and still accepts `use_cache`, renaming it on the wire.
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from structx_sdk import StructXError

_EXTRACTION = {
    "data": {},
    "model_used": "fast",
    "latency_ms": 1,
    "tokens": {"input": 1, "output": 1},
    "credits_used": 1,
}
_BATCH = {"results": [], "total_latency_ms": 1, "total_credits_used": 0}
_SCHEMA = {"type": "object"}


def _sent_options(route) -> dict | None:
    return json.loads(route.calls.last.request.read()).get("options")


@pytest.mark.parametrize(
    "options,expected",
    [
        ({"use_cache": False}, {"cache": False}),
        ({"use_cache": True}, {"cache": True}),
        ({"cache": False}, {"cache": False}),
        ({"cache": False, "use_cache": False}, {"cache": False}),
        ({"use_cache": None}, {}),
        (
            {"use_cache": False, "include_citations": True},
            {"cache": False, "include_citations": True},
        ),
    ],
)
@respx.mock
def test_extract_sends_cache(client, base_url, options, expected):
    route = respx.post(f"{base_url}/v1/extract").mock(
        return_value=httpx.Response(200, json=_EXTRACTION)
    )
    original = dict(options)
    client.extract(content="x", schema=_SCHEMA, options=options)
    assert _sent_options(route) == expected
    assert options == original  # the caller's dict is not changed


@respx.mock
def test_extract_without_options_sends_none(client, base_url):
    route = respx.post(f"{base_url}/v1/extract").mock(
        return_value=httpx.Response(200, json=_EXTRACTION)
    )
    client.extract(content="x", schema=_SCHEMA)
    assert _sent_options(route) is None


@pytest.mark.parametrize("cache,use_cache", [(True, False), (False, True)])
@respx.mock
def test_extract_conflicting_values_raise_before_sending(client, base_url, cache, use_cache):
    route = respx.post(f"{base_url}/v1/extract").mock(
        return_value=httpx.Response(200, json=_EXTRACTION)
    )
    with pytest.raises(StructXError, match="use_cache"):
        client.extract(
            content="x", schema=_SCHEMA, options={"cache": cache, "use_cache": use_cache},
        )
    assert route.call_count == 0


@respx.mock
def test_batch_extract_sends_cache(client, base_url):
    route = respx.post(f"{base_url}/v1/extract/batch").mock(
        return_value=httpx.Response(200, json=_BATCH)
    )
    client.batch_extract(
        items=[{"content": "x", "schema": _SCHEMA}], options={"use_cache": False},
    )
    assert _sent_options(route) == {"cache": False}


@respx.mock
def test_batch_extract_conflict_raises(client, base_url):
    route = respx.post(f"{base_url}/v1/extract/batch").mock(
        return_value=httpx.Response(200, json=_BATCH)
    )
    with pytest.raises(StructXError):
        client.batch_extract(
            items=[{"content": "x", "schema": _SCHEMA}],
            options={"cache": True, "use_cache": False},
        )
    assert route.call_count == 0


@respx.mock
async def test_async_extract_sends_cache(aclient, base_url):
    route = respx.post(f"{base_url}/v1/extract").mock(
        return_value=httpx.Response(200, json=_EXTRACTION)
    )
    await aclient.extract(content="x", schema=_SCHEMA, options={"use_cache": False})
    assert _sent_options(route) == {"cache": False}


@respx.mock
async def test_async_batch_extract_sends_cache(aclient, base_url):
    route = respx.post(f"{base_url}/v1/extract/batch").mock(
        return_value=httpx.Response(200, json=_BATCH)
    )
    await aclient.batch_extract(
        items=[{"content": "x", "schema": _SCHEMA}], options={"use_cache": False},
    )
    assert _sent_options(route) == {"cache": False}


@respx.mock
async def test_async_extract_conflict_raises(aclient, base_url):
    route = respx.post(f"{base_url}/v1/extract").mock(
        return_value=httpx.Response(200, json=_EXTRACTION)
    )
    with pytest.raises(StructXError):
        await aclient.extract(
            content="x", schema=_SCHEMA, options={"cache": False, "use_cache": True},
        )
    assert route.call_count == 0
