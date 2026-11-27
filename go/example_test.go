package structx_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"time"

	structx "github.com/struct-x-ai/struct-x-sdks/go"
)

// ExampleClient_Extract shows a basic structured extraction with an inline
// JSON Schema. It requires a real API key in STRUCTX_API_KEY and network
// access, so it has no "// Output:" line and is compiled-but-not-run by
// `go test`.
func ExampleClient_Extract() {
	client, err := structx.NewClient("") // reads STRUCTX_API_KEY from the env
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

	fmt.Printf("data: %s\n", res.Data)
	fmt.Printf("model: %s  credits: %d\n", res.ModelUsed, res.CreditsUsed)
	for _, fc := range res.FieldConfidences {
		fmt.Printf("  %s => %.2f\n", fc.Field, fc.Confidence)
	}
}

// ExampleClient_Extract_errorHandling demonstrates the typed error taxonomy:
// errors.Is for the category and errors.As for the details.
func ExampleClient_Extract_errorHandling() {
	client, err := structx.NewClient("sx_...",
		structx.WithTimeout(30*time.Second),
		structx.WithMaxRetries(3),
	)
	if err != nil {
		log.Fatal(err)
	}

	_, err = client.Extract(context.Background(), structx.ExtractRequest{
		Content: "…",
		Schema:  json.RawMessage(`{"type":"object"}`),
	})
	switch {
	case errors.Is(err, structx.ErrRateLimit):
		var apiErr *structx.APIError
		errors.As(err, &apiErr)
		fmt.Printf("rate limited; retry after %v, %v credits left\n",
			apiErr.RetryAfter, apiErr.CreditsRemaining)
	case errors.Is(err, structx.ErrValidation):
		fmt.Println("bad input — fix the schema/content")
	case errors.Is(err, structx.ErrTransport):
		fmt.Println("network problem — the request never reached the API")
	case err != nil:
		fmt.Printf("unexpected: %v\n", err)
	}
}

// ExampleClient_BatchExtract shows extracting several items in one call and
// discriminating per-item success from per-item failure.
func ExampleClient_BatchExtract() {
	client, err := structx.NewClient("")
	if err != nil {
		log.Fatal(err)
	}

	batch, err := client.BatchExtract(context.Background(), structx.BatchExtractRequest{
		Items: []structx.BatchItem{
			{Content: "<div>$99 Widget</div>", Schema: json.RawMessage(`{"type":"object"}`)},
			{Content: "event payload…", TemplateSlug: "logs.stripe.event"},
		},
	})
	if err != nil {
		log.Fatal(err)
	}

	fmt.Printf("total credits: %d\n", batch.TotalCreditsUsed)
	for i, item := range batch.Results {
		if item.IsError() {
			fmt.Printf("  item %d failed: %s — %s\n", i, item.Error.Code, item.Error.Error)
			continue
		}
		fmt.Printf("  item %d: %s\n", i, item.Extraction.Data)
	}
}
