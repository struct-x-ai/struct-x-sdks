---
name: structured-extraction
description: Extract validated, typed JSON from web pages, HTML, text, JSON API responses, emails or PDFs with the Struct-X tools. Use when the user wants fields pulled out of content into a fixed shape, wants a JSON Schema drafted from a sample, or wants records normalized to a framework such as OpenTelemetry or OCSF.
---

# Structured extraction with Struct-X

Use the `structx_*` tools to turn content into JSON that matches a schema. Every call that runs a model spends the user's Struct-X credits, so pick the cheapest path that answers the request.

## 1. Choose the schema

Reuse before you write.

1. If the content is a common format (a webhook payload, a log line, a product page, an article, an invoice), call `structx_recommend_templates` with a short sample of the content. If a result fits, use its slug as `template_slug`.
2. If the user names a vendor or format, search the catalog with `structx_list_templates` (`q` takes tag words such as `stripe payments`; results come one page at a time, so follow `next_offset` for more) and read a match with `structx_get_template`.
3. Only when nothing fits, call `structx_infer_schema` on one representative sample (at least 200 characters). It costs more than an extraction, so run it once, not per item. Show the user the drafted fields before extracting at scale, and refine with `structx_augment_schema` when they want changes: pass the `event_id` from the infer call and the same sample as `content` (the API rejects different content).

When you write or edit a schema yourself:

- The root `type` must be `object` or `array`.
- Give every array an `items` schema. A bare `{"type": "array"}` lets each run choose a different item shape.
- Use the user's field names, and add a `description` to any field whose meaning isn't obvious from its name.

## 2. Pick the tool for the source

| Source | Tool |
| --- | --- |
| Text, HTML, markdown or JSON already in the conversation | `structx_extract` with `content` |
| A public web page | `structx_extract_from_url` (HTTPS only; it follows no redirects, so pass the final URL) |
| A PDF on disk | `structx_extract_document` with `file_path`; set `max_credits` for large files. A 404 means document extraction isn't enabled for that key |
| Many items with the same shape | `structx_extract_batch` (up to 100 items per call, 500 KB each) instead of looping `structx_extract` |

With a template, `tier` controls how many fields come back: start with `required` and raise it to `recommended`, `optional` or `extended` only when the user needs more fields.

## 3. Check cost before large jobs

- `structx_template_cost_estimate` previews the credit cost of a template at each tier without running a model.
- `structx_usage` shows credits used and the daily limit. Check it before a batch, and after a rate-limit error to tell a spent quota from a brief throttle.

Tell the user the expected cost before a job that will use a large share of their remaining credits.

## 4. Report results honestly

- Each extraction returns per-field confidence. Call out low-confidence fields when you report results, and don't present them as certain.
- Pass `options: {"confidence_threshold": 0.8}` (or the user's threshold) to have weak fields flagged, and `options: {"include_citations": true}` when the user needs the source snippet behind each value.
- If a field is missing from the content, leave it empty and say so. Don't fill it from your own knowledge.

## 5. Normalize to a framework (optional)

To map an extracted record onto a standard framework, call `structx_canonical_frameworks` for the valid names, then `structx_canonical_project` with the record, the framework names and the same template slug or schema used for extraction. Projection is deterministic and never re-runs the model, and fields it can't map are listed in `coverage` rather than guessed.

## 6. Account changes need the user's go-ahead

- Save a schema with `structx_save_my_schema` only when the user asks to keep it. These tools change only the user's personal schemas; sharing a schema with an organization or offering it to the public community catalog is done in the Struct-X dashboard.
- Before calling `structx_delete_my_schema`, `structx_delete_dataset` or `structx_request_withdrawal`, confirm with the user. These remove data from their account.
