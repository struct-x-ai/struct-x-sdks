/**
 * TypeScript types mirroring the Python SDK's Pydantic models.
 *
 * The Python SDK uses `extra="allow"` so old SDK versions accept new
 * backend fields silently — the same posture here is achieved by
 * leaving every type open via `[key: string]: unknown` index signature
 * on a base interface. New backend fields appear on the runtime object
 * but aren't typed; callers can still reach them via index access if
 * they need to.
 *
 * NO RUNTIME VALIDATION. The Python SDK uses pydantic for both typing
 * AND runtime safety; we deliberately ship only types here to avoid
 * the zod/io-ts dependency. The backend is the source of truth — if
 * the wire format doesn't match, callers see a `TypeError` at the
 * property access, which is the same failure-mode pydantic produces
 * with `extra="allow"`.
 */

/** Base shape that every SDK model extends. Index signature mirrors
 *  pydantic's `extra="allow"` posture — extra fields from the backend
 *  pass through untyped. */
export interface OpenObject {
  [key: string]: unknown;
}

// ── /v1/extract ──────────────────────────────────────────────

export interface FieldConfidence extends OpenObject {
  field: string;
  confidence: number;
  source_snippet?: string | null;
}

export interface TokenCounts extends OpenObject {
  input: number;
  output: number;
}

export interface Extraction extends OpenObject {
  data: Record<string, unknown> | unknown[];
  /**
   * Public quality-tier label: `"fast"`, `"balanced"`, `"quality"`.
   * Never the raw vendor model name — see backend/services/model_router
   * and the credit-pricing-disclosure rule.
   */
  model_used: string;
  latency_ms: number;
  tokens: TokenCounts;
  credits_used: number;
  credits_remaining?: number | null;
  confidence?: number | null;
  field_confidences?: FieldConfidence[];
  cached?: boolean;
  /**
   * Server-assigned extraction id. The backend emits this on the wire as
   * `id` (see backend/models/schemas.py:ExtractionResponse). Use this.
   */
  id?: string | null;
  /**
   * @deprecated The backend never sends `extraction_id` — it sends `id`.
   * This field is always `undefined` at runtime; it is kept only so code
   * written against the pre-0.4.1 SDK still type-checks. Read `id`.
   */
  extraction_id?: string | null;
  warnings?: string[];
  /** `"model"` for document extractions (per-field confidence reported by the
   *  model); absent on text extractions, whose confidence is source-grounded. */
  confidence_source?: string | null;
}

// ── /v1/schemas/infer ────────────────────────────────────────

export interface InferredField extends OpenObject {
  name: string;
  type: string;
  description: string;
  required: boolean;
  rationale: string;
  confidence: number;
}

export interface InferredSchema extends OpenObject {
  json_schema: Record<string, unknown>;
  fields?: InferredField[];
  overall_confidence: number;
  needs_review?: boolean;
}

export interface Recommendation extends OpenObject {
  source_type: 'template' | 'user_schema' | 'inferred_draft';
  source_id: string;
  slug: string;
  name: string;
  score: number;
  json_schema: Record<string, unknown>;
  score_breakdown?: Record<string, number>;
}

export interface InferenceResult extends OpenObject {
  event_id: string;
  inferred: InferredSchema;
  recommendations?: Recommendation[];
  credits_used: number;
  partial?: boolean;
}

// ── /v1/schemas (template gallery) ───────────────────────────

/**
 * One row from GET /v1/schemas. The wire field for the JSON Schema is
 * `"schema"` but TypeScript / JavaScript don't reserve that name the
 * way Pydantic does, so we keep the wire name. Callers do `tpl.schema`
 * directly — no alias dance.
 */
export interface Template extends OpenObject {
  slug: string;
  name: string;
  description?: string | null;
  category?: string | null;
  schema: Record<string, unknown>;
  is_first_party?: boolean;
  usage_count?: number | null;
  acceptance_rate?: number | null;
  avg_confidence?: number | null;
}

// ── /v1/models ───────────────────────────────────────────────

/**
 * One row from GET /v1/models. Admin-only endpoint post audit H6 —
 * non-admin API keys get 403. Most callers don't need this; the
 * backend's router picks per call.
 */
export interface Model extends OpenObject {
  name: string;
  provider?: string;
  capability?: string | null;
  notes?: string | null;
}

// ── /billing/usage (billing router mounts at /billing, no /v1) ──

export interface Usage extends OpenObject {
  credits_used_today: number;
  credits_limit_daily: number;
  tier?: string | null;
  period_start?: string | null;
  period_end?: string | null;
}
