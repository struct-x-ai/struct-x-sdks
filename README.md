# Struct-X SDKs

Official client libraries and the MCP server for the [Struct-X](https://struct-x.ai) structured-extraction API: send HTML, text or a PDF plus a JSON Schema, get back validated JSON with per-field confidence.

| Package | Install | Source |
|---|---|---|
| Python SDK | `pip install structx-sdk` | [`python/`](python/) |
| Node / TypeScript SDK | `npm install @struct-x/sdk` | [`node/`](node/) |
| Go SDK | `go get github.com/struct-x-ai/struct-x-sdks/go` | [`go/`](go/) |
| MCP server (for Claude and other MCP clients) | `pip install structx-mcp` | [`mcp/`](mcp/) |
| Claude Code plugin (the MCP server plus an extraction skill) | `claude plugin marketplace add struct-x-ai/struct-x-sdks`, then `claude plugin install struct-x@struct-x` | [`claude-plugin/`](claude-plugin/) |

You need an API key from [struct-x.ai](https://struct-x.ai). Full documentation: [docs.struct-x.ai](https://docs.struct-x.ai).

Each package has its own README and CHANGELOG, and they are versioned independently. The MCP server builds on the Python SDK.

## Quick start (Python)

```python
from structx_sdk import StructX

client = StructX(api_key="sx_...")
result = client.extract(
    content="<html>...product page...</html>",
    schema={"type": "object", "properties": {"name": {"type": "string"}, "price": {"type": "number"}}},
)
print(result.data, result.confidence)
```

## Releases

Releases are tag-driven (see [RELEASE.md](RELEASE.md)): `sdk-python-v*`, `sdk-node-v*` and `structx-mcp-v*` tags publish to PyPI and npm from CI. `go/v*` tags publish the Go module.

## Issues and security

Report bugs and feature requests in [GitHub Issues](https://github.com/struct-x-ai/struct-x-sdks/issues). For vulnerabilities, see [SECURITY.md](SECURITY.md); please don't open a public issue.

## License

MIT. See [LICENSE](LICENSE).
