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

// "cache" is the server's name for the cache switch. Releases up to 0.2.0
// documented "use_cache", which the server ignored, so callers could never
// skip the extraction cache. The SDK now sends "cache" and still accepts
// "use_cache", renaming it on the wire.

const extractionJSON = `{"data":{},"model_used":"fast","latency_ms":1,"tokens":{"input":1,"output":1},"credits_used":1}`

// optionsServer records the "options" object of every request it receives.
func optionsServer(t *testing.T, reply string) (*httptest.Server, *[]map[string]any, *int32) {
	t.Helper()
	var calls int32
	var got []map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		raw, _ := io.ReadAll(r.Body)
		var body struct {
			Options map[string]any `json:"options"`
		}
		if err := json.Unmarshal(raw, &body); err != nil {
			t.Errorf("bad request body: %v", err)
		}
		got = append(got, body.Options)
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, reply)
	}))
	t.Cleanup(srv.Close)
	return srv, &got, &calls
}

func TestExtract_SendsCacheOption(t *testing.T) {
	cases := []struct {
		name string
		in   map[string]any
		want map[string]any
	}{
		{"use_cache false", map[string]any{"use_cache": false}, map[string]any{"cache": false}},
		{"use_cache true", map[string]any{"use_cache": true}, map[string]any{"cache": true}},
		{"cache only", map[string]any{"cache": false}, map[string]any{"cache": false}},
		{"both equal", map[string]any{"cache": false, "use_cache": false}, map[string]any{"cache": false}},
		{"use_cache nil", map[string]any{"use_cache": nil}, map[string]any{}},
		{"other keys kept", map[string]any{"use_cache": false, "include_citations": true},
			map[string]any{"cache": false, "include_citations": true}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv, got, _ := optionsServer(t, extractionJSON)
			c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()))
			original := map[string]any{}
			for k, v := range tc.in {
				original[k] = v
			}
			_, err := c.Extract(context.Background(), ExtractRequest{
				Content: "x", Schema: json.RawMessage(`{"type":"object"}`), Options: tc.in,
			})
			if err != nil {
				t.Fatalf("Extract: %v", err)
			}
			if len(*got) != 1 || !reflect.DeepEqual((*got)[0], tc.want) {
				t.Fatalf("sent options = %v, want %v", *got, tc.want)
			}
			if !reflect.DeepEqual(tc.in, original) {
				t.Fatalf("caller's map was changed: %v, want %v", tc.in, original)
			}
		})
	}
}

func TestExtract_ConflictingCacheOptionsFailBeforeSending(t *testing.T) {
	for _, pair := range [][2]bool{{true, false}, {false, true}} {
		srv, _, calls := optionsServer(t, extractionJSON)
		c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()))
		_, err := c.Extract(context.Background(), ExtractRequest{
			Content: "x", Schema: json.RawMessage(`{"type":"object"}`),
			Options: map[string]any{"cache": pair[0], "use_cache": pair[1]},
		})
		if err == nil {
			t.Fatalf("cache=%v use_cache=%v: expected an error", pair[0], pair[1])
		}
		if !errors.Is(err, ErrStructX) || errors.Is(err, ErrAPI) {
			t.Fatalf("error should be a client-side ErrStructX, got %v", err)
		}
		if atomic.LoadInt32(calls) != 0 {
			t.Fatalf("no request should be sent, got %d", *calls)
		}
	}
}

func TestBatchExtract_SendsCacheOption(t *testing.T) {
	srv, got, _ := optionsServer(t, `{"results":[],"total_latency_ms":1,"total_credits_used":0}`)
	c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()))
	_, err := c.BatchExtract(context.Background(), BatchExtractRequest{
		Items:   []BatchItem{{Content: "x", Schema: json.RawMessage(`{"type":"object"}`)}},
		Options: map[string]any{"use_cache": false},
	})
	if err != nil {
		t.Fatalf("BatchExtract: %v", err)
	}
	if len(*got) != 1 || !reflect.DeepEqual((*got)[0], map[string]any{"cache": false}) {
		t.Fatalf("sent options = %v, want map[cache:false]", *got)
	}
}

func TestBatchExtract_ConflictingCacheOptionsFailBeforeSending(t *testing.T) {
	srv, _, calls := optionsServer(t, `{"results":[]}`)
	c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()))
	_, err := c.BatchExtract(context.Background(), BatchExtractRequest{
		Items:   []BatchItem{{Content: "x", Schema: json.RawMessage(`{"type":"object"}`)}},
		Options: map[string]any{"cache": true, "use_cache": false},
	})
	if !errors.Is(err, ErrStructX) {
		t.Fatalf("expected an ErrStructX error, got %v", err)
	}
	if atomic.LoadInt32(calls) != 0 {
		t.Fatalf("no request should be sent, got %d", *calls)
	}
}
