"""Pydantic response models — mirror backend/models/schemas.py response
shapes but with SDK-idiomatic names. The backend's `ExtractionResponse`
becomes `Extraction` here; the SDK doesn't need the "Response" suffix
because the caller isn't dealing with HTTP-layer concerns.

Stays in lockstep with the backend by accepting EXTRA fields silently
(`model_config.extra = 'allow'`) — when the backend adds a new field,
old SDK versions don't break, they just don't surface it as a typed
attribute. Callers can still reach it via `obj.model_dump()` or
`obj.__pydantic_extra__`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


# ── Extraction ───────────────────────────────────────────────


class FieldConfidence(_Base):
    """Per-field confidence score returned by `/v1/extract`. The backend
    populates `source_snippet` only when `include_citations=true` was
    requested; otherwise it's None."""

    field: str
    confidence: float
    source_snippet: str | None = None


class TokenCounts(_Base):
    input: int
    output: int


class Extraction(_Base):
    """The result of a single `/v1/extract` call."""

    data: dict[str, Any] | list[Any]
    model_used: str
    latency_ms: int
    tokens: TokenCounts
    credits_used: int
    credits_remaining: int | None = None
    confidence: float | None = None
    field_confidences: list[FieldConfidence] = Field(default_factory=list)
    cached: bool = False
    # The server-assigned extraction id. The backend emits this on the
    # wire as `id` (see backend/models/schemas.py:ExtractionResponse); an
    # earlier SDK typed it as `extraction_id`, which never matched the
    # wire key, so the id always decoded as None. The field is now `id`
    # to match the wire; `extraction_id` is kept as a read-only alias
    # (below) for backward compatibility.
    id: str | None = None
    warnings: list[str] = Field(default_factory=list)
    # "model" for document extractions (per-field confidence reported by the
    # model); absent on text extractions, whose confidence is source-grounded.
    confidence_source: str | None = None

    @property
    def extraction_id(self) -> str | None:
        """Backward-compat alias for :attr:`id`.

        Reads the same server-assigned extraction id. Prefer ``.id`` —
        this alias exists only so code written against the pre-0.4.1 SDK
        keeps working (before 0.4.1 this attribute always read None
        because the SDK looked for a wire field the backend never sent)."""
        return self.id


# ── Schema inference (recommender) ───────────────────────────


class InferredField(_Base):
    name: str
    type: str
    description: str
    required: bool
    rationale: str
    confidence: float


class InferredSchema(_Base):
    """The inferred JSON Schema plus per-field rationale. Returned by
    `/v1/schemas/infer`. `json_schema` is a valid JSON Schema dict the
    caller can immediately pass back to `extract()`."""

    json_schema: dict[str, Any]
    fields: list[InferredField] = Field(default_factory=list)
    overall_confidence: float
    needs_review: bool = False


class Recommendation(_Base):
    """A template or user-schema candidate returned by the recommender."""

    source_type: Literal["template", "user_schema", "inferred_draft"]
    source_id: str
    slug: str
    name: str
    score: float
    json_schema: dict[str, Any]
    score_breakdown: dict[str, float] = Field(default_factory=dict)


class InferenceResult(_Base):
    """The full payload from `/v1/schemas/infer` — the inferred schema,
    optional template recommendations, and the `event_id` you'd echo
    back to the feedback endpoint."""

    event_id: str
    inferred: InferredSchema
    recommendations: list[Recommendation] = Field(default_factory=list)
    credits_used: int
    partial: bool = False


# ── Templates ────────────────────────────────────────────────


class Template(_Base):
    """A row from the public template gallery (`/v1/schemas`).

    Note: the JSON Schema field is named `schema_def` on the Python
    object (with alias `"schema"` for the wire format). Pydantic v2
    reserves `.schema` as a model-introspection method, so we can't
    use the bare name — same convention the backend uses in
    `backend/models/schemas.py:ExtractionRequest`.
    """

    slug: str
    name: str
    description: str | None = None
    category: str | None = None
    schema_def: dict[str, Any] = Field(default_factory=dict, alias="schema")
    usage_count: int | None = None
    acceptance_rate: float | None = None
    avg_confidence: float | None = None


# ── Models / routing info ────────────────────────────────────


class Model(_Base):
    """A model entry from `/v1/models`. The SDK exposes these for
    introspection; callers normally don't pick a specific model and let
    the backend's router decide."""

    name: str
    provider: str
    capability: str | None = None
    notes: str | None = None


# ── Usage ────────────────────────────────────────────────────


class Usage(_Base):
    """Summary returned by `/billing/usage` (the billing router mounts at
    `/billing`, no `/v1` prefix). Field set intentionally minimal; extra
    backend fields surface via `extra=allow`."""

    credits_used_today: int
    credits_limit_daily: int
    tier: str | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None


# ── Batch extraction (v0.4.0) ────────────────────────────────


class ExtractionErrorItem(_Base):
    """One failed item inside a `BatchExtraction.results` list. The batch
    endpoint never aborts on a single item's failure — it surfaces a
    structured error in-line instead, so `results` is a mix of
    `Extraction` and `ExtractionErrorItem` entries positionally aligned
    with the request items."""

    error: str
    code: str
    retry_after: int | None = None
    details: dict[str, Any] | None = None


class BatchExtraction(_Base):
    """The result of a `/v1/extract/batch` call. `results` preserves input
    order; each entry is either an `Extraction` (success) or an
    `ExtractionErrorItem` (failure). `total_credits_used` is the sum the
    server metered across all items — the SDK does no client-side
    accounting."""

    results: list[Extraction | ExtractionErrorItem] = Field(default_factory=list)
    total_latency_ms: int
    total_credits_used: int = 0


# ── Canonical projection (v0.4.0) ────────────────────────────


class Framework(_Base):
    """One canonical normalization target from
    `/v1/canonical/frameworks` — e.g. otel, ocsf, ecs."""

    framework: str
    version: str
    kind: str | None = None


class ProjectionOut(_Base):
    """A single framework's projection of a record: the reshaped
    `record`, a `coverage` report of mapped/unmapped fields, and whether
    the projection passed the framework's validation."""

    record: dict[str, Any] = Field(default_factory=dict)
    coverage: dict[str, Any] = Field(default_factory=dict)
    valid: bool = False


class CanonicalProjection(_Base):
    """Response from `/v1/canonical/project` and
    `/v1/templates/{slug}/canonical`: `projections` maps each requested
    framework name to its `ProjectionOut`."""

    projections: dict[str, ProjectionOut] = Field(default_factory=dict)
