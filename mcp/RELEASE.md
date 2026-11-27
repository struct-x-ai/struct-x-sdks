# Release runbook — structx-mcp + structx-sdk

This file is the **MCP-registry-submission** reference for the `structx-mcp` package (the ready-to-paste `server.json` / `smithery.yaml` / mcp.so field block are in "Register on the MCP server registries" below). For the release *pipeline* — publishing all three monorepo packages to PyPI/npm — **`docs/ops/release-checklist.md` is canonical.**

> ⚠️ **Publishing is CI-driven, not manual.** PyPI upload happens via GitHub Actions + PyPI Trusted Publishing (OIDC) on a version tag — there is no `twine upload` to run on your laptop. Do NOT run the manual-upload commands from earlier drafts of this doc; they bypass OIDC and can fail on credentials. See "Publish to PyPI (CI)" below.

> ⚠️ The MCP package depends on `structx-sdk>=0.5.0`. **Publish the SDK FIRST** (tag `sdk-python-v*` before `structx-mcp-v*`) so pip can resolve the dep when someone installs `structx-mcp` from a fresh environment.

## Publish to PyPI (CI)

Publishing is done by GitHub Actions on a version-tag push, via PyPI Trusted Publishing (OIDC). The workflows are `.github/workflows/sdk-python-publish.yml` and `.github/workflows/structx-mcp-publish.yml`. **SDK first, then MCP** (the MCP wheel depends on `structx-sdk>=0.5.0`).

> ⚠️ **Tag prefixes must match the workflow triggers exactly** — `sdk-python-v*` and `structx-mcp-v*`. Older drafts used `sdk-v*` / `mcp-v*`, which do NOT trigger the publish workflows.

```bash
# 1. Bump versions (two files each must match):
#    python/src/structx_sdk/_version.py
#    mcp/src/structx_mcp/_version.py + mcp/pyproject.toml
#    (+ the CHANGELOG in each package)

# 2. SDK first — push the tag; CI builds + tests + publishes:
git tag sdk-python-v0.5.0 -m "structx-sdk 0.5.0 — extract_file (PDF documents)"
git push origin sdk-python-v0.5.0
curl -s https://pypi.org/pypi/structx-sdk/0.5.0/json | jq '.info.version'   # confirm live

# 3. Then MCP:
git tag structx-mcp-v0.4.0 -m "structx-mcp 0.4.0 — 26 tools, adds structx_extract_document"
git push origin structx-mcp-v0.4.0
curl -s https://pypi.org/pypi/structx-mcp/0.4.0/json | jq '.info.version'   # confirm live
```

Local build-verify (optional, before tagging — CI still owns the upload):

```bash
cd mcp && rm -rf dist/ build/ && python -m build && twine check dist/*
python -m venv /tmp/mcp-smoke
/tmp/mcp-smoke/bin/pip install dist/structx_mcp-0.4.0-py3-none-any.whl \
  --extra-index-url https://pypi.org/simple   # structx-sdk must already be live
/tmp/mcp-smoke/bin/python -c "from structx_mcp._tools import ALL_TOOLS; assert len(ALL_TOOLS) == 26, len(ALL_TOOLS); print(f'OK — {len(ALL_TOOLS)} tools')"
```

## Register on the MCP server registries

MCP registries are curated lists of MCP servers that agent hosts (Claude Desktop, Cursor, Windsurf, etc.) and developers browse to discover what's installable.

> **NOT a target:** `github.com/modelcontextprotocol/servers` — that's Anthropic's *reference servers* repo, curated internally; it isn't accepting third-party submissions.

> ⚠️ **PRECONDITION — verify before submitting anything.** Every registry below points
> the consumer at the PyPI package. The "Publish to PyPI (CI)" step above MUST be complete
> and `pip index versions structx-mcp` must show the version you intend to advertise (e.g.
> `0.4.0`) BEFORE you submit to any registry — otherwise every listing builds/installs a
> stale, smaller tool surface. (Historically the live index lagged at `0.1.0`; confirm the
> current version directly with `curl -s https://pypi.org/pypi/structx-mcp/json | jq '.info.version'`.)
> The official-registry ownership marker is now committed in the package README, so it
> ships in the wheel automatically once that version is published (see PulseMCP below).

> ℹ️ **The submission flows have all changed** since this runbook was first drafted:
> Smithery is no longer a registry-repo PR (it's GitHub-connect / CLI / web), mcp.so is a
> web form (not a `chatmcp/mcpso` PR), and PulseMCP now ingests automatically from the
> **official MCP registry** (`registry.modelcontextprotocol.io`) rather than its own form.
> Each section below has a ready-to-use artifact.

> 🔒 **Repo-scope note for automation:** none of these submissions can be completed by an
> agent scoped to `struct-x-ai/struct-x-sdks` alone — they all require either an external web
> click (Smithery, mcp.so) or pushing to the official registry via the `mcp-publisher`
> CLI with the maintainer's GitHub identity (PulseMCP). The artifacts here are
> copy-paste-ready so the manual step is a few minutes.

### Smithery.ai (primary — highest distribution)

**Current flow: GitHub-connect deploy from this repo's `smithery.yaml`, OR CLI/web — NOT a PR to `smithery-ai/registry`.** That repo is now issues-only ("This repository is used to track issues and feature requests for Smithery Registry"); it does not accept server-listing PRs. Do not fork it.

Two supported paths:

1. **GitHub-connect (recommended for a stdio/PyPI server):** sign in at https://smithery.ai with GitHub, **Add server / Deploy**, pick `struct-x-ai/struct-x-sdks`, and point it at `mcp/smithery.yaml`. Smithery reads the `smithery.yaml` already in this repo (`startCommand.type: stdio` + `configSchema` + `commandFunction`) and builds the listing + config form from it. This is the manual click the maintainer must do.
2. **CLI (for the URL/MCPB path):** `npm i -g @smithery/cli` then `smithery mcp publish ...`. Only relevant if we ever host a remote HTTP endpoint; for the PyPI stdio package, path 1 is correct.

The repo's `mcp/smithery.yaml` is the ready-to-use artifact — verify it still validates against https://smithery.ai/docs/build/project-config before deploying. Its shape (`startCommand: { type: stdio, configSchema, commandFunction }`) matches current published stdio examples.

### mcp.so

**Current flow: web form (self-registration) at https://mcp.so/submit — NOT a PR to `chatmcp/mcpso`.** The old PR path in the previous draft is stale.

Paste these field values into the form:

```
Name:               Struct-X (structx-mcp)
Description:        Structured-extraction MCP server. Turn raw HTML, markdown,
                    text, JSON or PDFs into validated typed JSON using JSON
                    schemas, with per-field confidence scores. 26 tools cover
                    extraction (incl. batch and PDF documents), catalog
                    browsing + cost/version introspection, personal-schema
                    management, schema inference + augmentation,
                    canonical-framework projection, synthetic-dataset
                    lifecycle, and community publication.
GitHub repo URL:    https://github.com/struct-x-ai/struct-x-sdks/tree/main/mcp
Homepage / website: https://struct-x.ai
Transport:          stdio
Tool count:         26
Category / tags:    data, developer-tools, productivity, structured-extraction,
                    json-schema, llm, agents
Install command:    pip install structx-mcp
Client config (Claude Desktop / Cursor / Windsurf):
                    {
                      "mcpServers": {
                        "structx": {
                          "command": "structx-mcp",
                          "env": { "STRUCTX_API_KEY": "sx_..." }
                        }
                      }
                    }
API key:            Get one at https://app.struct-x.ai/keys
```

### PulseMCP

**Current flow: PulseMCP no longer runs its own intake form — it auto-ingests from the official MCP registry (`registry.modelcontextprotocol.io`) daily, processed weekly.** So the real action is to publish to the official registry; PulseMCP (and other downstream aggregators) pick it up automatically. Email `hello@pulsemcp.com` only for corrections or to expedite after a week.

Publish to the official registry with the `mcp-publisher` CLI and the **`mcp/server.json`** artifact committed alongside this runbook:

```bash
# 1. Install the publisher (Homebrew or the prebuilt binary)
brew install mcp-publisher        # or grab the release binary

# 2. PRECONDITION (already satisfied in-repo) — the official registry verifies PyPI
#    ownership by looking for an `mcp-name` marker in the PUBLISHED package README. It is
#    committed at the top of mcp/README.md:
#        <!-- mcp-name: io.github.struct-x-ai/structx-mcp -->
#    so it ships in the wheel/sdist automatically. Just ensure the version carrying it is
#    live on PyPI (re-publish if the marker was added after the last release).

# 3. Authenticate. The server name uses the io.github.* namespace, so GitHub auth
#    proves ownership of the struct-x-ai org:
mcp-publisher login github

# 4. Publish using the committed manifest (server.json is already filled in):
cd mcp
mcp-publisher publish

# 5. Verify it's live (PulseMCP ingests within ~a day after this):
curl "https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.struct-x-ai/structx-mcp"
```

`mcp-publisher login github` is the manual step the maintainer must run (it needs the org owner's GitHub identity).

### Ready-to-use artifacts in this directory

| Artifact | Registry it serves | Status |
|---|---|---|
| `smithery.yaml` | Smithery (GitHub-connect deploy) | Ready — verify against current docs |
| field block above | mcp.so (web form) | Ready — copy-paste |
| `server.json` | Official registry → PulseMCP (+ other aggregators) | Ready — needs `mcp-publisher login github` (README marker already committed) |

### Recommended order

1. **Publish to PyPI first** (see "Publish to PyPI (CI)" above) — every registry depends on it.
2. **Smithery** — highest discovery surface; GitHub-connect deploy from `smithery.yaml`.
3. **Official registry** (`mcp-publisher publish` with `server.json`) — this is what feeds **PulseMCP** and other aggregators automatically.
4. **mcp.so** — paste the field block into https://mcp.so/submit.

After Smithery / the official registry list you, the Anthropic MCP team may pick the server up for Claude Desktop's "Featured" tab — that's editorial, not an application process.

## Rollback

If a release ships broken: PyPI doesn't allow re-uploading the same version, so bump the version (e.g. 0.4.0 → 0.4.1) in `_version.py` + `pyproject.toml`, update the CHANGELOG, and push a new `structx-mcp-v0.4.1` tag — CI republishes. A same-version re-tag will fail at the upload step.

For an emergency yank:

```bash
# Via the PyPI web UI: project → release → "Options" → "Yank release"
# This is reversible and is the right move for a release that has a
# bad bug but isn't actively malicious.
```
