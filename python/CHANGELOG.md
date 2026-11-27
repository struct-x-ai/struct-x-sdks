# Changelog

All notable changes to the `structx-sdk` Python SDK are documented here.
This project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.5.0] — 2026-11-26

Document extraction release: `extract_file()` reads PDFs through `POST /v1/extract/document`. Also fixes the default API base URL.

This is the first PyPI release since 0.2.0. The 0.3.0, 0.4.0 and 0.4.1 entries below were never published on their own; upgrading from 0.2.0 picks up all of them.

### Added

- **`extract_file()` / `AsyncStructX.extract_file()`** — extract from a PDF via `POST /v1/extract/document`. Takes a path, bytes or a binary file object plus `schema` or `template_slug`, `model`, `tier`, `fields`, `max_credits`, `options`. 180 s timeout; never retried automatically (a timed-out document may still be charged server-side). `Extraction.confidence_source` is new (`"model"` on documents).
- `_request` accepts a per-call `timeout` and `max_attempts`.
- pytest: `asyncio_default_fixture_loop_scope = "function"` (pytest-asyncio 1.x with `filterwarnings = error`).

### Fixed

- **Default API base URL corrected to `https://api.struct-x.ai`.** The built-in default was `https://api.structx.ai` (missing the hyphen) — a host that doesn't resolve — so any client constructed without an explicit `base_url` (or the `STRUCTX_BASE_URL` env var) never reached the live API. It now points at `https://api.struct-x.ai`, matching the backend and MCP server. Callers who set `base_url` explicitly are unaffected.
- Package metadata (homepage, docs URL, author email) now uses `struct-x.ai` too.

## [0.4.1] — 2026-08-01

Bug-fix release. Three fixes that align the SDK with the live backend contract.

### Fixed

- **Extract response id now decodes.** The backend emits the extraction id on the wire as `id`; the SDK typed it as `extraction_id`, so `Extraction.extraction_id` was always `None`. `Extraction.id` is now the real field (decodes from `id`). `Extraction.extraction_id` is kept as a read-only backward-compat property that returns `.id`.
- **`usage()` now hits the correct path.** The billing router mounts at `/billing` (no `/v1`), so `usage()` was calling `/v1/billing/usage` and 404ing. It now calls `/billing/usage`.

### Added

- **Optional bearer-token (JWT) auth.** `StructX(api_key=..., bearer_token=...)` / `AsyncStructX(...)` — or the `STRUCTX_BEARER_TOKEN` env var — adds a second credential path. The JWT-only methods (`usage()` and the `/v1/schemas/mine` personal-schema CRUD: `list_my_schemas`, `get_my_schema`, `save_my_schema`, `update_my_schema`, `delete_my_schema`) now send `Authorization: Bearer <jwt>` and work with a dashboard token instead of failing with 401 under an API key. Calling one of these methods without a bearer token raises a clear `StructXError` up-front rather than 401ing. When both credentials are set, both headers are sent and the backend picks per endpoint; the API-key methods keep using `X-API-Key`.

## [0.4.0] — 2026-06-30

### Added

Core agent surface — the same methods on both `StructX` and `AsyncStructX` (identical signatures):

**Batch extraction**
- `batch_extract(items, *, options=None, output_format=None, csv_array_mode=None)` — extract up to 100 items in one call (`POST /v1/extract/batch`). API-key auth. Items are metered individually server-side; the thin wrapper surfaces `total_credits_used` / per-item `credits_remaining` with no client-side credit logic. Treated as a write path — retried only on transport errors, never on 5xx.

**Canonical projection**
- `list_canonical_frameworks()` — list available normalization targets (`GET /v1/canonical/frameworks`).
- `project_canonical(extracted, frameworks, *, template_slug=None, schema=None)` — project an extracted record onto canonical framework(s) (`POST /v1/canonical/project`). Provide exactly one of `template_slug` / `schema`.
- `get_template_canonical(slug, *, framework=None)` — project a template's stored sample (`GET /v1/templates/{slug}/canonical`).

**Synth dataset management** (generation already shipped via `synthesize()`)
- `list_synth_datasets(*, limit=100)`, `get_synth_dataset(dataset_id)`, `export_synth_dataset(dataset_id, *, format="jsonl")` (returns raw `bytes`), `delete_synth_dataset(dataset_id)`.

**Template extras**
- `get_template_cost_estimate(slug, *, tier="required")` — estimated field count / cost without an LLM round-trip (`GET /v1/templates/{slug}/cost-estimate`).
- `list_template_versions(slug)` — version history for a template family (`GET /v1/templates/{slug}/versions`).

### Added (models)
- `BatchExtraction`, `ExtractionErrorItem`, `Framework`, `ProjectionOut`, `CanonicalProjection` — typed responses for the batch + canonical surfaces. All accept extra fields silently for forward compatibility.

### Notes
- Auth remains API-key-only across the SDK. Request-id capture + the typed exception hierarchy are unchanged.

## [0.3.0] — 2026-06-03

### Added (AsyncStructX)

Ten new methods on the async client covering the broader Struct-X platform — designed to back the `structx-mcp` v0.2.0 tool surface but useful directly to any async-Python consumer:

**Catalog browsing**
- `list_categories()` — return the template-catalog category hierarchy.
- `get_template(slug)` — fetch one template's full schema + metadata by slug.

**Personal schemas (user_schemas)**
- `list_my_schemas()` — list schemas the caller has saved to their account.
- `get_my_schema(slug)` — fetch one of the caller's saved schemas.
- `save_my_schema(name=, json_schema=, ...)` — persist a new schema. Optional `publishable_consent=True` opts into community-catalog promotion (admin-reviewed).
- `update_my_schema(slug, **fields)` — patch a saved schema.
- `delete_my_schema(slug)` — remove a saved schema.

**Schema lifecycle**
- `recommend_templates(content=, draft_schema=, k=5)` — find existing templates matching content / draft schema. Returns an `event_id` usable with `augment_schema`.
- `augment_schema(event_id, guidance)` — refine a previously-inferred or recommended schema using natural-language guidance.

**Community**
- `request_withdrawal(slug, mode=, justification=)` — submit a removal request for a community template the caller authored. `mode` is `fork_detach` or `legal_erasure`.

### Notes
- Both `AsyncStructX` and `StructX` gained the same 10 methods — sync + async have identical surfaces.
- All new methods return raw `dict[str, Any]` for now — no Pydantic response models yet. The MCP server passes them straight through to agents. Typed models will follow once the backend response shapes settle.

## [0.2.0] — 2026-05-25

### BREAKING
- Renamed the package from `structx` to `structx-sdk` on PyPI. Pre-existing unrelated package at `structx` on PyPI made coexistence impossible.
- Renamed the Python module from `structx` to `structx_sdk`. Update imports: `from structx_sdk import StructX, AsyncStructX, ...`
- Renamed the public classes to use capital `X`: `Structx` → `StructX`, `AsyncStructx` → `AsyncStructX`, `StructxError` → `StructXError`. Brand-consistent with marketing's "Struct-X" spelling.
- Env variables (`STRUCTX_API_KEY`, `STRUCTX_BASE_URL`) unchanged.
- User-Agent header prefix updated to `structx-sdk/<version>`.

### Migration
Replace `from structx import Structx` with `from structx_sdk import StructX`. The class API is otherwise unchanged.

## [0.1.1] - 2026-05-25

### Security
- `ApiError.response_body` now has sensitive-key values redacted at exception-construction time (Phase 5.5 / rho). Closes a credential/PII leak that surfaced when customer code logged `repr(exc)` or routed exceptions to error trackers like Sentry. Redacted keys (case-insensitive): `x-api-key`, `authorization`, `apikey`, `api_key`, `password`, `token`, `secret`, `private_key`, `content`. Values become `<redacted N chars>` (length preserved for debugging).
- `ApiError.__repr__` now omits `response_body` entirely — second layer of defense against accidental `f"{exc!r}"` logging.
- Backward compatible: `.response_body` attribute is still accessible for programmatic inspection; only the values for sensitive keys are sentinelized.

## [0.1.0] — 2026-05-24

Initial release.

### Added
- `Structx` sync client + `AsyncStructx` async client (parallel surfaces).
- `extract(content, *, schema=None, template_slug=None, tier=..., options=...)`.
- `infer_schema(content, *, content_type=None, hints=None, ...)`.
- `list_templates()`, `list_models()`, `usage()`.
- Typed exception hierarchy: `StructxError → ApiError → {Authentication, PermissionDenied, NotFound, Validation, RateLimit, Server}Error`, plus `TransportError` for network failures.
- Typed Pydantic response models: `Extraction`, `FieldConfidence`, `TokenCounts`, `InferenceResult`, `InferredSchema`, `InferredField`, `Recommendation`, `Template`, `Model`, `Usage`. All accept extra fields silently for forward compatibility.
- `RetryPolicy` with read-vs-write retry differentiation — reads auto-retry on 5xx, writes only on transport errors (avoid double-billing).
- `STRUCTX_API_KEY` / `STRUCTX_BASE_URL` environment fallbacks, plus `Structx.from_env()`.
- Context-manager support (`with Structx(...)` closes the underlying httpx client).
- `py.typed` marker for full type-checker support.

### Compatible with
- Python 3.10, 3.11, 3.12, 3.13.
- struct-x API v1.x.
- httpx 0.27 / 0.28; pydantic 2.5+.
