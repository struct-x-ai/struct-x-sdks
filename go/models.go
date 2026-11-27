package structx

import "encoding/json"

// Response models mirror the Python SDK's Pydantic models (_models.py) and the
// backend's response shapes (backend/models/schemas.py) in Go idiom.
//
// Forward compatibility: Go's json.Unmarshal silently ignores unknown fields,
// so when the backend adds a new field, older SDK builds keep decoding without
// error — they simply don't surface it as a typed field. This matches the
// intent of the Python SDK's extra="allow" / the Node SDK's OpenObject index
// signature (no decode break on new backend fields). To read a field the SDK
// doesn't yet type, decode the raw payload yourself (e.g. Extraction.Data is
// already json.RawMessage) or upgrade the SDK.

// ── Extraction ───────────────────────────────────────────────

// FieldConfidence is a per-field confidence score returned by /v1/extract.
// SourceSnippet is populated only when include_citations=true was requested.
type FieldConfidence struct {
	Field         string  `json:"field"`
	Confidence    float64 `json:"confidence"`
	SourceSnippet *string `json:"source_snippet,omitempty"`
}

// TokenCounts is the input/output token accounting for a call.
type TokenCounts struct {
	Input  int `json:"input"`
	Output int `json:"output"`
}

// Extraction is the result of a single /v1/extract call.
//
// Data holds the extracted record as raw JSON — unmarshal it into whatever
// shape your schema produced (an object or an array). ModelUsed is the public
// quality-tier label ("fast" / "balanced" / "quality"), never a raw vendor
// model name.
type Extraction struct {
	Data             json.RawMessage   `json:"data"`
	ModelUsed        string            `json:"model_used"`
	LatencyMs        int               `json:"latency_ms"`
	Tokens           TokenCounts       `json:"tokens"`
	CreditsUsed      int               `json:"credits_used"`
	CreditsRemaining *int              `json:"credits_remaining,omitempty"`
	Confidence       *float64          `json:"confidence,omitempty"`
	FieldConfidences []FieldConfidence `json:"field_confidences,omitempty"`
	Cached           bool              `json:"cached"`
	// ExtractionID is the server-assigned extraction id. The backend emits
	// it on the wire as `id` (see backend/models/schemas.py:ExtractionResponse);
	// an earlier SDK decoded the wrong wire key (`extraction_id`), so this
	// always stayed empty. The field name is kept for source compatibility;
	// only its JSON tag was corrected to `id`.
	ExtractionID string   `json:"id,omitempty"`
	Warnings     []string `json:"warnings,omitempty"`
	// ConfidenceSource is "model" for document extractions (per-field
	// confidence reported by the model); empty for text extractions, whose
	// confidence is source-grounded.
	ConfidenceSource string `json:"confidence_source,omitempty"`
}

// ── Schema inference (recommender) ───────────────────────────

// InferredField is one field of an inferred schema, with its rationale.
type InferredField struct {
	Name        string  `json:"name"`
	Type        string  `json:"type"`
	Description string  `json:"description"`
	Required    bool    `json:"required"`
	Rationale   string  `json:"rationale"`
	Confidence  float64 `json:"confidence"`
}

// InferredSchema is the inferred JSON Schema plus per-field rationale. JSONSchema
// is a valid JSON Schema you can pass straight back to Extract.
type InferredSchema struct {
	JSONSchema        json.RawMessage `json:"json_schema"`
	Fields            []InferredField `json:"fields,omitempty"`
	OverallConfidence float64         `json:"overall_confidence"`
	NeedsReview       bool            `json:"needs_review"`
}

// Recommendation is a template or user-schema candidate from the recommender.
type Recommendation struct {
	SourceType     string             `json:"source_type"`
	SourceID       string             `json:"source_id"`
	Slug           string             `json:"slug"`
	Name           string             `json:"name"`
	Score          float64            `json:"score"`
	JSONSchema     json.RawMessage    `json:"json_schema"`
	ScoreBreakdown map[string]float64 `json:"score_breakdown,omitempty"`
}

// InferenceResult is the full payload from /v1/schemas/infer.
type InferenceResult struct {
	EventID         string           `json:"event_id"`
	Inferred        InferredSchema   `json:"inferred"`
	Recommendations []Recommendation `json:"recommendations,omitempty"`
	CreditsUsed     int              `json:"credits_used"`
	Partial         bool             `json:"partial"`
}

// ── Templates ────────────────────────────────────────────────

// Template is a row from the public template gallery (GET /v1/schemas). The
// JSON Schema is carried in SchemaDef (wire field "schema").
type Template struct {
	SchemaDef      json.RawMessage `json:"schema"`
	Slug           string          `json:"slug"`
	Name           string          `json:"name"`
	Description    *string         `json:"description,omitempty"`
	Category       *string         `json:"category,omitempty"`
	IsFirstParty   *bool           `json:"is_first_party,omitempty"`
	UsageCount     *int            `json:"usage_count,omitempty"`
	AcceptanceRate *float64        `json:"acceptance_rate,omitempty"`
	AvgConfidence  *float64        `json:"avg_confidence,omitempty"`
}

// ── Models / routing info ────────────────────────────────────

// Model is a row from GET /v1/models. Most callers don't need this — the
// backend's router picks a model per call. Operator-only post audit H6;
// non-admin keys get 403 (ErrPermissionDenied).
type Model struct {
	Name       string  `json:"name"`
	Provider   string  `json:"provider"`
	Capability *string `json:"capability,omitempty"`
	Notes      *string `json:"notes,omitempty"`
}

// ── Usage ────────────────────────────────────────────────────

// Usage is the summary returned by /billing/usage (the billing router
// mounts at /billing, no /v1 prefix). PeriodStart/PeriodEnd
// are left as raw strings (they may be ISO-8601 timestamps or absent) to keep
// decoding forward-compatible.
type Usage struct {
	CreditsUsedToday  int     `json:"credits_used_today"`
	CreditsLimitDaily int     `json:"credits_limit_daily"`
	Tier              *string `json:"tier,omitempty"`
	PeriodStart       *string `json:"period_start,omitempty"`
	PeriodEnd         *string `json:"period_end,omitempty"`
}

// ── Batch extraction ─────────────────────────────────────────

// ExtractionErrorItem is one failed item inside a BatchExtraction. The batch
// endpoint never aborts on a single item's failure — it surfaces a structured
// error in-line instead.
type ExtractionErrorItem struct {
	Error      string         `json:"error"`
	Code       string         `json:"code"`
	RetryAfter *int           `json:"retry_after,omitempty"`
	Details    map[string]any `json:"details,omitempty"`
}

// BatchResultItem is one entry in a BatchExtraction.Results list: either a
// successful Extraction or an ExtractionErrorItem. Exactly one of the two
// pointers is non-nil. Use IsError to discriminate.
type BatchResultItem struct {
	Extraction *Extraction
	Error      *ExtractionErrorItem
}

// IsError reports whether this entry is a per-item failure.
func (b BatchResultItem) IsError() bool { return b.Error != nil }

// UnmarshalJSON discriminates a batch result entry by the presence of an
// "error" key (the failure shape) versus an extraction result.
func (b *BatchResultItem) UnmarshalJSON(data []byte) error {
	var probe struct {
		Error *json.RawMessage `json:"error"`
	}
	if err := json.Unmarshal(data, &probe); err != nil {
		return err
	}
	if probe.Error != nil {
		var e ExtractionErrorItem
		if err := json.Unmarshal(data, &e); err != nil {
			return err
		}
		b.Error = &e
		return nil
	}
	var ex Extraction
	if err := json.Unmarshal(data, &ex); err != nil {
		return err
	}
	b.Extraction = &ex
	return nil
}

// BatchExtraction is the result of a /v1/extract/batch call. Results preserves
// input order. TotalCreditsUsed is the server-metered sum across all items —
// the SDK does no client-side accounting.
type BatchExtraction struct {
	Results          []BatchResultItem `json:"results"`
	TotalLatencyMs   int               `json:"total_latency_ms"`
	TotalCreditsUsed int               `json:"total_credits_used"`
}

// ── Canonical projection ─────────────────────────────────────

// Framework is one canonical normalization target from
// /v1/canonical/frameworks — e.g. otel, ocsf, ecs.
type Framework struct {
	Framework string  `json:"framework"`
	Version   string  `json:"version"`
	Kind      *string `json:"kind,omitempty"`
}

// ProjectionOut is a single framework's projection of a record: the reshaped
// Record, a Coverage report of mapped/unmapped fields, and whether the
// projection passed the framework's validation.
type ProjectionOut struct {
	Record   map[string]any `json:"record"`
	Coverage map[string]any `json:"coverage"`
	Valid    bool           `json:"valid"`
}

// CanonicalProjection is the response from /v1/canonical/project and
// /v1/templates/{slug}/canonical: Projections maps each requested framework
// name to its ProjectionOut.
type CanonicalProjection struct {
	Projections map[string]ProjectionOut `json:"projections"`
}
