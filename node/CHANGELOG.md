# Changelog

All notable changes to `@struct-x/sdk` will be documented here.

## [Unreleased]

### Added

- **`browseTemplates({ q?, category?, vendor?, tag?, firstParty?, limit?, offset? })`**: one page of the template catalog from `GET /v1/templates`, 25 per page by default (`limit` 1-500). Resolves to a new `TemplatePage` (`items`, `total`, `limit`, `offset`, and `next_offset`, which the SDK computes and sets to `null` on the last page). Items are the new `TemplateSummary` type and carry no JSON Schema; `getTemplate(slug)` returns one. New exported types `BrowseTemplatesOptions`, `TemplatePage`, `TemplateSummary`. This is now the preferred way to list templates. `listTemplates()` is unchanged and still returns the whole gallery from `GET /v1/schemas` (several MB, every schema included).

### Fixed

- **`options.cache` now reaches the API.** The docs told callers to pass `options: { use_cache: false }`, but the API's key is `cache` and it ignores unknown option keys, so the cache was never skipped. `extract()` and `batchExtract()` now send `use_cache` as `cache`, and the doc comments name `cache`. Passing both with different values throws `StructXError` before the request. The caller's object is not changed.
- **`usage()` and the personal-schema methods work with an API key.** `usage()`, `listMySchemas()`, `getMySchema()`, `saveMySchema()`, `updateMySchema()` and `deleteMySchema()` rejected with `StructXError` ("requires a bearer token") unless a JWT was set. The API now accepts `X-API-Key` on `/billing/usage` and `/v1/schemas/mine`, so the guard is gone. With an API key, writes are limited to personal schemas (an org write gets `PermissionDeniedError`, code `api_key_personal_schemas_only`). `bearerToken` is still accepted and sent when set; the API uses the API key when both are sent.
- **`augmentSchema()` now refines the schema. Breaking: new required `content` argument.** The old call never worked: it posted `{ decision: 'augment', ... }` to `/v1/schemas/recommendations/{eventId}/feedback`, which records feedback, returns 204 and never refines anything. It now calls `POST /v1/schemas/recommendations/{eventId}/augment` and resolves to an `InferenceResult`, like `inferSchema()`. The signature is now `augmentSchema(eventId, guidance, content, { contentType? })`: `content` is required and must be the content sent to the `inferSchema()` call that returned `eventId` (the API rejects different content with 400). New exported type `AugmentSchemaOptions`. Augment is billed, so `isRetryableResponse()` treats its path as a write path and a 5xx is not retried.
- **`Usage` matches what `/billing/usage` returns.** The type declared a `credits_limit_daily` field that the API never sends, so it read `undefined`. `Usage` now types `credits_limit_today`, `credits_remaining_today`, the monthly totals and `period`, and `usage()` fills the deprecated `credits_limit_daily` from `credits_limit_today`.
- **`saveMySchema()` no longer sends `publishable_consent` unless you set it.** It sent `publishable_consent: false` on every call. Opting a schema into community-catalog promotion is done in the dashboard: with an API key, `publishableConsent: true` on `saveMySchema()`, or a value that changes it on `updateMySchema()`, rejects with `PermissionDeniedError` (403, code `publishable_consent_portal_only`). The doc comments also note that the API ignores `category` for personal schemas.

## [0.5.0] — 2026-11-26

Document extraction release: `extractFile()` reads PDFs through `POST /v1/extract/document`. Also fixes the default API base URL.

First release published to npm; it includes everything in the entries below.

### Added

- **`extractFile(document, options)`** — extract from a PDF (`Uint8Array` / `Buffer` / `ArrayBuffer`) via `POST /v1/extract/document`. 180 s timeout; never retried automatically. `Extraction.confidence_source` is new. `/v1/extract/document` joins `WRITE_PATHS`.

### Fixed

- **Default API base URL corrected to `https://api.struct-x.ai`.** The built-in default was `https://api.structx.ai` (missing the hyphen) — a host that doesn't resolve — so any client constructed without a `baseUrl` (or the `STRUCTX_BASE_URL` env var) never reached the live API. It now points at `https://api.struct-x.ai`, matching the backend and MCP server. Callers who set `baseUrl` explicitly are unaffected.
- Package metadata (`homepage`, `author` email) now uses `struct-x.ai` too.

### Compatibility

- No breaking changes. `User-Agent` is now `structx-sdk-node/0.5.0 …`.

## [0.4.1] — 2026-08-01

Bug-fix release. Three fixes that align the SDK with the live backend contract.

### Fixed

- **Extract response id now typed.** The backend emits the extraction id on the wire as `id`; the SDK typed only `extraction_id`, which the server never sends. `Extraction.id` is now the typed field. `Extraction.extraction_id` is kept but marked `@deprecated` (always `undefined` at runtime) for source compatibility — read `id`.
- **`usage()` now hits the correct path.** The billing router mounts at `/billing` (no `/v1`), so `usage()` was calling `/v1/billing/usage` and 404ing. It now calls `/billing/usage`.

### Added

- **Optional bearer-token (JWT) auth.** `new StructX({ apiKey, bearerToken })` — or the `STRUCTX_BEARER_TOKEN` env var — adds a second credential path. The JWT-only methods (`usage()` and the `/v1/schemas/mine` personal-schema CRUD: `listMySchemas`, `getMySchema`, `saveMySchema`, `updateMySchema`, `deleteMySchema`) now send `Authorization: Bearer <jwt>` and work with a dashboard token instead of failing with 401 under an API key. Calling one of these methods without a bearer token rejects with a clear `StructXError` up-front rather than 401ing. When both credentials are set, both headers are sent and the backend picks per endpoint; the API-key methods keep using `X-API-Key`.

### Compatibility

- No breaking changes. `User-Agent` is now `structx-sdk-node/0.4.1 …`.

## [0.4.0] — 2026-06-30

Closes the gap with the Python SDK (synth generation) and adds the agreed Core agent surface: canonical projection, synth dataset management, batch extract, and the template cost/version extras. Version jumps 0.2.0 → 0.4.0 to align the Node line number with Python.

### Features

- `synthesize({ templateSlug? | jsonSchema?, count?, directives?, params?, mode? })` — generate an anonymized, schema-valid synthetic dataset (`POST /v1/synth/generate`); WRITE path, billed against the API key's quota
- `listDatasets()` / `getDataset(id)` / `exportDataset(id, format?)` / `deleteDataset(id)` — manage saved synth datasets; `exportDataset` returns the raw `jsonl` / `json` / `csv` body string
- `listFrameworks()` — list canonical normalization targets + pinned versions
- `project(extracted, frameworks, { templateSlug? | schema? })` — project an extracted record onto canonical framework(s) (`POST /v1/canonical/project`)
- `projectTemplate(slug, framework?)` — body-free projection of a template's stored sample (`GET /v1/templates/{slug}/canonical`)
- `batchExtract(items, { options? })` — extract up to 100 items (`POST /v1/extract/batch`, API-key only); thin wrapper, per-item metering is server-side, surfaces `total_credits_used`
- `getTemplateCostEstimate(slug, tier?)` — estimated field count + credits for a tier-filtered extraction
- `listTemplateVersions(slug)` — version history for a template family

### Internal

- `_request` gained a `rawText` flag so `exportDataset` can return file bodies untouched
- `/v1/synth/generate` added to `WRITE_PATHS` — never retried on 5xx
- New exported types: `SynthesizeOptions`, `ProjectOptions`, `BatchExtractOptions`, `BatchExtractItem`

### Compatibility

- No breaking changes — every v0.2.0 method keeps its signature
- `User-Agent` is now `structx-sdk-node/0.4.0 …`

## [0.2.0] — 2026-06-05

Catches Node up to the Python SDK 0.3.0 surface: catalog browsing, personal-schema CRUD, the recommend → augment learning loop, and community withdrawal requests.

### Features

- `listCategories()` — return the public catalog's category → family → vendor hierarchy
- `getTemplate(slug)` — fetch one template's full JSON Schema + metadata
- `listMySchemas()` — list schemas the calling user has saved
- `getMySchema(slug)` — fetch one of the caller's saved schemas
- `saveMySchema({ name, jsonSchema, slug?, description?, category?, publishableConsent? })` — persist a JSON Schema to the caller's account
- `updateMySchema(slug, { ...fields })` — partial-update a saved schema
- `deleteMySchema(slug)` — permanently delete one of the caller's saved schemas
- `recommendTemplates({ content?, draftSchema?, k? })` — find templates matching content or a draft schema; returns an `event_id` you can use with `augmentSchema()`
- `augmentSchema(eventId, guidance)` — refine an inferred schema using natural-language guidance (e.g. `"rename event_id to id and add a customer_country field"`)
- `requestWithdrawal(slug, { mode, justification })` — request removal of a community template you authored

### Internal

- `_request` now supports `PATCH` and `DELETE` (used by the personal-schema CRUD surface)
- New exported types: `SaveMySchemaOptions`, `UpdateMySchemaFields`, `RecommendTemplatesOptions`, `RequestWithdrawalOptions`

### Compatibility

- No breaking changes to v0.1.0 callers — `extract`, `inferSchema`, `listTemplates`, `listModels`, `usage` keep their signatures
- `User-Agent` is now `structx-sdk-node/0.2.0 …`

## [0.1.0] — 2026-05-31

Initial release. Mirrors the Python SDK 0.2.0's API surface.

### Features

- `StructX` client with `extract`, `inferSchema`, `listTemplates`, `listModels`, `usage`
- Configurable `RetryPolicy` (exponential backoff with jitter, Retry-After honoring, write-path safety)
- Typed exception hierarchy: `StructXError`, `TransportError`, `ApiError`, `AuthenticationError`, `PermissionDeniedError`, `NotFoundError`, `ValidationError`, `RateLimitError`, `ServerError`
- `ApiError.responseBody` redacts sensitive keys (`api_key`, `password`, `content`, etc.) at construction time
- `ApiError.toString()` omits the body as a second redaction layer
- Per-request timeout via `AbortController`
- Dual ESM + CJS output via tsup; works with `import` and `require`
- TypeScript declarations included
- Zero runtime dependencies — uses native `fetch`, requires Node 18+
- Environment-variable fallbacks: `STRUCTX_API_KEY`, `STRUCTX_BASE_URL`
- Custom `defaultHeaders` for observability headers
