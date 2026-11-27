"""extract_file: POST /v1/extract/document. respx-mocked."""
from __future__ import annotations

import base64
import io
import json

import httpx
import pytest
import respx

from structx_sdk import AsyncStructX, RetryPolicy, StructX
from structx_sdk._exceptions import StructXError, TransportError

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n"
SCHEMA = {"type": "object", "properties": {"total": {"type": "number"}}}
OK = {
    "id": "e1", "data": {"total": 12.5}, "confidence": 0.97, "field_confidences": [],
    "confidence_source": "model", "model_used": "fast", "latency_ms": 900,
    "tokens": {"input": 5600, "output": 120}, "credits_used": 4, "credits_remaining": 96,
}


def _sent(route) -> dict:
    return json.loads(route.calls.last.request.content)


@pytest.fixture
def retrying(api_key, base_url):
    """A client whose policy WOULD retry: extract_file must still send once."""
    c = StructX(api_key=api_key, base_url=base_url,
                retry=RetryPolicy(max_attempts=3, initial_backoff=0.0, max_backoff=0.0))
    yield c
    c.close()


@respx.mock
def test_sends_the_pdf_as_base64_with_the_options(client, base_url, tmp_path):
    route = respx.post(f"{base_url}/v1/extract/document").mock(return_value=httpx.Response(200, json=OK))
    f = tmp_path / "invoice.pdf"
    f.write_bytes(PDF)
    result = client.extract_file(f, schema=SCHEMA, model="balanced", max_credits=40,
                                 fields=["total"], options={"confidence_threshold": 0.8})
    body = _sent(route)
    assert body["document"] == {"type": "base64", "media_type": "application/pdf",
                                "data": base64.b64encode(PDF).decode()}
    assert body["schema"] == SCHEMA and body["model"] == "balanced" and body["max_credits"] == 40
    assert body["fields"] == ["total"] and body["options"] == {"confidence_threshold": 0.8}
    assert "template_slug" not in body and "tier" not in body
    assert result.data == {"total": 12.5} and result.confidence_source == "model"
    assert result.credits_used == 4 and result.id == "e1"


@respx.mock
@pytest.mark.parametrize("make", [
    lambda p: str(p), lambda p: p, lambda p: p.read_bytes(), lambda p: bytearray(p.read_bytes()),
    lambda p: io.BytesIO(p.read_bytes()),
])
def test_accepts_paths_bytes_and_file_objects(client, base_url, tmp_path, make):
    route = respx.post(f"{base_url}/v1/extract/document").mock(return_value=httpx.Response(200, json=OK))
    f = tmp_path / "doc.pdf"
    f.write_bytes(PDF)
    client.extract_file(make(f), template_slug="finance.invoice")
    assert base64.b64decode(_sent(route)["document"]["data"]) == PDF
    assert _sent(route)["template_slug"] == "finance.invoice"


def test_rejects_text_mode_files_empty_and_unknown_inputs(client, tmp_path):
    with pytest.raises(StructXError, match="binary file object"):
        client.extract_file(io.StringIO("not bytes"), schema=SCHEMA)
    with pytest.raises(StructXError, match="empty"):
        client.extract_file(b"", schema=SCHEMA)
    with pytest.raises(StructXError, match="takes a path"):
        client.extract_file(12345, schema=SCHEMA)


@respx.mock
def test_never_retried_on_5xx_or_transport_errors(retrying, base_url):
    route = respx.post(f"{base_url}/v1/extract/document").mock(
        return_value=httpx.Response(503, json={"error": "busy", "code": "document_provider_unavailable"}))
    with pytest.raises(StructXError):
        retrying.extract_file(PDF, schema=SCHEMA)
    assert route.call_count == 1

    route.side_effect = httpx.ReadTimeout("slow")
    with pytest.raises(TransportError):
        retrying.extract_file(PDF, schema=SCHEMA)
    assert route.call_count == 2           # one more call, not three


@respx.mock
def test_uses_a_document_sized_timeout(client, base_url, monkeypatch):
    seen = {}
    real = client._http.request

    def spy(*a, **kw):
        seen["timeout"] = kw.get("timeout")
        return real(*a, **kw)

    monkeypatch.setattr(client._http, "request", spy)
    respx.post(f"{base_url}/v1/extract/document").mock(return_value=httpx.Response(200, json=OK))
    client.extract_file(PDF, schema=SCHEMA)
    assert seen["timeout"] == 180.0
    client.extract_file(PDF, schema=SCHEMA, timeout=30)
    assert seen["timeout"] == 30


@respx.mock
def test_text_extract_still_uses_the_client_default_timeout(client, base_url, monkeypatch):
    seen = {}
    real = client._http.request

    def spy(*a, **kw):
        seen["timeout"] = kw.get("timeout")
        return real(*a, **kw)

    monkeypatch.setattr(client._http, "request", spy)
    respx.post(f"{base_url}/v1/extract").mock(return_value=httpx.Response(200, json={**OK, "confidence_source": None}))
    client.extract("Total: 12.50", schema=SCHEMA)
    assert seen["timeout"] is httpx.USE_CLIENT_DEFAULT


@respx.mock
async def test_async_extract_file(api_key, base_url, tmp_path):
    route = respx.post(f"{base_url}/v1/extract/document").mock(return_value=httpx.Response(200, json=OK))
    f = tmp_path / "doc.pdf"
    f.write_bytes(PDF)
    async with AsyncStructX(api_key=api_key, base_url=base_url,
                            retry=RetryPolicy(max_attempts=3, initial_backoff=0.0, max_backoff=0.0)) as c:
        result = await c.extract_file(f, schema=SCHEMA, model="fast")
        assert result.confidence_source == "model"
        assert base64.b64decode(_sent(route)["document"]["data"]) == PDF
        route.mock(return_value=httpx.Response(502, json={"error": "bad", "code": "llm_output_invalid"}))
        with pytest.raises(StructXError):
            await c.extract_file(PDF, schema=SCHEMA)
        assert route.call_count == 2
