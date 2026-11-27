/**
 * `StructX` — the SDK's single public client class.
 *
 * Mirrors the Python SDK's API surface 1:1 (extract / infer_schema /
 * list_templates / list_models / usage), with these adjustments for
 * Node:
 *
 *   - Single class instead of sync + async pair. Node has no sync HTTP;
 *     all methods are async. Cuts maintenance in half vs. the Python
 *     split.
 *
 *   - Native `fetch` instead of httpx. Built-in to Node 18+; zero
 *     runtime dependencies for the SDK as a whole. Customer security
 *     teams audit `package.json` in 10 seconds; no transitive supply-
 *     chain risk.
 *
 *   - `AbortController` for per-request timeout. The Python SDK uses
 *     httpx's built-in `timeout=` arg; Node's fetch needs an explicit
 *     AbortSignal — we wrap that internally so the caller-facing
 *     `timeoutMs` option is the same shape.
 *
 *   - Construction takes a config OBJECT (not positional args). More
 *     ergonomic in TypeScript and matches the popular SDK conventions
 *     in the npm ecosystem (Stripe, OpenAI, Anthropic).
 */
import {
  ApiError,
  AuthenticationError,
  ERROR_BY_STATUS,
  NotFoundError,
  PermissionDeniedError,
  RateLimitError,
  ServerError,
  StructXError,
  TransportError,
  ValidationError,
} from './errors.js';
import {
  RetryPolicy,
  RetryPolicyOptions,
  isRetryableResponse,
} from './retry.js';
import type {
  Extraction,
  InferenceResult,
  Model,
  Template,
  Usage,
} from './models.js';
import { VERSION } from './version.js';

const DEFAULT_BASE_URL = 'https://api.struct-x.ai';

/** User-Agent string. Includes SDK version, Node version, and platform —
 *  enough for the backend to attribute requests during ops debugging
 *  without leaking customer identity. */
function userAgent(): string {
  const node = process.versions?.node ?? 'unknown';
  const platform = process.platform ?? 'unknown';
  return `structx-sdk-node/${VERSION} Node/${node} (${platform})`;
}

/** Configuration accepted by the `StructX` constructor. */
export interface StructXOptions {
  /** sx_-prefixed API key. Falls back to env var `STRUCTX_API_KEY`. */
  apiKey?: string;
  /** Optional JWT bearer token — the second credential path. Falls back
   *  to env var `STRUCTX_BEARER_TOKEN`. Required by the JWT-only methods
   *  (`usage()` + the `/v1/schemas/mine` personal-schema CRUD), whose
   *  backend routes authenticate via `Authorization: Bearer` only (an API
   *  key is rejected with 401). The API-key methods keep using
   *  `X-API-Key`. */
  bearerToken?: string;
  /** Override the base URL. Falls back to env var `STRUCTX_BASE_URL`
   *  and finally to `https://api.struct-x.ai`. */
  baseUrl?: string;
  /** Per-request timeout in milliseconds. Default 30 000. */
  timeoutMs?: number;
  /** Retry behavior. Pass a `RetryPolicy` instance to fully customize,
   *  or a partial options object to tweak defaults. */
  retry?: RetryPolicy | RetryPolicyOptions;
  /** Additional headers sent on every request. Useful for custom
   *  observability headers (X-Request-Trace, X-Tenant, etc.). */
  defaultHeaders?: Record<string, string>;
  /** Substitute fetch implementation. Used in tests; production
   *  callers shouldn't need to set this. */
  fetch?: typeof fetch;
}

/** Options for a single call to one of the HTTP-emitting methods.
 *  PATCH / DELETE landed in v0.2.0 with the personal-schema CRUD surface. */
interface RequestOptions {
  method: 'GET' | 'POST' | 'PATCH' | 'DELETE';
  path: string;
  body?: unknown;
  query?: Record<string, string | number | boolean | null | undefined>;
  headers?: Record<string, string>;
  /** Return the raw 2xx response body as a string instead of parsing it
   *  as JSON. Used by `exportDataset()`, whose backend emits
   *  jsonl / csv / json file bodies rather than a JSON envelope. Error
   *  responses are still mapped to typed exceptions as usual. */
  rawText?: boolean;
  /** Per-request timeout, overriding the client's `timeoutMs`. */
  timeoutMs?: number;
  /** Per-request attempt cap, overriding the client's retry policy. */
  maxAttempts?: number;
}

/** The server allows 120 s per document; this covers upload as well. */
const DOCUMENT_TIMEOUT_MS = 180_000;
/** Client-side guard against an accidental huge read. The API itself accepts
 *  at most 20 MB, depending on the plan. */
const DOCUMENT_MAX_BYTES = 32 * 1024 * 1024;

/** Base64 for Node (Buffer) and browsers (btoa), chunked to stay off the
 *  call-stack limit on large documents. */
function toBase64(bytes: Uint8Array): string {
  const B = (globalThis as { Buffer?: { from(b: Uint8Array): { toString(enc: string): string } } }).Buffer;
  if (B) return B.from(bytes).toString('base64');
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

/**
 * The `options` object sent to `/v1/extract` and `/v1/extract/batch`.
 *
 * The server's name for the cache switch is `cache`. Releases up to 0.5.0
 * documented `use_cache` instead, which older servers ignore, so
 * `{ use_cache: false }` never skipped the cache. `use_cache` is still
 * accepted: it is sent as `cache`. Given both with different values, throw
 * `StructXError` before the request (the server answers 422). A
 * `use_cache` of `null` / `undefined` counts as not given. The caller's
 * object is not changed.
 */
function extractOptions(
  options: Record<string, unknown> | undefined,
): Record<string, unknown> | undefined {
  if (options === undefined || options === null || !('use_cache' in options)) return options;
  const { use_cache: useCache, ...wire } = options;
  if (useCache === undefined || useCache === null) return wire;
  const cache = wire['cache'];
  if (cache !== undefined && cache !== null && cache !== useCache) {
    throw new StructXError(
      "options has both 'cache' and 'use_cache' with different values; " +
        "they are the same option, so pass only 'cache'",
    );
  }
  wire['cache'] = useCache;
  return wire;
}

/** Options accepted by `extract()`. */
export interface ExtractOptions {
  /** Inline JSON Schema. EXACTLY ONE of `schema` or `templateSlug` is
   *  required; the backend enforces this. */
  schema?: Record<string, unknown>;
  /** Catalog template slug like `"logs.stripe.event"`. */
  templateSlug?: string;
  /** Field-depth dial. `"required"` is the default + cheapest;
   *  `"extended"` returns every field in the schema. */
  tier?: string;
  /** Forwarded to the backend — see backend docs for the full set
   *  (`include_citations`, `cache`, `confidence_threshold`, …). `cache: false`
   *  skips the extraction cache. The older name `use_cache` still works: it
   *  is sent as `cache`; both with different values throws `StructXError`. */
  options?: Record<string, unknown>;
}

/** Options accepted by `extractFile()` (`POST /v1/extract/document`). */
export interface ExtractFileOptions {
  /** Inline JSON Schema. EXACTLY ONE of `schema` or `templateSlug`. */
  schema?: Record<string, unknown>;
  /** Catalog template slug. */
  templateSlug?: string;
  /** `"auto"` (default), `"fast"`, `"balanced"` or `"quality"` (paid plans). */
  model?: string;
  /** Field tier, as on `extract()`. */
  tier?: string;
  /** Restrict the extraction to these top-level fields. */
  fields?: string[];
  /** The most this document may cost; a higher estimate is refused (422
   *  `max_credits_exceeded`) before any work. */
  maxCredits?: number;
  /** Forwarded as-is (e.g. `confidence_threshold`). */
  options?: Record<string, unknown>;
  /** Defaults to `"application/pdf"`, the only type accepted today. */
  mediaType?: string;
  /** Defaults to 180 000 ms. */
  timeoutMs?: number;
}

/** Options accepted by `inferSchema()`. */
export interface InferSchemaOptions {
  contentType?: string;
  hints?: Record<string, unknown>;
  k?: number;
  returnRecommendations?: boolean;
}

/** Options accepted by `saveMySchema()`. Wire field is `json_schema`; we
 *  use camelCase in the SDK and map to snake_case at the boundary so the
 *  JS surface stays idiomatic. */
export interface SaveMySchemaOptions {
  /** Human-readable name shown in the catalog UI. Required. */
  name: string;
  /** The JSON Schema itself. Required. */
  jsonSchema: Record<string, unknown>;
  /** Slug for URL paths. Server generates one if omitted. */
  slug?: string;
  /** Long-form description shown on the catalog card. */
  description?: string;
  /** Category slug (e.g. `"logs"`, `"ecommerce"`). */
  category?: string;
  /** Opt in to having an admin consider this schema for the public
   *  catalog. Default false — schema stays private to the calling user. */
  publishableConsent?: boolean;
}

/** Options accepted by `recommendTemplates()`. At least one of
 *  `content` / `draftSchema` must be provided. */
export interface RecommendTemplatesOptions {
  content?: string;
  draftSchema?: Record<string, unknown>;
  k?: number;
}

/** Options accepted by `requestWithdrawal()`. */
export interface RequestWithdrawalOptions {
  /** `"fork_detach"` preserves forks; `"legal_erasure"` deletes them.
   *  See backend community-template docs for the policy difference. */
  mode: 'fork_detach' | 'legal_erasure';
  /** Reason shown to admin reviewers. */
  justification: string;
}

/** Partial-update body accepted by `updateMySchema()`. Pass only the
 *  fields you want to change. Wire mapping is camelCase → snake_case. */
export interface UpdateMySchemaFields {
  name?: string;
  description?: string;
  category?: string;
  jsonSchema?: Record<string, unknown>;
  publishableConsent?: boolean;
}

/** Options accepted by `synthesize()`. Wire mapping is camelCase →
 *  snake_case at the boundary. Provide EXACTLY ONE of `templateSlug` /
 *  `jsonSchema`; the backend enforces this. */
export interface SynthesizeOptions {
  /** Catalog template slug whose schema seeds the generation. */
  templateSlug?: string;
  /** Inline JSON Schema to generate against. */
  jsonSchema?: Record<string, unknown>;
  /** Number of records to produce. Default 10; the backend caps this
   *  at the caller's per-tier ceiling. */
  count?: number;
  /** Free-text guidance shaping the dataset (e.g. `"mostly denies"`). */
  directives?: string;
  /** Generator knobs forwarded as-is to the backend. */
  params?: Record<string, unknown>;
  /** `"hybrid"` (default), `"llm"`, or `"deterministic"`. */
  mode?: string;
}

/** Options accepted by `project()`. Provide EXACTLY ONE of
 *  `templateSlug` / `schema`; the backend enforces this. */
export interface ProjectOptions {
  /** Catalog template whose schema carries the StructX concept
   *  annotations used for the mapping. */
  templateSlug?: string;
  /** Inline JSON Schema with `x-structx-concept` /
   *  `x-structx-canonical-map` annotations. */
  schema?: Record<string, unknown>;
}

/** Options accepted by `batchExtract()`. */
export interface BatchExtractOptions {
  /** Shared extraction options applied to every item in the batch
   *  (`include_citations`, `cache`, `confidence_threshold`, …), handled as
   *  on `extract()` (`use_cache` is sent as `cache`). */
  options?: Record<string, unknown>;
}

/** One item in a `batchExtract()` call. EXACTLY ONE of `schema` /
 *  `templateSlug` is required per item — same contract as `extract()`. */
export interface BatchExtractItem {
  /** Raw HTML / markdown / text to extract from. Required. */
  content: string;
  /** Inline JSON Schema for this item. */
  schema?: Record<string, unknown>;
  /** Catalog template slug for this item. */
  templateSlug?: string;
  /** Per-item field-depth dial. Defaults to `"required"`. */
  tier?: string;
}

export class StructX {
  public readonly apiKey: string;
  public readonly bearerToken: string | undefined;
  public readonly baseUrl: string;
  public readonly timeoutMs: number;
  public readonly retry: RetryPolicy;
  public readonly defaultHeaders: Record<string, string>;
  private readonly _fetch: typeof fetch;

  constructor(options: StructXOptions = {}) {
    const apiKey = options.apiKey ?? process.env['STRUCTX_API_KEY'] ?? '';
    if (!apiKey) {
      throw new StructXError(
        'apiKey is required. Pass it explicitly or set the STRUCTX_API_KEY environment variable.',
      );
    }
    this.apiKey = apiKey;
    // Optional bearer (JWT) credential for the bearer-only methods.
    this.bearerToken = options.bearerToken ?? process.env['STRUCTX_BEARER_TOKEN'] ?? undefined;
    const baseUrl = options.baseUrl
      ?? process.env['STRUCTX_BASE_URL']
      ?? DEFAULT_BASE_URL;
    // Trailing slash so URL construction with relative paths is
    // predictable. We strip leading slashes from path inputs below.
    this.baseUrl = baseUrl.replace(/\/+$/, '') + '/';
    this.timeoutMs = options.timeoutMs ?? 30_000;
    this.retry = options.retry instanceof RetryPolicy
      ? options.retry
      : new RetryPolicy(options.retry ?? {});
    this.defaultHeaders = options.defaultHeaders ?? {};
    this._fetch = options.fetch ?? globalThis.fetch;
    if (typeof this._fetch !== 'function') {
      throw new StructXError(
        'fetch is not available. The SDK requires Node 18+ (native fetch) or a polyfill passed via `options.fetch`.',
      );
    }
  }

  // ── Public API ───────────────────────────────────────────────

  /**
   * Run a structured extraction against `content`.
   *
   * Pass EXACTLY ONE of `schema` (inline JSON Schema) or `templateSlug`
   * (a catalog template like `"logs.stripe.event"`). The backend
   * enforces this; passing both raises `ValidationError`.
   *
   * `tier` selects field depth: `"required"` (default) is the cheapest /
   * narrowest extraction; `"extended"` returns every field in the
   * schema. See backend docs for the full tier ladder.
   */
  async extract(content: string, options: ExtractOptions = {}): Promise<Extraction> {
    const body: Record<string, unknown> = {
      content,
      tier: options.tier ?? 'required',
    };
    if (options.schema !== undefined) body['schema'] = options.schema;
    if (options.templateSlug !== undefined) body['template_slug'] = options.templateSlug;
    if (options.options !== undefined) body['options'] = extractOptions(options.options);
    return (await this._request({
      method: 'POST',
      path: '/v1/extract',
      body,
    })) as Extraction;
  }

  /**
   * Extract structured data from a PDF (`POST /v1/extract/document`).
   *
   * `document` is the file's bytes: a `Uint8Array` (so a Node `Buffer`) or an
   * `ArrayBuffer`. Pass EXACTLY ONE of `schema` or `templateSlug`. The result
   * has `confidence_source: "model"`: document confidence is reported per field
   * by the model, not grounded in text.
   *
   * Never retried automatically: the server may still finish (and charge) a
   * document after the client gives up, so a silent retry could charge twice.
   */
  async extractFile(
    document: Uint8Array | ArrayBuffer,
    options: ExtractFileOptions = {},
  ): Promise<Extraction> {
    const bytes = document instanceof Uint8Array ? document : new Uint8Array(document);
    if (bytes.byteLength === 0) throw new StructXError('document is empty');
    if (bytes.byteLength > DOCUMENT_MAX_BYTES) {
      throw new StructXError(
        `document is larger than ${DOCUMENT_MAX_BYTES / (1024 * 1024)} MB; the API accepts at most 20 MB (by plan)`,
      );
    }
    const body: Record<string, unknown> = {
      document: {
        type: 'base64',
        media_type: options.mediaType ?? 'application/pdf',
        data: toBase64(bytes),
      },
    };
    if (options.schema !== undefined) body['schema'] = options.schema;
    if (options.templateSlug !== undefined) body['template_slug'] = options.templateSlug;
    if (options.model !== undefined) body['model'] = options.model;
    if (options.tier !== undefined) body['tier'] = options.tier;
    if (options.fields !== undefined) body['fields'] = options.fields;
    if (options.maxCredits !== undefined) body['max_credits'] = options.maxCredits;
    if (options.options !== undefined) body['options'] = options.options;
    return (await this._request({
      method: 'POST',
      path: '/v1/extract/document',
      body,
      timeoutMs: options.timeoutMs ?? DOCUMENT_TIMEOUT_MS,
      maxAttempts: 1,
    })) as Extraction;
  }

  /**
   * Infer a JSON Schema from raw content + optionally return template
   * recommendations that match. Costs `infer_min_credits` per call
   * (configured on the backend; defaults to 3-5).
   */
  async inferSchema(
    content: string,
    options: InferSchemaOptions = {},
  ): Promise<InferenceResult> {
    const body: Record<string, unknown> = {
      content,
      k: options.k ?? 5,
      return_recommendations: options.returnRecommendations ?? true,
    };
    if (options.contentType !== undefined) body['content_type'] = options.contentType;
    if (options.hints !== undefined) body['hints'] = options.hints;
    return (await this._request({
      method: 'POST',
      path: '/v1/schemas/infer',
      body,
    })) as InferenceResult;
  }

  /** Public template gallery. */
  async listTemplates(): Promise<Template[]> {
    const raw = await this._request({ method: 'GET', path: '/v1/schemas' });
    if (Array.isArray(raw)) return raw as Template[];
    const obj = raw as Record<string, unknown>;
    const items = obj['templates'];
    return Array.isArray(items) ? (items as Template[]) : [];
  }

  /**
   * Available models. Most callers don't need this — the backend's
   * router picks per call. Admin-only; non-admin
   * keys get 403.
   */
  async listModels(): Promise<Model[]> {
    const raw = await this._request({ method: 'GET', path: '/v1/models' });
    if (Array.isArray(raw)) return raw as Model[];
    const obj = raw as Record<string, unknown>;
    const items = obj['models'];
    return Array.isArray(items) ? (items as Model[]) : [];
  }

  /**
   * Current credit usage for the authenticated user.
   *
   * Bearer-token method: the backend mounts this at `/billing/usage` (no
   * `/v1` prefix) and authenticates it via JWT Bearer only. Set
   * `bearerToken` on the client (or `STRUCTX_BEARER_TOKEN`).
   */
  async usage(): Promise<Usage> {
    this._requireBearer('usage');
    return (await this._request({
      method: 'GET',
      path: '/billing/usage',
    })) as Usage;
  }

  // ── Catalog browsing (v0.2.0) ────────────────────────────────

  /**
   * Return the category → family → vendor hierarchy used by the
   * public template catalog. Raw object; we don't ship a typed model
   * for it yet — the shape is still evolving on the backend side.
   */
  async listCategories(): Promise<Record<string, unknown>> {
    return (await this._request({
      method: 'GET',
      path: '/v1/templates/categories',
    })) as Record<string, unknown>;
  }

  /**
   * Fetch one template's full JSON Schema + metadata by slug. Returns
   * the raw object — typed fields overlap with `Template` but include
   * additional fields the gallery list doesn't carry.
   */
  async getTemplate(slug: string): Promise<Record<string, unknown>> {
    return (await this._request({
      method: 'GET',
      path: `/v1/templates/${encodeURIComponent(slug)}`,
    })) as Record<string, unknown>;
  }

  // ── Personal schemas (v0.2.0) ────────────────────────────────

  /** List schemas the calling user has saved to their account.
   *  Bearer-token method (`/v1/schemas/mine` is JWT-only). */
  async listMySchemas(): Promise<Record<string, unknown>[]> {
    this._requireBearer('listMySchemas');
    const raw = await this._request({ method: 'GET', path: '/v1/schemas/mine' });
    if (Array.isArray(raw)) return raw as Record<string, unknown>[];
    const obj = raw as Record<string, unknown>;
    const items = obj['schemas'];
    return Array.isArray(items) ? (items as Record<string, unknown>[]) : [];
  }

  /** Fetch one of the caller's saved schemas by slug.
   *  Bearer-token method (`/v1/schemas/mine` is JWT-only). */
  async getMySchema(slug: string): Promise<Record<string, unknown>> {
    this._requireBearer('getMySchema');
    return (await this._request({
      method: 'GET',
      path: `/v1/schemas/mine/${encodeURIComponent(slug)}`,
    })) as Record<string, unknown>;
  }

  /**
   * Persist a new JSON Schema to the caller's account. Returns the
   * saved row including its server-generated slug if `slug` was omitted.
   */
  async saveMySchema(opts: SaveMySchemaOptions): Promise<Record<string, unknown>> {
    this._requireBearer('saveMySchema');
    const body: Record<string, unknown> = {
      name: opts.name,
      json_schema: opts.jsonSchema,
      publishable_consent: opts.publishableConsent ?? false,
    };
    if (opts.slug !== undefined) body['slug'] = opts.slug;
    if (opts.description !== undefined) body['description'] = opts.description;
    if (opts.category !== undefined) body['category'] = opts.category;
    return (await this._request({
      method: 'POST',
      path: '/v1/schemas/mine',
      body,
    })) as Record<string, unknown>;
  }

  /**
   * Patch one of the caller's schemas. Pass only the fields you want
   * to change. `slug` identifies the row in the URL; renaming the slug
   * requires delete + save.
   */
  async updateMySchema(
    slug: string,
    fields: UpdateMySchemaFields,
  ): Promise<Record<string, unknown>> {
    this._requireBearer('updateMySchema');
    const body: Record<string, unknown> = {};
    if (fields.name !== undefined) body['name'] = fields.name;
    if (fields.description !== undefined) body['description'] = fields.description;
    if (fields.category !== undefined) body['category'] = fields.category;
    if (fields.jsonSchema !== undefined) body['json_schema'] = fields.jsonSchema;
    if (fields.publishableConsent !== undefined) {
      body['publishable_consent'] = fields.publishableConsent;
    }
    return (await this._request({
      method: 'PATCH',
      path: `/v1/schemas/mine/${encodeURIComponent(slug)}`,
      body,
    })) as Record<string, unknown>;
  }

  /**
   * Permanently delete one of the caller's saved schemas. Community
   * copies (if `publishableConsent` was true and an admin published)
   * stay — use `requestWithdrawal()` to remove those too.
   */
  async deleteMySchema(slug: string): Promise<void> {
    this._requireBearer('deleteMySchema');
    await this._request({
      method: 'DELETE',
      path: `/v1/schemas/mine/${encodeURIComponent(slug)}`,
    });
  }

  // ── Schema lifecycle (v0.2.0) ────────────────────────────────

  /**
   * Find existing templates that match content and/or a draft schema.
   * At least one of `content` / `draftSchema` must be provided. Returns
   * an `event_id` you can use with `augmentSchema()` to iterate on the
   * result with natural-language guidance.
   */
  async recommendTemplates(
    opts: RecommendTemplatesOptions = {},
  ): Promise<Record<string, unknown>> {
    const body: Record<string, unknown> = { k: opts.k ?? 5 };
    if (opts.content !== undefined) body['content'] = opts.content;
    if (opts.draftSchema !== undefined) body['draft_schema'] = opts.draftSchema;
    return (await this._request({
      method: 'POST',
      path: '/v1/schemas/recommend',
      body,
    })) as Record<string, unknown>;
  }

  /**
   * Refine a schema (from a prior infer or recommend call) using
   * natural-language guidance. Example guidance: `"rename event_id to
   * id and add a customer_country field"`. Requires `eventId` from a
   * prior infer/recommend response.
   */
  async augmentSchema(
    eventId: string,
    guidance: string,
  ): Promise<Record<string, unknown>> {
    const body = { decision: 'augment', augment_guidance: guidance };
    return (await this._request({
      method: 'POST',
      path: `/v1/schemas/recommendations/${encodeURIComponent(eventId)}/feedback`,
      body,
    })) as Record<string, unknown>;
  }

  // ── Community (v0.2.0) ───────────────────────────────────────

  /**
   * Request removal of a community template the caller authored.
   * `mode` is `"fork_detach"` (preserve forks) or `"legal_erasure"`
   * (delete forks too). `justification` is visible to admin reviewers.
   */
  async requestWithdrawal(
    slug: string,
    opts: RequestWithdrawalOptions,
  ): Promise<Record<string, unknown>> {
    const body = { mode: opts.mode, justification: opts.justification };
    return (await this._request({
      method: 'POST',
      path: `/v1/templates/${encodeURIComponent(slug)}/withdraw`,
      body,
    })) as Record<string, unknown>;
  }

  // ── Synthetic data (v0.4.0) ──────────────────────────────────

  /**
   * Generate an anonymized, schema-valid synthetic dataset from a
   * template slug OR an inline JSON Schema, shaped by free-text
   * `directives`. Pass EXACTLY ONE of `templateSlug` / `jsonSchema`;
   * the backend enforces this.
   *
   * Returns the raw `{ records: [...], manifest: {...} }` object — the
   * manifest reports counts, method breakdown, validation, PII scrubbed,
   * and the anonymity attestation. Billed against the API key's credit
   * quota. WRITE path: never retried on 5xx.
   */
  async synthesize(opts: SynthesizeOptions = {}): Promise<Record<string, unknown>> {
    const body: Record<string, unknown> = {
      count: opts.count ?? 10,
      mode: opts.mode ?? 'hybrid',
    };
    if (opts.templateSlug !== undefined) body['template_slug'] = opts.templateSlug;
    if (opts.jsonSchema !== undefined) body['json_schema'] = opts.jsonSchema;
    if (opts.directives !== undefined) body['directives'] = opts.directives;
    if (opts.params !== undefined) body['params'] = opts.params;
    return (await this._request({
      method: 'POST',
      path: '/v1/synth/generate',
      body,
    })) as Record<string, unknown>;
  }

  /**
   * List the caller's saved synthetic datasets (newest first). Returns
   * metadata only — record payloads are fetched via `getDataset()`.
   * Accepts both a bare array and a `{ datasets: [...] }` envelope.
   */
  async listDatasets(): Promise<Record<string, unknown>[]> {
    const raw = await this._request({ method: 'GET', path: '/v1/synth/datasets' });
    if (Array.isArray(raw)) return raw as Record<string, unknown>[];
    const obj = raw as Record<string, unknown>;
    const items = obj['datasets'];
    return Array.isArray(items) ? (items as Record<string, unknown>[]) : [];
  }

  /** Fetch one saved dataset in full (records + manifest) by id. */
  async getDataset(datasetId: string): Promise<Record<string, unknown>> {
    return (await this._request({
      method: 'GET',
      path: `/v1/synth/datasets/${encodeURIComponent(datasetId)}`,
    })) as Record<string, unknown>;
  }

  /**
   * Export a saved dataset as `"jsonl"` (default), `"json"`, or `"csv"`.
   * Returns the encoded body as a string (the backend streams a file
   * attachment; the SDK surfaces its text).
   */
  async exportDataset(datasetId: string, format = 'jsonl'): Promise<string> {
    return (await this._request({
      method: 'GET',
      path: `/v1/synth/datasets/${encodeURIComponent(datasetId)}/export`,
      query: { format },
      headers: { Accept: '*/*' },
      rawText: true,
    })) as string;
  }

  /** Delete one of the caller's saved datasets by id. */
  async deleteDataset(datasetId: string): Promise<Record<string, unknown>> {
    return (await this._request({
      method: 'DELETE',
      path: `/v1/synth/datasets/${encodeURIComponent(datasetId)}`,
    })) as Record<string, unknown>;
  }

  // ── Canonical projection (v0.4.0) ────────────────────────────

  /**
   * List the canonical normalization targets (frameworks) available for
   * projection, with their pinned versions. Returns the raw
   * `{ frameworks: [...] }` object.
   */
  async listFrameworks(): Promise<Record<string, unknown>> {
    return (await this._request({
      method: 'GET',
      path: '/v1/canonical/frameworks',
    })) as Record<string, unknown>;
  }

  /**
   * Project an already-extracted record onto one or more canonical
   * frameworks. Pure post-processing — no LLM call, no re-extraction.
   * Pass EXACTLY ONE of `templateSlug` / `schema` (the source carrying
   * the StructX concept annotations); the backend enforces this.
   * Returns `{ projections: { <fw>: { record, coverage, valid } } }`.
   */
  async project(
    extracted: Record<string, unknown>,
    frameworks: string[],
    opts: ProjectOptions = {},
  ): Promise<Record<string, unknown>> {
    const body: Record<string, unknown> = { extracted, frameworks };
    if (opts.templateSlug !== undefined) body['template_slug'] = opts.templateSlug;
    if (opts.schema !== undefined) body['schema'] = opts.schema;
    return (await this._request({
      method: 'POST',
      path: '/v1/canonical/project',
      body,
    })) as Record<string, unknown>;
  }

  /**
   * Canonical projection of a template's stored sample — the body-free,
   * cacheable counterpart to `project()`. Projects the template's own
   * `sample_output` onto the given framework(s), or all available
   * targets when `framework` is omitted. Returns the same
   * `{ projections: {...} }` shape.
   */
  async projectTemplate(
    slug: string,
    framework?: string | string[],
  ): Promise<Record<string, unknown>> {
    const fw = Array.isArray(framework) ? framework.join(',') : framework;
    return (await this._request({
      method: 'GET',
      path: `/v1/templates/${encodeURIComponent(slug)}/canonical`,
      query: fw !== undefined ? { framework: fw } : undefined,
    })) as Record<string, unknown>;
  }

  // ── Batch extract (v0.4.0) ───────────────────────────────────

  /**
   * Extract up to 100 items in one request. Thin wrapper over
   * `POST /v1/extract/batch`: each item is metered server-side, so the
   * SDK does NO client-side credit logic. Auth is API-key-only on this
   * endpoint. WRITE path — never retried on 5xx (a partial batch must
   * not be replayed).
   *
   * The response carries `results` (a per-item mix of extraction
   * results and `{ error, code }` objects) plus `total_latency_ms` and
   * `total_credits_used` for caller-side accounting.
   */
  async batchExtract(
    items: BatchExtractItem[],
    opts: BatchExtractOptions = {},
  ): Promise<Record<string, unknown>> {
    const body: Record<string, unknown> = {
      items: items.map((item) => {
        const wire: Record<string, unknown> = {
          content: item.content,
          tier: item.tier ?? 'required',
        };
        if (item.schema !== undefined) wire['schema'] = item.schema;
        if (item.templateSlug !== undefined) wire['template_slug'] = item.templateSlug;
        return wire;
      }),
    };
    if (opts.options !== undefined) body['options'] = extractOptions(opts.options);
    return (await this._request({
      method: 'POST',
      path: '/v1/extract/batch',
      body,
    })) as Record<string, unknown>;
  }

  // ── Template extras (v0.4.0) ─────────────────────────────────

  /**
   * Estimated field count + cost for a tier-filtered extraction against
   * a template. No LLM round-trip — values come from the template's
   * `field_catalog` + token heuristics. `tier` defaults to `"required"`.
   */
  async getTemplateCostEstimate(
    slug: string,
    tier = 'required',
  ): Promise<Record<string, unknown>> {
    return (await this._request({
      method: 'GET',
      path: `/v1/templates/${encodeURIComponent(slug)}/cost-estimate`,
      query: { tier },
    })) as Record<string, unknown>;
  }

  /**
   * Version history for a template family. `slug` is the bare
   * family_slug (not family_slug@version). Returns the raw
   * `{ family_slug, versions: [...] }` object.
   */
  async listTemplateVersions(slug: string): Promise<Record<string, unknown>> {
    return (await this._request({
      method: 'GET',
      path: `/v1/templates/${encodeURIComponent(slug)}/versions`,
    })) as Record<string, unknown>;
  }

  // ── Internal request loop ────────────────────────────────────

  private async _request(opts: RequestOptions): Promise<unknown> {
    const url = this._buildUrl(opts.path, opts.query);
    const headers = this._buildHeaders(opts.headers);

    let lastTransportError: TransportError | null = null;
    const maxAttempts = opts.maxAttempts ?? this.retry.maxAttempts;
    const timeoutMs = opts.timeoutMs ?? this.timeoutMs;
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      const controller = new AbortController();
      const timeoutHandle = setTimeout(() => controller.abort(), timeoutMs);
      let response: Response;
      try {
        response = await this._fetch(url, {
          method: opts.method,
          headers,
          body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
          signal: controller.signal,
        });
      } catch (err: unknown) {
        clearTimeout(timeoutHandle);
        // AbortError from our own timeout, network failure, DNS error,
        // TLS handshake failure — all the same call-site decision: retry
        // or surface as TransportError. The original error type doesn't
        // affect what the caller does.
        const message = err instanceof Error ? `${err.name}: ${err.message}` : String(err);
        lastTransportError = new TransportError(message);
        if (attempt < maxAttempts) {
          await sleep(this.retry.backoffFor(attempt) * 1000);
          continue;
        }
        throw lastTransportError;
      }
      clearTimeout(timeoutHandle);

      // 5xx retry rule — writes never retry on 5xx (see WRITE_PATHS).
      if (
        attempt < maxAttempts
        && isRetryableResponse(opts.path, response.status, this.retry)
      ) {
        const retryAfterHeader = response.headers.get('retry-after');
        const retryAfter = parseRetryAfter(retryAfterHeader);
        await sleep(this.retry.backoffFor(attempt, retryAfter) * 1000);
        continue;
      }

      return await this._parseResponse(response, opts.rawText ?? false);
    }

    // Unreachable — the loop always returns or throws.
    throw lastTransportError ?? new StructXError('retry loop exhausted with no response');
  }

  private _buildUrl(
    path: string,
    query?: Record<string, string | number | boolean | null | undefined>,
  ): string {
    const u = new URL(path.replace(/^\/+/, ''), this.baseUrl);
    if (query) {
      for (const [k, v] of Object.entries(query)) {
        if (v !== undefined && v !== null) u.searchParams.set(k, String(v));
      }
    }
    return u.toString();
  }

  private _buildHeaders(extra?: Record<string, string>): Record<string, string> {
    const headers: Record<string, string> = {
      'X-API-Key': this.apiKey,
      'User-Agent': userAgent(),
      'Accept': 'application/json',
      'Content-Type': 'application/json',
    };
    // Bearer (JWT) credential — used by the JWT-only methods. When both
    // credentials are configured we send both headers and let the backend
    // pick the right one per endpoint.
    if (this.bearerToken) headers['Authorization'] = `Bearer ${this.bearerToken}`;
    return { ...headers, ...this.defaultHeaders, ...(extra ?? {}) };
  }

  /**
   * Guard for the JWT-only methods. Throws a clear SDK-side error (instead
   * of letting the request 401) when no bearer token is configured. These
   * methods (`usage`, and the `/v1/schemas/mine` personal-schema CRUD) hit
   * backend routes guarded by `get_current_user_from_token`, which accepts
   * an `Authorization: Bearer` JWT only — an API key alone gets 401.
   */
  private _requireBearer(method: string): void {
    if (!this.bearerToken) {
      throw new StructXError(
        `${method}() requires a bearer token (JWT). This endpoint authenticates ` +
          `via Authorization: Bearer only — an API key alone is rejected with 401. ` +
          `Pass bearerToken to the StructX constructor or set STRUCTX_BEARER_TOKEN.`,
      );
    }
  }

  /**
   * Translate a raw fetch Response into either the parsed JSON body
   * or an appropriate typed exception. Single source of truth for
   * error mapping.
   */
  private async _parseResponse(response: Response, rawText = false): Promise<unknown> {
    const requestId = response.headers.get('x-request-id') ?? response.headers.get('request-id');

    if (response.status >= 200 && response.status < 300) {
      const text = await response.text();
      // File-export endpoints emit jsonl / csv / json bodies, not a JSON
      // envelope — hand the caller the raw string untouched.
      if (rawText) return text;
      if (!text) return {};
      try {
        return JSON.parse(text);
      } catch (e) {
        throw new ApiError(
          `Server returned a non-JSON 2xx response: ${(e as Error).message}`,
          { statusCode: response.status, requestId },
        );
      }
    }

    // Error path. Backend convention: { error: string, code: string }.
    let body: Record<string, unknown> = {};
    try {
      const text = await response.text();
      if (text) body = JSON.parse(text) as Record<string, unknown>;
    } catch {
      // Body wasn't JSON — fall through with empty body.
    }

    const message = stringOrFallback(
      body['error'],
      response.statusText || `HTTP ${response.status}`,
    );
    const code = stringOrNull(body['code']);

    const ExcClass = ERROR_BY_STATUS.get(response.status)
      ?? (response.status >= 500 ? ServerError : ApiError);

    if (ExcClass === RateLimitError) {
      const retryAfterHeader = response.headers.get('retry-after');
      const retryAfter = parseRetryAfter(retryAfterHeader);
      throw new RateLimitError(message, {
        statusCode: response.status,
        code,
        responseBody: body,
        requestId,
        retryAfter,
        creditsUsed: numberOrNull(body['credits_used']),
        creditsRemaining: numberOrNull(body['credits_remaining']),
      });
    }

    // Construct the typed error. TypeScript can't see that every entry
    // in ERROR_BY_STATUS produces an ApiError-subclass that accepts the
    // standard init; we cast through `unknown` to express the runtime
    // guarantee.
    const Ctor = ExcClass as unknown as new (m: string, init: import('./errors.js').ApiErrorInit) => ApiError;
    throw new Ctor(message, {
      statusCode: response.status,
      code,
      responseBody: body,
      requestId,
    });
  }
}

// ── Small internal helpers ───────────────────────────────────

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Parse a `Retry-After` header value (seconds form). Returns null on
 *  missing / malformed. We don't accept the HTTP-date form because no
 *  endpoint emits it today, and the date form would need timezone
 *  arithmetic we'd rather avoid. */
function parseRetryAfter(value: string | null): number | null {
  if (value === null) return null;
  const n = Number.parseFloat(value);
  return Number.isFinite(n) ? n : null;
}

function stringOrFallback(value: unknown, fallback: string): string {
  return typeof value === 'string' ? value : fallback;
}

function stringOrNull(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

function numberOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

// Re-export the typed error classes so `catch (e instanceof RateLimitError)`
// works from a single import root. `index.ts` does the same at the
// package boundary; this internal re-export keeps test files honest.
export {
  ApiError,
  AuthenticationError,
  NotFoundError,
  PermissionDeniedError,
  RateLimitError,
  ServerError,
  StructXError,
  TransportError,
  ValidationError,
};
