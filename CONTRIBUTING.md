# Contributing

Thanks for helping improve the Struct-X SDKs.

- **Bugs and feature requests:** open an issue and include the package, version and a minimal example.
- **Pull requests:** keep each PR to one package where possible, add or update tests, and add a line to that package's CHANGELOG under `[Unreleased]`.
- **Tests:**
  - Python SDK: `cd python && pip install -e ".[dev]" && pytest`
  - MCP server: `cd mcp && pip install -e ../python -e ".[dev]" && pytest`
  - Node SDK: `cd node && npm ci && npm test`
  - Go SDK: `cd go && go test ./...`
- **API behaviour** lives in the Struct-X service, not here. If an SDK needs a new API field, open an issue first.
