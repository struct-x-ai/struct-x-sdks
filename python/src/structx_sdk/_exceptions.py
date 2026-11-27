"""Typed exception hierarchy for the struct-x SDK.

Every backend error response carries `{"error": str, "code": str}` (see
backend/routers/*.py — convention). The SDK maps `code` (or, if absent,
HTTP status) to a typed exception class so callers can `except
RateLimitError` instead of inspecting status codes.

Hierarchy is intentionally narrow — three categories that map to three
call-site decisions:

    StructXError                 base; matches anything from the SDK
      ApiError                   server returned an error response
        AuthenticationError      401 — fix your API key
        PermissionDeniedError    403 — your key works but lacks scope
        RateLimitError           429 — back off; carries credits info
        ValidationError          400/422 — fix your input
        NotFoundError            404 — wrong slug or id
        ServerError              5xx — retry or contact support
      TransportError             network/timeout failure, never reached the server
"""
from __future__ import annotations

from typing import Any


class StructXError(Exception):
    """Base exception for all SDK errors."""


class TransportError(StructXError):
    """Network-level failure — the request never reached the API or the
    response never made it back. Always safe to retry idempotent calls;
    write calls (extract/infer) are *probably* safe but the backend may
    have partially processed before the connection dropped."""


# Keys whose VALUES are stripped from `response_body` before the body
# is attached to the exception. Common sense + the platform's known
# field names. Customer code that logs `repr(exc)` or pipes
# exceptions to Sentry won't leak: API keys (`x-api-key`, `authorization`,
# `apikey`), session credentials (`password`, `token`, `secret`), or
# raw payload content (`content` — extraction inputs are often the
# most sensitive thing in the request). Match is case-insensitive.
_REDACTED_KEYS = frozenset({
    "x-api-key", "authorization", "apikey", "api_key",
    "password", "token", "secret", "private_key",
    "content",
})

# Sentinel returned in place of the redacted value. The original
# payload size is preserved (in chars) so debugging can see "this
# field had data" without seeing what.
def _redact(value: object) -> str:
    if isinstance(value, str):
        return f"<redacted {len(value)} chars>"
    if isinstance(value, (bytes, bytearray)):
        return f"<redacted {len(value)} bytes>"
    return "<redacted>"


def _redact_body(body: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a shallow copy of `body` with sensitive keys' values
    replaced by a length-bounded redacted sentinel. Defends against
    customer `repr(exc)` / Sentry captures of payloads that may
    contain credentials or PII. Keys checked case-insensitively against
    `_REDACTED_KEYS`."""
    if not body:
        return body
    redacted: dict[str, Any] = {}
    for k, v in body.items():
        if isinstance(k, str) and k.lower() in _REDACTED_KEYS:
            redacted[k] = _redact(v)
        else:
            redacted[k] = v
    return redacted


class ApiError(StructXError):
    """Server responded with an error. Subclasses below are status/code-
    typed; if none matches, raw `ApiError` is raised so callers can still
    pattern-match `except ApiError`.

    Privacy posture (Phase 5.5 / rho): `response_body` has sensitive
    keys redacted at exception-construction time — see `_REDACTED_KEYS`.
    Customer code that logs `repr(exc)` or routes exceptions to Sentry
    will see `<redacted N chars>` instead of credentials / payload
    content. `__repr__` ALSO omits the body entirely as a second layer
    — pass `.response_body` explicitly if you need the (redacted)
    dict for programmatic inspection.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        code: str | None = None,
        response_body: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        # Redact at construction time so any post-creation access
        # (including third-party traceback formatters) sees the safe
        # version, not the raw payload.
        self.response_body = _redact_body(response_body)
        self.request_id = request_id

    def __repr__(self) -> str:
        # Intentionally does NOT include response_body — second layer
        # of defense against `f"{exc!r}"` patterns in customer logs.
        # The body is still accessible via `.response_body` for
        # callers that need programmatic inspection.
        bits = [f"status={self.status_code}"]
        if self.code:
            bits.append(f"code={self.code!r}")
        if self.request_id:
            bits.append(f"request_id={self.request_id!r}")
        return f"{self.__class__.__name__}({self.message!r}, {', '.join(bits)})"


class AuthenticationError(ApiError):
    """401 — the API key is missing, malformed, or revoked."""


class PermissionDeniedError(ApiError):
    """403 — the API key is valid but doesn't have access to this
    resource (e.g. admin-only endpoint)."""


class NotFoundError(ApiError):
    """404 — template slug, key id, or other resource doesn't exist."""


class ValidationError(ApiError):
    """400 / 422 — your request payload is malformed. Common cases:
    schema isn't a JSON object, content is too large, template_slug
    doesn't resolve."""


class RateLimitError(ApiError):
    """429 — you've hit the daily credit cap OR the per-window request
    cap. The `retry_after` attribute (seconds) is populated from the
    `Retry-After` response header when present. Credits are populated
    from the response body when the backend supplied them (it does for
    credit-exhaustion 429s; not for IP-based throttling)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 429,
        code: str | None = None,
        response_body: dict[str, Any] | None = None,
        request_id: str | None = None,
        retry_after: float | None = None,
        credits_used: int | None = None,
        credits_remaining: int | None = None,
    ) -> None:
        super().__init__(
            message,
            status_code=status_code,
            code=code,
            response_body=response_body,
            request_id=request_id,
        )
        self.retry_after = retry_after
        self.credits_used = credits_used
        self.credits_remaining = credits_remaining


class ServerError(ApiError):
    """5xx — the backend returned a server-side failure. Retrying may
    help for 502/503/504; 500 usually means a bug — report it with the
    `request_id` attribute attached."""
