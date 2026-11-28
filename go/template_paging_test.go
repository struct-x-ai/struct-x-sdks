package structx

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"reflect"
	"strings"
	"testing"
)

// BrowseTemplates pages GET /v1/templates; SaveMySchema sends
// publishable_consent only when set; Usage decodes the API's real
// /billing/usage shape; AugmentSchema never touches /feedback.

const templateItem = `{"id":"5f0c1e2a-0000-4000-8000-000000000001","family_slug":"logs.stripe.event",` +
	`"version":"1.0.0","name":"Stripe event","slug":"logs.stripe.event","category":"PAYMENTS",` +
	`"vendor":"stripe","tags":["stripe","payments"],"is_first_party":true,"default_tier":"required",` +
	`"field_counts":{"required":9,"recommended":5,"optional":2,"extended":0,"total":16},` +
	`"usage_count":12,"published_at":"2026-06-01T00:00:00Z"}`

func templatePage(count, total, limit, offset int) string {
	items := make([]string, count)
	for i := range items {
		items[i] = templateItem
	}
	b, _ := json.Marshal(map[string]any{
		"items": json.RawMessage("[" + strings.Join(items, ",") + "]"),
		"total": total, "limit": limit, "offset": offset,
	})
	return string(b)
}

// recordingServer answers every request with body and records the last one.
func recordingServer(t *testing.T, status int, body string, gotPath *string, gotQuery *url.Values) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		*gotPath = r.URL.Path
		*gotQuery = r.URL.Query()
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_, _ = io.WriteString(w, body)
	}))
}

func flatQuery(q url.Values) map[string]string {
	out := map[string]string{}
	for k, v := range q {
		out[k] = v[0]
	}
	return out
}

func TestBrowseTemplates_Defaults(t *testing.T) {
	var gotPath string
	var gotQuery url.Values
	srv := recordingServer(t, 200, templatePage(25, 60, 25, 0), &gotPath, &gotQuery)
	defer srv.Close()
	c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()))

	page, err := c.BrowseTemplates(context.Background(), BrowseTemplatesParams{})
	if err != nil {
		t.Fatalf("BrowseTemplates: %v", err)
	}
	if gotPath != "/v1/templates" {
		t.Fatalf("path = %q, want /v1/templates", gotPath)
	}
	if want := map[string]string{"limit": "25", "offset": "0"}; !reflect.DeepEqual(flatQuery(gotQuery), want) {
		t.Fatalf("query = %v, want %v", flatQuery(gotQuery), want)
	}
	if len(page.Items) != 25 || page.Total != 60 {
		t.Fatalf("page = %d items / total %d, want 25 / 60", len(page.Items), page.Total)
	}
	item := page.Items[0]
	if item.Slug != "logs.stripe.event" || item.Category != "PAYMENTS" || item.FieldCounts.Required != 9 {
		t.Fatalf("item = %+v", item)
	}
	if next, ok := page.NextOffset(); !ok || next != 25 {
		t.Fatalf("NextOffset = %d, %v; want 25, true", next, ok)
	}
}

func TestBrowseTemplates_FiltersAndPaging(t *testing.T) {
	var gotPath string
	var gotQuery url.Values
	srv := recordingServer(t, 200, templatePage(10, 60, 50, 50), &gotPath, &gotQuery)
	defer srv.Close()
	c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()))

	firstParty := false
	page, err := c.BrowseTemplates(context.Background(), BrowseTemplatesParams{
		Q: "stripe payments", Category: "PAYMENTS", Vendor: "stripe", Tag: "input:document",
		FirstParty: &firstParty, Limit: 50, Offset: 50,
	})
	if err != nil {
		t.Fatalf("BrowseTemplates: %v", err)
	}
	want := map[string]string{
		"q": "stripe payments", "category": "PAYMENTS", "vendor": "stripe", "tag": "input:document",
		"first_party": "false", "limit": "50", "offset": "50",
	}
	if !reflect.DeepEqual(flatQuery(gotQuery), want) {
		t.Fatalf("query = %v, want %v", flatQuery(gotQuery), want)
	}
	if _, ok := page.NextOffset(); ok {
		t.Fatalf("NextOffset ok on the last page")
	}
}

func TestTemplatePage_NextOffset(t *testing.T) {
	cases := []struct {
		count, total, offset, want int
		ok                         bool
	}{
		{25, 60, 0, 25, true},
		{25, 60, 25, 50, true},
		{10, 60, 50, 0, false},
		{0, 60, 60, 0, false},
		{0, 0, 0, 0, false},
	}
	for _, tc := range cases {
		p := TemplatePage{Items: make([]TemplateSummary, tc.count), Total: tc.total, Offset: tc.offset}
		if got, ok := p.NextOffset(); got != tc.want || ok != tc.ok {
			t.Errorf("NextOffset(%d items, total %d, offset %d) = %d, %v; want %d, %v",
				tc.count, tc.total, tc.offset, got, ok, tc.want, tc.ok)
		}
	}
}

func TestBrowseTemplates_ValidationError(t *testing.T) {
	var gotPath string
	var gotQuery url.Values
	srv := recordingServer(t, 422, `{"error":"q: string should match pattern","code":"validation_error"}`, &gotPath, &gotQuery)
	defer srv.Close()
	c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()))

	if _, err := c.BrowseTemplates(context.Background(), BrowseTemplatesParams{Q: "Stripe!"}); !errors.Is(err, ErrValidation) {
		t.Fatalf("err = %v, want ErrValidation", err)
	}
}

func TestListTemplates_StillUsesTheWholeGallery(t *testing.T) {
	var gotPath string
	var gotQuery url.Values
	srv := recordingServer(t, 200, `[{"slug":"a","name":"A","schema":{}}]`, &gotPath, &gotQuery)
	defer srv.Close()
	c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()))

	tpls, err := c.ListTemplates(context.Background())
	if err != nil || len(tpls) != 1 || gotPath != "/v1/schemas" {
		t.Fatalf("ListTemplates = %v, %v (path %q)", tpls, err, gotPath)
	}
}

// bodyServer records the last JSON request body.
func bodyServer(t *testing.T, status int, resp string, gotBody *map[string]any) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		*gotBody = map[string]any{}
		if err := json.Unmarshal(raw, gotBody); err != nil {
			t.Errorf("request body is not JSON: %v", err)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_, _ = io.WriteString(w, resp)
	}))
}

func TestSaveMySchema_PublishableConsentOnlyWhenSet(t *testing.T) {
	var gotBody map[string]any
	srv := bodyServer(t, 201, `{"slug":"a"}`, &gotBody)
	defer srv.Close()
	c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()))
	ctx := context.Background()

	if _, err := c.SaveMySchema(ctx, SaveSchemaParams{Name: "A", JSONSchema: json.RawMessage(`{"type":"object"}`)}); err != nil {
		t.Fatalf("SaveMySchema: %v", err)
	}
	if _, present := gotBody["publishable_consent"]; present {
		t.Fatalf("publishable_consent sent by default: %v", gotBody)
	}
	if _, err := c.SaveMySchema(ctx, SaveSchemaParams{
		Name: "A", JSONSchema: json.RawMessage(`{"type":"object"}`), PublishableConsent: true,
	}); err != nil {
		t.Fatalf("SaveMySchema: %v", err)
	}
	if gotBody["publishable_consent"] != true {
		t.Fatalf("publishable_consent = %v, want true", gotBody["publishable_consent"])
	}
}

func TestUpdateMySchema_PublishableConsentOnlyWhenSet(t *testing.T) {
	var gotBody map[string]any
	srv := bodyServer(t, 200, `{"slug":"a"}`, &gotBody)
	defer srv.Close()
	c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()))

	name := "B"
	if _, err := c.UpdateMySchema(context.Background(), "a", SchemaUpdate{Name: &name}); err != nil {
		t.Fatalf("UpdateMySchema: %v", err)
	}
	if want := map[string]any{"name": "B"}; !reflect.DeepEqual(gotBody, want) {
		t.Fatalf("body = %v, want %v", gotBody, want)
	}
}

func TestSaveMySchema_PortalOnlyConsentIsPermissionDenied(t *testing.T) {
	var gotBody map[string]any
	srv := bodyServer(t, 403, `{"error":"Set publishable_consent in the dashboard.","code":"publishable_consent_portal_only"}`, &gotBody)
	defer srv.Close()
	c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()))

	_, err := c.SaveMySchema(context.Background(), SaveSchemaParams{
		Name: "A", JSONSchema: json.RawMessage(`{"type":"object"}`), PublishableConsent: true,
	})
	if !errors.Is(err, ErrPermissionDenied) {
		t.Fatalf("err = %v, want ErrPermissionDenied", err)
	}
	var apiErr *APIError
	if !errors.As(err, &apiErr) || apiErr.Code != "publishable_consent_portal_only" {
		t.Fatalf("err = %#v, want code publishable_consent_portal_only", err)
	}
}

// The fields GET /billing/usage returns (the API's UsageSummary model).
const realUsage = `{"period":"2026-11-27","tier":"developer","extractions":4,"extractions_today":4,` +
	`"credits_used":30,"credits_limit":334,"credits_remaining":304,"credits_used_today":30,` +
	`"credits_limit_today":334,"credits_remaining_today":304,"extractions_this_month":51,` +
	`"credits_used_this_month":410,"credits_limit_this_month":10020,"period_start":null,"period_end":null}`

func TestUsage_DecodesTheRealResponse(t *testing.T) {
	var gotPath string
	var gotQuery url.Values
	srv := recordingServer(t, 200, realUsage, &gotPath, &gotQuery)
	defer srv.Close()
	c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()))

	u, err := c.Usage(context.Background())
	if err != nil {
		t.Fatalf("Usage: %v", err)
	}
	if u.CreditsUsedToday != 30 || u.CreditsLimitToday != 334 || u.CreditsRemainingToday != 304 {
		t.Fatalf("today = %+v", u)
	}
	if u.CreditsUsedThisMonth != 410 || u.CreditsLimitThisMonth != 10020 || u.ExtractionsThisMonth != 51 {
		t.Fatalf("month = %+v", u)
	}
	if u.Tier == nil || *u.Tier != "developer" || u.Period == nil || *u.Period != "2026-11-27" {
		t.Fatalf("tier/period = %+v", u)
	}
	if u.CreditsLimitDaily != 334 {
		t.Fatalf("deprecated CreditsLimitDaily = %d, want 334 (filled from credits_limit_today)", u.CreditsLimitDaily)
	}
}

func TestAugmentSchema_NeverCallsFeedback(t *testing.T) {
	var paths []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		paths = append(paths, r.URL.Path)
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, augmentResponse)
	}))
	defer srv.Close()
	c := mustClient(t, srv.URL, WithHTTPClient(srv.Client()))

	if _, err := c.AugmentSchema(context.Background(), augmentEventID, "drop trace_id", "sample text", AugmentParams{}); err != nil {
		t.Fatalf("AugmentSchema: %v", err)
	}
	want := []string{"/v1/schemas/recommendations/" + augmentEventID + "/augment"}
	if !reflect.DeepEqual(paths, want) {
		t.Fatalf("paths = %v, want %v (never /feedback)", paths, want)
	}
}
