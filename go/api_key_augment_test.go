package structx

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"sync/atomic"
	"testing"
)

// Usage and the personal-schema methods work with an API key alone, and
// AugmentSchema calls POST /v1/schemas/recommendations/{event_id}/augment.

const augmentEventID = "8a3f7c2d-1b9e-4d6a-b2c3-1f8a7e6c5d4b"

const augmentResponse = `{"event_id":"0b5e2a91-77c4-4f0e-9d3a-5c1e2f8b6a70",` +
	`"inferred":{"json_schema":{"type":"object"},"fields":[],"overall_confidence":0.9,"needs_review":false},` +
	`"recommendations":[],"credits_used":4,"partial":false,"cached":false}`

func TestAPIKeyOnly_UsageAndPersonalSchemas(t *testing.T) {
	t.Setenv("STRUCTX_BEARER_TOKEN", "") // no bearer token, even if the shell has one
	cases := []struct {
		name, method, path, body string
		call                     func(c *Client) error
	}{
		{"Usage", http.MethodGet, "/billing/usage", `{"credits_used_today":3,"credits_limit_daily":334}`,
			func(c *Client) error { _, err := c.Usage(context.Background()); return err }},
		{"ListMySchemas", http.MethodGet, "/v1/schemas/mine", `[{"slug":"a"}]`,
			func(c *Client) error { _, err := c.ListMySchemas(context.Background()); return err }},
		{"GetMySchema", http.MethodGet, "/v1/schemas/mine/a", `{"slug":"a"}`,
			func(c *Client) error { _, err := c.GetMySchema(context.Background(), "a"); return err }},
		{"SaveMySchema", http.MethodPost, "/v1/schemas/mine", `{"slug":"a"}`,
			func(c *Client) error {
				_, err := c.SaveMySchema(context.Background(), SaveSchemaParams{Name: "A", JSONSchema: json.RawMessage(`{}`)})
				return err
			}},
		{"UpdateMySchema", http.MethodPatch, "/v1/schemas/mine/a", `{"slug":"a"}`,
			func(c *Client) error {
				d := "v2"
				_, err := c.UpdateMySchema(context.Background(), "a", SchemaUpdate{Description: &d})
				return err
			}},
		{"DeleteMySchema", http.MethodDelete, "/v1/schemas/mine/a", ``,
			func(c *Client) error { return c.DeleteMySchema(context.Background(), "a") }},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var calls atomic.Int32
			var gotMethod, gotPath, gotAPIKey, gotAuth string
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				gotMethod, gotPath = r.Method, r.URL.Path
				gotAPIKey, gotAuth = r.Header.Get("X-API-Key"), r.Header.Get("Authorization")
				if tc.body == "" {
					w.WriteHeader(http.StatusNoContent)
					return
				}
				w.Header().Set("Content-Type", "application/json")
				_, _ = io.WriteString(w, tc.body)
			}))
			defer srv.Close()
			c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()))

			if err := tc.call(c); err != nil {
				t.Fatalf("%s: %v", tc.name, err)
			}
			if got := calls.Load(); got != 1 {
				t.Fatalf("server calls = %d, want 1", got)
			}
			if gotMethod != tc.method || gotPath != tc.path {
				t.Fatalf("request = %s %s, want %s %s", gotMethod, gotPath, tc.method, tc.path)
			}
			if gotAPIKey != "sx_test" {
				t.Fatalf("X-API-Key = %q, want sx_test", gotAPIKey)
			}
			if gotAuth != "" {
				t.Fatalf("Authorization = %q, want none", gotAuth)
			}
		})
	}
}

// augmentServer records the augment request body and answers with
// augmentResponse.
func augmentServer(t *testing.T, gotPath *string, gotBody *map[string]any) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		*gotPath = r.URL.Path
		raw, _ := io.ReadAll(r.Body)
		if err := json.Unmarshal(raw, gotBody); err != nil {
			t.Errorf("request body is not JSON: %v", err)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, augmentResponse)
	}))
}

func TestAugmentSchema_WireShape(t *testing.T) {
	var gotPath string
	var gotBody map[string]any
	srv := augmentServer(t, &gotPath, &gotBody)
	defer srv.Close()
	c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()))

	res, err := c.AugmentSchema(context.Background(), augmentEventID, "rename event_id to id", "<p>sample</p>",
		AugmentParams{ContentType: "html"})
	if err != nil {
		t.Fatalf("AugmentSchema: %v", err)
	}
	if want := "/v1/schemas/recommendations/" + augmentEventID + "/augment"; gotPath != want {
		t.Fatalf("path = %q, want %q", gotPath, want)
	}
	want := map[string]any{"guidance": "rename event_id to id", "content": "<p>sample</p>", "content_type": "html"}
	if !reflect.DeepEqual(gotBody, want) {
		t.Fatalf("body = %v, want %v", gotBody, want)
	}
	if res.EventID != "0b5e2a91-77c4-4f0e-9d3a-5c1e2f8b6a70" || res.CreditsUsed != 4 {
		t.Fatalf("result = %+v, want the decoded InferenceResult", res)
	}
}

func TestAugmentSchema_OmitsEmptyContentType(t *testing.T) {
	var gotPath string
	var gotBody map[string]any
	srv := augmentServer(t, &gotPath, &gotBody)
	defer srv.Close()
	c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()))

	if _, err := c.AugmentSchema(context.Background(), augmentEventID, "drop trace_id", "sample text", AugmentParams{}); err != nil {
		t.Fatalf("AugmentSchema: %v", err)
	}
	want := map[string]any{"guidance": "drop trace_id", "content": "sample text"}
	if !reflect.DeepEqual(gotBody, want) {
		t.Fatalf("body = %v, want %v", gotBody, want)
	}
}

func TestAugmentSchema_NoRetryOn5xx(t *testing.T) {
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(503)
		_, _ = io.WriteString(w, `{"error":"down","code":"unavailable"}`)
	}))
	defer srv.Close()
	c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()), WithRetryPolicy(fastRetry(3)))

	_, err := c.AugmentSchema(context.Background(), augmentEventID, "drop trace_id", "sample text", AugmentParams{})
	if !errors.Is(err, ErrServer) {
		t.Fatalf("expected ErrServer, got %v", err)
	}
	if got := calls.Load(); got != 1 {
		t.Fatalf("server calls = %d, want 1 (augment is billed, never retried on 5xx)", got)
	}
}
