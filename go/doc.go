// Package structx is the official Go client for struct-x — the agent-native
// structured-extraction API. Send raw content (HTML, markdown, text) plus a
// JSON Schema and receive validated, typed JSON with per-field confidence
// scores.
//
// # Quickstart
//
//	client, err := structx.NewClient("sx_...")
//	if err != nil {
//		log.Fatal(err)
//	}
//
//	schema := json.RawMessage(`{
//		"type": "object",
//		"properties": {
//			"title":       {"type": "string"},
//			"price_cents": {"type": "integer"}
//		}
//	}`)
//
//	result, err := client.Extract(context.Background(), structx.ExtractRequest{
//		Content: "<div><h1>Aeron Chair</h1><span>$1,795.00</span></div>",
//		Schema:  schema,
//	})
//	if err != nil {
//		log.Fatal(err)
//	}
//	fmt.Printf("%s\n", result.Data) // {"title":"Aeron Chair","price_cents":179500}
//
// # Configuration
//
// NewClient reads STRUCTX_API_KEY and STRUCTX_BASE_URL from the environment
// when not passed explicitly. Tune behavior with functional options:
//
//	client, err := structx.NewClient("sx_...",
//		structx.WithBaseURL("https://api.struct-x.ai"),
//		structx.WithTimeout(30*time.Second),
//		structx.WithMaxRetries(3),
//		structx.WithHTTPClient(myClient),
//	)
//
// # Errors
//
// Non-2xx responses return an *APIError; match a category with errors.Is and
// read details with errors.As:
//
//	_, err := client.Extract(ctx, req)
//	if errors.Is(err, structx.ErrRateLimit) {
//		var apiErr *structx.APIError
//		errors.As(err, &apiErr)
//		// apiErr.RetryAfter, apiErr.CreditsRemaining are populated
//	}
//
// # Retries
//
// Read calls auto-retry transient 5xx and transport errors with exponential
// backoff. Write calls (Extract, BatchExtract, InferSchema, Synthesize) retry
// only on transport errors, never on a 5xx — a 5xx after a partial backend run
// may already have billed the call.
//
// The module path is github.com/struct-x-ai/struct-x-sdks/go; the package name
// is structx (the trailing "go" path segment is a Go keyword and cannot name a
// package, so import it as structx).
package structx
