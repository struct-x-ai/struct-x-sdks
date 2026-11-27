"""Sync (`StructX`) and async (`AsyncStructX`) clients.

Both share `_BaseClient` for configuration + response parsing + retry
policy; only the I/O path differs. We intentionally don't subclass
across sync/async (Python's protocol mismatch makes that messy);
instead, the response-parsing and retry logic are pure functions
operating on `httpx.Response`-shaped data, callable from either side.

httpx is the underlying HTTP library — supports sync + async with one
API, ships its own connection pool, and respects PEP 8.
"""
from __future__ import annotations

import asyncio
import base64
import os
import platform
import random
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urljoin

import httpx

from ._exceptions import (
    ApiError,
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    StructXError,
    TransportError,
    ValidationError,
)
from ._models import (
    BatchExtraction,
    CanonicalProjection,
    Extraction,
    Framework,
    InferenceResult,
    Model,
    Template,
    Usage,
)
from ._version import __version__

_DEFAULT_BASE_URL = "https://api.struct-x.ai"
_USER_AGENT = (
    f"structx-sdk/{__version__} "
    f"httpx/{httpx.__version__} "
    f"Python/{platform.python_version()}"
)

# Endpoints that mutate / bill — retry only on TransportError, never
# on 5xx (the server may have partially processed before failing).
_WRITE_PATHS = frozenset({
    "/v1/extract", "/v1/extract/batch", "/v1/schemas/infer", "/v1/extract/document",
})

# Document extraction (POST /v1/extract/document): the file is sent base64 in
# JSON. The server enforces the real limits (5-20 MB by plan); this cap only
# stops an accidental multi-gigabyte read on the client.
_DOCUMENT_MAX_BYTES = 32 * 1024 * 1024
_DOCUMENT_TIMEOUT = 180.0  # the server allows 120 s per document, plus upload


# ── Retry policy ────────────────────────────────────────────


@dataclass(frozen=True)
class RetryPolicy:
    """Controls retry behavior for transient failures.

    Defaults are conservative: 3 attempts total, exponential backoff
    capped at 30s, 5xx-retry enabled for reads but disabled for writes
    (the SDK enforces the write-exclusion at call sites; this flag only
    affects reads). `respect_retry_after` lets the server tell us
    when to come back — used by 429s.
    """

    max_attempts: int = 3
    initial_backoff: float = 1.0
    max_backoff: float = 30.0
    retry_on_5xx: bool = True
    respect_retry_after: bool = True
    jitter: float = 0.2

    def backoff_for(self, attempt: int, retry_after: float | None = None) -> float:
        """Seconds to sleep before `attempt` (1-indexed). If the server
        sent `Retry-After`, use that — caps still apply."""
        if retry_after is not None and self.respect_retry_after:
            return min(retry_after, self.max_backoff)
        base = self.initial_backoff * (2 ** (attempt - 1))
        jitter_range = base * self.jitter
        return min(base + random.uniform(-jitter_range, jitter_range), self.max_backoff)


# ── Base client (shared config + parsing) ────────────────────


_EXC_BY_STATUS: dict[int, type[ApiError]] = {
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    400: ValidationError,
    422: ValidationError,
    429: RateLimitError,
}


@dataclass
class _BaseClient:
    api_key: str
    # Optional JWT bearer token — the second credential path. Required by
    # the JWT-only methods (`usage()` + the `/v1/schemas/mine` personal-
    # schema CRUD), whose backend routes authenticate via
    # `get_current_user_from_token` (Authorization: Bearer only; an API
    # key is rejected with 401). The API-key methods keep using X-API-Key.
    bearer_token: str | None = None
    base_url: str = _DEFAULT_BASE_URL
    timeout: float = 30.0
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    default_headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.api_key:
            raise StructXError(
                "api_key is required. Pass it directly or set the "
                "STRUCTX_API_KEY environment variable."
            )
        # Normalize base_url so urljoin works predictably with leading-
        # slash paths.
        self.base_url = self.base_url.rstrip("/") + "/"

    def _headers(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        h = {
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
        }
        # API-key credential — used by the dual-auth + API-key methods.
        if self.api_key:
            h["X-API-Key"] = self.api_key
        # Bearer (JWT) credential — used by the JWT-only methods. When both
        # credentials are configured we send both headers and let the
        # backend pick the right one per endpoint.
        if self.bearer_token:
            h["Authorization"] = f"Bearer {self.bearer_token}"
        h.update(self.default_headers)
        if extra:
            h.update(extra)
        return h

    def _require_bearer(self, method: str) -> None:
        """Guard for the JWT-only methods. Raise a clear SDK-side error
        (instead of letting the request 401) when no bearer token is set.

        These methods (`usage`, and the `list/get/save/update/delete
        _my_schema` personal-schema CRUD) hit backend routes guarded by
        `get_current_user_from_token`, which accepts an Authorization:
        Bearer JWT only — an API key alone is rejected with 401."""
        if not self.bearer_token:
            raise StructXError(
                f"{method}() requires a bearer token (JWT). This endpoint "
                "authenticates via Authorization: Bearer only — an API key "
                "alone is rejected with 401. Pass bearer_token=... when "
                "constructing the client or set the STRUCTX_BEARER_TOKEN "
                "environment variable."
            )

    def _url(self, path: str) -> str:
        # urljoin requires trailing-slash on base; we ensured that in
        # __post_init__. Path may be absolute (/v1/...) or relative.
        return urljoin(self.base_url, path.lstrip("/"))

    @staticmethod
    def _parse_response(response: httpx.Response) -> dict[str, Any]:
        """Translate a raw HTTP response into either the JSON body or
        an appropriate typed exception. Single source of truth for
        error mapping — both sync and async paths route through here."""
        request_id = response.headers.get("x-request-id") or response.headers.get(
            "request-id"
        )
        if 200 <= response.status_code < 300:
            if not response.content:
                return {}
            try:
                return response.json()
            except ValueError as e:
                raise ApiError(
                    f"Server returned a non-JSON 2xx response: {e}",
                    status_code=response.status_code,
                    request_id=request_id,
                ) from e

        # Error path. Backend convention is {"error": str, "code": str}.
        body: dict[str, Any] = {}
        try:
            body = response.json() if response.content else {}
        except ValueError:
            pass

        message = body.get("error") or response.text or response.reason_phrase
        code = body.get("code")
        exc_cls = _EXC_BY_STATUS.get(response.status_code)
        if exc_cls is None:
            exc_cls = ServerError if response.status_code >= 500 else ApiError

        if exc_cls is RateLimitError:
            retry_after_header = response.headers.get("retry-after")
            try:
                retry_after = float(retry_after_header) if retry_after_header else None
            except ValueError:
                retry_after = None
            raise RateLimitError(
                message,
                status_code=response.status_code,
                code=code,
                response_body=body,
                request_id=request_id,
                retry_after=retry_after,
                credits_used=body.get("credits_used"),
                credits_remaining=body.get("credits_remaining"),
            )

        raise exc_cls(
            message,
            status_code=response.status_code,
            code=code,
            response_body=body,
            request_id=request_id,
        )

    @staticmethod
    def _read_document(file: Any) -> bytes:
        """Bytes from a path, bytes-like object, or binary file object."""
        if isinstance(file, (bytes, bytearray, memoryview)):
            data = bytes(file)
        elif isinstance(file, (str, os.PathLike)):
            with open(os.fspath(file), "rb") as fh:
                data = fh.read(_DOCUMENT_MAX_BYTES + 1)
        elif hasattr(file, "read"):
            data = file.read(_DOCUMENT_MAX_BYTES + 1)
            if isinstance(data, str):
                raise StructXError("extract_file needs a binary file object (open it with 'rb')")
        else:
            raise StructXError(
                "extract_file takes a path, bytes, or a binary file object, "
                f"not {type(file).__name__}"
            )
        if len(data) > _DOCUMENT_MAX_BYTES:
            raise StructXError(
                f"document is larger than {_DOCUMENT_MAX_BYTES // (1024 * 1024)} MB; "
                "the API accepts at most 20 MB (by plan)"
            )
        if not data:
            raise StructXError("document is empty")
        return data

    @staticmethod
    def _document_body(
        data: bytes,
        *,
        media_type: str,
        schema: dict[str, Any] | None,
        template_slug: str | None,
        model: str | None,
        tier: str | None,
        fields: list[str] | None,
        max_credits: int | None,
        options: dict[str, Any] | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "document": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.b64encode(data).decode("ascii"),
            },
        }
        for key, value in (("schema", schema), ("template_slug", template_slug),
                           ("model", model), ("tier", tier), ("fields", fields),
                           ("max_credits", max_credits), ("options", options)):
            if value is not None:
                body[key] = value
        return body

    def _is_retryable_response(self, path: str, status: int) -> bool:
        """5xx retry rule. Writes never retry on 5xx — see _WRITE_PATHS
        and the docstring on TransportError."""
        if not self.retry.retry_on_5xx:
            return False
        if path in _WRITE_PATHS:
            return False
        return 500 <= status < 600 and status != 501  # Not Implemented isn't transient

    @staticmethod
    def _parse_bytes_response(response: httpx.Response) -> bytes:
        """Like `_parse_response`, but for endpoints that return a raw
        file body (e.g. dataset export) rather than JSON. On 2xx returns
        the raw bytes; on error routes through `_parse_response` so the
        same typed exceptions + `{"error","code"}` mapping apply."""
        if 200 <= response.status_code < 300:
            return response.content
        _BaseClient._parse_response(response)  # always raises on non-2xx
        raise StructXError("unreachable: error response did not raise")


# ── Sync client ──────────────────────────────────────────────


class StructX(_BaseClient):
    """Synchronous client.

    Example:
        >>> from structx_sdk import StructX
        >>> client = StructX(api_key="sx_...")
        >>> result = client.extract(
        ...     content="<div>$99 Widget</div>",
        ...     schema={"type": "object", "properties": {
        ...         "price_cents": {"type": "integer"},
        ...         "title": {"type": "string"},
        ...     }},
        ... )
        >>> result.data
        {'price_cents': 9900, 'title': 'Widget'}
        >>> result.field_confidences[0].confidence
        0.92

    Picks up `STRUCTX_API_KEY` and `STRUCTX_BASE_URL` from the
    environment if not passed explicitly:

        >>> client = StructX.from_env()
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        bearer_token: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
        retry: RetryPolicy | None = None,
        default_headers: Mapping[str, str] | None = None,
        _http: httpx.Client | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key or os.environ.get("STRUCTX_API_KEY", ""),
            bearer_token=bearer_token or os.environ.get("STRUCTX_BEARER_TOKEN"),
            base_url=base_url or os.environ.get("STRUCTX_BASE_URL", _DEFAULT_BASE_URL),
            timeout=timeout,
            retry=retry or RetryPolicy(),
            default_headers=default_headers or {},
        )
        self._http = _http or httpx.Client(timeout=timeout)
        self._owns_http = _http is None

    @classmethod
    def from_env(cls, **overrides: Any) -> "StructX":
        return cls(**overrides)

    def __enter__(self) -> "StructX":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    # ── Internal request loop ────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        max_attempts: int | None = None,
    ) -> dict[str, Any]:
        last_exc: Exception | None = None
        attempts = max_attempts or self.retry.max_attempts
        for attempt in range(1, attempts + 1):
            try:
                response = self._http.request(
                    method=method,
                    url=self._url(path),
                    headers=self._headers(headers),
                    json=json,
                    params=params,
                    timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
                )
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last_exc = TransportError(f"{type(e).__name__}: {e}")
                if attempt < attempts:
                    time.sleep(self.retry.backoff_for(attempt))
                    continue
                raise last_exc from e

            if attempt < attempts and self._is_retryable_response(
                path, response.status_code
            ):
                retry_after = response.headers.get("retry-after")
                try:
                    ra = float(retry_after) if retry_after else None
                except ValueError:
                    ra = None
                time.sleep(self.retry.backoff_for(attempt, retry_after=ra))
                continue

            return self._parse_response(response)

        # The loop always returns or raises; this is unreachable.
        raise last_exc or StructXError("retry loop exhausted with no response")

    def _request_bytes(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> bytes:
        """Request loop variant for raw-file responses (dataset export).
        Shares the retry policy with `_request`; only the success-path
        decoding differs (raw bytes instead of JSON)."""
        last_exc: Exception | None = None
        for attempt in range(1, self.retry.max_attempts + 1):
            try:
                response = self._http.request(
                    method=method,
                    url=self._url(path),
                    headers=self._headers(headers),
                    params=params,
                )
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last_exc = TransportError(f"{type(e).__name__}: {e}")
                if attempt < self.retry.max_attempts:
                    time.sleep(self.retry.backoff_for(attempt))
                    continue
                raise last_exc from e

            if attempt < self.retry.max_attempts and self._is_retryable_response(
                path, response.status_code
            ):
                retry_after = response.headers.get("retry-after")
                try:
                    ra = float(retry_after) if retry_after else None
                except ValueError:
                    ra = None
                time.sleep(self.retry.backoff_for(attempt, retry_after=ra))
                continue

            return self._parse_bytes_response(response)

        raise last_exc or StructXError("retry loop exhausted with no response")

    # ── Public API ───────────────────────────────────────────

    def extract(
        self,
        content: str,
        *,
        schema: dict[str, Any] | None = None,
        template_slug: str | None = None,
        tier: str = "required",
        options: dict[str, Any] | None = None,
    ) -> Extraction:
        """Run a structured extraction against `content`.

        Pass EXACTLY ONE of `schema` (inline JSON Schema) or
        `template_slug` (a catalog template like `"logs.stripe.event"`).
        The backend enforces this; passing both raises `ValidationError`.

        `tier` selects field depth: `"required"` is the cheapest /
        narrowest extraction; `"extended"` returns every field in the
        schema. See backend docs for the full tier ladder.

        `options` is forwarded as-is to the backend — use it for
        `include_citations`, `use_cache`, `confidence_threshold`, etc.
        """
        body: dict[str, Any] = {"content": content, "tier": tier}
        if schema is not None:
            body["schema"] = schema
        if template_slug is not None:
            body["template_slug"] = template_slug
        if options is not None:
            body["options"] = options
        return Extraction.model_validate(self._request("POST", "/v1/extract", json=body))

    def extract_file(
        self,
        file: Any,
        *,
        schema: dict[str, Any] | None = None,
        template_slug: str | None = None,
        model: str | None = None,
        tier: str | None = None,
        fields: list[str] | None = None,
        max_credits: int | None = None,
        options: dict[str, Any] | None = None,
        media_type: str = "application/pdf",
        timeout: float = _DOCUMENT_TIMEOUT,
    ) -> Extraction:
        """Extract structured data from a PDF (`POST /v1/extract/document`).

        `file` is a path, `bytes`, or a binary file object. Pass EXACTLY ONE of
        `schema` or `template_slug`. `model` is `"auto"` (default), `"fast"`,
        `"balanced"` or `"quality"` (paid plans). `max_credits` caps what this
        document may cost; a higher estimate is refused before any work.

        Not retried automatically: the server may still finish (and charge) a
        document after the client gives up, so a silent retry could charge twice.
        Retry yourself on `RateLimitError` / 5xx if you want to. The returned
        `Extraction` has `confidence_source == "model"`: document confidence is
        reported per field by the model, not grounded in text.
        """
        data = self._read_document(file)
        body = self._document_body(
            data, media_type=media_type, schema=schema, template_slug=template_slug,
            model=model, tier=tier, fields=fields, max_credits=max_credits, options=options,
        )
        return Extraction.model_validate(self._request(
            "POST", "/v1/extract/document", json=body, timeout=timeout, max_attempts=1,
        ))

    def infer_schema(
        self,
        content: str,
        *,
        content_type: str | None = None,
        hints: dict[str, Any] | None = None,
        k: int = 5,
        return_recommendations: bool = True,
    ) -> InferenceResult:
        """Infer a JSON Schema from raw content + optionally return
        template recommendations that match. Costs `infer_min_credits`
        per call (configured on the backend; defaults to 3-5)."""
        body: dict[str, Any] = {
            "content": content,
            "k": k,
            "return_recommendations": return_recommendations,
        }
        if content_type is not None:
            body["content_type"] = content_type
        if hints is not None:
            body["hints"] = hints
        return InferenceResult.model_validate(
            self._request("POST", "/v1/schemas/infer", json=body)
        )

    def list_templates(self) -> list[Template]:
        """Public template gallery."""
        raw = self._request("GET", "/v1/schemas")
        items = raw if isinstance(raw, list) else raw.get("templates", [])
        return [Template.model_validate(t) for t in items]

    def list_models(self) -> list[Model]:
        """Available models. Most callers don't need this — the
        backend's router picks per call."""
        raw = self._request("GET", "/v1/models")
        items = raw if isinstance(raw, list) else raw.get("models", [])
        return [Model.model_validate(m) for m in items]

    def usage(self) -> Usage:
        """Current credit usage for the authenticated user.

        Bearer-token method: the backend mounts this at `/billing/usage`
        (no `/v1` prefix) and authenticates it via JWT Bearer only. Set
        `bearer_token=...` on the client (or `STRUCTX_BEARER_TOKEN`)."""
        self._require_bearer("usage")
        return Usage.model_validate(self._request("GET", "/billing/usage"))

    # ── Catalog browsing (v0.3.0) ──────────────────────────

    def list_categories(self) -> dict[str, Any]:
        """Return the category → family → vendor hierarchy used by the
        public template catalog. Raw dict; no Pydantic model yet."""
        return self._request("GET", "/v1/templates/categories")

    def get_template(self, slug: str) -> dict[str, Any]:
        """Fetch one template's full JSON Schema + metadata by slug."""
        return self._request("GET", f"/v1/templates/{slug}")

    # ── Personal schemas (v0.3.0) ──────────────────────────

    def list_my_schemas(self) -> list[dict[str, Any]]:
        """List schemas the calling user has saved to their account.

        Bearer-token method (`/v1/schemas/mine` is JWT-only)."""
        self._require_bearer("list_my_schemas")
        raw = self._request("GET", "/v1/schemas/mine")
        return raw if isinstance(raw, list) else raw.get("schemas", [])

    def get_my_schema(self, slug: str) -> dict[str, Any]:
        """Fetch one of the caller's saved schemas by slug.

        Bearer-token method (`/v1/schemas/mine` is JWT-only)."""
        self._require_bearer("get_my_schema")
        return self._request("GET", f"/v1/schemas/mine/{slug}")

    def save_my_schema(
        self,
        *,
        name: str,
        json_schema: dict[str, Any],
        slug: str | None = None,
        description: str | None = None,
        category: str | None = None,
        publishable_consent: bool = False,
    ) -> dict[str, Any]:
        """Persist a new JSON Schema to the caller's account.

        Bearer-token method (`/v1/schemas/mine` is JWT-only)."""
        self._require_bearer("save_my_schema")
        body: dict[str, Any] = {
            "name": name,
            "json_schema": json_schema,
            "publishable_consent": publishable_consent,
        }
        if slug is not None:
            body["slug"] = slug
        if description is not None:
            body["description"] = description
        if category is not None:
            body["category"] = category
        return self._request("POST", "/v1/schemas/mine", json=body)

    def update_my_schema(self, slug: str, **fields: Any) -> dict[str, Any]:
        """Patch one of the caller's schemas. Pass only the fields you
        want to change.

        Bearer-token method (`/v1/schemas/mine` is JWT-only)."""
        self._require_bearer("update_my_schema")
        body = {k: v for k, v in fields.items() if v is not None}
        return self._request("PATCH", f"/v1/schemas/mine/{slug}", json=body)

    def delete_my_schema(self, slug: str) -> None:
        """Permanently delete one of the caller's saved schemas.

        Bearer-token method (`/v1/schemas/mine` is JWT-only)."""
        self._require_bearer("delete_my_schema")
        self._request("DELETE", f"/v1/schemas/mine/{slug}")

    # ── Schema lifecycle (v0.3.0) ──────────────────────────

    def recommend_templates(
        self,
        *,
        content: str | None = None,
        draft_schema: dict[str, Any] | None = None,
        k: int = 5,
    ) -> dict[str, Any]:
        """Find existing templates that match content / draft schema.
        At least one of content / draft_schema must be provided."""
        body: dict[str, Any] = {"k": k}
        if content is not None:
            body["content"] = content
        if draft_schema is not None:
            body["draft_schema"] = draft_schema
        return self._request("POST", "/v1/schemas/recommend", json=body)

    def augment_schema(self, event_id: str, guidance: str) -> dict[str, Any]:
        """Refine a schema using natural-language guidance. Requires the
        event_id from a prior infer/recommend call."""
        body = {"decision": "augment", "augment_guidance": guidance}
        return self._request(
            "POST", f"/v1/schemas/recommendations/{event_id}/feedback",
            json=body,
        )

    # ── Synthetic data (v0.4.0) ────────────────────────────

    def synthesize(
        self,
        *,
        template_slug: str | None = None,
        json_schema: dict[str, Any] | None = None,
        count: int = 10,
        directives: str | None = None,
        params: dict[str, Any] | None = None,
        mode: str = "hybrid",
    ) -> dict[str, Any]:
        """Generate an anonymized, schema-valid synthetic dataset from a
        template slug OR an inline JSON Schema, shaped by free-text `directives`.
        Returns ``{"records": [...], "manifest": {...}}`` — the manifest reports
        counts, method breakdown, validation, PII scrubbed, and the anonymity
        attestation. Provide exactly one of template_slug / json_schema. Billed
        against the API key's credit quota."""
        body: dict[str, Any] = {"count": count, "mode": mode}
        if template_slug is not None:
            body["template_slug"] = template_slug
        if json_schema is not None:
            body["json_schema"] = json_schema
        if directives is not None:
            body["directives"] = directives
        if params is not None:
            body["params"] = params
        return self._request("POST", "/v1/synth/generate", json=body)

    # ── Batch extraction (v0.4.0) ──────────────────────────

    def batch_extract(
        self,
        items: list[dict[str, Any]],
        *,
        options: dict[str, Any] | None = None,
        output_format: str | None = None,
        csv_array_mode: str | None = None,
    ) -> BatchExtraction:
        """Extract from up to 100 items in one call (`POST /v1/extract/batch`).

        Each element of `items` is a per-item extraction request: a dict
        carrying `content` plus EXACTLY ONE of `schema` (inline JSON
        Schema) or `template_slug`, and optionally `tier`. Items are
        metered individually server-side; this is a thin wrapper that
        surfaces the batch totals — there is no client-side credit logic.

        `options` is a batch-wide override forwarded as-is (per-item
        `options` are ignored by the backend). `output_format`
        (`"json"` / `"ndjson"` / `"csv"`) and `csv_array_mode` control
        batch-level serialization; omit them to use the account default.

        Like `extract()`, this is a billed write path: it is retried only
        on transport errors, never on a 5xx (the batch may have been
        partially processed).
        """
        body: dict[str, Any] = {"items": items}
        if options is not None:
            body["options"] = options
        if output_format is not None:
            body["output_format"] = output_format
        if csv_array_mode is not None:
            body["csv_array_mode"] = csv_array_mode
        return BatchExtraction.model_validate(
            self._request("POST", "/v1/extract/batch", json=body)
        )

    # ── Canonical projection (v0.4.0) ──────────────────────

    def list_canonical_frameworks(self) -> list[Framework]:
        """List the canonical normalization targets available for
        projection (`GET /v1/canonical/frameworks`)."""
        raw = self._request("GET", "/v1/canonical/frameworks")
        items = raw if isinstance(raw, list) else raw.get("frameworks", [])
        return [Framework.model_validate(f) for f in items]

    def project_canonical(
        self,
        extracted: dict[str, Any],
        frameworks: list[str],
        *,
        template_slug: str | None = None,
        schema: dict[str, Any] | None = None,
    ) -> CanonicalProjection:
        """Project an already-extracted record onto one or more canonical
        frameworks (`POST /v1/canonical/project`).

        `extracted` is a record — e.g. the `data` of an `extract()`
        result. Pass EXACTLY ONE of `template_slug` (a catalog template
        carrying the StructX concept annotations) or `schema` (an inline
        JSON Schema with `x-structx-concept` / `x-structx-canonical-map`
        annotations). Projection is deterministic post-processing — it
        never re-runs the model and reports unmapped fields rather than
        inventing them."""
        body: dict[str, Any] = {"extracted": extracted, "frameworks": frameworks}
        if template_slug is not None:
            body["template_slug"] = template_slug
        if schema is not None:
            body["schema"] = schema
        return CanonicalProjection.model_validate(
            self._request("POST", "/v1/canonical/project", json=body)
        )

    def get_template_canonical(
        self,
        slug: str,
        *,
        framework: str | None = None,
    ) -> CanonicalProjection:
        """Project a template's stored sample onto canonical framework(s)
        (`GET /v1/templates/{slug}/canonical`). Body-free, cacheable
        counterpart to `project_canonical`. `framework` is a
        comma-separated list (e.g. `"ocsf,ecs"`); omit for all targets."""
        params = {"framework": framework} if framework is not None else None
        return CanonicalProjection.model_validate(
            self._request("GET", f"/v1/templates/{slug}/canonical", params=params)
        )

    # ── Templates extras (v0.4.0) ──────────────────────────

    def get_template_cost_estimate(
        self,
        slug: str,
        *,
        tier: str = "required",
    ) -> dict[str, Any]:
        """Estimated cost + field count for a tier-filtered extraction of
        a template (`GET /v1/templates/{slug}/cost-estimate`). No LLM
        round-trip — values come from the field catalog + token
        heuristics."""
        return self._request(
            "GET", f"/v1/templates/{slug}/cost-estimate", params={"tier": tier}
        )

    def list_template_versions(self, slug: str) -> dict[str, Any]:
        """Version history for a template family
        (`GET /v1/templates/{slug}/versions`). `slug` is the bare
        family_slug (not family_slug@version)."""
        return self._request("GET", f"/v1/templates/{slug}/versions")

    # ── Synth dataset management (v0.4.0) ──────────────────

    def list_synth_datasets(self, *, limit: int = 100) -> dict[str, Any]:
        """List the caller's saved synthetic datasets, newest first
        (`GET /v1/synth/datasets`). Metadata only — no record payloads."""
        return self._request(
            "GET", "/v1/synth/datasets", params={"limit": limit}
        )

    def get_synth_dataset(self, dataset_id: str) -> dict[str, Any]:
        """Fetch one saved synthetic dataset in full — records + manifest
        (`GET /v1/synth/datasets/{dataset_id}`), owner-scoped."""
        return self._request("GET", f"/v1/synth/datasets/{dataset_id}")

    def export_synth_dataset(
        self,
        dataset_id: str,
        *,
        format: str = "jsonl",
    ) -> bytes:
        """Download a saved synthetic dataset as raw bytes
        (`GET /v1/synth/datasets/{dataset_id}/export`). `format` is one
        of `"json"`, `"jsonl"`, or `"csv"`."""
        return self._request_bytes(
            "GET",
            f"/v1/synth/datasets/{dataset_id}/export",
            params={"format": format},
        )

    def delete_synth_dataset(self, dataset_id: str) -> dict[str, Any]:
        """Delete one of the caller's saved synthetic datasets
        (`DELETE /v1/synth/datasets/{dataset_id}`)."""
        return self._request("DELETE", f"/v1/synth/datasets/{dataset_id}")

    # ── Community (v0.3.0) ─────────────────────────────────

    def request_withdrawal(
        self,
        slug: str,
        *,
        mode: str,
        justification: str,
    ) -> dict[str, Any]:
        """Request removal of a community template the caller authored."""
        body = {"mode": mode, "justification": justification}
        return self._request(
            "POST", f"/v1/templates/{slug}/withdraw", json=body,
        )


# ── Async client ─────────────────────────────────────────────


class AsyncStructX(_BaseClient):
    """Asynchronous counterpart of `StructX`. Same surface, same
    typed responses, same exception classes — only the I/O is async.

    Example:
        >>> from structx_sdk import AsyncStructX
        >>> async with AsyncStructX(api_key="sx_...") as client:
        ...     result = await client.extract(content="...", schema={...})
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        bearer_token: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
        retry: RetryPolicy | None = None,
        default_headers: Mapping[str, str] | None = None,
        _http: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key or os.environ.get("STRUCTX_API_KEY", ""),
            bearer_token=bearer_token or os.environ.get("STRUCTX_BEARER_TOKEN"),
            base_url=base_url or os.environ.get("STRUCTX_BASE_URL", _DEFAULT_BASE_URL),
            timeout=timeout,
            retry=retry or RetryPolicy(),
            default_headers=default_headers or {},
        )
        self._http = _http or httpx.AsyncClient(timeout=timeout)
        self._owns_http = _http is None

    @classmethod
    def from_env(cls, **overrides: Any) -> "AsyncStructX":
        return cls(**overrides)

    async def __aenter__(self) -> "AsyncStructX":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        max_attempts: int | None = None,
    ) -> dict[str, Any]:
        last_exc: Exception | None = None
        attempts = max_attempts or self.retry.max_attempts
        for attempt in range(1, attempts + 1):
            try:
                response = await self._http.request(
                    method=method,
                    url=self._url(path),
                    headers=self._headers(headers),
                    json=json,
                    params=params,
                    timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
                )
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last_exc = TransportError(f"{type(e).__name__}: {e}")
                if attempt < attempts:
                    await asyncio.sleep(self.retry.backoff_for(attempt))
                    continue
                raise last_exc from e

            if attempt < attempts and self._is_retryable_response(
                path, response.status_code
            ):
                retry_after = response.headers.get("retry-after")
                try:
                    ra = float(retry_after) if retry_after else None
                except ValueError:
                    ra = None
                await asyncio.sleep(self.retry.backoff_for(attempt, retry_after=ra))
                continue

            return self._parse_response(response)

        raise last_exc or StructXError("retry loop exhausted with no response")

    async def _request_bytes(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> bytes:
        """Async counterpart of `StructX._request_bytes` — raw-file
        responses (dataset export) with the shared retry policy."""
        last_exc: Exception | None = None
        for attempt in range(1, self.retry.max_attempts + 1):
            try:
                response = await self._http.request(
                    method=method,
                    url=self._url(path),
                    headers=self._headers(headers),
                    params=params,
                )
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last_exc = TransportError(f"{type(e).__name__}: {e}")
                if attempt < self.retry.max_attempts:
                    await asyncio.sleep(self.retry.backoff_for(attempt))
                    continue
                raise last_exc from e

            if attempt < self.retry.max_attempts and self._is_retryable_response(
                path, response.status_code
            ):
                retry_after = response.headers.get("retry-after")
                try:
                    ra = float(retry_after) if retry_after else None
                except ValueError:
                    ra = None
                await asyncio.sleep(self.retry.backoff_for(attempt, retry_after=ra))
                continue

            return self._parse_bytes_response(response)

        raise last_exc or StructXError("retry loop exhausted with no response")

    async def extract(
        self,
        content: str,
        *,
        schema: dict[str, Any] | None = None,
        template_slug: str | None = None,
        tier: str = "required",
        options: dict[str, Any] | None = None,
    ) -> Extraction:
        body: dict[str, Any] = {"content": content, "tier": tier}
        if schema is not None:
            body["schema"] = schema
        if template_slug is not None:
            body["template_slug"] = template_slug
        if options is not None:
            body["options"] = options
        return Extraction.model_validate(
            await self._request("POST", "/v1/extract", json=body)
        )

    async def extract_file(
        self,
        file: Any,
        *,
        schema: dict[str, Any] | None = None,
        template_slug: str | None = None,
        model: str | None = None,
        tier: str | None = None,
        fields: list[str] | None = None,
        max_credits: int | None = None,
        options: dict[str, Any] | None = None,
        media_type: str = "application/pdf",
        timeout: float = _DOCUMENT_TIMEOUT,
    ) -> Extraction:
        """Extract structured data from a PDF (`POST /v1/extract/document`).

        `file` is a path, `bytes`, or a binary file object. Pass EXACTLY ONE of
        `schema` or `template_slug`. `model` is `"auto"` (default), `"fast"`,
        `"balanced"` or `"quality"` (paid plans). `max_credits` caps what this
        document may cost; a higher estimate is refused before any work.

        Not retried automatically: the server may still finish (and charge) a
        document after the client gives up, so a silent retry could charge twice.
        Retry yourself on `RateLimitError` / 5xx if you want to. The returned
        `Extraction` has `confidence_source == "model"`: document confidence is
        reported per field by the model, not grounded in text.
        """
        if isinstance(file, (str, os.PathLike)):
            data = await asyncio.to_thread(self._read_document, file)
        else:
            data = self._read_document(file)
        body = self._document_body(
            data, media_type=media_type, schema=schema, template_slug=template_slug,
            model=model, tier=tier, fields=fields, max_credits=max_credits, options=options,
        )
        return Extraction.model_validate(await self._request(
            "POST", "/v1/extract/document", json=body, timeout=timeout, max_attempts=1,
        ))

    async def infer_schema(
        self,
        content: str,
        *,
        content_type: str | None = None,
        hints: dict[str, Any] | None = None,
        k: int = 5,
        return_recommendations: bool = True,
    ) -> InferenceResult:
        body: dict[str, Any] = {
            "content": content,
            "k": k,
            "return_recommendations": return_recommendations,
        }
        if content_type is not None:
            body["content_type"] = content_type
        if hints is not None:
            body["hints"] = hints
        return InferenceResult.model_validate(
            await self._request("POST", "/v1/schemas/infer", json=body)
        )

    async def list_templates(self) -> list[Template]:
        raw = await self._request("GET", "/v1/schemas")
        items = raw if isinstance(raw, list) else raw.get("templates", [])
        return [Template.model_validate(t) for t in items]

    async def list_models(self) -> list[Model]:
        raw = await self._request("GET", "/v1/models")
        items = raw if isinstance(raw, list) else raw.get("models", [])
        return [Model.model_validate(m) for m in items]

    async def usage(self) -> Usage:
        """Current credit usage for the authenticated user.

        Bearer-token method: the backend mounts this at `/billing/usage`
        (no `/v1` prefix) and authenticates it via JWT Bearer only. Set
        `bearer_token=...` on the client (or `STRUCTX_BEARER_TOKEN`)."""
        self._require_bearer("usage")
        return Usage.model_validate(await self._request("GET", "/billing/usage"))

    # ── Catalog browsing (v0.2.0) ──────────────────────────

    async def list_categories(self) -> dict[str, Any]:
        """Return the category → family → vendor hierarchy used by the
        public template catalog. Raw dict; no Pydantic model yet."""
        return await self._request("GET", "/v1/templates/categories")

    async def get_template(self, slug: str) -> dict[str, Any]:
        """Fetch one template's full JSON Schema + metadata by slug."""
        return await self._request("GET", f"/v1/templates/{slug}")

    # ── Personal schemas (v0.2.0) ──────────────────────────

    async def list_my_schemas(self) -> list[dict[str, Any]]:
        """List schemas the calling user has saved to their account.

        Bearer-token method (`/v1/schemas/mine` is JWT-only)."""
        self._require_bearer("list_my_schemas")
        raw = await self._request("GET", "/v1/schemas/mine")
        return raw if isinstance(raw, list) else raw.get("schemas", [])

    async def get_my_schema(self, slug: str) -> dict[str, Any]:
        """Fetch one of the caller's saved schemas by slug.

        Bearer-token method (`/v1/schemas/mine` is JWT-only)."""
        self._require_bearer("get_my_schema")
        return await self._request("GET", f"/v1/schemas/mine/{slug}")

    async def save_my_schema(
        self,
        *,
        name: str,
        json_schema: dict[str, Any],
        slug: str | None = None,
        description: str | None = None,
        category: str | None = None,
        publishable_consent: bool = False,
    ) -> dict[str, Any]:
        """Persist a new JSON Schema to the caller's account. Returns the
        saved row including its server-generated slug if `slug` was None.

        Bearer-token method (`/v1/schemas/mine` is JWT-only)."""
        self._require_bearer("save_my_schema")
        body: dict[str, Any] = {
            "name": name,
            "json_schema": json_schema,
            "publishable_consent": publishable_consent,
        }
        if slug is not None:
            body["slug"] = slug
        if description is not None:
            body["description"] = description
        if category is not None:
            body["category"] = category
        return await self._request("POST", "/v1/schemas/mine", json=body)

    async def update_my_schema(
        self,
        slug: str,
        **fields: Any,
    ) -> dict[str, Any]:
        """Patch one of the caller's schemas. Pass only the fields you
        want to change. `slug` identifies the row; other slug-renames
        require delete+save.

        Bearer-token method (`/v1/schemas/mine` is JWT-only)."""
        self._require_bearer("update_my_schema")
        body = {k: v for k, v in fields.items() if v is not None}
        return await self._request("PATCH", f"/v1/schemas/mine/{slug}", json=body)

    async def delete_my_schema(self, slug: str) -> None:
        """Permanently delete one of the caller's saved schemas. Community
        copies (if `publishable_consent` was True and an admin published)
        stay — use request_withdrawal to remove those too.

        Bearer-token method (`/v1/schemas/mine` is JWT-only)."""
        self._require_bearer("delete_my_schema")
        await self._request("DELETE", f"/v1/schemas/mine/{slug}")

    # ── Schema lifecycle (v0.2.0) ──────────────────────────

    async def recommend_templates(
        self,
        *,
        content: str | None = None,
        draft_schema: dict[str, Any] | None = None,
        k: int = 5,
    ) -> dict[str, Any]:
        """Find existing templates that match content and/or a draft
        schema. At least one of content / draft_schema must be provided.
        Returns an event_id you can use with `augment_schema`."""
        body: dict[str, Any] = {"k": k}
        if content is not None:
            body["content"] = content
        if draft_schema is not None:
            body["draft_schema"] = draft_schema
        return await self._request("POST", "/v1/schemas/recommend", json=body)

    async def augment_schema(
        self,
        event_id: str,
        guidance: str,
    ) -> dict[str, Any]:
        """Refine a schema (from a prior infer or recommend call) using
        natural-language guidance. Example: 'rename event_id to id and
        add a customer_country field'. Requires event_id from a prior
        infer/recommend response."""
        body = {"decision": "augment", "augment_guidance": guidance}
        return await self._request(
            "POST", f"/v1/schemas/recommendations/{event_id}/feedback",
            json=body,
        )

    # ── Community (v0.2.0) ─────────────────────────────────

    async def request_withdrawal(
        self,
        slug: str,
        *,
        mode: str,
        justification: str,
    ) -> dict[str, Any]:
        """Request removal of a community template the caller authored.
        `mode` is "fork_detach" (preserve forks) or "legal_erasure"
        (delete forks too). `justification` is visible to admin reviewers."""
        body = {"mode": mode, "justification": justification}
        return await self._request(
            "POST", f"/v1/templates/{slug}/withdraw", json=body,
        )

    # ── Synthetic data (v0.4.0) ────────────────────────────

    async def synthesize(
        self,
        *,
        template_slug: str | None = None,
        json_schema: dict[str, Any] | None = None,
        count: int = 10,
        directives: str | None = None,
        params: dict[str, Any] | None = None,
        mode: str = "hybrid",
    ) -> dict[str, Any]:
        """Async counterpart of ``StructX.synthesize``. Generate an anonymized,
        schema-valid synthetic dataset from a template slug OR an inline JSON
        Schema, shaped by free-text ``directives``. Returns
        ``{"records": [...], "manifest": {...}}``. Provide exactly one of
        template_slug / json_schema."""
        body: dict[str, Any] = {"count": count, "mode": mode}
        if template_slug is not None:
            body["template_slug"] = template_slug
        if json_schema is not None:
            body["json_schema"] = json_schema
        if directives is not None:
            body["directives"] = directives
        if params is not None:
            body["params"] = params
        return await self._request("POST", "/v1/synth/generate", json=body)

    # ── Batch extraction (v0.4.0) ──────────────────────────

    async def batch_extract(
        self,
        items: list[dict[str, Any]],
        *,
        options: dict[str, Any] | None = None,
        output_format: str | None = None,
        csv_array_mode: str | None = None,
    ) -> BatchExtraction:
        """Async counterpart of ``StructX.batch_extract``. Extract from up
        to 100 items in one call (`POST /v1/extract/batch`). Each item is a
        dict with `content` plus EXACTLY ONE of `schema` / `template_slug`
        and optionally `tier`. Items are metered individually server-side;
        this thin wrapper surfaces the batch totals with no client-side
        credit logic. Like `extract()`, it is a billed write path —
        retried only on transport errors, never on a 5xx."""
        body: dict[str, Any] = {"items": items}
        if options is not None:
            body["options"] = options
        if output_format is not None:
            body["output_format"] = output_format
        if csv_array_mode is not None:
            body["csv_array_mode"] = csv_array_mode
        return BatchExtraction.model_validate(
            await self._request("POST", "/v1/extract/batch", json=body)
        )

    # ── Canonical projection (v0.4.0) ──────────────────────

    async def list_canonical_frameworks(self) -> list[Framework]:
        """List the canonical normalization targets available for
        projection (`GET /v1/canonical/frameworks`)."""
        raw = await self._request("GET", "/v1/canonical/frameworks")
        items = raw if isinstance(raw, list) else raw.get("frameworks", [])
        return [Framework.model_validate(f) for f in items]

    async def project_canonical(
        self,
        extracted: dict[str, Any],
        frameworks: list[str],
        *,
        template_slug: str | None = None,
        schema: dict[str, Any] | None = None,
    ) -> CanonicalProjection:
        """Project an already-extracted record onto one or more canonical
        frameworks (`POST /v1/canonical/project`). Pass EXACTLY ONE of
        `template_slug` or inline `schema`. Deterministic post-processing
        — never re-runs the model; reports unmapped fields."""
        body: dict[str, Any] = {"extracted": extracted, "frameworks": frameworks}
        if template_slug is not None:
            body["template_slug"] = template_slug
        if schema is not None:
            body["schema"] = schema
        return CanonicalProjection.model_validate(
            await self._request("POST", "/v1/canonical/project", json=body)
        )

    async def get_template_canonical(
        self,
        slug: str,
        *,
        framework: str | None = None,
    ) -> CanonicalProjection:
        """Project a template's stored sample onto canonical framework(s)
        (`GET /v1/templates/{slug}/canonical`). Body-free, cacheable
        counterpart to `project_canonical`. `framework` is a
        comma-separated list (e.g. `"ocsf,ecs"`); omit for all targets."""
        params = {"framework": framework} if framework is not None else None
        return CanonicalProjection.model_validate(
            await self._request(
                "GET", f"/v1/templates/{slug}/canonical", params=params
            )
        )

    # ── Templates extras (v0.4.0) ──────────────────────────

    async def get_template_cost_estimate(
        self,
        slug: str,
        *,
        tier: str = "required",
    ) -> dict[str, Any]:
        """Estimated cost + field count for a tier-filtered extraction of
        a template (`GET /v1/templates/{slug}/cost-estimate`). No LLM
        round-trip."""
        return await self._request(
            "GET", f"/v1/templates/{slug}/cost-estimate", params={"tier": tier}
        )

    async def list_template_versions(self, slug: str) -> dict[str, Any]:
        """Version history for a template family
        (`GET /v1/templates/{slug}/versions`). `slug` is the bare
        family_slug (not family_slug@version)."""
        return await self._request("GET", f"/v1/templates/{slug}/versions")

    # ── Synth dataset management (v0.4.0) ──────────────────

    async def list_synth_datasets(self, *, limit: int = 100) -> dict[str, Any]:
        """List the caller's saved synthetic datasets, newest first
        (`GET /v1/synth/datasets`). Metadata only — no record payloads."""
        return await self._request(
            "GET", "/v1/synth/datasets", params={"limit": limit}
        )

    async def get_synth_dataset(self, dataset_id: str) -> dict[str, Any]:
        """Fetch one saved synthetic dataset in full — records + manifest
        (`GET /v1/synth/datasets/{dataset_id}`), owner-scoped."""
        return await self._request("GET", f"/v1/synth/datasets/{dataset_id}")

    async def export_synth_dataset(
        self,
        dataset_id: str,
        *,
        format: str = "jsonl",
    ) -> bytes:
        """Download a saved synthetic dataset as raw bytes
        (`GET /v1/synth/datasets/{dataset_id}/export`). `format` is one
        of `"json"`, `"jsonl"`, or `"csv"`."""
        return await self._request_bytes(
            "GET",
            f"/v1/synth/datasets/{dataset_id}/export",
            params={"format": format},
        )

    async def delete_synth_dataset(self, dataset_id: str) -> dict[str, Any]:
        """Delete one of the caller's saved synthetic datasets
        (`DELETE /v1/synth/datasets/{dataset_id}`)."""
        return await self._request("DELETE", f"/v1/synth/datasets/{dataset_id}")
