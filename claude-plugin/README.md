# Struct-X for Claude

Struct-X turns unstructured content (web pages, HTML, markdown, plain text, JSON API responses, emails and PDFs) into validated, typed JSON. You describe the output with a JSON Schema, or pick a ready-made template from the Struct-X catalog, and every extracted field comes back with a confidence score so you can see which values to trust.

This plugin gives Claude the Struct-X tools through the [`structx-mcp`](https://pypi.org/project/structx-mcp/) MCP server, plus a skill that teaches Claude a reliable extraction workflow: reuse a catalog template before writing a schema, check cost before large jobs, and flag low-confidence fields instead of presenting them as facts.

## Requirements

- A Struct-X account and API key (keys start with `sx_`). Create one at [app.struct-x.ai/keys](https://app.struct-x.ai/keys). The free plan includes a daily credit allowance.
- [uv](https://docs.astral.sh/uv/getting-started/installation/), which provides the `uvx` command the plugin uses to start the MCP server.

## Setup

When you enable the plugin, Claude Code asks for your Struct-X API key. The key is stored in your system's secure credential store, not in a settings file, and is passed to the MCP server as the `STRUCTX_API_KEY` environment variable.

To install from this repository in Claude Code:

```bash
claude plugin marketplace add struct-x-ai/struct-x-sdks
claude plugin install struct-x@struct-x
```

## Where it works

The Struct-X tools run as a local MCP server, so they work in Claude Code. In chat on claude.ai, the desktop app and mobile, Claude doesn't start local MCP servers, so only the skill loads there. Cowork skips the server too, because it doesn't prompt for the API key the server needs.

## Tools

The MCP server provides 26 tools. The main ones:

| Tool | What it does |
| --- | --- |
| `structx_extract` | Extract JSON from content using an inline JSON Schema or a template slug |
| `structx_extract_from_url` | Fetch a public HTTPS page and extract from it in one call |
| `structx_extract_document` | Extract from a PDF (text, tables or scanned pages); not enabled on every account yet |
| `structx_extract_batch` | Extract from up to 100 items in one call |
| `structx_infer_schema` | Draft a JSON Schema from a content sample |
| `structx_recommend_templates`, `structx_list_templates`, `structx_get_template` | Find and read catalog templates |
| `structx_template_cost_estimate`, `structx_usage` | Preview credit cost and check remaining credits |
| `structx_canonical_project` | Map an extracted record onto frameworks such as OpenTelemetry or OCSF |

The rest manage saved schemas and synthetic datasets on your account. The full list is in the [`structx-mcp` README](https://github.com/struct-x-ai/struct-x-sdks/tree/main/mcp#tools-the-agent-gets).

## What this plugin runs, sends and stores

- **Runs:** `uvx structx-mcp==0.4.0`. On first use, `uvx` downloads `structx-mcp` and its dependencies (`structx-sdk`, `mcp`, `httpx`) from PyPI and runs the server on your machine.
- **Sends:** the content, schemas and options Claude passes to a tool, together with your API key, to the Struct-X API at `https://api.struct-x.ai` over HTTPS. Nothing is sent anywhere else.
- **Fetches:** `structx_extract_from_url` downloads the page you name from your machine before sending its text to the API. It accepts public HTTPS URLs only, refuses private and link-local addresses, follows no redirects and stops at 10 MB.
- **Reads local files:** `structx_extract_document` reads a PDF from your disk only when you give it a `file_path`, and uploads that file to the API.
- **Stores:** saving a schema or a synthetic dataset stores it on your Struct-X account. A saved schema is offered for the public community catalog only if you explicitly opt in, and an admin reviews it before publication. The delete tools permanently remove saved schemas and datasets.
- **Bills:** extraction, schema inference and dataset tools use credits from your Struct-X plan.

How the Struct-X API handles your data is described in the [privacy policy](https://struct-x.ai/privacy) and [terms of service](https://struct-x.ai/terms).

## Support

- Documentation: [struct-x.ai/docs](https://struct-x.ai/docs)
- Support: [struct-x.ai/support](https://struct-x.ai/support) or support@struct-x.ai
- Source: [github.com/struct-x-ai/struct-x-sdks](https://github.com/struct-x-ai/struct-x-sdks)

## License

MIT. See [LICENSE](LICENSE).
