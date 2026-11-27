"""`browse_templates()` pages `GET /v1/templates`; `save_my_schema()` /
`update_my_schema()` send `publishable_consent` only when the caller sets
it; `usage()` parses the API's real `/billing/usage` shape; `augment_schema()`
never touches the old `/feedback` endpoint.

respx-mocked httpx transport, no live network.
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from structx_sdk import (
    AsyncStructX,
    PermissionDeniedError,
    RetryPolicy,
    StructX,
    TemplatePage,
    TemplateSummary,
    Usage,
)

_ITEM = {
    "id": "5f0c1e2a-0000-4000-8000-000000000001",
    "family_slug": "logs.stripe.event",
    "version": "1.0.0",
    "name": "Stripe event",
    "slug": "logs.stripe.event",
    "description": "A Stripe webhook event.",
    "category": "PAYMENTS",
    "vendor": "stripe",
    "vendor_display_name": "Stripe",
    "log_type": "event",
    "tags": ["stripe", "payments", "logs"],
    "is_first_party": True,
    "default_tier": "required",
    "field_counts": {"required": 9, "recommended": 5, "optional": 2, "extended": 0, "total": 16},
    "usage_count": 12,
    "published_at": "2026-06-01T00:00:00Z",
}


def _page(items, total, limit=25, offset=0):
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _retry() -> RetryPolicy:
    return RetryPolicy(max_attempts=1, initial_backoff=0.0, max_backoff=0.0)


@pytest.fixture
def client(monkeypatch, api_key, base_url):
    monkeypatch.delenv("STRUCTX_BEARER_TOKEN", raising=False)
    return StructX(api_key=api_key, base_url=base_url, retry=_retry())


@pytest.fixture
async def aclient(monkeypatch, api_key, base_url):
    monkeypatch.delenv("STRUCTX_BEARER_TOKEN", raising=False)
    c = AsyncStructX(api_key=api_key, base_url=base_url, retry=_retry())
    yield c
    await c.aclose()


def _params(route) -> dict[str, str]:
    return dict(route.calls.last.request.url.params)


# ── browse_templates ─────────────────────────────────────────


@respx.mock
def test_browse_templates_defaults(client, api_key, base_url):
    """Default page: limit 25, offset 0, no filters; never /v1/schemas."""
    whole = respx.get(f"{base_url}/v1/schemas").mock(return_value=httpx.Response(200, json=[]))
    route = respx.get(f"{base_url}/v1/templates").mock(
        return_value=httpx.Response(200, json=_page([_ITEM], total=1))
    )
    page = client.browse_templates()
    assert _params(route) == {"limit": "25", "offset": "0"}
    assert route.calls.last.request.headers["x-api-key"] == api_key
    assert whole.call_count == 0
    assert isinstance(page, TemplatePage)
    assert page.total == 1
    item = page.items[0]
    assert isinstance(item, TemplateSummary)
    assert item.slug == "logs.stripe.event"
    assert item.field_counts["required"] == 9
    assert "schema" not in item.model_dump() and "json_schema" not in item.model_dump()


@respx.mock
def test_browse_templates_sends_filters_and_paging(client, base_url):
    route = respx.get(f"{base_url}/v1/templates").mock(
        return_value=httpx.Response(200, json=_page([], total=0, limit=50, offset=50))
    )
    client.browse_templates(
        q="stripe payments", category="PAYMENTS", vendor="stripe",
        tag="input:document", first_party=False, limit=50, offset=50,
    )
    assert _params(route) == {
        "q": "stripe payments",
        "category": "PAYMENTS",
        "vendor": "stripe",
        "tag": "input:document",
        "first_party": "false",
        "limit": "50",
        "offset": "50",
    }


@respx.mock
def test_browse_templates_skips_empty_filters(client, base_url):
    """An empty string is not sent (the API rejects an empty `q` with 422)."""
    route = respx.get(f"{base_url}/v1/templates").mock(
        return_value=httpx.Response(200, json=_page([], total=0))
    )
    client.browse_templates(q="", category="", vendor=None, tag=None)
    assert _params(route) == {"limit": "25", "offset": "0"}


@pytest.mark.parametrize(
    "count,total,offset,expected",
    [(25, 60, 0, 25), (25, 60, 25, 50), (10, 60, 50, None), (0, 0, 0, None), (0, 60, 60, None)],
)
def test_template_page_next_offset(count, total, offset, expected):
    page = TemplatePage.model_validate(_page([_ITEM] * count, total=total, offset=offset))
    assert page.next_offset == expected


@respx.mock
async def test_async_browse_templates(aclient, base_url):
    route = respx.get(f"{base_url}/v1/templates").mock(
        return_value=httpx.Response(200, json=_page([_ITEM], total=40, offset=25))
    )
    page = await aclient.browse_templates(q="stripe", offset=25)
    assert _params(route) == {"q": "stripe", "limit": "25", "offset": "25"}
    assert page.items[0].slug == "logs.stripe.event"
    assert page.offset == 25
    assert page.next_offset == 26  # offset + items returned, still short of total


@respx.mock
def test_list_templates_still_returns_the_whole_gallery(client, base_url):
    """The old method keeps working for existing callers."""
    route = respx.get(f"{base_url}/v1/schemas").mock(
        return_value=httpx.Response(200, json=[{"slug": "a", "name": "A", "schema": {"type": "object"}}])
    )
    tpls = client.list_templates()
    assert route.call_count == 1
    assert tpls[0].slug == "a"


# ── publishable_consent is sent only when set ───────────────


@respx.mock
def test_save_my_schema_omits_publishable_consent_by_default(client, base_url):
    route = respx.post(f"{base_url}/v1/schemas/mine").mock(
        return_value=httpx.Response(201, json={"slug": "a"})
    )
    client.save_my_schema(name="A", json_schema={"type": "object"})
    assert json.loads(route.calls.last.request.content) == {
        "name": "A", "json_schema": {"type": "object"},
    }


@respx.mock
async def test_async_save_my_schema_omits_publishable_consent_by_default(aclient, base_url):
    route = respx.post(f"{base_url}/v1/schemas/mine").mock(
        return_value=httpx.Response(201, json={"slug": "a"})
    )
    await aclient.save_my_schema(name="A", json_schema={"type": "object"})
    assert "publishable_consent" not in json.loads(route.calls.last.request.content)


@respx.mock
@pytest.mark.parametrize("value", [True, False])
def test_save_my_schema_sends_publishable_consent_when_set(client, base_url, value):
    route = respx.post(f"{base_url}/v1/schemas/mine").mock(
        return_value=httpx.Response(201, json={"slug": "a"})
    )
    client.save_my_schema(name="A", json_schema={"type": "object"}, publishable_consent=value)
    assert json.loads(route.calls.last.request.content)["publishable_consent"] is value


@respx.mock
def test_update_my_schema_omits_publishable_consent_unless_passed(client, base_url):
    route = respx.patch(f"{base_url}/v1/schemas/mine/a").mock(
        return_value=httpx.Response(200, json={"slug": "a"})
    )
    client.update_my_schema("a", description="v2")
    assert json.loads(route.calls.last.request.content) == {"description": "v2"}


@respx.mock
def test_publishable_consent_portal_only_surfaces_as_permission_denied(client, base_url):
    respx.post(f"{base_url}/v1/schemas/mine").mock(
        return_value=httpx.Response(
            403,
            json={"error": "Set publishable_consent in the dashboard.",
                  "code": "publishable_consent_portal_only"},
        )
    )
    with pytest.raises(PermissionDeniedError) as exc:
        client.save_my_schema(name="A", json_schema={"type": "object"}, publishable_consent=True)
    assert exc.value.code == "publishable_consent_portal_only"


# ── usage() parses the real /billing/usage response ─────────

# The fields `GET /billing/usage` returns (the API's UsageSummary model).
_USAGE = {
    "period": "2026-11-27",
    "tier": "developer",
    "extractions": 4,
    "extractions_today": 4,
    "credits_used": 30,
    "credits_limit": 334,
    "credits_remaining": 304,
    "credits_used_today": 30,
    "credits_limit_today": 334,
    "credits_remaining_today": 304,
    "extractions_this_month": 51,
    "credits_used_this_month": 410,
    "credits_limit_this_month": 10020,
    "period_start": None,
    "period_end": None,
}


@respx.mock
def test_usage_parses_the_real_response(client, base_url):
    respx.get(f"{base_url}/billing/usage").mock(return_value=httpx.Response(200, json=_USAGE))
    u = client.usage()
    assert isinstance(u, Usage)
    assert u.credits_used_today == 30
    assert u.credits_limit_today == 334
    assert u.credits_remaining_today == 304
    assert u.credits_used_this_month == 410
    assert u.credits_limit_this_month == 10020
    assert u.tier == "developer"
    # Backward-compat alias for code written against the old field name.
    assert u.credits_limit_daily == 334


@respx.mock
async def test_async_usage_parses_the_real_response(aclient, base_url):
    respx.get(f"{base_url}/billing/usage").mock(return_value=httpx.Response(200, json=_USAGE))
    u = await aclient.usage()
    assert u.credits_limit_today == 334
    assert u.model_dump()["credits_limit_this_month"] == 10020


# ── augment never uses the old /feedback endpoint ───────────

_EVENT_ID = "8a3f7c2d-1b9e-4d6a-b2c3-1f8a7e6c5d4b"
_INFER = {
    "event_id": "0b5e2a91-77c4-4f0e-9d3a-5c1e2f8b6a70",
    "inferred": {"json_schema": {"type": "object"}, "fields": [], "overall_confidence": 0.9},
    "recommendations": [],
    "credits_used": 4,
}


@respx.mock
def test_augment_schema_does_not_call_feedback(client, base_url):
    prefix = f"{base_url}/v1/schemas/recommendations/{_EVENT_ID}"
    feedback = respx.post(f"{prefix}/feedback").mock(return_value=httpx.Response(204))
    augment = respx.post(f"{prefix}/augment").mock(return_value=httpx.Response(200, json=_INFER))
    client.augment_schema(_EVENT_ID, "drop trace_id", "sample text")
    assert augment.call_count == 1
    assert feedback.call_count == 0
    assert "decision" not in json.loads(augment.calls.last.request.content)
