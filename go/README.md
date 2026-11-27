# structx-go — Go SDK for struct-x

Official Go client for **[struct-x](https://struct-x.ai)** — the agent-native structured-extraction API. Send raw content and a JSON Schema, get back validated, typed JSON with per-field confidence scores.

- **Dependency-light.** Standard library only (`net/http` + `encoding/json`) — no third-party runtime dependencies. Customer security teams can audit `go.mod` in 10 seconds.
- **Context-first.** Every request method takes `context.Context` for cancellation and deadlines.
- **Same error taxonomy as the Python and Node SDKs** so polyglot stacks can port retry/catch logic 1:1 — with idiomatic `errors.Is` / `errors.As`.
- **Credential-redacted errors** out of the box — `api_key`, `password`, `content`, etc. are scrubbed before the body is attached to the error, so logs never accidentally leak secrets.

## Install

```bash
go get github.com/struct-x-ai/struct-x-sdks/go
```

The package name is `structx` (the trailing `go` path segment is a Go keyword and can't name a package):

```go
import structx "github.com/struct-x-ai/struct-x-sdks/go"
```

Requires **Go 1.21+**.

## Quickstart

```go
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"

	structx "github.com/struct-x-ai/struct-x-sdks/go"
)

func main() {
	client, err := structx.NewClient("sx_...") // or set STRUCTX_API_KEY
	if err != nil {
		log.Fatal(err)
	}

	schema := json.RawMessage(`{
		"type": "object",
		"required": ["title", "price_cents"],
		"properties": {
			"title":       {"type": "string"},
			"price_cents": {"type": "integer"}
		}
	}`)

	res, err := client.Extract(context.Background(), structx.ExtractRequest{
		Content: "<div><h1>Aeron Chair</h1><span>$1,795.00</span></div>",
		Schema:  schema,
	})
	if err != nil {
		log.Fatal(err)
	}

	fmt.Printf("%s\n", res.Data)        // {"title":"Aeron Chair","price_cents":179500}
	fmt.Println(res.ModelUsed)          // fast   (public quality tier, never the vendor name)
	fmt.Println(res.CreditsUsed)        // 2
	for _, fc := range res.FieldConfidences {
		fmt.Printf("%s: %.2f\n", fc.Field, fc.Confidence)
	}
}
```

`res.Data` is `json.RawMessage` — unmarshal it into whatever shape your schema produces.

## Use a catalog template instead of an inline schema

```go
res, err := client.Extract(ctx, structx.ExtractRequest{
	Content:      stripeWebhookPayload,
	TemplateSlug: "logs.stripe.event",      // latest published version
})
```

Pin a specific version with `family_slug@version`, e.g. `"logs.stripe.event@1.0.0"`.

## Authentication

Every method authenticates with your API key (`X-API-Key`), set with `NewClient("sx_...")` or `STRUCTX_API_KEY`. That includes `Usage` and the personal-schema methods (`ListMySchemas`, `GetMySchema`, `SaveMySchema`, `UpdateMySchema`, `DeleteMySchema`).

With an API key, the personal-schema methods write personal schemas only. Binding a schema to an organization or sharing it is done in the dashboard; the API answers a 403 (`errors.Is(err, structx.ErrPermissionDenied)`, code `api_key_personal_schemas_only`) to an org write made with an API key.

Opting a schema into community-catalog promotion (`PublishableConsent`) is also done in the dashboard. `SaveMySchema` sends `publishable_consent` only when `PublishableConsent` is true, and `UpdateMySchema` only when it is non-nil; with an API key, either one that sets or changes it gets a 403 (`errors.Is(err, structx.ErrPermissionDenied)`, code `publishable_consent_portal_only`).

A JWT bearer token (`WithBearerToken("ey...")` or `STRUCTX_BEARER_TOKEN`) is optional. When set, the SDK sends it as `Authorization: Bearer` next to the API key; a route that accepts both uses the API key.

## Configuration

`NewClient` reads `STRUCTX_API_KEY`, `STRUCTX_BEARER_TOKEN`, and `STRUCTX_BASE_URL` from the environment when not passed explicitly. Tune it with functional options:

```go
client, err := structx.NewClient("sx_...",
	structx.WithBearerToken("ey..."),               // optional; or STRUCTX_BEARER_TOKEN
	structx.WithBaseURL("https://api.struct-x.ai"), // or STRUCTX_BASE_URL
	structx.WithTimeout(30*time.Second),           // per-request, via context
	structx.WithMaxRetries(3),                      // retries after the first attempt
	structx.WithHTTPClient(myClient),               // custom transport / proxy
	structx.WithRetryPolicy(structx.RetryPolicy{ MaxAttempts: 4, /* … */ }),
	structx.WithDefaultHeaders(map[string]string{"X-Request-Trace": "id"}),
)
```

| Option | Default | Notes |
|---|---|---|
| `WithBearerToken` | `STRUCTX_BEARER_TOKEN` env | Optional. Sent as `Authorization: Bearer` when set; no method requires it. |
| `WithBaseURL` | `STRUCTX_BASE_URL` env, else `https://api.struct-x.ai` | Override for staging / self-hosted. |
| `WithTimeout` | `30s` | Applied per request via `context.WithTimeout`. `0` disables it. |
| `WithMaxRetries` | `2` (3 total attempts) | Retries after the initial attempt. |
| `WithHTTPClient` | `&http.Client{}` | Bring your own transport / proxy. |
| `WithRetryPolicy` | see below | Full control over backoff, jitter, and the read-vs-write rule. |
| `WithDefaultHeaders` | `{}` | Merged into every request (tracing IDs, etc.). |

## API surface

| Method | Endpoint |
|---|---|
| `Extract` | `POST /v1/extract` |
| `BatchExtract` | `POST /v1/extract/batch` |
| `InferSchema` | `POST /v1/schemas/infer` |
| `BrowseTemplates` (preferred) | `GET /v1/templates` (one page, 25 by default) |
| `ListTemplates` | `GET /v1/schemas` (the whole gallery, several MB) |
| `ListModels` | `GET /v1/models` (operator-only) |
| `Usage` | `GET /billing/usage` |
| `ListCategories` / `GetTemplate` | `GET /v1/templates/categories`, `GET /v1/templates/{slug}` |
| `ListMySchemas` / `GetMySchema` / `SaveMySchema` / `UpdateMySchema` / `DeleteMySchema` | `…/v1/schemas/mine…` CRUD |
| `RecommendTemplates` / `AugmentSchema` | `POST /v1/schemas/recommend`, `POST /v1/schemas/recommendations/{event_id}/augment` |
| `RequestWithdrawal` | `POST /v1/templates/{slug}/withdraw` |
| `Synthesize` / `ListSynthDatasets` / `GetSynthDataset` / `ExportSynthDataset` / `DeleteSynthDataset` | `…/v1/synth…` |
| `ListCanonicalFrameworks` / `ProjectCanonical` / `GetTemplateCanonical` | `…/v1/canonical…`, `GET /v1/templates/{slug}/canonical` |
| `GetTemplateCostEstimate` / `ListTemplateVersions` | `GET /v1/templates/{slug}/cost-estimate`, `…/versions` |

This matches the Python (`structx-sdk`) and Node (`@struct-x/sdk`) SDKs at their 0.4.0 line. Every method takes `context.Context` first.

## Browse the template catalog

`BrowseTemplates` returns one page of the catalog, 25 templates by default (`Limit` 1-500), filtered by tag tokens (`Q`), `Category`, `Vendor`, a curated `Tag` facet or `FirstParty`. Items carry no JSON Schema; `GetTemplate` returns one. `ListTemplates` still returns the whole gallery with every schema in one response of several MB; prefer `BrowseTemplates`.

```go
params := structx.BrowseTemplatesParams{Q: "stripe", Category: "PAYMENTS"}
for {
	page, err := client.BrowseTemplates(ctx, params)
	if err != nil {
		log.Fatal(err)
	}
	for _, t := range page.Items {
		fmt.Println(t.Slug, t.FieldCounts.Required)
	}
	next, ok := page.NextOffset()
	if !ok {
		break
	}
	params.Offset = next
}
```

## Infer a schema, then refine it

Pass `AugmentSchema` the `EventID` from `InferSchema` and the same content you sent it; the server checks the content against that call and returns a 400 (`ErrValidation`) if it differs.

```go
draft, err := client.InferSchema(ctx, structx.InferSchemaRequest{Content: page})
if err != nil {
	log.Fatal(err)
}
refined, err := client.AugmentSchema(ctx, draft.EventID,
	"rename event_id to id and add a customer_country field",
	page,                                          // the same content passed to InferSchema
	structx.AugmentParams{ContentType: "html"})    // optional; detected when empty
if err != nil {
	log.Fatal(err)
}
fmt.Println(string(refined.Inferred.JSONSchema)) // *InferenceResult, like InferSchema
// refined.EventID can be refined again with the same content
```

## Batch extraction

```go
batch, err := client.BatchExtract(ctx, structx.BatchExtractRequest{
	Items: []structx.BatchItem{
		{Content: pageA, TemplateSlug: "ecommerce.product"},
		{Content: pageB, Schema: json.RawMessage(`{"type":"object"}`)},
	},
})
if err != nil {
	log.Fatal(err)
}
fmt.Println(batch.TotalCreditsUsed)
for i, item := range batch.Results {          // order matches the request
	if item.IsError() {
		fmt.Printf("item %d failed: %s — %s\n", i, item.Error.Code, item.Error.Error)
		continue
	}
	fmt.Printf("item %d: %s\n", i, item.Extraction.Data)
}
```

## Canonical projection

```go
frameworks, _ := client.ListCanonicalFrameworks(ctx)

var record map[string]any
_ = json.Unmarshal(res.Data, &record)

proj, err := client.ProjectCanonical(ctx, record, []string{"ocsf", "ecs"},
	structx.ProjectParams{TemplateSlug: "logs.stripe.event"}) // or Schema: …
if err != nil {
	log.Fatal(err)
}
fmt.Println(proj.Projections["ocsf"].Record)
fmt.Println(proj.Projections["ocsf"].Coverage)
```

## Errors

Non-2xx responses return an `*APIError`. Match a category with `errors.Is`, read the details with `errors.As`:

```go
_, err := client.Extract(ctx, req)
switch {
case errors.Is(err, structx.ErrRateLimit):
	var apiErr *structx.APIError
	errors.As(err, &apiErr)
	fmt.Printf("retry after %v; %v credits left\n", apiErr.RetryAfter, apiErr.CreditsRemaining)
case errors.Is(err, structx.ErrValidation):
	fmt.Println("bad input — fix your schema/content")
case errors.Is(err, structx.ErrTransport):
	fmt.Println("network failure — the request never reached the API")
}
```

Sentinel hierarchy (every SDK error satisfies `errors.Is(err, ErrStructX)`):

```
ErrStructX
├── ErrTransport            // network failure — request never reached the server
└── ErrAPI                  // server responded with an error status
    ├── ErrAuthentication   // 401
    ├── ErrPermissionDenied // 403
    ├── ErrNotFound         // 404
    ├── ErrValidation       // 400, 422
    ├── ErrRateLimit        // 429  (APIError carries RetryAfter, CreditsRemaining)
    └── ErrServer           // 5xx
```

`*APIError` carries `StatusCode`, `Code` (machine-readable, from the backend's `{error, code}` envelope), `RequestID` (from `x-request-id` — include it when reporting bugs), and `ResponseBody` (with sensitive keys redacted). `Error()` omits the body entirely as a second redaction layer.

## Retries

Read calls (`BrowseTemplates`, `ListTemplates`, `ListModels`, `Usage`, the catalog / synth / canonical getters) auto-retry transient 5xx and transport errors with exponential backoff (capped at 30s, honoring `Retry-After`).

Write calls (`Extract`, `BatchExtract`, `InferSchema`, `AugmentSchema`, `Synthesize`) retry **only** on transport errors, never on a 5xx — a 5xx after a partial backend run may already have billed the call. `429` is never auto-retried; it surfaces as `ErrRateLimit` so your application can decide how to back off.

## Forward compatibility

Response structs decode with the standard library, which ignores unknown fields — when the backend adds a field, older SDK builds keep decoding without error, they just don't surface it as a typed field. `Extraction.Data`, template/schema payloads, and projection records are `json.RawMessage` / `map[string]any` so arbitrary shapes always round-trip.

## Development

```bash
cd go
go build ./...
go test ./...
go vet ./...
gofmt -l .
```

## License

MIT — see [LICENSE](./LICENSE).

## Sibling SDKs

- **Python** — [`structx-sdk`](https://pypi.org/project/structx-sdk/) on PyPI
- **Node / TypeScript** — [`@struct-x/sdk`](https://www.npmjs.com/package/@struct-x/sdk) on npm
- **MCP server** — [`structx-mcp`](https://pypi.org/project/structx-mcp/) for Claude Desktop / Cursor / Windsurf
