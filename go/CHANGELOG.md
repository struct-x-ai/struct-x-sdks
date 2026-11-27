# Changelog

All notable changes to the struct-x Go SDK are documented here.
This project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- **The `"cache"` option now reaches the API.** The docs told callers to set `Options: map[string]any{"use_cache": false}`, but the API's key is `cache` and it ignores unknown option keys, so the cache was never skipped. `Extract` and `BatchExtract` now send `use_cache` as `cache`, and the doc comments name `cache`. Both with different values returns an error wrapping `ErrStructX` before the request. The caller's map is not changed.
- **`Usage` and the personal-schema methods work with an API key.** `Usage`, `ListMySchemas`, `GetMySchema`, `SaveMySchema`, `UpdateMySchema` and `DeleteMySchema` returned an error ("requires a bearer token") unless a JWT was set. The API now accepts `X-API-Key` on `/billing/usage` and `/v1/schemas/mine`, so the guard is gone. With an API key, writes are limited to personal schemas (an org write gets `ErrPermissionDenied`, code `api_key_personal_schemas_only`). `WithBearerToken` still works and the token is sent when set; the API uses the API key when both are sent.
- **`AugmentSchema` now refines the schema.** It posted `{"decision":"augment",...}` to `/v1/schemas/recommendations/{id}/feedback`, which records feedback, returns 204 and never refines anything. It now calls `POST /v1/schemas/recommendations/{event_id}/augment` and returns `*InferenceResult`, like `InferSchema`. The signature is now `AugmentSchema(ctx, eventID, guidance, content string, params AugmentParams)`: `content` is required and must be the content sent to the `InferSchema` call that returned `eventID` (the API rejects different content with 400); `AugmentParams.ContentType` is optional. Augment is billed, so its path is a write path and a 5xx is not retried.

## [0.2.0] — 2026-11-26

Document extraction release, aligned with the Python / Node SDKs' 0.5.0: `ExtractFile` reads PDFs through `POST /v1/extract/document`. Also fixes the default API base URL.

First tagged release (`go/v0.2.0`); it includes everything in the entries below.

### Added

- **`ExtractFile(ctx, document, ExtractFileRequest)`** — extract from a PDF via `POST /v1/extract/document`. 180 s timeout; never retried automatically. `Extraction.ConfidenceSource` is new. `/v1/extract/document` joins the write paths.

### Fixed

- **Default API base URL corrected to `https://api.struct-x.ai`.** The built-in default was `https://api.structx.ai` (missing the hyphen) — a host that doesn't resolve — so a `Client` created without `WithBaseURL` (or the `STRUCTX_BASE_URL` env var) never reached the live API. It now points at `https://api.struct-x.ai`, matching the backend and MCP server. Callers who pass `WithBaseURL` explicitly are unaffected.

## [0.1.1] — 2026-08-01

Bug-fix release, aligned with the Python / Node SDKs' 0.4.1 line. Three fixes that align the SDK with the live backend contract.

### Fixed

- **Extract response id now decodes.** The backend emits the extraction id on the wire as `id`; `Extraction.ExtractionID` was tagged `json:"extraction_id"`, so it always stayed empty. Its JSON tag is now `json:"id"` — the Go field name `ExtractionID` is unchanged (source-compatible), it simply decodes the correct wire key.
- **`Usage` now hits the correct path.** The billing router mounts at `/billing` (no `/v1`), so `Usage` was calling `/v1/billing/usage` and 404ing. It now calls `/billing/usage`.

### Added

- **Optional bearer-token (JWT) auth.** `WithBearerToken(token)` — or the `STRUCTX_BEARER_TOKEN` env var — adds a second credential path. The JWT-only methods (`Usage` and the `/v1/schemas/mine` personal-schema CRUD: `ListMySchemas`, `GetMySchema`, `SaveMySchema`, `UpdateMySchema`, `DeleteMySchema`) now send `Authorization: Bearer <jwt>` and work with a dashboard token instead of failing with 401 under an API key. Calling one of these methods without a bearer token returns a clear error (matching `errors.Is(err, ErrStructX)`) before any request, rather than 401ing. When both credentials are set, both headers are sent and the backend picks per endpoint; the API-key methods keep using `X-API-Key`.

## [0.1.0] — 2026-07-30

Initial release. Mirrors the Python (`structx-sdk`) and Node (`@struct-x/sdk`)
SDKs at their 0.4.0 API surface, in idiomatic Go: standard-library only
(`net/http` + `encoding/json`), `context.Context`-first, and `errors.Is` /
`errors.As` throughout.

### Added

- `Client` + `NewClient(apiKey string, opts ...Option)` with functional
  options: `WithBaseURL`, `WithHTTPClient`, `WithTimeout`, `WithMaxRetries`,
  `WithRetryPolicy`, `WithDefaultHeaders`. Reads `STRUCTX_API_KEY` /
  `STRUCTX_BASE_URL` from the environment.
- **Core**: `Extract`, `BatchExtract`, `InferSchema`, `ListTemplates`
  (`GET /v1/schemas`), `ListModels`, `Usage`.
- **Catalog browsing**: `ListCategories`, `GetTemplate`.
- **Personal schemas (CRUD)**: `ListMySchemas`, `GetMySchema`, `SaveMySchema`,
  `UpdateMySchema`, `DeleteMySchema`.
- **Schema lifecycle**: `RecommendTemplates`, `AugmentSchema`.
- **Community**: `RequestWithdrawal`.
- **Synthetic data**: `Synthesize`, `ListSynthDatasets`, `GetSynthDataset`,
  `ExportSynthDataset` (returns raw `[]byte`), `DeleteSynthDataset`.
- **Canonical projection**: `ListCanonicalFrameworks`, `ProjectCanonical`,
  `GetTemplateCanonical`.
- **Template extras**: `GetTemplateCostEstimate`, `ListTemplateVersions`.
- Typed response models: `Extraction`, `FieldConfidence`, `TokenCounts`,
  `InferenceResult`, `InferredSchema`, `InferredField`, `Recommendation`,
  `Template`, `Model`, `Usage`, `BatchExtraction`, `BatchResultItem`,
  `ExtractionErrorItem`, `Framework`, `ProjectionOut`, `CanonicalProjection`.
  Arbitrary JSON (extracted data, user schemas, projection records) is
  `json.RawMessage` / `map[string]any`.
- Error taxonomy mirroring the Python `_exceptions.py`: sentinels `ErrStructX`,
  `ErrTransport`, `ErrAPI`, `ErrAuthentication`, `ErrPermissionDenied`,
  `ErrNotFound`, `ErrValidation`, `ErrRateLimit`, `ErrServer` for `errors.Is`,
  plus `*APIError` (status, code, request id, redacted body, rate-limit info)
  and `*TransportError` for `errors.As`.
- `RetryPolicy` with read-vs-write differentiation — reads auto-retry transient
  5xx + transport errors with exponential backoff and jitter; writes
  (`/v1/extract`, `/v1/extract/batch`, `/v1/schemas/infer`, `/v1/synth/generate`)
  retry only on transport errors. `Retry-After` honored; `429` never
  auto-retried (surfaced as `ErrRateLimit`).
- Credential redaction on `APIError.ResponseBody` (`api_key`, `authorization`,
  `password`, `secret`, `content`, …) at construction time; `Error()` omits the
  body entirely.

### Notes

- Module path: `github.com/struct-x-ai/struct-x-sdks/go`; package name
  `structx`. API-key auth only (`X-API-Key`), matching the sibling SDKs.
- Versioning starts a fresh Go `v0.x` line at `0.1.0` (as both sibling SDKs
  did for their first release), shipping the current 0.4.0-equivalent surface.

### Compatible with

- Go 1.21+.
- struct-x API v1.x.
