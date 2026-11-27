package structx

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"runtime"
	"strconv"
	"strings"
	"time"
)

const (
	defaultBaseURL = "https://api.struct-x.ai"
	defaultTimeout = 30 * time.Second
)

// Client is the struct-x API client. Construct one with NewClient. A Client is
// safe for concurrent use by multiple goroutines.
type Client struct {
	apiKey         string
	bearerToken    string
	baseURL        string
	httpClient     *http.Client
	timeout        time.Duration
	retry          RetryPolicy
	defaultHeaders map[string]string
	userAgent      string
}

// Option configures a Client. Pass any number to NewClient.
type Option func(*Client)

// WithBaseURL overrides the API base URL (default: the STRUCTX_BASE_URL env
// var, else https://api.struct-x.ai). Useful for staging or self-hosted.
func WithBaseURL(u string) Option {
	return func(c *Client) {
		if u != "" {
			c.baseURL = u
		}
	}
}

// WithBearerToken sets an optional JWT bearer token — the second credential
// path (default: the STRUCTX_BEARER_TOKEN env var). It is required by the
// JWT-only methods (Usage + the /v1/schemas/mine personal-schema CRUD), whose
// backend routes authenticate via Authorization: Bearer only — an API key is
// rejected with 401. The API-key methods keep using X-API-Key. When both
// credentials are set, both headers are sent and the backend picks per
// endpoint. An empty token is ignored (keeps any env-derived value).
func WithBearerToken(token string) Option {
	return func(c *Client) {
		if token != "" {
			c.bearerToken = token
		}
	}
}

// WithHTTPClient supplies a custom *http.Client (for proxies, custom
// transports, connection tuning, or test servers). The SDK still applies its
// per-request timeout via context unless WithTimeout(0) disables it.
func WithHTTPClient(h *http.Client) Option {
	return func(c *Client) {
		if h != nil {
			c.httpClient = h
		}
	}
}

// WithTimeout sets the per-request timeout, applied via context.WithTimeout on
// every attempt (default 30s). Pass 0 to disable the SDK-applied timeout and
// rely entirely on the caller's context / http.Client.
func WithTimeout(d time.Duration) Option {
	return func(c *Client) { c.timeout = d }
}

// WithMaxRetries sets the maximum number of retries after the initial attempt
// (so total attempts = n + 1). The default is 2 retries (3 attempts). Negative
// values are treated as 0.
func WithMaxRetries(n int) Option {
	return func(c *Client) {
		if n < 0 {
			n = 0
		}
		c.retry.MaxAttempts = n + 1
	}
}

// WithRetryPolicy replaces the entire retry policy. Use this for full control
// over backoff, jitter, and the read-vs-write 5xx rule.
func WithRetryPolicy(p RetryPolicy) Option {
	return func(c *Client) { c.retry = p }
}

// WithDefaultHeaders adds headers sent on every request (e.g. tracing IDs).
// They are merged over the SDK's own headers but cannot remove X-API-Key.
func WithDefaultHeaders(h map[string]string) Option {
	return func(c *Client) {
		for k, v := range h {
			c.defaultHeaders[k] = v
		}
	}
}

// NewClient builds a Client. The API key falls back to the STRUCTX_API_KEY
// environment variable when apiKey is empty; if neither is set, NewClient
// returns an error that matches errors.Is(err, ErrStructX).
func NewClient(apiKey string, opts ...Option) (*Client, error) {
	if apiKey == "" {
		apiKey = os.Getenv("STRUCTX_API_KEY")
	}
	baseURL := os.Getenv("STRUCTX_BASE_URL")
	if baseURL == "" {
		baseURL = defaultBaseURL
	}
	c := &Client{
		apiKey:         apiKey,
		bearerToken:    os.Getenv("STRUCTX_BEARER_TOKEN"),
		baseURL:        baseURL,
		httpClient:     &http.Client{},
		timeout:        defaultTimeout,
		retry:          DefaultRetryPolicy(),
		defaultHeaders: map[string]string{},
	}
	for _, opt := range opts {
		opt(c)
	}
	if c.apiKey == "" {
		return nil, fmt.Errorf(
			"structx: api key is required (pass it to NewClient or set STRUCTX_API_KEY): %w",
			ErrStructX,
		)
	}
	// Guard the zero-value footgun: WithRetryPolicy(RetryPolicy{}) would set
	// MaxAttempts to 0 and make every request a no-op. At least one attempt.
	if c.retry.MaxAttempts < 1 {
		c.retry.MaxAttempts = 1
	}
	c.baseURL = strings.TrimRight(c.baseURL, "/") + "/"
	c.userAgent = fmt.Sprintf(
		"structx-sdk-go/%s Go/%s (%s/%s)",
		Version, runtime.Version(), runtime.GOOS, runtime.GOARCH,
	)
	return c, nil
}

// BaseURL returns the normalized base URL (always ending in "/").
func (c *Client) BaseURL() string { return c.baseURL }

// requireBearer guards the JWT-only methods. It returns a clear SDK-side
// error (instead of letting the request 401) when no bearer token is set.
// These methods (Usage + the /v1/schemas/mine personal-schema CRUD) hit
// backend routes guarded by get_current_user_from_token, which accepts an
// Authorization: Bearer JWT only — an API key alone gets 401.
func (c *Client) requireBearer(method string) error {
	if c.bearerToken == "" {
		return fmt.Errorf(
			"structx: %s requires a bearer token (JWT) — this endpoint "+
				"authenticates via Authorization: Bearer only; an API key alone "+
				"is rejected with 401. Use WithBearerToken(...) or set "+
				"STRUCTX_BEARER_TOKEN: %w",
			method, ErrStructX,
		)
	}
	return nil
}

// ── Public API: core ─────────────────────────────────────────

// ExtractRequest is the input to Extract. Provide EXACTLY ONE of Schema (an
// inline JSON Schema) or TemplateSlug (a catalog template such as
// "logs.stripe.event"); the backend enforces this.
type ExtractRequest struct {
	// Content is the raw HTML, markdown, or text to extract from. Required.
	Content string
	// Schema is an inline JSON Schema defining the output structure.
	Schema json.RawMessage
	// TemplateSlug selects a catalog template instead of an inline schema.
	TemplateSlug string
	// Tier is the field-depth dial ("required" default … "extended").
	Tier string
	// Options is forwarded as-is (include_citations, use_cache, …).
	Options map[string]any
}

// Extract runs a structured extraction against req.Content.
func (c *Client) Extract(ctx context.Context, req ExtractRequest) (*Extraction, error) {
	tier := req.Tier
	if tier == "" {
		tier = "required"
	}
	body := map[string]any{"content": req.Content, "tier": tier}
	if len(req.Schema) > 0 {
		body["schema"] = req.Schema
	}
	if req.TemplateSlug != "" {
		body["template_slug"] = req.TemplateSlug
	}
	if req.Options != nil {
		body["options"] = req.Options
	}
	var out Extraction
	if err := c.doJSON(ctx, http.MethodPost, "/v1/extract", body, nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// documentTimeout covers the server's 120 s per-document limit plus upload.
const documentTimeout = 180 * time.Second

// documentMaxBytes is a client-side guard against an accidental huge read; the
// API itself accepts at most 20 MB, depending on the plan.
const documentMaxBytes = 32 << 20

// ExtractFileRequest is the input to ExtractFile (POST /v1/extract/document).
// Provide EXACTLY ONE of Schema or TemplateSlug.
type ExtractFileRequest struct {
	// Schema is an inline JSON Schema defining the output structure.
	Schema json.RawMessage
	// TemplateSlug selects a catalog template instead of an inline schema.
	TemplateSlug string
	// Model is "auto" (default), "fast", "balanced" or "quality" (paid plans).
	Model string
	// Tier is the field-depth dial, as on Extract.
	Tier string
	// Fields restricts the extraction to these top-level fields.
	Fields []string
	// MaxCredits caps what this document may cost (0 = no cap). A higher
	// estimate is refused (422 max_credits_exceeded) before any work.
	MaxCredits int
	// Options is forwarded as-is (e.g. confidence_threshold).
	Options map[string]any
	// MediaType defaults to "application/pdf", the only type accepted today.
	MediaType string
	// Timeout defaults to 180 s.
	Timeout time.Duration
}

// ExtractFile extracts structured data from a PDF. document is the file's
// bytes. The result has ConfidenceSource "model": document confidence is
// reported per field by the model, not grounded in text.
//
// It is never retried automatically: the server may still finish (and charge)
// a document after the client gives up, so a silent retry could charge twice.
func (c *Client) ExtractFile(ctx context.Context, document []byte, req ExtractFileRequest) (*Extraction, error) {
	if len(document) == 0 {
		return nil, fmt.Errorf("structx: document is empty: %w", ErrStructX)
	}
	if len(document) > documentMaxBytes {
		return nil, fmt.Errorf("structx: document is larger than %d MB; the API accepts at most 20 MB (by plan): %w",
			documentMaxBytes>>20, ErrStructX)
	}
	mediaType := req.MediaType
	if mediaType == "" {
		mediaType = "application/pdf"
	}
	body := map[string]any{
		"document": map[string]any{
			"type":       "base64",
			"media_type": mediaType,
			"data":       base64.StdEncoding.EncodeToString(document),
		},
	}
	if len(req.Schema) > 0 {
		body["schema"] = req.Schema
	}
	if req.TemplateSlug != "" {
		body["template_slug"] = req.TemplateSlug
	}
	if req.Model != "" {
		body["model"] = req.Model
	}
	if req.Tier != "" {
		body["tier"] = req.Tier
	}
	if len(req.Fields) > 0 {
		body["fields"] = req.Fields
	}
	if req.MaxCredits > 0 {
		body["max_credits"] = req.MaxCredits
	}
	if req.Options != nil {
		body["options"] = req.Options
	}
	timeout := req.Timeout
	if timeout <= 0 {
		timeout = documentTimeout
	}
	data, err := c.requestWith(ctx, http.MethodPost, "/v1/extract/document", body, nil, nil, timeout, 1)
	if err != nil {
		return nil, err
	}
	var out Extraction
	if err := json.Unmarshal(data, &out); err != nil {
		return nil, fmt.Errorf("structx: failed to decode response: %w", ErrStructX)
	}
	return &out, nil
}

// InferSchemaRequest is the input to InferSchema.
type InferSchemaRequest struct {
	// Content is the raw content to infer a schema from. Required.
	Content string
	// ContentType optionally hints the input format ("html", "markdown", …).
	ContentType string
	// Hints is optional generation guidance forwarded as-is.
	Hints map[string]any
	// K is the number of template recommendations to return (default 5).
	K int
	// ReturnRecommendations toggles the recommendation pass (default true).
	// nil means true; set to a pointer to false to disable.
	ReturnRecommendations *bool
}

// InferSchema infers a JSON Schema from raw content and optionally returns
// matching template recommendations. Billed against the API key's quota.
func (c *Client) InferSchema(ctx context.Context, req InferSchemaRequest) (*InferenceResult, error) {
	k := req.K
	if k == 0 {
		k = 5
	}
	returnRecs := true
	if req.ReturnRecommendations != nil {
		returnRecs = *req.ReturnRecommendations
	}
	body := map[string]any{
		"content":                req.Content,
		"k":                      k,
		"return_recommendations": returnRecs,
	}
	if req.ContentType != "" {
		body["content_type"] = req.ContentType
	}
	if req.Hints != nil {
		body["hints"] = req.Hints
	}
	var out InferenceResult
	if err := c.doJSON(ctx, http.MethodPost, "/v1/schemas/infer", body, nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ListTemplates returns the public template gallery (GET /v1/schemas).
func (c *Client) ListTemplates(ctx context.Context) ([]Template, error) {
	data, err := c.request(ctx, http.MethodGet, "/v1/schemas", nil, nil, nil)
	if err != nil {
		return nil, err
	}
	return decodeList[Template](data, "templates")
}

// ListModels returns the available models (GET /v1/models). Operator-only post
// audit H6 — non-admin keys get 403 (errors.Is(err, ErrPermissionDenied)).
func (c *Client) ListModels(ctx context.Context) ([]Model, error) {
	data, err := c.request(ctx, http.MethodGet, "/v1/models", nil, nil, nil)
	if err != nil {
		return nil, err
	}
	return decodeList[Model](data, "models")
}

// Usage returns current credit usage for the authenticated user
// (GET /billing/usage — the billing router mounts at /billing, no /v1).
//
// Bearer-token method: authenticated via JWT Bearer only. Set a token with
// WithBearerToken(...) or STRUCTX_BEARER_TOKEN, or Usage returns an error
// (matching errors.Is(err, ErrStructX)) before making a request.
func (c *Client) Usage(ctx context.Context) (*Usage, error) {
	if err := c.requireBearer("Usage"); err != nil {
		return nil, err
	}
	var out Usage
	if err := c.doJSON(ctx, http.MethodGet, "/billing/usage", nil, nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ── Public API: batch extraction ─────────────────────────────

// BatchItem is one item in a BatchExtractRequest. Provide EXACTLY ONE of
// Schema or TemplateSlug per item — same contract as ExtractRequest.
type BatchItem struct {
	Content      string
	Schema       json.RawMessage
	TemplateSlug string
	Tier         string
}

// BatchExtractRequest is the input to BatchExtract (up to 100 items).
type BatchExtractRequest struct {
	// Items are the per-item extraction requests. 1..100.
	Items []BatchItem
	// Options is a batch-wide override forwarded as-is (per-item options are
	// ignored by the backend).
	Options map[string]any
	// OutputFormat controls batch-level serialization ("json" / "ndjson" / "csv").
	OutputFormat string
	// CSVArrayMode controls CSV list rendering ("json" / "join" / "explode").
	CSVArrayMode string
}

// BatchExtract extracts up to 100 items in one call (POST /v1/extract/batch).
// Items are metered individually server-side; this thin wrapper surfaces the
// batch totals with no client-side credit logic. Like Extract, it is a billed
// write path — retried only on transport errors, never on a 5xx.
func (c *Client) BatchExtract(ctx context.Context, req BatchExtractRequest) (*BatchExtraction, error) {
	items := make([]map[string]any, 0, len(req.Items))
	for _, it := range req.Items {
		tier := it.Tier
		if tier == "" {
			tier = "required"
		}
		wire := map[string]any{"content": it.Content, "tier": tier}
		if len(it.Schema) > 0 {
			wire["schema"] = it.Schema
		}
		if it.TemplateSlug != "" {
			wire["template_slug"] = it.TemplateSlug
		}
		items = append(items, wire)
	}
	body := map[string]any{"items": items}
	if req.Options != nil {
		body["options"] = req.Options
	}
	if req.OutputFormat != "" {
		body["output_format"] = req.OutputFormat
	}
	if req.CSVArrayMode != "" {
		body["csv_array_mode"] = req.CSVArrayMode
	}
	var out BatchExtraction
	if err := c.doJSON(ctx, http.MethodPost, "/v1/extract/batch", body, nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ── Public API: catalog browsing ─────────────────────────────

// ListCategories returns the category → family → vendor hierarchy used by the
// public template catalog. Raw object; the shape is still evolving.
func (c *Client) ListCategories(ctx context.Context) (map[string]any, error) {
	return c.doMap(ctx, http.MethodGet, "/v1/templates/categories", nil, nil)
}

// GetTemplate fetches one template's full JSON Schema + metadata by slug.
func (c *Client) GetTemplate(ctx context.Context, slug string) (map[string]any, error) {
	return c.doMap(ctx, http.MethodGet, "/v1/templates/"+url.PathEscape(slug), nil, nil)
}

// ── Public API: personal schemas ─────────────────────────────

// ListMySchemas lists schemas the calling user has saved to their account.
// Bearer-token method (/v1/schemas/mine is JWT-only).
func (c *Client) ListMySchemas(ctx context.Context) ([]map[string]any, error) {
	if err := c.requireBearer("ListMySchemas"); err != nil {
		return nil, err
	}
	data, err := c.request(ctx, http.MethodGet, "/v1/schemas/mine", nil, nil, nil)
	if err != nil {
		return nil, err
	}
	return decodeList[map[string]any](data, "schemas")
}

// GetMySchema fetches one of the caller's saved schemas by slug.
// Bearer-token method (/v1/schemas/mine is JWT-only).
func (c *Client) GetMySchema(ctx context.Context, slug string) (map[string]any, error) {
	if err := c.requireBearer("GetMySchema"); err != nil {
		return nil, err
	}
	return c.doMap(ctx, http.MethodGet, "/v1/schemas/mine/"+url.PathEscape(slug), nil, nil)
}

// SaveSchemaParams is the input to SaveMySchema. Name and JSONSchema are required.
type SaveSchemaParams struct {
	Name               string
	JSONSchema         json.RawMessage
	Slug               string
	Description        string
	Category           string
	PublishableConsent bool
}

// SaveMySchema persists a new JSON Schema to the caller's account.
// Bearer-token method (/v1/schemas/mine is JWT-only).
func (c *Client) SaveMySchema(ctx context.Context, params SaveSchemaParams) (map[string]any, error) {
	if err := c.requireBearer("SaveMySchema"); err != nil {
		return nil, err
	}
	body := map[string]any{
		"name":                params.Name,
		"json_schema":         params.JSONSchema,
		"publishable_consent": params.PublishableConsent,
	}
	if params.Slug != "" {
		body["slug"] = params.Slug
	}
	if params.Description != "" {
		body["description"] = params.Description
	}
	if params.Category != "" {
		body["category"] = params.Category
	}
	return c.doMap(ctx, http.MethodPost, "/v1/schemas/mine", body, nil)
}

// SchemaUpdate carries a partial update for UpdateMySchema. Only the non-nil /
// non-empty fields are sent, so unspecified fields are left unchanged.
type SchemaUpdate struct {
	Name               *string
	Description        *string
	Category           *string
	JSONSchema         json.RawMessage
	PublishableConsent *bool
}

// UpdateMySchema patches one of the caller's schemas.
// Bearer-token method (/v1/schemas/mine is JWT-only).
func (c *Client) UpdateMySchema(ctx context.Context, slug string, update SchemaUpdate) (map[string]any, error) {
	if err := c.requireBearer("UpdateMySchema"); err != nil {
		return nil, err
	}
	body := map[string]any{}
	if update.Name != nil {
		body["name"] = *update.Name
	}
	if update.Description != nil {
		body["description"] = *update.Description
	}
	if update.Category != nil {
		body["category"] = *update.Category
	}
	if len(update.JSONSchema) > 0 {
		body["json_schema"] = update.JSONSchema
	}
	if update.PublishableConsent != nil {
		body["publishable_consent"] = *update.PublishableConsent
	}
	return c.doMap(ctx, http.MethodPatch, "/v1/schemas/mine/"+url.PathEscape(slug), body, nil)
}

// DeleteMySchema permanently deletes one of the caller's saved schemas.
// Bearer-token method (/v1/schemas/mine is JWT-only).
func (c *Client) DeleteMySchema(ctx context.Context, slug string) error {
	if err := c.requireBearer("DeleteMySchema"); err != nil {
		return err
	}
	_, err := c.request(ctx, http.MethodDelete, "/v1/schemas/mine/"+url.PathEscape(slug), nil, nil, nil)
	return err
}

// ── Public API: schema lifecycle ─────────────────────────────

// RecommendParams is the input to RecommendTemplates. Provide at least one of
// Content or DraftSchema.
type RecommendParams struct {
	Content     string
	DraftSchema json.RawMessage
	K           int
}

// RecommendTemplates finds existing templates that match content / a draft
// schema. Returns an event_id usable with AugmentSchema.
func (c *Client) RecommendTemplates(ctx context.Context, params RecommendParams) (map[string]any, error) {
	k := params.K
	if k == 0 {
		k = 5
	}
	body := map[string]any{"k": k}
	if params.Content != "" {
		body["content"] = params.Content
	}
	if len(params.DraftSchema) > 0 {
		body["draft_schema"] = params.DraftSchema
	}
	return c.doMap(ctx, http.MethodPost, "/v1/schemas/recommend", body, nil)
}

// AugmentSchema refines a schema from a prior infer/recommend call using
// natural-language guidance. eventID comes from InferSchema / RecommendTemplates.
func (c *Client) AugmentSchema(ctx context.Context, eventID, guidance string) (map[string]any, error) {
	body := map[string]any{"decision": "augment", "augment_guidance": guidance}
	path := "/v1/schemas/recommendations/" + url.PathEscape(eventID) + "/feedback"
	return c.doMap(ctx, http.MethodPost, path, body, nil)
}

// ── Public API: community ────────────────────────────────────

// WithdrawalParams is the input to RequestWithdrawal.
type WithdrawalParams struct {
	// Mode is "fork_detach" (preserve forks) or "legal_erasure" (delete forks too).
	Mode string
	// Justification is visible to admin reviewers.
	Justification string
}

// RequestWithdrawal requests removal of a community template the caller authored.
func (c *Client) RequestWithdrawal(ctx context.Context, slug string, params WithdrawalParams) (map[string]any, error) {
	body := map[string]any{"mode": params.Mode, "justification": params.Justification}
	return c.doMap(ctx, http.MethodPost, "/v1/templates/"+url.PathEscape(slug)+"/withdraw", body, nil)
}

// ── Public API: synthetic data ───────────────────────────────

// SynthesizeParams is the input to Synthesize. Provide EXACTLY ONE of
// TemplateSlug or JSONSchema.
type SynthesizeParams struct {
	TemplateSlug string
	JSONSchema   json.RawMessage
	// Count is the number of records to produce (default 10).
	Count int
	// Directives is free-text guidance shaping the dataset.
	Directives string
	// Params is generator knobs forwarded as-is.
	Params map[string]any
	// Mode is "hybrid" (default), "llm", or "deterministic".
	Mode string
}

// Synthesize generates an anonymized, schema-valid synthetic dataset
// (POST /v1/synth/generate). Billed against the API key's quota; a write path
// (never retried on 5xx). Returns the raw {records, manifest} object.
func (c *Client) Synthesize(ctx context.Context, params SynthesizeParams) (map[string]any, error) {
	count := params.Count
	if count == 0 {
		count = 10
	}
	mode := params.Mode
	if mode == "" {
		mode = "hybrid"
	}
	body := map[string]any{"count": count, "mode": mode}
	if params.TemplateSlug != "" {
		body["template_slug"] = params.TemplateSlug
	}
	if len(params.JSONSchema) > 0 {
		body["json_schema"] = params.JSONSchema
	}
	if params.Directives != "" {
		body["directives"] = params.Directives
	}
	if params.Params != nil {
		body["params"] = params.Params
	}
	return c.doMap(ctx, http.MethodPost, "/v1/synth/generate", body, nil)
}

// ListSynthDatasets lists the caller's saved synthetic datasets, newest first
// (GET /v1/synth/datasets). Metadata only. limit <= 0 defaults to 100.
func (c *Client) ListSynthDatasets(ctx context.Context, limit int) (map[string]any, error) {
	if limit <= 0 {
		limit = 100
	}
	q := url.Values{}
	q.Set("limit", strconv.Itoa(limit))
	return c.doMap(ctx, http.MethodGet, "/v1/synth/datasets", nil, q)
}

// GetSynthDataset fetches one saved synthetic dataset in full (records + manifest).
func (c *Client) GetSynthDataset(ctx context.Context, datasetID string) (map[string]any, error) {
	return c.doMap(ctx, http.MethodGet, "/v1/synth/datasets/"+url.PathEscape(datasetID), nil, nil)
}

// ExportSynthDataset downloads a saved synthetic dataset as raw bytes. format
// is one of "json", "jsonl" (default), or "csv".
func (c *Client) ExportSynthDataset(ctx context.Context, datasetID, format string) ([]byte, error) {
	if format == "" {
		format = "jsonl"
	}
	q := url.Values{}
	q.Set("format", format)
	path := "/v1/synth/datasets/" + url.PathEscape(datasetID) + "/export"
	return c.request(ctx, http.MethodGet, path, nil, q, map[string]string{"Accept": "*/*"})
}

// DeleteSynthDataset deletes one of the caller's saved synthetic datasets.
func (c *Client) DeleteSynthDataset(ctx context.Context, datasetID string) (map[string]any, error) {
	return c.doMap(ctx, http.MethodDelete, "/v1/synth/datasets/"+url.PathEscape(datasetID), nil, nil)
}

// ── Public API: canonical projection ─────────────────────────

// ProjectParams selects the annotation source for ProjectCanonical. Provide
// EXACTLY ONE of TemplateSlug or Schema.
type ProjectParams struct {
	TemplateSlug string
	Schema       json.RawMessage
}

// ListCanonicalFrameworks lists the canonical normalization targets available
// for projection (GET /v1/canonical/frameworks).
func (c *Client) ListCanonicalFrameworks(ctx context.Context) ([]Framework, error) {
	data, err := c.request(ctx, http.MethodGet, "/v1/canonical/frameworks", nil, nil, nil)
	if err != nil {
		return nil, err
	}
	return decodeList[Framework](data, "frameworks")
}

// ProjectCanonical projects an already-extracted record onto one or more
// canonical frameworks (POST /v1/canonical/project). extracted is a record —
// e.g. the decoded Data of an Extract result. Deterministic post-processing:
// it never re-runs the model and reports unmapped fields rather than inventing them.
func (c *Client) ProjectCanonical(ctx context.Context, extracted any, frameworks []string, params ProjectParams) (*CanonicalProjection, error) {
	body := map[string]any{"extracted": extracted, "frameworks": frameworks}
	if params.TemplateSlug != "" {
		body["template_slug"] = params.TemplateSlug
	}
	if len(params.Schema) > 0 {
		body["schema"] = params.Schema
	}
	var out CanonicalProjection
	if err := c.doJSON(ctx, http.MethodPost, "/v1/canonical/project", body, nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// GetTemplateCanonical projects a template's stored sample onto canonical
// framework(s) (GET /v1/templates/{slug}/canonical). framework is a
// comma-separated list (e.g. "ocsf,ecs"); pass "" for all targets.
func (c *Client) GetTemplateCanonical(ctx context.Context, slug, framework string) (*CanonicalProjection, error) {
	var q url.Values
	if framework != "" {
		q = url.Values{}
		q.Set("framework", framework)
	}
	var out CanonicalProjection
	path := "/v1/templates/" + url.PathEscape(slug) + "/canonical"
	if err := c.doJSON(ctx, http.MethodGet, path, nil, q, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ── Public API: template extras ──────────────────────────────

// GetTemplateCostEstimate returns the estimated field count + cost for a
// tier-filtered extraction of a template. No LLM round-trip. tier defaults to
// "required".
func (c *Client) GetTemplateCostEstimate(ctx context.Context, slug, tier string) (map[string]any, error) {
	if tier == "" {
		tier = "required"
	}
	q := url.Values{}
	q.Set("tier", tier)
	return c.doMap(ctx, http.MethodGet, "/v1/templates/"+url.PathEscape(slug)+"/cost-estimate", nil, q)
}

// ListTemplateVersions returns the version history for a template family. slug
// is the bare family_slug (not family_slug@version).
func (c *Client) ListTemplateVersions(ctx context.Context, slug string) (map[string]any, error) {
	return c.doMap(ctx, http.MethodGet, "/v1/templates/"+url.PathEscape(slug)+"/versions", nil, nil)
}

// ── Internal request machinery ───────────────────────────────

// doJSON performs a request and decodes a 2xx body into out (which may be nil
// for endpoints that return no body).
func (c *Client) doJSON(ctx context.Context, method, path string, body any, query url.Values, out any) error {
	data, err := c.request(ctx, method, path, body, query, nil)
	if err != nil {
		return err
	}
	if out == nil || len(bytes.TrimSpace(data)) == 0 {
		return nil
	}
	if err := json.Unmarshal(data, out); err != nil {
		return fmt.Errorf("structx: failed to decode response: %w", ErrStructX)
	}
	return nil
}

// doMap is doJSON specialized to the endpoints that return an arbitrary JSON
// object (the untyped surface the Python/Node SDKs return as dict / Record).
func (c *Client) doMap(ctx context.Context, method, path string, body any, query url.Values) (map[string]any, error) {
	data, err := c.request(ctx, method, path, body, query, nil)
	if err != nil {
		return nil, err
	}
	if len(bytes.TrimSpace(data)) == 0 {
		return nil, nil
	}
	var out map[string]any
	if err := json.Unmarshal(data, &out); err != nil {
		return nil, fmt.Errorf("structx: failed to decode response: %w", ErrStructX)
	}
	return out, nil
}

// request runs the retry loop and returns the raw 2xx body bytes, or a typed
// error (*APIError / *TransportError) on failure.
func (c *Client) request(ctx context.Context, method, path string, body any, query url.Values, extraHeaders map[string]string) ([]byte, error) {
	return c.requestWith(ctx, method, path, body, query, extraHeaders, c.timeout, c.retry.MaxAttempts)
}

// requestWith is request with a per-call timeout and attempt cap.
func (c *Client) requestWith(ctx context.Context, method, path string, body any, query url.Values,
	extraHeaders map[string]string, timeout time.Duration, maxAttempts int) ([]byte, error) {
	var bodyBytes []byte
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return nil, fmt.Errorf("structx: failed to encode request body: %w", ErrStructX)
		}
		bodyBytes = b
	}
	fullURL := c.buildURL(path, query)

	var lastErr error
	for attempt := 1; attempt <= maxAttempts; attempt++ {
		reqCtx := ctx
		var cancel context.CancelFunc
		if timeout > 0 {
			reqCtx, cancel = context.WithTimeout(ctx, timeout)
		}

		var reader io.Reader
		if bodyBytes != nil {
			reader = bytes.NewReader(bodyBytes)
		}
		req, err := http.NewRequestWithContext(reqCtx, method, fullURL, reader)
		if err != nil {
			if cancel != nil {
				cancel()
			}
			return nil, fmt.Errorf("structx: failed to build request: %w", ErrStructX)
		}
		c.applyHeaders(req, bodyBytes != nil, extraHeaders)

		resp, err := c.httpClient.Do(req)
		if err != nil {
			if cancel != nil {
				cancel()
			}
			te := &TransportError{Message: err.Error(), cause: err}
			lastErr = te
			if attempt < maxAttempts {
				if serr := sleepWithContext(ctx, c.retry.backoffFor(attempt, -1)); serr != nil {
					return nil, &TransportError{Message: serr.Error(), cause: serr}
				}
				continue
			}
			return nil, te
		}

		data, readErr := io.ReadAll(resp.Body)
		resp.Body.Close()
		if cancel != nil {
			cancel()
		}
		if readErr != nil {
			te := &TransportError{Message: "reading response body: " + readErr.Error(), cause: readErr}
			lastErr = te
			if attempt < maxAttempts {
				if serr := sleepWithContext(ctx, c.retry.backoffFor(attempt, -1)); serr != nil {
					return nil, &TransportError{Message: serr.Error(), cause: serr}
				}
				continue
			}
			return nil, te
		}

		if attempt < maxAttempts && isRetryableResponse(path, resp.StatusCode, c.retry) {
			ra := parseRetryAfter(resp.Header.Get("Retry-After"))
			if serr := sleepWithContext(ctx, c.retry.backoffFor(attempt, ra)); serr != nil {
				return nil, &TransportError{Message: serr.Error(), cause: serr}
			}
			continue
		}

		return parseResponse(resp.StatusCode, resp.Header, data)
	}

	if lastErr != nil {
		return nil, lastErr
	}
	return nil, fmt.Errorf("structx: retry loop exhausted with no response: %w", ErrStructX)
}

func (c *Client) buildURL(path string, query url.Values) string {
	u := c.baseURL + strings.TrimLeft(path, "/")
	if len(query) > 0 {
		u += "?" + query.Encode()
	}
	return u
}

func (c *Client) applyHeaders(req *http.Request, hasBody bool, extra map[string]string) {
	req.Header.Set("X-API-Key", c.apiKey)
	// Bearer (JWT) credential — used by the JWT-only methods. When both
	// credentials are set we send both headers and let the backend pick the
	// right one per endpoint.
	if c.bearerToken != "" {
		req.Header.Set("Authorization", "Bearer "+c.bearerToken)
	}
	req.Header.Set("User-Agent", c.userAgent)
	req.Header.Set("Accept", "application/json")
	if hasBody {
		req.Header.Set("Content-Type", "application/json")
	}
	for k, v := range c.defaultHeaders {
		req.Header.Set(k, v)
	}
	for k, v := range extra {
		req.Header.Set(k, v)
	}
}

// parseResponse maps a raw HTTP response into the 2xx body bytes or a typed
// error. Single source of truth for the {"error","code"} → typed-error mapping.
func parseResponse(status int, header http.Header, data []byte) ([]byte, error) {
	requestID := header.Get("X-Request-Id")
	if requestID == "" {
		requestID = header.Get("Request-Id")
	}
	if status >= 200 && status < 300 {
		return data, nil
	}

	var body map[string]any
	if len(bytes.TrimSpace(data)) > 0 {
		_ = json.Unmarshal(data, &body) // best-effort; error body may not be JSON
	}

	message := ""
	if body != nil {
		if s, ok := body["error"].(string); ok {
			message = s
		}
	}
	if message == "" {
		if t := strings.TrimSpace(string(data)); t != "" {
			message = t
		} else {
			message = http.StatusText(status)
		}
	}
	code := ""
	if body != nil {
		if s, ok := body["code"].(string); ok {
			code = s
		}
	}

	apiErr := &APIError{
		Message:      message,
		StatusCode:   status,
		Code:         code,
		RequestID:    requestID,
		ResponseBody: redactBody(body),
		kind:         kindForStatus(status),
	}
	if status == http.StatusTooManyRequests {
		if ra := parseRetryAfter(header.Get("Retry-After")); ra >= 0 {
			secs := ra.Seconds()
			apiErr.RetryAfter = &secs
		}
		apiErr.CreditsUsed = intPtrFromBody(body, "credits_used")
		apiErr.CreditsRemaining = intPtrFromBody(body, "credits_remaining")
	}
	return nil, apiErr
}

// intPtrFromBody extracts an integer field from a JSON-decoded body (where
// numbers arrive as float64).
func intPtrFromBody(body map[string]any, key string) *int {
	if body == nil {
		return nil
	}
	v, ok := body[key]
	if !ok {
		return nil
	}
	switch n := v.(type) {
	case float64:
		i := int(n)
		return &i
	case int:
		return &n
	case json.Number:
		if i, err := n.Int64(); err == nil {
			x := int(i)
			return &x
		}
	}
	return nil
}

// decodeList decodes a response that may be either a bare JSON array or an
// object wrapping the array under wrapperKey (e.g. {"templates": [...]}).
func decodeList[T any](data []byte, wrapperKey string) ([]T, error) {
	trimmed := bytes.TrimSpace(data)
	if len(trimmed) == 0 {
		return nil, nil
	}
	if trimmed[0] == '[' {
		var out []T
		if err := json.Unmarshal(trimmed, &out); err != nil {
			return nil, fmt.Errorf("structx: failed to decode response: %w", ErrStructX)
		}
		return out, nil
	}
	var wrapper map[string]json.RawMessage
	if err := json.Unmarshal(trimmed, &wrapper); err != nil {
		return nil, fmt.Errorf("structx: failed to decode response: %w", ErrStructX)
	}
	raw, ok := wrapper[wrapperKey]
	if !ok {
		return nil, nil
	}
	var out []T
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, fmt.Errorf("structx: failed to decode response: %w", ErrStructX)
	}
	return out, nil
}
