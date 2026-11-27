package structx

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
)

var testPDF = []byte("%PDF-1.7\n1 0 obj\n<<>>\nendobj\n")

const docOK = `{"id":"e1","data":{"total":12.5},"confidence":0.97,"field_confidences":[],
"confidence_source":"model","model_used":"fast","latency_ms":900,
"tokens":{"input":5600,"output":120},"credits_used":4,"credits_remaining":96}`

func TestExtractFileSendsBase64AndOptions(t *testing.T) {
	var got map[string]any
	var path string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path = r.URL.Path
		b, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(b, &got)
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, docOK)
	}))
	defer srv.Close()
	c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()), WithRetryPolicy(fastRetry(3)))

	out, err := c.ExtractFile(context.Background(), testPDF, ExtractFileRequest{
		Schema: json.RawMessage(`{"type":"object","properties":{"total":{"type":"number"}}}`),
		Model:  "balanced", MaxCredits: 40, Fields: []string{"total"},
		Options: map[string]any{"confidence_threshold": 0.8},
	})
	if err != nil {
		t.Fatal(err)
	}
	if path != "/v1/extract/document" {
		t.Fatalf("path = %q", path)
	}
	doc := got["document"].(map[string]any)
	if doc["type"] != "base64" || doc["media_type"] != "application/pdf" ||
		doc["data"] != base64.StdEncoding.EncodeToString(testPDF) {
		t.Fatalf("document = %#v", doc)
	}
	if got["model"] != "balanced" || got["max_credits"].(float64) != 40 {
		t.Fatalf("body = %#v", got)
	}
	if _, ok := got["template_slug"]; ok {
		t.Fatal("template_slug should be absent")
	}
	if out.ConfidenceSource != "model" || out.CreditsUsed != 4 || out.ExtractionID != "e1" {
		t.Fatalf("extraction = %#v", out)
	}
}

func TestExtractFileIsNeverRetried(t *testing.T) {
	var calls int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(503)
		_, _ = io.WriteString(w, `{"error":"busy","code":"document_provider_unavailable"}`)
	}))
	defer srv.Close()
	c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()), WithRetryPolicy(fastRetry(3)))

	_, err := c.ExtractFile(context.Background(), testPDF, ExtractFileRequest{TemplateSlug: "finance.invoice"})
	if !errors.Is(err, ErrServer) {
		t.Fatalf("err = %v", err)
	}
	if n := atomic.LoadInt32(&calls); n != 1 {
		t.Fatalf("calls = %d, want 1", n)
	}
	if _, ok := writePaths["/v1/extract/document"]; !ok {
		t.Fatal("/v1/extract/document must be a write path")
	}
}

func TestExtractFileRejectsAnEmptyDocument(t *testing.T) {
	c := mustClient(t, "https://api.test.struct-x.ai")
	if _, err := c.ExtractFile(context.Background(), nil, ExtractFileRequest{}); !errors.Is(err, ErrStructX) {
		t.Fatalf("err = %v", err)
	}
}
