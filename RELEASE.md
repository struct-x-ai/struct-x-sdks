# Releasing the SDKs + MCP server — maintainer runbook

Concise, repeatable runbook for shipping the four publishable artifacts and
listing the MCP server on the community registries. It drives the automation in
[`scripts/release/`](../scripts/release/); each command below is exact.

- **This doc** = the script-driven maintainer flow (what to type, in order).
- [`ops/release-checklist.md`](ops/release-checklist.md) = the underlying **CI
  pipeline** reference (workflows, one-time trusted-publisher setup, rollback).
- [`../mcp/RELEASE.md`](../mcp/RELEASE.md) = the **MCP
  registry** field artifacts (the copy-paste blocks for mcp.so etc.).

These agree by construction — the scripts are the single mutation path; the two
older docs describe the same mechanics.

## The four artifacts

| Artifact | Package | Registry | Version source(s) | Publish trigger |
|---|---|---|---|---|
| **Python SDK** | `structx-sdk` | PyPI | `python/src/structx_sdk/_version.py` (hatch reads it) | CI on tag `sdk-python-v*` |
| **MCP server** | `structx-mcp` | PyPI | `mcp/pyproject.toml` **and** `.../src/structx_mcp/_version.py` (must match) | CI on tag `structx-mcp-v*` |
| **Node SDK** | `@struct-x/sdk` | npm | `node/package.json` **and** `node/src/version.ts` (must match — a test asserts the User-Agent) | CI on tag `sdk-node-v*` |
| **Go SDK** | `github.com/struct-x-ai/struct-x-sdks/go` | pkg.go.dev / module proxy | `go/version.go` (`const Version`) + the git **tag** | git tag `go/vX.Y.Z` (no CI) |

The four are on **independent version lines** (currently: Python 0.5.0 · Node
0.5.0 · Go 0.2.0 · MCP 0.4.0). Bump each from its own current version.

**How publishing works.** PyPI/npm publishing is **CI-tag-triggered** — push a
tag, GitHub Actions builds + tests + uploads (PyPI via OIDC Trusted Publishing,
npm via `NPM_TOKEN` + provenance). The scripts default to this canonical path;
they *trigger* the existing `.github/workflows/sdk-*-publish.yml` /
`structx-mcp-publish.yml`, they don't replace them. **Go has no CI** — its git
tag is consumed directly by the module proxy, so `publish_go.sh` (tag push) is
the whole publish. `--local` on the PyPI/npm scripts is a manual build+upload
fallback for when CI/OIDC is unavailable.

### Go tag format (monorepo submodule)

The Go module lives at `go`, so its version tags **must** be prefixed with
that subdirectory (Go's multi-module-repo rule — the module path's directory
relative to the repo root is the tag prefix):

```
go/v0.1.0     ✅ correct — the proxy indexes github.com/struct-x-ai/struct-x-sdks/go@v0.1.0
v0.1.0            ❌ wrong   — that tags the repo root, not go
```

`publish_go.sh` emits the correct `go/vX.Y.Z` tag automatically.

## One-time prerequisites

Do these once; then every release is just the per-release flow.

| Prereq | Where / how | Needed for |
|---|---|---|
| PyPI **Trusted Publisher** for `structx-sdk` | pypi.org → project → Settings → Publishing → add pending publisher for this repo + `sdk-python-publish.yml` | CI path (no token) |
| PyPI **Trusted Publisher** for `structx-mcp` | same, workflow `structx-mcp-publish.yml` | CI path (no token) |
| npm org **`@struct-x`**, then **trusted publishing** | First publish: a granular access token (Read and write, scoped to `@struct-x`, bypass 2FA, ~7-day expiry) as the `NPM_TOKEN` secret in the `npm` environment. After that: npmjs.com → `@struct-x/sdk` → Settings → Trusted Publisher → GitHub Actions (`struct-x-ai` / `struct-x-sdks` / `sdk-node-publish.yml` / env `npm`, allow `npm publish`), then delete the token. npm ends direct publishing with granular tokens in January 2027 | CI path (Node) |
| npm you're an **owner/maintainer** of `@struct-x/sdk` | npmjs.com | first Node publish |
| **pkg.go.dev** | nothing — automatic on tag push | Go |
| MCP registries: **GitHub org-owner identity** | you already own `struct-x-ai` | `mcp-publisher` + Smithery |
| **Public source repo** | Done: this repo is public, so the CI Node publish runs with npm `--provenance`, and the Go module proxy indexes `go/vX.Y.Z` tags. PyPI trusted publishing must point at this repo and its workflows | Node via CI, Go |
| *(manual fallback only)* `PYPI_TOKEN` / `TESTPYPI_TOKEN` / `NPM_TOKEN` in your shell | `export PYPI_TOKEN=pypi-…` etc. | `--local` publishes only |

The scripts **fail loudly** if a required token is missing on a manual publish
(they never silently no-op).

## Per-release flow

> **Order:** `structx-mcp` depends on `structx-sdk>=0.5.0` — **publish the
> Python SDK first**, then MCP. Node and Go are independent.
>
> When the MCP floor points at an SDK version that is not on PyPI yet (as in
> SDK 0.5.0 / MCP 0.4.0), run `release.sh py --yes`, wait until PyPI shows the
> new SDK, then run `release.sh mcp --yes`. `release.sh` runs the MCP tests with
> `uv run`, which installs `structx-sdk` from PyPI, so `release.sh all --yes`
> (or `py mcp` together) stops at the MCP tests before anything is published.

> **Commit before you tag.** The CI publish workflows check out the *tagged
> commit*. So: bump → commit/PR → **merge to `main`** → then tag. `release.sh`
> tags the current `HEAD`, so tag only after the bump is on `main`.

### 0. Bump + changelog (all local, nothing published)

```bash
# One artifact, explicit version:
./scripts/release/bump_version.sh py 0.6.0
# …or a bump level applied to that artifact's own current version:
./scripts/release/bump_version.sh node minor      # 0.5.0 → 0.6.0
./scripts/release/bump_version.sh go   patch       # 0.2.0 → 0.2.1
./scripts/release/bump_version.sh mcp  patch       # 0.4.0 → 0.4.1
```

`bump_version.sh` updates every version file for the target (both files for
Node/MCP) and prepends a dated CHANGELOG stub. **Edit the stub** to describe the
release, then commit and merge to `main`.

### 1. Python SDK → PyPI

```bash
# after the bump is merged to main:
./scripts/release/publish_python.sh --via-ci            # prompts, then pushes tag sdk-python-v<ver>
# CI (sdk-python-publish.yml) builds + uploads via OIDC. Verify:
curl -s https://pypi.org/pypi/structx-sdk/0.5.0/json | jq -r .info.version
pip install structx-sdk==0.5.0        # fresh-env smoke
```

Manual fallback (no CI): `PYPI_TOKEN=… ./scripts/release/publish_python.sh`
(add `--test` with `TESTPYPI_TOKEN` to rehearse on TestPyPI first).

### 2. MCP server → PyPI (after the SDK is live)

```bash
./scripts/release/publish_mcp.sh --via-ci               # pushes tag structx-mcp-v<ver>
curl -s https://pypi.org/pypi/structx-mcp/json | jq -r .info.version
pip install structx-mcp==0.4.0 && structx-mcp --help    # entry point prints tools
```

Then submit/refresh the community-registry listings — see
[MCP registry submission](#mcp-registry-submission-290) below.

### 3. Node SDK → npm

```bash
./scripts/release/publish_node.sh --via-ci              # pushes tag sdk-node-v<ver>
npm view @struct-x/sdk version                          # verify
```

Manual fallback: `NPM_TOKEN=… ./scripts/release/publish_node.sh`
(local publish omits `--provenance` — that's CI-only).

### 4. Go SDK → module proxy (tag only)

```bash
./scripts/release/publish_go.sh --push                  # creates + pushes go/v<ver>
# consumers then:
go get github.com/struct-x-ai/struct-x-sdks/go@v0.2.0
# verify it resolved (proxy indexes within a few minutes):
GOPROXY=https://proxy.golang.org go list -m github.com/struct-x-ai/struct-x-sdks/go@v0.2.0
```

`publish_go.sh` **defaults to dry-run**; `--push` is required to actually tag.

### One-shot orchestration

`release.sh` runs the whole thing in dependency order (Python → MCP → Node → Go),
checks version consistency, runs each target's tests, and guards behind a
confirmation:

```bash
./scripts/release/release.sh all --dry-run    # preview the full plan, publish nothing
./scripts/release/release.sh all --yes        # publish all four via the CI-tag path
./scripts/release/release.sh py mcp --yes     # just the two PyPI packages
./scripts/release/release.sh all --local --test   # rehearse PyPI on TestPyPI + npm dry-run
```

Without `--yes` it is always a dry run.

## Upgrade the version referenced on the sites

Good news: **a routine release requires no site edits.** The install snippets on
docs.struct-x.ai (`docs-site/` Starlight quickstart) and the marketing pages
(`prototype/src/…`) reference *package names, not pinned versions*
(`pip install structx-sdk`, `npm install @struct-x/sdk`, the `structx-mcp`
command), and PyPI/npm resolve "latest" automatically. The README badges are
shields.io — they auto-reflect the live version.

The few places that *do* carry a number, and when to touch them:

- **MCP → SDK dependency floor.** `mcp/pyproject.toml` pins
  `structx-sdk>=0.5.0`. Bump this floor only when the MCP server starts relying
  on a newer SDK method — then re-release MCP.
- **The "26 tools" count.** Duplicated in `mcp/smithery.yaml`,
  `README.md`, and `RELEASE.md`. Update them together **only when the MCP tool
  surface changes**, not on every release.
- **`mcp/server.json` version.** Its top-level `version` and
  `packages[0].version` must equal the MCP release (bump them with
  `pyproject.toml`). Its `description` must stay at 100 characters or fewer, or
  `mcp-publisher publish` rejects it.
- **Before any release, sanity-check for stray pins** you may have added:
  ```bash
  grep -rnE 'structx-sdk==|@struct-x/sdk@[0-9]|structx-mcp==|go@v[0-9]' \
    docs-site prototype frontend docs --include='*.md' --include='*.mdx' \
    --include='*.jsx' --include='*.tsx'
  ```
  If that returns a hardcoded version in a snippet, bump it in the same PR.

## MCP registry submission

Listing `structx-mcp` on the community registries so agent hosts (Claude Desktop,
Cursor, Windsurf) discover it. **Precondition:** the version you want to advertise
must be **live on PyPI first** (`curl -s https://pypi.org/pypi/structx-mcp/json | jq -r .info.version`).

Run `./scripts/release/publish_mcp.sh --registries` any time to reprint this
checklist with the current version filled in.

### Already scaffolded in-repo (nothing more to author)

| Artifact | Serves | State |
|---|---|---|
| `mcp/server.json` | Official registry (`registry.modelcontextprotocol.io`) | Filled: `io.github.struct-x-ai/structx-mcp`, PyPI package, env vars |
| `mcp/smithery.yaml` | Smithery | Filled: stdio `configSchema` + `commandFunction` |
| `mcp/README.md` | Official-registry PyPI-ownership check | Carries `<!-- mcp-name: io.github.struct-x-ai/structx-mcp -->` (ships in the wheel) |
| field block in `mcp/RELEASE.md` | mcp.so web form | Copy-paste ready |

### What a maintainer must do by hand

Each step needs a web click or the org owner's GitHub identity — a repo-scoped
agent cannot complete them.

1. **Smithery** (highest reach) — sign in at <https://smithery.ai> with GitHub →
   *Add server / Deploy* → point at `struct-x-ai/struct-x-sdks` +
   `mcp/smithery.yaml`. Smithery builds the listing + config form
   from that file.
2. **Official registry** (auto-feeds PulseMCP + other aggregators):
   ```bash
   brew install mcp-publisher                  # or the prebuilt release binary
   mcp-publisher login github                  # org-owner identity → proves io.github.struct-x-ai
   cd mcp && mcp-publisher publish  # uses the committed server.json
   curl "https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.struct-x-ai/structx-mcp"
   ```
3. **mcp.so** — paste the field block from `RELEASE.md` at
   <https://mcp.so/submit>.

You only re-submit when the **listing content** changes (new tools, new
description). A routine version bump that keeps the same surface needs no
re-submission — the registries point at the PyPI "latest".

## Periodic patch release — quick path

Cut a patch across all four in four commands (adjust which targets you actually
changed):

```bash
./scripts/release/bump_version.sh all patch     # 1. bump every artifact's own patch level + changelog stubs
#    → edit the CHANGELOG stubs, commit, PR, merge to main
./scripts/release/release.sh all --dry-run      # 2. preview the full plan (publishes nothing)
./scripts/release/release.sh all --yes          # 3. publish all four (Python→MCP→Node→Go, CI tags + Go tag)
#    4. verify:
curl -s https://pypi.org/pypi/structx-sdk/json | jq -r .info.version
curl -s https://pypi.org/pypi/structx-mcp/json | jq -r .info.version
npm view @struct-x/sdk version
GOPROXY=https://proxy.golang.org go list -m github.com/struct-x-ai/struct-x-sdks/go@vX.Y.Z
```

MCP registry listings need no action on a same-surface patch (they track PyPI
latest).

## Rollback

PyPI and npm forbid re-uploading the same version — **bump the patch and
re-release** to fix a bad release. Emergency: PyPI "Yank release" (reversible,
hides from default resolution); npm `unpublish` within 72h else `npm deprecate`.
Full detail in [`ops/release-checklist.md`](ops/release-checklist.md#rollback).
