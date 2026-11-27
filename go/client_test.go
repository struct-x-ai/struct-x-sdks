package structx

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// fastRetry is a retry policy with near-zero, deterministic backoff for tests.
func fastRetry(maxAttempts int) RetryPolicy {
	p := DefaultRetryPolicy()
	p.MaxAttempts = maxAttempts
	p.InitialBackoff = time.Millisecond
	p.MaxBackoff = time.Millisecond
	p.Jitter = 0
	return p
}

func mustClient(t *testing.T, serverURL string, opts ...Option) *Client {
	t.Helper()
	all := append([]Option{WithBaseURL(serverURL)}, opts...)
	c, err := NewClient("sx_test", all...)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return c
}

// ── Construction ─────────────────────────────────────────────

func TestNewClient_RequiresAPIKey(t *testing.T) {
	t.Setenv("STRUCTX_API_KEY", "")
	t.Setenv("STRUCTX_BASE_URL", "")
	_, err := NewClient("")
	if err == nil {
		t.Fatal("expected an error when no api key is provided")
	}
	if !errors.Is(err, ErrStructX) {
		t.Fatalf("error should match ErrStructX, got %v", err)
	}
}

func TestNewClient_EnvFallbackAndOverride(t *testing.T) {
	t.Setenv("STRUCTX_API_KEY", "sx_from_env")
	c, err := NewClient("")
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if c.apiKey != "sx_from_env" {
		t.Fatalf("apiKey = %q, want sx_from_env", c.apiKey)
	}
	c2, err := NewClient("sx_explicit")
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if c2.apiKey != "sx_explicit" {
		t.Fatalf("explicit apiKey should win, got %q", c2.apiKey)
	}
}

func TestNewClient_BaseURLNormalization(t *testing.T) {
	t.Setenv("STRUCTX_BASE_URL", "")
	c, err := NewClient("sx_x", WithBaseURL("https://x.example///"))
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if c.BaseURL() != "https://x.example/" {
		t.Fatalf("BaseURL = %q, want https://x.example/", c.BaseURL())
	}
	def, _ := NewClient("sx_x")
	if def.BaseURL() != "https://api.struct-x.ai/" {
		t.Fatalf("default BaseURL = %q, want https://api.struct-x.ai/", def.BaseURL())
	}
}

func TestWithMaxRetries(t *testing.T) {
	c, _ := NewClient("sx_x", WithMaxRetries(5))
	if c.retry.MaxAttempts != 6 {
		t.Fatalf("MaxAttempts = %d, want 6 (5 retries + 1)", c.retry.MaxAttempts)
	}
}

// ── Extract happy path + wire shape ──────────────────────────

func TestExtract_HappyPathAndWireShape(t *testing.T) {
	var gotBody map[string]any
	var gotMethod, gotPath, gotAPIKey, gotUA, gotCT string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotMethod = r.Method
		gotPath = r.URL.Path
		gotAPIKey = r.Header.Get("X-API-Key")
		gotUA = r.Header.Get("User-Agent")
		gotCT = r.Header.Get("Content-Type")
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"data":{"title":"Widget","price_cents":9900},"model_used":"fast","latency_ms":420,"tokens":{"input":1250,"output":85},"credits_used":2,"cached":false}`)
	}))
	defer srv.Close()

	c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()))
	res, err := c.Extract(context.Background(), ExtractRequest{
		Content: "<div>$99 Widget</div>",
		Schema:  json.RawMessage(`{"type":"object","properties":{"title":{"type":"string"}}}`),
	})
	if err != nil {
		t.Fatalf("Extract: %v", err)
	}

	if res.ModelUsed != "fast" || res.CreditsUsed != 2 {
		t.Fatalf("unexpected result: model_used=%q credits_used=%d", res.ModelUsed, res.CreditsUsed)
	}
	var data map[string]any
	if err := json.Unmarshal(res.Data, &data); err != nil {
		t.Fatalf("decode data: %v", err)
	}
	if data["title"] != "Widget" {
		t.Fatalf("data.title = %v, want Widget", data["title"])
	}
	if res.Tokens.Input != 1250 || res.Tokens.Output != 85 {
		t.Fatalf("tokens = %+v", res.Tokens)
	}

	// Wire shape.
	if gotMethod != http.MethodPost {
		t.Fatalf("method = %s, want POST", gotMethod)
	}
	if gotPath != "/v1/extract" {
		t.Fatalf("path = %s, want /v1/extract", gotPath)
	}
	if gotAPIKey != "sx_test" {
		t.Fatalf("X-API-Key = %q, want sx_test", gotAPIKey)
	}
	if gotCT != "application/json" {
		t.Fatalf("Content-Type = %q, want application/json", gotCT)
	}
	if !strings.HasPrefix(gotUA, "structx-sdk-go/"+Version) {
		t.Fatalf("User-Agent = %q, want prefix structx-sdk-go/%s", gotUA, Version)
	}
	if gotBody["tier"] != "required" {
		t.Fatalf("tier = %v, want required (default)", gotBody["tier"])
	}
	if _, ok := gotBody["schema"]; !ok {
		t.Fatal("body missing schema")
	}
	if _, ok := gotBody["template_slug"]; ok {
		t.Fatal("body should not contain template_slug")
	}
}

// ── Error mapping by status code ─────────────────────────────

func statusServer(t *testing.T, status int, body string, headers map[string]string) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		for k, v := range headers {
			w.Header().Set(k, v)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_, _ = io.WriteString(w, body)
	}))
}

func TestErrorMapping(t *testing.T) {
	cases := []struct {
		name     string
		status   int
		sentinel error
	}{
		{"401 auth", 401, ErrAuthentication},
		{"403 permission", 403, ErrPermissionDenied},
		{"404 not found", 404, ErrNotFound},
		{"400 validation", 400, ErrValidation},
		{"422 validation", 422, ErrValidation},
		{"500 server", 500, ErrServer},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := statusServer(t, tc.status, `{"error":"nope","code":"nope_code"}`, nil)
			defer srv.Close()
			c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()), WithRetryPolicy(fastRetry(1)))

			_, err := c.Extract(context.Background(), ExtractRequest{
				Content: "x",
				Schema:  json.RawMessage(`{"type":"object"}`),
			})
			if err == nil {
				t.Fatal("expected an error")
			}
			if !errors.Is(err, tc.sentinel) {
				t.Fatalf("errors.Is(_, %v) = false; err = %v", tc.sentinel, err)
			}
			// Every API error also matches the ErrAPI and ErrStructX bases.
			if !errors.Is(err, ErrAPI) || !errors.Is(err, ErrStructX) {
				t.Fatalf("API error should match ErrAPI and ErrStructX; err = %v", err)
			}
			var apiErr *APIError
			if !errors.As(err, &apiErr) {
				t.Fatalf("errors.As(*APIError) failed; err = %v", err)
			}
			if apiErr.StatusCode != tc.status {
				t.Fatalf("StatusCode = %d, want %d", apiErr.StatusCode, tc.status)
			}
			if apiErr.Code != "nope_code" {
				t.Fatalf("Code = %q, want nope_code", apiErr.Code)
			}
		})
	}
}

func TestExtract_401IsTyped(t *testing.T) {
	srv := statusServer(t, 401, `{"error":"bad key","code":"invalid_api_key"}`,
		map[string]string{"x-request-id": "req_abc"})
	defer srv.Close()
	c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()), WithRetryPolicy(fastRetry(1)))

	_, err := c.Extract(context.Background(), ExtractRequest{Content: "x", Schema: json.RawMessage(`{}`)})
	if !errors.Is(err, ErrAuthentication) {
		t.Fatalf("expected ErrAuthentication, got %v", err)
	}
	var apiErr *APIError
	errors.As(err, &apiErr)
	if apiErr.RequestID != "req_abc" {
		t.Fatalf("RequestID = %q, want req_abc", apiErr.RequestID)
	}
	if strings.Contains(apiErr.Error(), "response_body") {
		t.Fatal("Error() must not include the body")
	}
}

func TestRateLimit_TypedNoAutoRetry(t *testing.T) {
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.Header().Set("Retry-After", "60")
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(429)
		_, _ = io.WriteString(w, `{"error":"rate limited","code":"daily_cap","credits_used":1000,"credits_remaining":0}`)
	}))
	defer srv.Close()
	// maxAttempts 5 — but 429 is NOT auto-retried, so exactly one call.
	c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()), WithRetryPolicy(fastRetry(5)))

	_, err := c.Extract(context.Background(), ExtractRequest{Content: "x", Schema: json.RawMessage(`{}`)})
	if !errors.Is(err, ErrRateLimit) {
		t.Fatalf("expected ErrRateLimit, got %v", err)
	}
	var apiErr *APIError
	errors.As(err, &apiErr)
	if apiErr.RetryAfter == nil || *apiErr.RetryAfter != 60 {
		t.Fatalf("RetryAfter = %v, want 60", apiErr.RetryAfter)
	}
	if apiErr.CreditsUsed == nil || *apiErr.CreditsUsed != 1000 {
		t.Fatalf("CreditsUsed = %v, want 1000", apiErr.CreditsUsed)
	}
	if apiErr.CreditsRemaining == nil || *apiErr.CreditsRemaining != 0 {
		t.Fatalf("CreditsRemaining = %v, want 0", apiErr.CreditsRemaining)
	}
	if got := calls.Load(); got != 1 {
		t.Fatalf("server calls = %d, want 1 (429 is not auto-retried)", got)
	}
}

// ── Retry behavior ───────────────────────────────────────────

func TestRetry_ReadRetriesOn503ThenSucceeds(t *testing.T) {
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := calls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		if n < 3 {
			w.WriteHeader(503)
			_, _ = io.WriteString(w, `{"error":"down","code":"unavailable"}`)
			return
		}
		_, _ = io.WriteString(w, `{"credits_used_today":5,"credits_limit_daily":100}`)
	}))
	defer srv.Close()
	c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()), WithRetryPolicy(fastRetry(3)))

	u, err := c.Usage(context.Background())
	if err != nil {
		t.Fatalf("Usage: %v", err)
	}
	if u.CreditsUsedToday != 5 {
		t.Fatalf("CreditsUsedToday = %d, want 5", u.CreditsUsedToday)
	}
	if got := calls.Load(); got != 3 {
		t.Fatalf("server calls = %d, want 3 (retry then success)", got)
	}
}

func TestRetry_WriteDoesNotRetryOn5xx(t *testing.T) {
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(502)
		_, _ = io.WriteString(w, `{"error":"bad gateway","code":"upstream"}`)
	}))
	defer srv.Close()
	c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()), WithRetryPolicy(fastRetry(3)))

	_, err := c.Extract(context.Background(), ExtractRequest{Content: "x", Schema: json.RawMessage(`{}`)})
	if !errors.Is(err, ErrServer) {
		t.Fatalf("expected ErrServer, got %v", err)
	}
	if got := calls.Load(); got != 1 {
		t.Fatalf("server calls = %d, want 1 (writes never retry on 5xx)", got)
	}
}

// flakyRoundTripper fails the first failN calls with a transport error, then
// returns a canned 200 response. Used to exercise transport-error retry
// without a real network.
type flakyRoundTripper struct {
	calls atomic.Int32
	failN int32
	body  string
}

func (f *flakyRoundTripper) RoundTrip(r *http.Request) (*http.Response, error) {
	n := f.calls.Add(1)
	if n <= f.failN {
		return nil, errors.New("connection refused")
	}
	return &http.Response{
		StatusCode: 200,
		Header:     http.Header{"Content-Type": []string{"application/json"}},
		Body:       io.NopCloser(strings.NewReader(f.body)),
		Request:    r,
	}, nil
}

func TestRetry_TransportErrorRetriedThenSucceeds(t *testing.T) {
	rt := &flakyRoundTripper{
		failN: 2,
		body:  `{"data":{},"model_used":"fast","latency_ms":1,"tokens":{"input":1,"output":1},"credits_used":1}`,
	}
	c, err := NewClient("sx_test",
		WithBaseURL("https://api.struct-x.ai"),
		WithHTTPClient(&http.Client{Transport: rt}),
		WithRetryPolicy(fastRetry(3)),
	)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	res, err := c.Extract(context.Background(), ExtractRequest{Content: "x", Schema: json.RawMessage(`{}`)})
	if err != nil {
		t.Fatalf("Extract after transport retries: %v", err)
	}
	if res.CreditsUsed != 1 {
		t.Fatalf("CreditsUsed = %d, want 1", res.CreditsUsed)
	}
	if got := rt.calls.Load(); got != 3 {
		t.Fatalf("round trips = %d, want 3", got)
	}
}

func TestTransportError_ExhaustsAndTypes(t *testing.T) {
	rt := &flakyRoundTripper{failN: 100} // always fails
	c, _ := NewClient("sx_test",
		WithHTTPClient(&http.Client{Transport: rt}),
		WithRetryPolicy(fastRetry(2)),
	)
	_, err := c.Usage(context.Background())
	if !errors.Is(err, ErrTransport) {
		t.Fatalf("expected ErrTransport, got %v", err)
	}
	if !errors.Is(err, ErrStructX) {
		t.Fatalf("transport error should match ErrStructX, got %v", err)
	}
	var te *TransportError
	if !errors.As(err, &te) {
		t.Fatalf("errors.As(*TransportError) failed; err = %v", err)
	}
	if got := rt.calls.Load(); got != 2 {
		t.Fatalf("round trips = %d, want 2", got)
	}
}

// ── Privacy: redaction reaches the raised error ──────────────

func TestRedaction_StripsSensitiveKeys(t *testing.T) {
	srv := statusServer(t, 500,
		`{"error":"boom","code":"oops","api_key":"sx_should_not_appear","content":"secret payload"}`, nil)
	defer srv.Close()
	c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()), WithRetryPolicy(fastRetry(1)))

	_, err := c.Extract(context.Background(), ExtractRequest{Content: "x", Schema: json.RawMessage(`{}`)})
	var apiErr *APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("expected *APIError, got %v", err)
	}
	if v, _ := apiErr.ResponseBody["api_key"].(string); !strings.HasPrefix(v, "<redacted") {
		t.Fatalf("api_key not redacted: %v", apiErr.ResponseBody["api_key"])
	}
	if v, _ := apiErr.ResponseBody["content"].(string); !strings.HasPrefix(v, "<redacted") {
		t.Fatalf("content not redacted: %v", apiErr.ResponseBody["content"])
	}
	// Non-sensitive keys survive.
	if apiErr.ResponseBody["code"] != "oops" {
		t.Fatalf("code should not be redacted: %v", apiErr.ResponseBody["code"])
	}
}

// ── Listing shape flexibility ────────────────────────────────

func TestListTemplates_AcceptsArrayOrWrapped(t *testing.T) {
	for _, body := range []string{
		`[{"slug":"a","name":"A","schema":{}}]`,
		`{"templates":[{"slug":"b","name":"B","schema":{}}]}`,
	} {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, body)
		}))
		c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()))
		tpls, err := c.ListTemplates(context.Background())
		if err != nil {
			t.Fatalf("ListTemplates: %v", err)
		}
		if len(tpls) != 1 {
			t.Fatalf("len = %d, want 1 (body=%s)", len(tpls), body)
		}
		srv.Close()
	}
}

// ── Batch: heterogeneous results discrimination ──────────────

func TestBatchExtract_ResultDiscrimination(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/extract/batch" {
			t.Errorf("path = %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"results":[
			{"data":{"ok":true},"model_used":"fast","latency_ms":1,"tokens":{"input":1,"output":1},"credits_used":1},
			{"error":"bad schema","code":"invalid_schema"}
		],"total_latency_ms":12,"total_credits_used":3}`)
	}))
	defer srv.Close()
	c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()))

	res, err := c.BatchExtract(context.Background(), BatchExtractRequest{
		Items: []BatchItem{
			{Content: "a", Schema: json.RawMessage(`{"type":"object"}`)},
			{Content: "b", Schema: json.RawMessage(`{"type":"object"}`)},
		},
	})
	if err != nil {
		t.Fatalf("BatchExtract: %v", err)
	}
	if res.TotalCreditsUsed != 3 || len(res.Results) != 2 {
		t.Fatalf("unexpected batch result: %+v", res)
	}
	if res.Results[0].IsError() || res.Results[0].Extraction == nil {
		t.Fatal("results[0] should be a successful extraction")
	}
	if !res.Results[1].IsError() || res.Results[1].Error.Code != "invalid_schema" {
		t.Fatalf("results[1] should be an error item; got %+v", res.Results[1])
	}
}

// ── Backoff math (unit) ──────────────────────────────────────

func TestBackoffCurve(t *testing.T) {
	p := RetryPolicy{
		InitialBackoff:    time.Second,
		MaxBackoff:        30 * time.Second,
		Jitter:            0,
		RespectRetryAfter: true,
	}
	if got := p.backoffFor(1, -1); got != time.Second {
		t.Fatalf("attempt 1 = %v, want 1s", got)
	}
	if got := p.backoffFor(2, -1); got != 2*time.Second {
		t.Fatalf("attempt 2 = %v, want 2s", got)
	}
	if got := p.backoffFor(3, -1); got != 4*time.Second {
		t.Fatalf("attempt 3 = %v, want 4s", got)
	}
	if got := p.backoffFor(10, -1); got != 30*time.Second {
		t.Fatalf("attempt 10 = %v, want 30s (capped)", got)
	}
	if got := p.backoffFor(1, 15*time.Second); got != 15*time.Second {
		t.Fatalf("retry-after 15s = %v, want 15s", got)
	}
	if got := p.backoffFor(1, 300*time.Second); got != 30*time.Second {
		t.Fatalf("retry-after 300s = %v, want 30s (capped)", got)
	}
}

// ── v0.1.1 bug fixes: id decode, usage path, bearer auth ─────

// TestExtract_IDDecodesFromWireID covers Bug 1: the backend emits the
// extraction id on the wire as `id`, and ExtractionID must decode from it.
func TestExtract_IDDecodesFromWireID(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"id":"8a3f7c2d-1b9e-4d6a-b2c3-1f8a7e6c5d4b","data":{},"model_used":"fast","latency_ms":1,"tokens":{"input":1,"output":1},"credits_used":1}`)
	}))
	defer srv.Close()
	c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()))

	res, err := c.Extract(context.Background(), ExtractRequest{Content: "x", Schema: json.RawMessage(`{"type":"object"}`)})
	if err != nil {
		t.Fatalf("Extract: %v", err)
	}
	if res.ExtractionID != "8a3f7c2d-1b9e-4d6a-b2c3-1f8a7e6c5d4b" {
		t.Fatalf("ExtractionID = %q, want the wire `id` value", res.ExtractionID)
	}
}

// TestUsage_PathAndBearerHeader covers Bug 2 (path /billing/usage, no /v1)
// and checks that a bearer token, when set, is sent next to the API key.
func TestUsage_PathAndBearerHeader(t *testing.T) {
	var gotPath, gotAuth, gotAPIKey string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		gotAuth = r.Header.Get("Authorization")
		gotAPIKey = r.Header.Get("X-API-Key")
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"credits_used_today":7,"credits_limit_daily":334,"tier":"developer"}`)
	}))
	defer srv.Close()
	c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()), WithBearerToken("jwt_test"))

	u, err := c.Usage(context.Background())
	if err != nil {
		t.Fatalf("Usage: %v", err)
	}
	if u.CreditsUsedToday != 7 {
		t.Fatalf("CreditsUsedToday = %d, want 7", u.CreditsUsedToday)
	}
	if gotPath != "/billing/usage" {
		t.Fatalf("path = %q, want /billing/usage (no /v1 prefix)", gotPath)
	}
	if gotAuth != "Bearer jwt_test" {
		t.Fatalf("Authorization = %q, want Bearer jwt_test", gotAuth)
	}
	if gotAPIKey != "sx_test" {
		t.Fatalf("X-API-Key = %q, want sx_test (both credentials sent)", gotAPIKey)
	}
}

// TestBearerToken_EnvFallback covers WithBearerToken / STRUCTX_BEARER_TOKEN
// resolution: the env var is picked up, and an explicit option wins.
func TestBearerToken_EnvFallback(t *testing.T) {
	t.Setenv("STRUCTX_BEARER_TOKEN", "jwt_from_env")
	c, err := NewClient("sx_x")
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if c.bearerToken != "jwt_from_env" {
		t.Fatalf("bearerToken = %q, want jwt_from_env", c.bearerToken)
	}
	c2, _ := NewClient("sx_x", WithBearerToken("jwt_explicit"))
	if c2.bearerToken != "jwt_explicit" {
		t.Fatalf("explicit bearer token should win, got %q", c2.bearerToken)
	}
}
