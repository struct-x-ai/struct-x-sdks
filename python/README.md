# structx-sdk — Python SDK for struct-x

[![PyPI](https://img.shields.io/pypi/v/structx-sdk.svg)](https://pypi.org/project/structx-sdk/)
[![Python](https://img.shields.io/pypi/pyversions/structx-sdk.svg)](https://pypi.org/project/structx-sdk/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Official Python client for **[struct-x](https://struct-x.ai)** — the agent-native structured-extraction API. Send raw content and a JSON Schema, get back validated, typed JSON with per-field confidence scores.

## Install

```bash
pip install structx-sdk
```

## Quickstart

```python
from structx_sdk import StructX

client = StructX(api_key="sx_...")

result = client.extract(
    content="<div><h1>Aeron Chair</h1><span>$1,795.00</span></div>",
    schema={
        "type": "object",
        "required": ["title", "price_cents"],
        "properties": {
            "title":       {"type": "string"},
            "price_cents": {"type": "integer"},
        },
    },
)

print(result.data)
# {'title': 'Aeron Chair', 'price_cents': 179500}

print(result.field_confidences[0])
# FieldConfidence(field='title', confidence=0.96, source_snippet=None)
```

## Use a catalog template instead of an inline schema

```python
result = client.extract(
    content=stripe_webhook_payload,
    template_slug="logs.stripe.event",   # latest published version
)
```

Pin to a specific template version with `family_slug@version`:

```python
template_slug="logs.stripe.event@1.0.0"
```

## Don't have a schema yet? Let the API infer one

```python
inference = client.infer_schema(
    content="<html>… some product page …</html>",
    content_type="html",
)

print(inference.inferred.json_schema)   # ready to pass back to extract()
for f in inference.inferred.fields:
    print(f"{f.name} ({f.type}) — {f.rationale}")

# Plus template recommendations, if any matched:
for r in inference.recommendations:
    print(f"{r.slug} (score={r.score:.2f})")
```

Refine the draft with natural-language guidance. Pass the `event_id` from
`infer_schema()` and the same `content` you sent it; the server checks the
content against that call and raises `ValidationError` if it differs:

```python
refined = client.augment_schema(
    inference.event_id,
    "rename event_id to id and add a customer_country field",
    content="<html>… some product page …</html>",   # the same content passed to infer_schema()
    content_type="html",               # optional; detected when omitted
)
print(refined.inferred.json_schema)    # an InferenceResult, like infer_schema()
# refined.event_id can be refined again with the same content
```

## Batch extraction

Extract from up to 100 items in one call. Each item carries `content` plus
exactly one of `schema` or `template_slug` (and optionally `tier`). Items are
metered individually server-side; the response reports the batch totals.

```python
from structx_sdk import Extraction

batch = client.batch_extract(
    items=[
        {"content": page_a, "template_slug": "ecommerce.product"},
        {"content": page_b, "schema": {"type": "object"}},
    ],
)

print(batch.total_credits_used)
for item in batch.results:                       # order matches the request
    if isinstance(item, Extraction):
        print(item.data)
    else:                                        # ExtractionErrorItem
        print(f"failed: {item.code} — {item.error}")
```

## Canonical projection

Project an already-extracted record onto one or more canonical frameworks
(OTel, OCSF, ECS, …) — a deterministic post-processing step that never re-runs
the model and reports unmapped fields rather than inventing them.

```python
for fw in client.list_canonical_frameworks():
    print(fw.framework, fw.version)

projection = client.project_canonical(
    extracted=result.data,
    frameworks=["ocsf", "ecs"],
    template_slug="logs.stripe.event",          # or schema={...}
)
print(projection.projections["ocsf"].record)
print(projection.projections["ocsf"].coverage)

# Or project a template's own stored sample (no body, cacheable):
sample = client.get_template_canonical("logs.stripe.event", framework="ocsf")
```

## Browse the template catalog

`browse_templates()` returns one page of the catalog (`GET /v1/templates`),
25 templates by default, filtered by tag tokens (`q`), `category`, `vendor`,
a curated `tag` facet or `first_party`. Items carry no JSON Schema; fetch one
with `get_template(slug)`.

```python
page = client.browse_templates(q="stripe", category="PAYMENTS", limit=25)
print(page.total)
for t in page.items:
    print(t.slug, t.field_counts["required"])

while page.next_offset is not None:              # None on the last page
    page = client.browse_templates(q="stripe", offset=page.next_offset)

schema = client.get_template("logs.stripe.event")["json_schema"]
```

`list_templates()` still returns the whole gallery (`GET /v1/schemas`) with
every schema in one response of several MB; prefer `browse_templates()`.

## Templates: cost estimate & version history

```python
est = client.get_template_cost_estimate("logs.stripe.event", tier="extended")
print(est["estimated_field_count"], est["estimated_credits"])

versions = client.list_template_versions("logs.stripe.event")
for v in versions["versions"]:
    print(v["slug"])
```

## Synthetic datasets

Generate with `synthesize(...)`, then manage saved datasets (generation with
`save=True` persists server-side):

```python
index = client.list_synth_datasets(limit=50)
dataset = client.get_synth_dataset(index["datasets"][0]["id"])

raw = client.export_synth_dataset(dataset["id"], format="jsonl")   # -> bytes
open("dataset.jsonl", "wb").write(raw)

client.delete_synth_dataset(dataset["id"])
```

## Async

Same surface, `await`-flavored:

```python
import asyncio
from structx_sdk import AsyncStructX

async def main():
    async with AsyncStructX(api_key="sx_...") as client:
        result = await client.extract(content="…", schema={…})
        print(result.data)

asyncio.run(main())
```

## Authentication

Every method authenticates with your API key (`X-API-Key`), set with `api_key="sx_..."` or `STRUCTX_API_KEY`. That includes `usage()` and the personal-schema methods (`list_my_schemas`, `get_my_schema`, `save_my_schema`, `update_my_schema`, `delete_my_schema`).

With an API key, the personal-schema methods write personal schemas only: sending `org_slug` or `visibility="org_shared"` to `update_my_schema()` raises `PermissionDeniedError` (403, code `api_key_personal_schemas_only`). Binding a schema to an organization or sharing it is done in the dashboard.

Opting a schema into community-catalog promotion (`publishable_consent`) is also done in the dashboard. The SDK sends `publishable_consent` only when you pass it; with an API key, `save_my_schema(publishable_consent=True)`, or an `update_my_schema()` that changes it, raises `PermissionDeniedError` (403, code `publishable_consent_portal_only`).

A JWT bearer token (`bearer_token="ey..."` or `STRUCTX_BEARER_TOKEN`) is optional. When set, the SDK sends it as `Authorization: Bearer` next to the API key; a route that accepts both uses the API key.

## Configuration

| Param          | Default                       | Notes                                                  |
|----------------|-------------------------------|--------------------------------------------------------|
| `api_key`      | `STRUCTX_API_KEY` env var     | Required.                                              |
| `bearer_token` | `STRUCTX_BEARER_TOKEN` env var | Optional. Sent as `Authorization: Bearer` when set; no method requires it. |
| `base_url`     | `STRUCTX_BASE_URL` env var, else `https://api.struct-x.ai` | Override for staging / self-hosted. |
| `timeout`      | `30.0` seconds                | Applied per request.                                   |
| `retry`        | `RetryPolicy(max_attempts=3, …)` | Tune via `RetryPolicy(...)`.                        |
| `default_headers` | `{}`                       | Merged into every request — e.g., for tracing IDs.    |

Pick up credentials from the environment with `StructX.from_env()`:

```python
import os
os.environ["STRUCTX_API_KEY"] = "sx_..."

from structx_sdk import StructX
client = StructX.from_env()
```

## Errors

All exceptions inherit from `StructXError`. Catch the specific class you care about:

```python
from structx_sdk import RateLimitError, ValidationError, ServerError

try:
    result = client.extract(content=…, schema=…)
except RateLimitError as e:
    # 429 — back off; e.retry_after, e.credits_used, e.credits_remaining are populated
    print(f"Sleep {e.retry_after}s. Used {e.credits_used}/{e.credits_used + e.credits_remaining}.")
except ValidationError as e:
    # 400/422 — fix your input. e.code carries the machine-readable reason.
    print(f"Bad input: {e.code} — {e.message}")
except ServerError as e:
    # 5xx — retry or contact support; e.request_id is your handle.
    print(f"Server error (request_id={e.request_id})")
```

Full hierarchy:

```
StructXError
├── TransportError              # network failure — request never reached the server
└── ApiError                    # server responded with an error status
    ├── AuthenticationError     # 401
    ├── PermissionDeniedError   # 403
    ├── NotFoundError           # 404
    ├── ValidationError         # 400, 422
    ├── RateLimitError          # 429  (carries retry_after, credits info)
    └── ServerError             # 5xx
```

## Retries

By default, **read** calls (`browse_templates`, `list_templates`, `list_models`, `usage`, the canonical / template / synth-dataset getters) auto-retry on transient 5xx and connection errors with exponential backoff.

**Write** calls (`extract`, `batch_extract`, `infer_schema`, `augment_schema`) retry ONLY on transport errors, never on 5xx — because a 5xx after a partial backend run may have already billed the call.

Customize via `RetryPolicy`:

```python
from structx_sdk import StructX, RetryPolicy

client = StructX(
    api_key="sx_...",
    retry=RetryPolicy(
        max_attempts=5,
        initial_backoff=0.5,
        max_backoff=60.0,
        retry_on_5xx=True,
        respect_retry_after=True,
    ),
)
```

## Forward compatibility

Response models accept extra fields silently. When the API adds a new field, old SDK versions don't break — they just don't surface it as a typed attribute. Reach it via `result.model_dump()` or `result.__pydantic_extra__`.

## Development

```bash
git clone https://github.com/struct-x-ai/struct-x-sdks
cd struct-x/python
pip install -e ".[dev]"
pytest -q
```

## License

MIT — see [LICENSE](LICENSE).
