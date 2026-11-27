# structx-mcp

<!-- mcp-name: io.github.struct-x-ai/structx-mcp -->

[![PyPI](https://img.shields.io/pypi/v/structx-mcp.svg)](https://pypi.org/project/structx-mcp/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Model Context Protocol (MCP) server for **[struct-x](https://struct-x.ai)** — install in Claude Desktop, Cursor, Windsurf, or any MCP-compatible client and give your agent first-class structured-extraction tools.

```
You: extract the product details from this Aeron chair page →
     [pastes URL or HTML]
Agent: [calls structx_extract under the hood, returns typed JSON]
     {
       "title": "Aeron Chair",
       "price_cents": 179500,
       "brand": "Herman Miller",
       …
     }
```

No HTTP plumbing, no schema-writing detours — the agent just *uses* extraction.

## Install

```bash
pip install structx-mcp
```

Then add to your MCP client's config.

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "structx": {
      "command": "structx-mcp",
      "env": {
        "STRUCTX_API_KEY": "sx_..."
      }
    }
  }
}
```

Restart Claude Desktop. Confirm by typing `/mcp` and verifying `structx` is listed.

### Cursor

`.cursor/mcp.json` (project-scope) or `~/.cursor/mcp.json` (user-scope):

```json
{
  "mcpServers": {
    "structx": {
      "command": "structx-mcp",
      "env": { "STRUCTX_API_KEY": "sx_..." }
    }
  }
}
```

### Windsurf / any other MCP client

Same pattern — `command: "structx-mcp"`, `env: { STRUCTX_API_KEY: ... }`. The server speaks the standard MCP stdio protocol.

## Tools the agent gets

**26 tools**, grouped below.

### Extraction

| Tool | What it does |
|---|---|
| **`structx_extract`** | Extract structured JSON from raw content (HTML / markdown / text / JSON / emails) using a JSON Schema you provide OR a pre-built template slug. Returns per-field confidence scores. |
| **`structx_extract_batch`** | Extract from up to 100 content items in one call — each with its own schema/template + tier, shared options. Per-item billing is metered server-side; surfaces `total_credits_used`. |
| **`structx_extract_from_url`** | Fetch a URL and extract in one call. Sandboxed (HTTPS only, public DNS only, 10MB cap, no redirects). Replaces the two-call `fetch_url → extract` pattern. |
| **`structx_extract_document`** | Extract from a PDF (text, tables or scanned pages): pass a local `file_path` or `document_base64`, plus a schema or template. Per-field confidence is reported by the model. PDF only (5-20 MB by plan); not enabled on every account yet. Uses `structx-sdk` 0.5.0+, which installs with this package. |
| **`structx_infer_schema`** | Don't have a schema yet? Paste raw content, get a draft schema back with per-field rationales. Plus matching templates from the public catalog. |

### Catalog & templates

| Tool | What it does |
|---|---|
| **`structx_list_templates`** | Browse every public template — saves the agent from hand-writing a schema for common formats (Stripe events, GitHub issues, product pages, news articles). |
| **`structx_get_template`** | Fetch one template's full JSON Schema + metadata by slug. |
| **`structx_template_cost_estimate`** | Estimate field count, tokens, and credits for a template at a given tier — no model call. Returns a per-tier breakdown. |
| **`structx_template_versions`** | List the version history of a template family by its bare slug. |
| **`structx_list_categories`** | Browse the catalog's category → family → vendor hierarchy. |
| **`structx_recommend_templates`** | Find existing templates (platform + community + your own) matching content or a draft schema. |
| **`structx_augment_schema`** | Refine a draft schema with natural-language guidance (needs an event_id from infer/recommend). |

### Canonical projection

| Tool | What it does |
|---|---|
| **`structx_canonical_frameworks`** | List the canonical normalization frameworks (OTel, OCSF, …) with versions. |
| **`structx_canonical_project`** | Project an extracted record onto one or more frameworks — deterministic, never re-runs the model, reports unmapped fields. |

### Personal schemas

| Tool | What it does |
|---|---|
| **`structx_list_my_schemas`** | List schemas saved to the caller's account. |
| **`structx_get_my_schema`** | Fetch one saved schema by slug. |
| **`structx_save_my_schema`** | Persist a JSON Schema to the account (opt into community promotion via `publishable_consent`). |
| **`structx_update_my_schema`** | Patch a saved schema (pass only changed fields). |
| **`structx_delete_my_schema`** | Permanently delete a saved schema. |

### Synthetic datasets

| Tool | What it does |
|---|---|
| **`structx_synthesize_dataset`** | Generate an anonymized, schema-valid synthetic dataset from a template or inline schema, shaped by free-text directives. |
| **`structx_list_datasets`** | List the caller's saved synthetic datasets (metadata only). |
| **`structx_get_dataset`** | Fetch one saved dataset in full (records + manifest). |
| **`structx_export_dataset`** | Download a saved dataset as json / jsonl / csv. |
| **`structx_delete_dataset`** | Delete one of the caller's saved datasets. |

### Account & community

| Tool | What it does |
|---|---|
| **`structx_usage`** | Current credit usage for the API key. Pre-flight check before batches, or post-mortem after a `RateLimitError`. |
| **`structx_request_withdrawal`** | Request removal of a community template the caller authored. |

## Why use an MCP server vs. the SDK directly?

The Python SDK ([`structx-sdk`](https://pypi.org/project/structx-sdk/)) is for building **applications**. This MCP package is for putting struct-x **inside the AI tools you already use** — Claude Desktop, Cursor, Windsurf. The agent calls these tools natively, no app code required.

Both are first-party; they share the same backend, same credit pool, same API key.

## Security

`structx_extract_from_url` is sandboxed at the network layer:

- HTTPS only (no `http://`, `file://`, `ftp://`, etc.)
- Hostname blocklist for cloud metadata targets (`169.254.169.254`, `metadata.google.internal`, etc.)
- Hostname suffix blocklist for internal-network conventions (`.internal`, `.local`, `.cluster.local`, `.consul`)
- DNS resolution + IP classification — refuses any private, loopback, link-local, multicast, or reserved IP
- No redirect following (one less SSRF vector)
- 10MB response cap

This matches the defenses on the struct-x backend's webhook outbound-URL validator. See [`src/structx_mcp/_url_safety.py`](src/structx_mcp/_url_safety.py) for the implementation.

## Development

```bash
git clone https://github.com/struct-x-ai/struct-x-sdks
cd struct-x/mcp

# Install editable + the SDK from the local checkout (the SDK is in
# the same monorepo at python/).
pip install -e "../python" -e ".[dev]"

pytest -q
```

## License

MIT — see [LICENSE](LICENSE).
