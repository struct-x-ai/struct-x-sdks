# @struct-x/sdk

Official Node.js / TypeScript SDK for [struct-x](https://struct-x.ai) — the agent-native structured-extraction API.

- **Zero runtime dependencies.** Uses native `fetch` + `AbortController`. Customer security teams can audit `package.json` in 10 seconds.
- **TypeScript-first** with strict types and IntelliSense for every model.
- **Dual ESM + CJS** — works with `import` and `require`.
- **Same exception hierarchy as the Python SDK** so polyglot stacks can port retry logic 1:1.
- **Credential-redacted errors** out of the box — `api_key`, `password`, `content`, etc. are scrubbed before the body is attached to the exception, so customer logs (Sentry, Datadog, console.error) never accidentally leak secrets.

## Install

```bash
npm install @struct-x/sdk
# or
pnpm add @struct-x/sdk
# or
yarn add @struct-x/sdk
```

Requires **Node 18+** (native `fetch`).

## Quickstart

```ts
import { StructX } from '@struct-x/sdk';

const client = new StructX({ apiKey: 'sx_...' });

const result = await client.extract(
  '<div>$99 Widget</div>',
  {
    schema: {
      type: 'object',
      properties: {
        title:       { type: 'string' },
        price_cents: { type: 'integer' },
      },
    },
  },
);

console.log(result.data);              // { title: 'Widget', price_cents: 9900 }
console.log(result.field_confidences); // [{ field: 'title', confidence: 0.97, ... }]
console.log(result.model_used);        // 'fast' (public tier label, never the vendor name)
console.log(result.credits_used);      // 2
```

## Authentication

Every method authenticates with your API key (`X-API-Key`), set with `apiKey: 'sx_...'` or `STRUCTX_API_KEY`. That includes `usage()` and the personal-schema methods (`listMySchemas`, `getMySchema`, `saveMySchema`, `updateMySchema`, `deleteMySchema`).

With an API key, the personal-schema methods write personal schemas only. Binding a schema to an organization or sharing it is done in the dashboard; the API answers a 403 (`PermissionDeniedError`, code `api_key_personal_schemas_only`) to an org write made with an API key.

A JWT bearer token (`bearerToken: 'ey...'` or `STRUCTX_BEARER_TOKEN`) is optional. When set, the SDK sends it as `Authorization: Bearer` next to the API key; a route that accepts both uses the API key.

## Configuration

```ts
import { StructX, RetryPolicy } from '@struct-x/sdk';

const client = new StructX({
  apiKey: 'sx_...',                       // or STRUCTX_API_KEY env var
  bearerToken: 'ey...',                   // optional — or STRUCTX_BEARER_TOKEN; sent as Authorization: Bearer
  baseUrl: 'https://api.struct-x.ai',      // or STRUCTX_BASE_URL env var
  timeoutMs: 30_000,                      // per-request timeout
  retry: new RetryPolicy({                // partial overrides also accepted: retry: { maxAttempts: 5 }
    maxAttempts: 3,
    initialBackoff: 1.0,
    maxBackoff: 30.0,
    retryOn5xx: true,
    respectRetryAfter: true,
    jitter: 0.2,
  }),
  defaultHeaders: {
    'X-Request-Trace': 'my-trace-id',
  },
});
```

## API

### `extract(content, options?)`

Run a structured extraction. Pass **exactly one** of `schema` or `templateSlug`.

```ts
// Inline schema
await client.extract('<div>$99 Widget</div>', {
  schema: { type: 'object', properties: { price: { type: 'number' } } },
  tier: 'required',
});

// Catalog template
await client.extract('Stripe webhook payload: ...', {
  templateSlug: 'logs.stripe.event',
  tier: 'recommended',
});
```

Tier ladder: `required` (default, cheapest) → `recommended` → `optional` → `extended`. See the [tier docs](https://docs.struct-x.ai/tiers).

### `inferSchema(content, options?)`

LLM-drafts a JSON Schema from raw content + returns matching template recommendations.

```ts
const result = await client.inferSchema('Some HTML or markdown...', {
  contentType: 'html',          // optional; auto-detected if omitted
  k: 5,                          // number of recommendations to return
  returnRecommendations: true,
});

console.log(result.inferred.json_schema);    // a valid JSON Schema you can pass to extract()
console.log(result.recommendations);          // matching catalog templates
```

### `listTemplates()` / `listModels()` / `usage()`

```ts
const templates = await client.listTemplates();
const usage = await client.usage();
```

`listModels()` is operator-only post audit H6 — non-admin keys get `403`.

### Catalog browsing (v0.2.0)

```ts
// Hierarchical browse — category → family → vendor → log_type
const tree = await client.listCategories();

// Pull one template's full JSON Schema + metadata
const stripeEvent = await client.getTemplate('logs.stripe.event');
```

### Personal schemas (v0.2.0)

Full CRUD over the caller's saved schemas — handy when iterating in an
agent loop without round-tripping through the Schema Studio UI.

```ts
// List
const mine = await client.listMySchemas();

// Save (publishableConsent: true is opt-in to admin review for the public catalog)
const saved = await client.saveMySchema({
  name: 'My receipts schema',
  jsonSchema: { type: 'object', properties: { total_cents: { type: 'integer' } } },
  description: 'Internal receipts ingest',
  category: 'finance',
  publishableConsent: false,
});

// Patch — pass only the fields you want to change
await client.updateMySchema(saved.slug as string, {
  description: 'Internal receipts ingest, v2',
});

// Delete
await client.deleteMySchema(saved.slug as string);
```

### Recommend templates, then refine an inferred schema

```ts
// 1. Ask the recommender what existing templates fit your content
const rec = await client.recommendTemplates({
  content: '<receipt>...</receipt>',
  k: 5,
});

// 2. Or draft a schema, then refine it with natural-language guidance.
//    Pass the event_id from inferSchema() and the SAME content you sent it;
//    the server checks the content against that call and rejects a
//    mismatch (ValidationError). Recommend event ids are not accepted.
const content = '<receipt>...</receipt>';
const draft = await client.inferSchema(content);
const refined = await client.augmentSchema(
  draft.event_id,
  'rename event_id to id and add a customer_country field',
  content,
  { contentType: 'html' },     // optional; detected when omitted
);
console.log(refined.inferred.json_schema); // same shape as inferSchema()
// refined.event_id can be refined again with the same content
```

### Community withdrawals (v0.2.0)

If you authored a community template and want it pulled:

```ts
await client.requestWithdrawal('logs.acme.event', {
  mode: 'fork_detach',                  // or 'legal_erasure' to delete forks too
  justification: 'Vendor format deprecated.',
});
```

### Synthetic data (v0.4.0)

Generate an anonymized, schema-valid synthetic dataset from a catalog
template **or** an inline JSON Schema, shaped by free-text directives.
Billed against your API key's credit quota; the returned `manifest`
carries the counts, method breakdown, validation, and the anonymity
attestation.

```ts
const ds = await client.synthesize({
  templateSlug: 'logs.aws.cloudtrail',   // or jsonSchema: { ... }
  count: 50,
  directives: 'mostly access-denied events from two regions',
  mode: 'hybrid',                        // 'hybrid' | 'llm' | 'deterministic'
});
console.log(ds.records, ds.manifest);
```

Manage saved datasets:

```ts
const list = await client.listDatasets();             // metadata only
const full = await client.getDataset('<dataset-id>');  // records + manifest
const csv  = await client.exportDataset('<dataset-id>', 'csv'); // 'jsonl' | 'json' | 'csv'
await client.deleteDataset('<dataset-id>');
```

### Canonical projection (v0.4.0)

Project an already-extracted record onto one or more normalization
frameworks (OCSF, ECS, OTel, …). Pure post-processing — no LLM call.

```ts
const frameworks = await client.listFrameworks();

const projected = await client.project(
  extraction.data as Record<string, unknown>,
  ['ocsf', 'ecs'],
  { templateSlug: 'logs.stripe.event' },  // or schema: { ... } with x-structx-concept annotations
);
console.log(projected.projections);       // { ocsf: { record, coverage, valid }, ... }

// Body-free projection of a template's own stored sample:
const sample = await client.projectTemplate('logs.stripe.event', ['ocsf']);
```

### Batch extract (v0.4.0)

Extract up to 100 items in a single request. **API-key auth only.** Each
item is metered server-side, so the SDK does no client-side credit math —
read `total_credits_used` off the response for accounting.

```ts
const batch = await client.batchExtract([
  { content: '<div>$99 Widget</div>', schema: { type: 'object' } },
  { content: 'event payload…', templateSlug: 'logs.stripe.event', tier: 'recommended' },
]);
console.log(batch.results, batch.total_credits_used);
```

### Template extras (v0.4.0)

```ts
const estimate = await client.getTemplateCostEstimate('logs.stripe.event', 'extended');
const versions = await client.listTemplateVersions('logs.stripe.event');
```

## Errors

Every error is one of:

```
StructXError                      base; matches anything from the SDK
  ApiError                        server returned an error response
    AuthenticationError           401 — fix your API key
    PermissionDeniedError         403 — your key works but lacks scope
    RateLimitError                429 — back off; carries credits info
    ValidationError               400/422 — fix your input
    NotFoundError                 404 — wrong slug or id
    ServerError                   5xx — retry or contact support
  TransportError                  network/timeout, never reached the server
```

Catch by class:

```ts
import { RateLimitError, ValidationError } from '@struct-x/sdk';

try {
  await client.extract('...', { schema: {} });
} catch (e) {
  if (e instanceof RateLimitError) {
    console.warn(`rate limited; retry after ${e.retryAfter}s, ${e.creditsRemaining} credits left`);
  } else if (e instanceof ValidationError) {
    console.error(`bad input: ${e.message}`);
  } else {
    throw e;
  }
}
```

Every `ApiError` carries `statusCode`, `code` (machine-readable from the backend's `{error, code}` envelope), `requestId` (from the `x-request-id` header — include this when reporting bugs), and `responseBody` (with sensitive keys redacted).

### Credential redaction

`ApiError.responseBody` has these keys' values replaced with a `<redacted N chars>` sentinel at exception-construction time:

```
x-api-key, authorization, apikey, api_key,
password, token, secret, private_key,
content
```

This protects against the common pattern of logging the whole error to Sentry / Datadog / console — credentials and extraction payloads never leak.

`ApiError.toString()` also intentionally omits the body entirely as a second layer.

## Retry behavior

By default the SDK retries up to 3 times with exponential backoff (capped at 30s) on:

- **Transport errors** — network down, DNS failure, TLS handshake, timeout. Always retried.
- **5xx responses on read paths** (`/v1/schemas`, `/v1/models`, `/billing/usage`).

The SDK **never** retries 5xx on write paths (`/v1/extract`, `/v1/extract/batch`, `/v1/schemas/infer`, `/v1/schemas/recommendations/{event_id}/augment`) — the server may have partially processed before failing, and re-sending the same content could double-bill.

`Retry-After` headers (on 429 and 503) are honored — the SDK waits exactly that long instead of using its own backoff.

Customize via the `retry` option in the constructor.

## Environment variables

| Variable | Purpose |
|---|---|
| `STRUCTX_API_KEY` | Falls back when `apiKey` not passed to constructor |
| `STRUCTX_BEARER_TOKEN` | Falls back when `bearerToken` not passed — optional JWT, sent as `Authorization: Bearer` |
| `STRUCTX_BASE_URL` | Falls back when `baseUrl` not passed |

## TypeScript

The SDK is written in TypeScript with strict settings and ships full `.d.ts` declarations. Every response model uses an `OpenObject` index signature (`[key: string]: unknown`) so new backend fields appear on the runtime object without breaking older SDK versions — same posture as the Python SDK's `extra="allow"`.

## License

MIT — see [LICENSE](./LICENSE).

## Sibling SDKs

- **Python** — [`structx-sdk`](https://pypi.org/project/structx-sdk/) on PyPI
- **MCP server** — [`structx-mcp`](https://pypi.org/project/structx-mcp/) for Claude Desktop / Cursor / Windsurf
