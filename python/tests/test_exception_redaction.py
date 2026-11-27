"""Tests for ApiError.response_body credential/PII redaction
(Phase 5.5 / rho).

The risk: customer code logs `repr(exc)` or routes exceptions to
Sentry. If the backend echoes the submitted payload back in an error
response (FastAPI's default 422 schema includes the offending field
values in `detail.body`), credentials or PII in the payload end up
in customer logs forever.

These tests verify three properties:
  1. Sensitive keys in response_body are replaced with a redacted
     sentinel at exception-construction time (not lazily at access)
  2. Non-sensitive keys are preserved unchanged
  3. __repr__ does NOT include response_body at all (second layer)
"""
from __future__ import annotations

import pytest

from structx_sdk import ApiError, AuthenticationError, RateLimitError, ValidationError


class TestRedactionAtConstruction:
    def test_api_key_redacted(self):
        exc = ApiError(
            "boom",
            status_code=500,
            response_body={"x-api-key": "sx_secret_abcdef0123", "ok": True},
        )
        assert exc.response_body["x-api-key"].startswith("<redacted")
        assert "20 chars" in exc.response_body["x-api-key"]
        assert exc.response_body["ok"] is True  # non-sensitive preserved

    def test_authorization_redacted(self):
        exc = ApiError(
            "boom",
            status_code=500,
            response_body={"authorization": "Bearer eyJhbGciOi..."},
        )
        assert exc.response_body["authorization"].startswith("<redacted")
        # Must NOT contain the actual token substring
        assert "eyJ" not in exc.response_body["authorization"]

    def test_content_redacted_preserving_length(self):
        """Customer might submit a 50KB document. The exception
        carries `content: '<redacted 50000 chars>'` — operator can
        see the size, can't see the data."""
        big = "PII_DATA " * 1000
        exc = ApiError(
            "boom",
            status_code=422,
            response_body={"content": big, "schema": {"type": "object"}},
        )
        assert exc.response_body["content"] == f"<redacted {len(big)} chars>"
        # Schema is metadata, not sensitive — kept as-is
        assert exc.response_body["schema"] == {"type": "object"}

    def test_case_insensitive_key_match(self):
        exc = ApiError(
            "boom",
            status_code=500,
            response_body={"X-API-Key": "sx_secret", "TOKEN": "abc"},
        )
        assert "redacted" in exc.response_body["X-API-Key"]
        assert "redacted" in exc.response_body["TOKEN"]

    @pytest.mark.parametrize("key", [
        "password", "token", "secret", "private_key", "api_key",
        "apikey", "x-api-key", "authorization", "content",
    ])
    def test_all_redacted_keys(self, key):
        exc = ApiError("x", status_code=500, response_body={key: "sensitive_value"})
        assert "redacted" in exc.response_body[key]
        assert "sensitive_value" not in exc.response_body[key]

    def test_non_string_value_redacted(self):
        """A field with a dict/list value — redact whole thing."""
        exc = ApiError(
            "x",
            status_code=500,
            response_body={"secret": {"nested": "credentials"}},
        )
        assert exc.response_body["secret"] == "<redacted>"
        # Especially: "nested" / "credentials" must NOT appear anywhere
        assert "nested" not in repr(exc.response_body)

    def test_none_response_body_preserved(self):
        exc = ApiError("x", status_code=500, response_body=None)
        assert exc.response_body is None

    def test_empty_response_body_preserved(self):
        exc = ApiError("x", status_code=500, response_body={})
        assert exc.response_body == {}


class TestReprOmitsBody:
    def test_repr_does_not_include_response_body(self):
        exc = ApiError(
            "Bad request",
            status_code=400,
            response_body={"error": "Bad request", "secret": "xyz"},
        )
        r = repr(exc)
        # status/code/message stay in repr
        assert "400" in r
        # response_body field name + values must NOT appear
        assert "response_body" not in r
        assert "secret" not in r
        assert "xyz" not in r

    def test_repr_still_includes_request_id(self):
        exc = ApiError(
            "x", status_code=500, request_id="req_abc", response_body={"secret": "y"}
        )
        assert "req_abc" in repr(exc)

    def test_response_body_accessible_via_attribute(self):
        """Code that EXPLICITLY wants the (redacted) body for
        programmatic inspection can still get it — only the
        accidental repr() exposure is blocked."""
        exc = ApiError(
            "x",
            status_code=400,
            response_body={"detail": "fix your input", "secret": "y"},
        )
        body = exc.response_body
        assert body["detail"] == "fix your input"
        assert "redacted" in body["secret"]


class TestSubclassesInherit:
    """All ApiError subclasses get the same redaction since they
    share __init__ via super()."""

    def test_authentication_error_redacts(self):
        exc = AuthenticationError(
            "Invalid", status_code=401,
            response_body={"x-api-key": "leaked", "code": "invalid_api_key"},
        )
        assert "redacted" in exc.response_body["x-api-key"]
        assert exc.response_body["code"] == "invalid_api_key"

    def test_validation_error_redacts(self):
        exc = ValidationError(
            "bad", status_code=422,
            response_body={"content": "x" * 500, "field": "content"},
        )
        assert exc.response_body["content"] == "<redacted 500 chars>"
        assert exc.response_body["field"] == "content"  # not a value of "content" key

    def test_rate_limit_error_redacts_and_keeps_credit_attrs(self):
        exc = RateLimitError(
            "rate limited",
            response_body={"password": "leaked", "credits_used": 100},
            retry_after=60.0,
            credits_used=100,
            credits_remaining=0,
        )
        assert "redacted" in exc.response_body["password"]
        assert exc.response_body["credits_used"] == 100
        # Top-level credit attrs (not in response_body) untouched
        assert exc.retry_after == 60.0
        assert exc.credits_used == 100
