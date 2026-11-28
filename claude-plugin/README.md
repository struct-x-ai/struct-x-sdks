# Struct-X for Claude

Struct-X turns unstructured content (web pages, HTML, markdown, plain text, JSON API responses, emails and PDFs) into validated, typed JSON. You describe the output with a JSON Schema, or pick a ready-made template from the Struct-X catalog, and every extracted field comes back with a confidence score so you can see which values to trust.

This plugin connects Claude to the hosted Struct-X MCP server at `https://api.struct-x.ai/mcp`, and adds a skill that teaches Claude a reliable extraction workflow: reuse a catalog template before writing a schema, check cost before large jobs, and flag low-confidence fields instead of presenting them as facts.

## Requirements

- A Struct-X account and API key (keys start with `sx_`). Create one at [app.struct-x.ai/keys](https://app.struct-x.ai/keys). The free plan includes a daily credit allowance.

Nothing is installed on your machine: Claude Code connects to the hosted server over HTTPS.

## Setup

When you enable the plugin, Claude Code asks for your Struct-X API key. The key is stored in your system's secure credential store, not in a settings file, and is sent only to `https://api.struct-x.ai/mcp`, in the `X-API-Key` header.

To install from this repository in Claude Code:

```bash
claude plugin marketplace add struct-x-ai/struct-x-sdks
claude plugin install struct-x@struct-x
```

## Where it works

The tools work in Claude Code. The hosted server needs your API key in a request header, which chat on claude.ai, the desktop app and mobile, and Cowork can't supply from this plugin, so there only the skill loads. A sign-in connector for those apps is planned.

## Tools

The hosted server provides up to 25 tools, depending on what your account has enabled. The main ones:

| Tool | What it does |
| --- | --- |
| `structx_extract` | Extract JSON from content using an inline JSON Schema or a template slug |
| `structx_extract_from_url` | Fetch a public HTTPS page on the Struct-X side and extract from it in one call (listed when URL fetching is enabled) |
| `structx_extract_document` | Extract from a PDF (text, tables or scanned pages) sent as base64, up to about 3 MB (listed when document extraction is enabled for your account) |
| `structx_extract_batch` | Extract from up to 100 items in one call |
| `structx_infer_schema` | Draft a JSON Schema from a content sample |
| `structx_recommend_templates`, `structx_list_templates`, `structx_get_template` | Find and read catalog templates |
| `structx_template_cost_estimate`, `structx_usage` | Preview credit cost and check remaining credits |
| `structx_canonical_project` | Map an extracted record onto frameworks such as OpenTelemetry or OCSF |

The rest manage saved schemas and synthetic datasets on your account.

## What this plugin runs, sends and stores

- **Runs:** nothing on your machine. Claude Code connects to the hosted MCP server at `https://api.struct-x.ai/mcp` over HTTPS.
- **Sends:** the content, schemas and options Claude passes to a tool, together with your API key in the `X-API-Key` header, to `https://api.struct-x.ai/mcp`. Nothing is sent anywhere else.
- **Fetches:** `structx_extract_from_url` asks the Struct-X servers to fetch the page you name; your machine makes no request to that site. The servers fetch public HTTPS URLs on port 443 only, refuse private and link-local addresses, follow no redirects, accept textual responses only and stop at 500 KB.
- **Reads local files:** none. `structx_extract_document` takes the PDF's bytes as base64 in the tool call; the plugin never reads your disk.
- **Stores:** saving a schema or a synthetic dataset stores it on your Struct-X account. With an API key, the plugin saves, changes and deletes only your personal schemas: binding a schema to an organization, sharing it, or offering it to the public community catalog is done in the Struct-X dashboard. The delete tools permanently remove saved schemas and datasets.
- **Bills:** extraction, schema inference and dataset tools use credits from your Struct-X plan.

How the Struct-X API handles your data is described in the [privacy policy](https://struct-x.ai/privacy) and [terms of service](https://struct-x.ai/terms).

## Support

- Documentation: [struct-x.ai/docs](https://struct-x.ai/docs)
- Support: [struct-x.ai/support](https://struct-x.ai/support) or support@struct-x.ai
- Source: [github.com/struct-x-ai/struct-x-sdks](https://github.com/struct-x-ai/struct-x-sdks)

## License

MIT. See [LICENSE](LICENSE).
