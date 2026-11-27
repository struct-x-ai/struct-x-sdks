"""Tool definitions for the structx MCP server.

Tool DESCRIPTIONS are the public API for any MCP — an LLM agent reads
them to decide whether/when to invoke a tool. They need to be:

  - SPECIFIC about what kind of input the tool eats (HTML? URL? text?)
  - CLEAR about the output shape (typed JSON? confidence scores?)
  - HONEST about edge cases the agent should know (5KB hard cap, etc.)
  - AGENT-FIRST in their phrasing — "use this for X, Y, Z" beats
    "this function does X"

Kept separate from the server runtime so the descriptions are easy
to review independently of the wiring code.
"""
from __future__ import annotations

from mcp.types import Tool


# ── Tool 1 — the workhorse ──────────────────────────────────


EXTRACT_TOOL = Tool(
    name="structx_extract",
    description=(
        "Extract structured JSON from raw content (HTML, markdown, plain text, "
        "JSON-API responses, emails, scraped web pages, documents) using a JSON "
        "Schema you provide OR a pre-built template slug. Returns typed data with "
        "per-field confidence scores so you know which fields to trust. "
        "Use this whenever you need to turn unstructured/semi-structured text into "
        "reliable JSON. Prefer the `template_slug` argument for common formats "
        "(Stripe events, GitHub issues, product pages, news articles) — see "
        "`structx_list_templates` to discover available ones. "
        "Content cap: 500KB. Calls bill against your API key's credit quota."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "Raw text/HTML/markdown to extract from. 500KB max.",
            },
            "schema": {
                "type": "object",
                "description": (
                    "Inline JSON Schema describing the desired output shape. "
                    "Use this OR `template_slug`, not both. Required: `type` "
                    "field at root; `type` must be 'object' or 'array'."
                ),
            },
            "template_slug": {
                "type": "string",
                "description": (
                    "Catalog template identifier. Bare slug (e.g. 'logs.stripe.event') "
                    "uses the latest published version; pin to a specific version with "
                    "'family.name@1.0.0'. Mutually exclusive with `schema`."
                ),
            },
            "tier": {
                "type": "string",
                "enum": ["required", "recommended", "optional", "extended"],
                "default": "required",
                "description": (
                    "Field-depth dial when using a template. 'required' returns only "
                    "the headline fields (cheapest). 'extended' returns every field "
                    "in the template. Ignored for inline-schema extractions."
                ),
            },
            "options": {
                "type": "object",
                "description": (
                    "Optional extraction overrides. Common keys: `include_citations` "
                    "(boolean) to add source snippets per field; `cache` (boolean, "
                    "default true) to allow cached responses, false to always run "
                    "a fresh extraction; `confidence_threshold` (float 0-1) to flag "
                    "low-confidence results. `use_cache` is accepted as an older "
                    "name for `cache`; don't send both with different values."
                ),
            },
        },
        "required": ["content"],
    },
)


# ── Tool 2 — the magic moment ──────────────────────────────


INFER_SCHEMA_TOOL = Tool(
    name="structx_infer_schema",
    description=(
        "Don't have a schema yet? Paste raw content and get a draft JSON Schema "
        "back, with per-field rationales explaining why each field was inferred. "
        "Optionally returns matching templates from the public catalog. "
        "Use this when starting fresh with unfamiliar content (a new SaaS webhook, "
        "an unknown API response, an unstructured document type) — saves the agent "
        "from hand-writing a schema. The returned schema can be passed directly "
        "back to `structx_extract`. Cost: 3-5 credits per call (higher than "
        "extraction because of the meta-reasoning involved)."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": (
                    "Sample content the schema should describe. 200 chars min, "
                    "100KB max. One representative sample is plenty — don't paste "
                    "a giant corpus."
                ),
            },
            "content_type": {
                "type": "string",
                "enum": ["html", "json", "markdown", "text"],
                "description": (
                    "Hint about the format. Optional — the backend can guess from "
                    "content, but a hint improves inference quality."
                ),
            },
            "hints": {
                "type": "object",
                "description": (
                    "Optional constraints. Keys: `vertical` (e.g. 'ecommerce', "
                    "'finance', 'logs'), `fields_must_include` (array of field names "
                    "that MUST appear in the inferred schema)."
                ),
            },
            "return_recommendations": {
                "type": "boolean",
                "default": True,
                "description": (
                    "If true, also returns matching templates from the public catalog "
                    "ranked by similarity. Useful for the agent to decide between "
                    "'use this template' and 'use my freshly-inferred schema'."
                ),
            },
        },
        "required": ["content"],
    },
)


# ── Tool 3 — the integration smoothie ──────────────────────


EXTRACT_FROM_URL_TOOL = Tool(
    name="structx_extract_from_url",
    description=(
        "Fetch a URL and extract structured JSON from it in one call. Removes "
        "the two-call pattern (fetch_url → structx_extract) that every other agent "
        "workflow ends up writing. Honors the same `schema` / `template_slug` / "
        "`tier` / `options` arguments as `structx_extract`. "
        "URL fetching is sandboxed: HTTPS only, public DNS only (no metadata "
        "endpoints, no link-local, no private ranges, no DNS rebinds), 10MB "
        "response cap, no redirects followed. Use for live-page extraction "
        "(product pages, news articles, RSS items, public docs)."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Public HTTPS URL to fetch. http://, file://, ftp://, etc. rejected.",
            },
            "schema": {
                "type": "object",
                "description": "Inline JSON Schema. Mutually exclusive with `template_slug`.",
            },
            "template_slug": {
                "type": "string",
                "description": "Catalog template identifier. Mutually exclusive with `schema`.",
            },
            "tier": {
                "type": "string",
                "enum": ["required", "recommended", "optional", "extended"],
                "default": "required",
            },
            "options": {
                "type": "object",
                "description": "Forwarded to /v1/extract — see `structx_extract`.",
            },
        },
        "required": ["url"],
    },
)


# ── Tool 4 — discovery ──────────────────────────────────────


LIST_TEMPLATES_TOOL = Tool(
    name="structx_list_templates",
    description=(
        "List every public template in the struct-x catalog. Each template has a "
        "slug (e.g. 'logs.stripe.event'), a name, a description, an optional "
        "category, and the underlying JSON Schema. Use this to discover what "
        "schemas already exist BEFORE writing your own — chances are someone "
        "(possibly the platform team, possibly another user via promotion) has "
        "already published a template for the format you care about. "
        "Pair with `structx_extract(template_slug=...)` to skip schema-writing entirely."
    ),
    inputSchema={"type": "object", "properties": {}},
)


# ── Tool 5 — observability ──────────────────────────────────


USAGE_TOOL = Tool(
    name="structx_usage",
    description=(
        "Return the current credit usage for the API key in use — credits used "
        "today, daily limit, current tier. Useful as a pre-flight check before a "
        "batch operation, OR after a `RateLimitError` to confirm the cap is "
        "actually exhausted vs. a transient throttle."
    ),
    inputSchema={"type": "object", "properties": {}},
)


# ── Tools 6–15 (v0.2.0) — catalog + personal schemas + lifecycle ──
#
# These map to the broader Struct-X surface beyond pure extraction:
# browsing the public catalog, managing user-saved schemas, refining
# inferred schemas via NLP, and submitting / withdrawing community
# publications. Designed for agents that build durable workflows
# (saving schemas they discovered, refining them over a session)
# instead of one-shot extractions.


LIST_CATEGORIES_TOOL = Tool(
    name="structx_list_categories",
    description=(
        "Return the template-catalog category hierarchy (category → family → "
        "vendor). Use as a sidebar-style discovery tool when the agent doesn't "
        "know what templates exist. Lightweight — no LLM call, no credit cost."
    ),
    inputSchema={"type": "object", "properties": {}},
)


GET_TEMPLATE_TOOL = Tool(
    name="structx_get_template",
    description=(
        "Fetch one template's full JSON Schema, description, and field-level "
        "documentation by slug. Returns enough to pass directly into "
        "structx_extract via the schema argument. Slugs come from "
        "structx_list_templates."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "slug": {
                "type": "string",
                "description": (
                    "Template slug (e.g. 'logs.stripe.event', "
                    "'ecommerce.shopify.order')."
                ),
            },
        },
        "required": ["slug"],
    },
)


LIST_MY_SCHEMAS_TOOL = Tool(
    name="structx_list_my_schemas",
    description=(
        "List schemas the caller has saved to their account. Includes drafts, "
        "in-progress, and any submitted to the community catalog. Use after "
        "structx_save_my_schema to confirm persistence."
    ),
    inputSchema={"type": "object", "properties": {}},
)


GET_MY_SCHEMA_TOOL = Tool(
    name="structx_get_my_schema",
    description=(
        "Fetch one of the caller's saved schemas by slug. Returns the full JSON "
        "Schema plus metadata (publishable_consent flag, published_template_id "
        "if promoted)."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "slug": {
                "type": "string",
                "description": "User schema slug.",
            },
        },
        "required": ["slug"],
    },
)


SAVE_MY_SCHEMA_TOOL = Tool(
    name="structx_save_my_schema",
    description=(
        "Persist a new JSON Schema to the caller's account. Returns the saved "
        "row including its server-generated slug. Use this after inferring a "
        "schema (structx_infer_schema) the agent wants to reuse. Pass "
        "publishable_consent=true to opt into community-catalog promotion "
        "(admin review gates publication)."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Human-readable name.",
            },
            "json_schema": {
                "type": "object",
                "description": "The JSON Schema body.",
            },
            "slug": {
                "type": "string",
                "description": (
                    "Optional URL-safe identifier. Auto-generated from name "
                    "if omitted."
                ),
            },
            "description": {
                "type": "string",
                "description": "What does this schema extract?",
            },
            "category": {
                "type": "string",
                "description": "Optional category (see structx_list_categories).",
            },
            "publishable_consent": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Opt in to community-catalog promotion. Only published "
                    "after admin review."
                ),
            },
        },
        "required": ["name", "json_schema"],
    },
)


UPDATE_MY_SCHEMA_TOOL = Tool(
    name="structx_update_my_schema",
    description=(
        "Patch one of the caller's saved schemas. Pass only the fields you want "
        "to change. Common use: toggling publishable_consent to opt in or out of "
        "community publication."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "slug": {"type": "string", "description": "Schema slug to update."},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "json_schema": {"type": "object"},
            "category": {"type": "string"},
            "publishable_consent": {"type": "boolean"},
        },
        "required": ["slug"],
    },
)


DELETE_MY_SCHEMA_TOOL = Tool(
    name="structx_delete_my_schema",
    description=(
        "Permanently delete one of the caller's saved schemas. Community copies "
        "(if previously published) stay in the catalog — to remove those too, "
        "call structx_request_withdrawal after this."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "slug": {"type": "string"},
        },
        "required": ["slug"],
    },
)


RECOMMEND_TEMPLATES_TOOL = Tool(
    name="structx_recommend_templates",
    description=(
        "Find existing templates (platform + community + the caller's own "
        "saved schemas) that match a piece of content or a draft schema. Use "
        "BEFORE inferring a new schema — there's often something close enough "
        "to fork. Returns an event_id you can pass to structx_augment_schema "
        "if the top hit needs refinement."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "Content sample to match against.",
            },
            "draft_schema": {
                "type": "object",
                "description": (
                    "Optional draft schema; field-name overlap factors into "
                    "ranking."
                ),
            },
            "k": {
                "type": "integer",
                "default": 5,
                "maximum": 20,
                "description": "Number of recommendations to return.",
            },
        },
    },
)


AUGMENT_SCHEMA_TOOL = Tool(
    name="structx_augment_schema",
    description=(
        "Refine a draft schema using natural-language guidance. Requires an "
        "event_id from a prior structx_infer_schema or "
        "structx_recommend_templates call. Example guidance: 'rename event_id "
        "to id and add a customer_country field'. Returns the refined schema."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "event_id": {
                "type": "string",
                "description": (
                    "event_id returned by a prior infer / recommend call."
                ),
            },
            "guidance": {
                "type": "string",
                "description": "Natural-language refinement instructions.",
            },
        },
        "required": ["event_id", "guidance"],
    },
)


REQUEST_WITHDRAWAL_TOOL = Tool(
    name="structx_request_withdrawal",
    description=(
        "Request removal of a community template the caller authored. Two "
        "modes: 'fork_detach' preserves community forks of the template; "
        "'legal_erasure' deletes the template AND all forks (heavier action, "
        "use for actual data-rights issues). Approval happens in admin review."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "slug": {
                "type": "string",
                "description": "Community-template slug to withdraw.",
            },
            "mode": {
                "type": "string",
                "enum": ["fork_detach", "legal_erasure"],
                "description": "fork_detach preserves forks; legal_erasure removes them.",
            },
            "justification": {
                "type": "string",
                "description": (
                    "Reason for the withdrawal. Visible to admin reviewers."
                ),
            },
        },
        "required": ["slug", "mode", "justification"],
    },
)


# ── Tool 16 (v0.4.0) — synthetic data ──────────────────────


SYNTHESIZE_TOOL = Tool(
    name="structx_synthesize_dataset",
    description=(
        "Generate a synthetic, anonymized, schema-valid dataset from a template "
        "slug OR an inline JSON Schema, shaped by a free-text prompt. Use this to "
        "fabricate realistic-but-fictional test data, demo fixtures, mapping-test "
        "corpora, or edge-case sets WITHOUT touching real or PII data. Describe "
        "the distribution you want in `directives` (e.g. 'mostly failed logins "
        "from EU at night; 5% privilege escalation'). Returns {records, manifest}; "
        "the manifest reports how many records came from the model vs. "
        "deterministic multiplication, validation results, what PII was scrubbed, "
        "and an anonymity attestation. Every record validates against the schema "
        "and contains only reserved-range values (RFC 5737/3849 IPs, example.* "
        "domains). NOTE: anonymized is NOT HIPAA de-identification — do not use "
        "for real PHI. Provide exactly one of `template_slug` / `json_schema`. "
        "Billed on the seed round-trip, so large counts cost ~the same as small."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "template_slug": {
                "type": "string",
                "description": (
                    "Catalog template to base records on (e.g. 'logs.aws.cloudtrail'). "
                    "Mutually exclusive with json_schema. Its curated examples seed the "
                    "deterministic fallback, so generation succeeds even if the model call fails."
                ),
            },
            "json_schema": {
                "type": "object",
                "description": "Inline JSON Schema the records must conform to. Mutually exclusive with template_slug.",
            },
            "count": {
                "type": "integer",
                "default": 10,
                "minimum": 1,
                "description": "How many records to produce (capped by your tier ceiling).",
            },
            "directives": {
                "type": "string",
                "description": "Free-text shaping of the distribution / scenario / edge-case mix.",
            },
            "params": {
                "type": "object",
                "description": "Structured shaping. Keys: `seed` (int, reproducibility), `edge_case_ratio`, `locale`, `time_window`.",
            },
            "mode": {
                "type": "string",
                "enum": ["hybrid", "llm", "deterministic"],
                "default": "hybrid",
                "description": (
                    "hybrid = LLM seed set × deterministic multiply (default); llm = model "
                    "produces all N; deterministic = no model (needs a template or a schema "
                    "with curated examples to derive from)."
                ),
            },
        },
    },
)


# ── Tools (v0.3.0) — core agent surface ────────────────────
#
# Canonical projection, saved-dataset lifecycle, the batch write path,
# and template cost/version introspection. These round out the surface
# an agent needs to run durable extraction + normalization workflows:
# project a result onto a standard framework, manage the synthetic
# datasets it generated, fan out a batch, and reason about a template's
# cost/versions before committing to it.
#
# The installed SDK doesn't yet expose dedicated methods for these
# endpoints, so dispatch calls the backend through the SDK's HTTP /
# `_request` layer directly (see server.py). That keeps the wiring
# SDK-version-independent and test-mockable via respx.


CANONICAL_FRAMEWORKS_TOOL = Tool(
    name="structx_canonical_frameworks",
    description=(
        "List the canonical normalization frameworks an extracted record can be "
        "projected onto (e.g. OTel, OCSF) with each framework's pinned version and "
        "kind. Use BEFORE structx_canonical_project to discover valid framework "
        "names. Lightweight — no LLM call, no credit cost."
    ),
    inputSchema={"type": "object", "properties": {}},
)


CANONICAL_PROJECT_TOOL = Tool(
    name="structx_canonical_project",
    description=(
        "Project an already-extracted record onto one or more canonical "
        "frameworks (e.g. ['otel','ocsf']). Deterministic post-processing — it "
        "reads the StructX concept annotations off the source schema and reshapes "
        "the record per framework; it never re-runs the model and never invents "
        "fields (unmapped fields are reported in `coverage`, not guessed). "
        "Provide the record in `extracted` (typically the `data` of a prior "
        "structx_extract call) plus EXACTLY ONE source schema: a `template_slug` "
        "(catalog template carrying the concepts) OR an inline annotated `schema`. "
        "Returns {projections: {framework: {record, coverage, valid}}}."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "extracted": {
                "type": "object",
                "description": (
                    "The extracted record to project — e.g. the `data` field of an "
                    "/extract response."
                ),
            },
            "frameworks": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "description": (
                    "Target frameworks, e.g. ['otel','ocsf']. Discover valid names "
                    "with structx_canonical_frameworks."
                ),
            },
            "template_slug": {
                "type": "string",
                "description": (
                    "Catalog template whose schema carries the StructX concept "
                    "annotations. Mutually exclusive with `schema`."
                ),
            },
            "schema": {
                "type": "object",
                "description": (
                    "Inline JSON Schema with x-structx-concept / "
                    "x-structx-canonical-map annotations. Mutually exclusive with "
                    "`template_slug`."
                ),
            },
        },
        "required": ["extracted", "frameworks"],
    },
)


LIST_DATASETS_TOOL = Tool(
    name="structx_list_datasets",
    description=(
        "List the caller's saved synthetic datasets (newest first), metadata only "
        "— id, name, template_ref, mode, record_count, anonymized flag, created_at. "
        "The record payloads are omitted to keep the index light; fetch one in full "
        "with structx_get_dataset. Datasets are created by passing save=true to a "
        "generation; use this to find their ids."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "default": 100,
                "minimum": 1,
                "maximum": 500,
                "description": "Max datasets to return (newest first).",
            },
        },
    },
)


GET_DATASET_TOOL = Tool(
    name="structx_get_dataset",
    description=(
        "Fetch one saved synthetic dataset in full — records + manifest — by id "
        "(owner-scoped). Ids come from structx_list_datasets. The manifest reports "
        "produce counts, method breakdown, validation, PII scrubbed, and the "
        "anonymity attestation."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "dataset_id": {
                "type": "string",
                "description": "Dataset UUID (from structx_list_datasets).",
            },
        },
        "required": ["dataset_id"],
    },
)


EXPORT_DATASET_TOOL = Tool(
    name="structx_export_dataset",
    description=(
        "Download a saved synthetic dataset as json, jsonl, or csv (owner-scoped). "
        "Returns the serialized file body as text plus the content-type and "
        "filename. Use jsonl/csv for feeding fixtures into other tools; use json "
        "for a single array. Ids come from structx_list_datasets."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "dataset_id": {
                "type": "string",
                "description": "Dataset UUID (from structx_list_datasets).",
            },
            "format": {
                "type": "string",
                "enum": ["json", "jsonl", "csv"],
                "default": "jsonl",
                "description": "Export wire format.",
            },
        },
        "required": ["dataset_id"],
    },
)


DELETE_DATASET_TOOL = Tool(
    name="structx_delete_dataset",
    description=(
        "Permanently delete one of the caller's saved synthetic datasets by id "
        "(owner-scoped). Ids come from structx_list_datasets."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "dataset_id": {
                "type": "string",
                "description": "Dataset UUID to delete.",
            },
        },
        "required": ["dataset_id"],
    },
)


EXTRACT_BATCH_TOOL = Tool(
    name="structx_extract_batch",
    description=(
        "Extract structured JSON from MANY content items in one call (up to 100). "
        "Each item is its own extraction with its own `schema` OR `template_slug` "
        "(+ optional `tier`); shared `options` apply to every item. Per-item "
        "billing is metered server-side — this is a thin wrapper, no client-side "
        "credit math. Returns {results: [...], total_latency_ms, "
        "total_credits_used} where each result is either a successful extraction "
        "(with confidence + per-item credits_used) or a per-item error object; one "
        "item failing does not abort the batch. Use for bulk/fan-out workloads "
        "instead of looping structx_extract. Content cap: 500KB per item."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "minItems": 1,
                "maxItems": 100,
                "description": (
                    "Items to extract. Each: {content (required, 500KB max), "
                    "schema OR template_slug, tier?}. Provide exactly one of "
                    "schema/template_slug per item."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "schema": {"type": "object"},
                        "template_slug": {"type": "string"},
                        "tier": {
                            "type": "string",
                            "enum": ["required", "recommended", "optional", "extended"],
                            "default": "required",
                        },
                    },
                    "required": ["content"],
                },
            },
            "options": {
                "type": "object",
                "description": (
                    "Shared extraction overrides applied to every item — see "
                    "structx_extract `options`."
                ),
            },
        },
        "required": ["items"],
    },
)


TEMPLATE_COST_ESTIMATE_TOOL = Tool(
    name="structx_template_cost_estimate",
    description=(
        "Estimate the field count, token usage, and credit cost of extracting with "
        "a catalog template at a given tier — WITHOUT running the model (values are "
        "derived from the template's field catalog + token heuristics). Every "
        "response also includes a `per_tier` breakdown for all four tiers, so ONE "
        "call is enough to compare tiers before committing. "
        "Use to decide which tier to extract at, or to preview cost. Slug comes "
        "from structx_list_templates."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "slug": {
                "type": "string",
                "description": (
                    "Template slug (bare for latest, or family.name@version)."
                ),
            },
            "tier": {
                "type": "string",
                "enum": ["required", "recommended", "optional", "extended"],
                "default": "required",
                "description": (
                    "Tier to estimate for: required, recommended, optional or extended "
                    "(default required). To compare tiers, call once with any tier and "
                    "read the response's `per_tier` breakdown; `per_tier` is a response "
                    "field, not a tier value."
                ),
            },
        },
        "required": ["slug"],
    },
)


TEMPLATE_VERSIONS_TOOL = Tool(
    name="structx_template_versions",
    description=(
        "List the version history of a template family by its bare family slug "
        "(e.g. 'logs.stripe.event', not 'logs.stripe.event@1.0.0'). Each entry "
        "carries its version + metadata. Use to discover which version to pin in a "
        "structx_extract call, or to audit how a template has evolved. Returns "
        "{family_slug, versions: [...]}."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "slug": {
                "type": "string",
                "description": "Bare template family slug (no @version suffix).",
            },
        },
        "required": ["slug"],
    },
)


# Ordered list for `list_tools()` — sort by likely usage frequency.
# Workhorses up top (extract / infer); discovery + management tools
# below them; the heavyweight community-action tools at the end so
# they don't accidentally outrank the everyday ones.
EXTRACT_DOCUMENT_TOOL = Tool(
    name="structx_extract_document",
    description=(
        "Extract structured JSON from a PDF — text PDFs, tables and scanned pages "
        "alike (the document is read natively, no OCR step). Give EITHER `file_path` "
        "(a PDF on the machine running this MCP server) OR `document_base64`, plus "
        "EITHER an inline `schema` OR a `template_slug`. Returns the same shape as "
        "`structx_extract`, with per-field confidence reported by the model "
        "(`confidence_source: \"model\"`). Only PDF files are accepted (anything "
        "without a %PDF- header is refused). Limits by plan: 5-20 MB, up to 100 "
        "pages. Credits are reserved up front from the exact document size and "
        "settle to actual usage; failed calls are refunded. Set `max_credits` to "
        "cap the cost. Not enabled on every account: a 404 means document "
        "extraction is not available to this API key yet."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to a local PDF (~ is expanded). Use this OR document_base64.",
            },
            "document_base64": {
                "type": "string",
                "description": "The PDF's bytes, base64-encoded. Use this OR file_path.",
            },
            "schema": {
                "type": "object",
                "description": "Inline JSON Schema for the output. Use this OR template_slug.",
            },
            "template_slug": {
                "type": "string",
                "description": "Catalog template identifier (see structx_list_templates).",
            },
            "model": {
                "type": "string",
                "enum": ["auto", "fast", "balanced", "quality"],
                "default": "auto",
                "description": "Quality preset. 'quality' needs a paid plan.",
            },
            "max_credits": {
                "type": "integer",
                "minimum": 1,
                "description": "Most credits this document may cost; a higher estimate is refused before any work.",
            },
            "tier": {
                "type": "string",
                "description": "Field tier when using a template (as on structx_extract).",
            },
            "fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Restrict the extraction to these top-level fields.",
            },
            "options": {
                "type": "object",
                "description": "Forwarded as-is, e.g. {\"confidence_threshold\": 0.8}.",
            },
        },
    },
)


ALL_TOOLS: list[Tool] = [
    EXTRACT_TOOL,
    EXTRACT_BATCH_TOOL,
    INFER_SCHEMA_TOOL,
    LIST_TEMPLATES_TOOL,
    GET_TEMPLATE_TOOL,
    TEMPLATE_COST_ESTIMATE_TOOL,
    TEMPLATE_VERSIONS_TOOL,
    LIST_CATEGORIES_TOOL,
    RECOMMEND_TEMPLATES_TOOL,
    AUGMENT_SCHEMA_TOOL,
    CANONICAL_FRAMEWORKS_TOOL,
    CANONICAL_PROJECT_TOOL,
    LIST_MY_SCHEMAS_TOOL,
    GET_MY_SCHEMA_TOOL,
    SAVE_MY_SCHEMA_TOOL,
    UPDATE_MY_SCHEMA_TOOL,
    DELETE_MY_SCHEMA_TOOL,
    EXTRACT_FROM_URL_TOOL,
    EXTRACT_DOCUMENT_TOOL,
    REQUEST_WITHDRAWAL_TOOL,
    USAGE_TOOL,
    SYNTHESIZE_TOOL,
    LIST_DATASETS_TOOL,
    GET_DATASET_TOOL,
    EXPORT_DATASET_TOOL,
    DELETE_DATASET_TOOL,
]
