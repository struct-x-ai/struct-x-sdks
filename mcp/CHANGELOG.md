# Changelog

All notable changes to the `structx-mcp` package are documented here.
This project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- **`options.cache` now reaches the API.** The `structx_extract` tool description told agents to send `use_cache`, but the API's key is `cache` and it ignores unknown option keys, so the cache was never skipped. The description now names `cache`, and `structx_extract`, `structx_extract_from_url` and `structx_extract_batch` send `use_cache` as `cache` (with any `structx-sdk` >= 0.5.0). Both with different values returns an error to the agent before any request, and before the URL fetch.
- `structx_template_cost_estimate`: the `tier` description no longer reads as if `per_tier` were a tier value (agents sent `tier: "per_tier"` and got an input-validation error). It now says to call once with any tier and read the response's `per_tier` breakdown.
- **`structx_usage` and the five personal-schema tools (`structx_list_my_schemas`, `structx_get_my_schema`, `structx_save_my_schema`, `structx_update_my_schema`, `structx_delete_my_schema`) work with the API key.** They failed with "requires a bearer token" because structx-sdk required a JWT for them, which an MCP install does not have. The save, update and delete descriptions now say that with an API key they change personal schemas only, and that binding or sharing a schema with an organization is done in the dashboard. The delete description no longer tells the agent to call another tool. Needs the structx-sdk release with the matching fix.
- **`structx_augment_schema` now refines the schema. Breaking: new required `content` argument.** The old call never worked: it went through structx-sdk to the recommendation feedback endpoint, which returns 204 and never refines anything. It now calls `/augment` and returns the result in the `structx_infer_schema` shape. New required `content` argument (the same sample sent to `structx_infer_schema`; the API rejects different content) and optional `content_type`. The description says event ids from `structx_recommend_templates` are not accepted (the API answers 400 for them), and `structx_recommend_templates` no longer says its event_id can be refined. Needs the structx-sdk release with the matching fix.
- **`structx_list_templates` returns one page instead of the whole catalog.** It returned every public template with its JSON Schema (about 1,100 templates, roughly 10 MB), far too much for an agent's context. It now pages `GET /v1/templates`: 25 templates per page by default, at most 50 (`limit`), with `offset`, and filters `q` (tag words; a template must carry every word) and `category` (a code such as `PAYMENTS`). `q` is lowercased and `category` uppercased before the request, and a value the API would reject returns an error to the agent before any request. The result is `{templates, total, limit, offset, next_offset}`; `next_offset` is null on the last page. Items carry no JSON Schema, and the description says `structx_get_template` returns one. Needs the structx-sdk release that adds `browse_templates()`.
- **`publishable_consent` is no longer offered to agents.** Opting a schema into the public community catalog is done in the struct-x dashboard (founder decision), so `structx_save_my_schema` and `structx_update_my_schema` no longer list `publishable_consent` in their input schemas, and the server drops it if an agent sends it anyway. `structx_save_my_schema` also stopped sending `"publishable_consent": false` on every save. Their descriptions say consent is set in the dashboard.
- `structx_save_my_schema` and `structx_update_my_schema` no longer list `category`: personal schemas have no category, so the API dropped it without saying so. `structx_update_my_schema` now forwards only the fields its input schema lists (`name`, `description`, `json_schema`), so `org_slug`, `visibility` or `publishable_consent` sent by an agent never reach the API.
- **`structx_usage` no longer fails on the API's response.** It went through structx-sdk's `Usage` model, which required a `credits_limit_daily` field that `/billing/usage` never returns, so the tool errored on every real response. The result now carries the API's fields (`credits_used_today`, `credits_limit_today`, `credits_remaining_today`, the monthly totals, `tier`). Needs the structx-sdk release with the matching fix.

### Changed

- Tests: every tool's input schema must be a valid JSON Schema whose defaults satisfy their own `enum` / `minimum` / `maximum`, and no tool may offer `publishable_consent`. `jsonschema` is listed in the `dev` extra (it was already installed with `mcp`).

## [0.4.0] — 2026-11-26

Adds PDF extraction for agents. Requires `structx-sdk` 0.5.0, so publish the SDK first.

This is the first PyPI release since 0.1.0. The 0.2.0 and 0.3.0 entries below were never published on their own; upgrading from 0.1.0 picks up all of them.

### Added

- **`structx_extract_document`** (26th tool) — extract from a local PDF (`file_path`) or `document_base64`. Refuses anything without a `%PDF-` header, so an agent can't be steered into uploading a local key or config file. `file_path` also refuses network paths (UNC, device and `file:` forms, mapped network drives on Windows) and symlinks / reparse points before any filesystem call, so a path can't make the host contact a remote share. If an older SDK is somehow installed, the tool returns an upgrade message instead of failing.
- pytest: `asyncio_default_fixture_loop_scope = "function"`.

### Changed

- Raised the `structx-sdk` dependency to `>=0.5.0` (the release that ships `extract_file()`).
- README carries the `<!-- mcp-name: io.github.struct-x-ai/structx-mcp -->` marker the official MCP registry checks on the published package.

### Fixed

- The missing-API-key message and the package metadata (homepage, docs URL, author email) pointed at `structx.ai` (no hyphen), which is the wrong domain. They now use `struct-x.ai`.

### Surface size

- 25 tools → **26 tools**.

## [0.3.0] — 2026-06-30

The "core agent surface" expansion. Rounds out the toolset an agent needs to run durable extraction + normalization workflows: a batch write path, canonical-framework projection, the synthetic-dataset lifecycle, and template cost/version introspection.

These nine tools call the backend through the SDK's HTTP layer directly (the dedicated SDK methods land in `structx-sdk` 0.4.0), so the wiring is SDK-version-independent and fully test-mockable.

### Added
- **`structx_extract_batch`** — extract from up to 100 content items in one call; each item carries its own schema/template + tier, shared options apply to all. Per-item billing is metered server-side (thin wrapper, no client-side credit math); surfaces `total_credits_used` / `total_latency_ms`.
- **`structx_canonical_frameworks`** — list canonical normalization frameworks (OTel, OCSF, …) with pinned versions + kinds.
- **`structx_canonical_project`** — project an extracted record onto one or more canonical frameworks. Deterministic: never re-runs the model, reports unmapped fields in `coverage`.
- **`structx_template_cost_estimate`** — estimate field count / tokens / credits for a template at a given tier (no model call), with a per-tier breakdown.
- **`structx_template_versions`** — list a template family's version history by its bare slug.
- **`structx_list_datasets`** — list the caller's saved synthetic datasets (metadata only).
- **`structx_get_dataset`** — fetch one saved dataset in full (records + manifest).
- **`structx_export_dataset`** — download a saved dataset as json / jsonl / csv.
- **`structx_delete_dataset`** — delete one of the caller's saved datasets.

### Changed
- Bumped `structx-sdk` dependency to `>=0.4.0` for the new canonical / dataset / batch / template methods.

### Fixed
- RELEASE.md smoke test asserted `len(ALL_TOOLS) == 15`, but the published 0.2.0 surface was already 16 (the `structx_synthesize_dataset` tool shipped without a matching count/CHANGELOG update). Corrected the count throughout.

### Surface size
- 16 tools → **25 tools**.

## [0.2.0] — 2026-06-03

Major tool-surface expansion. The original 5 tools focused narrowly on extraction; this release covers the broader Struct-X platform — template browsing, personal-schema management, schema lifecycle (recommend / infer / augment), and community publication/withdrawal — so agents can build durable workflows around the platform instead of one-shot extractions.

### Added
- **`structx_list_categories`** — return the template-catalog category hierarchy (category → family → vendor) for sidebar-style discovery.
- **`structx_get_template`** — fetch one template's full JSON Schema + metadata by slug.
- **`structx_list_my_schemas`** — list schemas the caller has saved to their account.
- **`structx_get_my_schema`** — fetch one of the caller's saved schemas.
- **`structx_save_my_schema`** — persist a new JSON Schema to the caller's account. Supports `publishable_consent` for community-catalog promotion (admin-reviewed).
- **`structx_update_my_schema`** — patch a saved schema in place.
- **`structx_delete_my_schema`** — remove a saved schema. Community copies (if published) stay; use `structx_request_withdrawal` to remove those too.
- **`structx_recommend_templates`** — find existing templates (platform + community + user) matching content or a draft schema. Returns an `event_id` that can be passed to `structx_augment_schema`.
- **`structx_augment_schema`** — refine a previously-inferred or recommended schema using natural-language guidance (e.g. "rename event_id to id and add a customer_country field").
- **`structx_request_withdrawal`** — submit a removal request for a community template the caller authored. Two modes: `fork_detach` (preserve forks) and `legal_erasure` (delete forks too).

### Changed
- `ALL_TOOLS` ordering: workhorses (`extract`, `infer_schema`) stay first; new discovery + management tools cluster in the middle; the heavyweight community-action tool (`request_withdrawal`) is near the end so it doesn't accidentally outrank everyday ones.
- Bumped `structx-sdk` dependency to `>=0.3.0` for the new platform-surface methods.

### Surface size
- 5 tools → **16 tools** (the 10 above plus `structx_synthesize_dataset`, which shipped in this window). Designed for MCP-registry publication (Anthropic, Cursor, Windsurf indexes). *(Originally documented as "15"; corrected in 0.3.0 — see that entry's Fixed note.)*

## [0.1.1] — 2026-05-30

### Changed
- Internal: bumped the `structx` dependency to `structx-sdk>=0.2.0` (SDK package rename). No customer-facing change — the MCP package name, CLI command, and configuration are unchanged.

## [0.1.0] — 2026-05-24

Initial release. Replaces the in-tree `backend/mcp/server.py` (which
used raw httpx) with a standalone PyPI package backed by the official
`structx` Python SDK.

### Added
- 5 MCP tools designed for agent-first use:
  - `structx_extract` — extract using inline schema OR `template_slug`.
  - `structx_infer_schema` — generate a schema from sample content.
  - `structx_extract_from_url` — fetch + extract in one call, SSRF-defended.
  - `structx_list_templates` — browse the public template catalog.
  - `structx_usage` — current credit usage.
- `structx-mcp` console-script entrypoint for Claude Desktop / Cursor / Windsurf configs.
- SSRF defense for URL fetching (HTTPS only, IP classification, hostname blocklist, no redirects, 10MB cap).
- Typed-error pass-through — SDK errors surface to the agent as JSON `{"error", "type"}` rather than vanishing into transport.

### Compatible with
- Python 3.10+.
- `structx>=0.1.0,<1.0.0` (the Python SDK).
- MCP protocol via the `mcp>=1.0.0` reference implementation.
- struct-x API v1.x.

### Migration note (from the in-tree server)
The in-tree `backend/mcp/server.py` had hardcoded convenience tools
(`structx_extract_product`, `_article`, `_contact`). Those are dropped
in favor of `structx_extract(template_slug="ecommerce.product")` etc.
— new templates ship without code changes, and the tool catalog stays
lean (agents handle small tool surfaces better).
