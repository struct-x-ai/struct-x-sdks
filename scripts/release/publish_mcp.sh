#!/usr/bin/env bash
#
# publish_mcp.sh — release the MCP server package (structx-mcp) to PyPI, and
# print the community-registry submission checklist.
#
# ⚠️ ORDER: structx-mcp depends on structx-sdk>=0.5.0. Publish the SDK FIRST
#    (publish_python.sh) so pip can resolve the dep from a fresh env.
#
# TWO paths (mirrors publish_python.sh):
#   --via-ci   (recommended, canonical)  cut + push the `structx-mcp-v<ver>`
#              tag; GitHub Actions (structx-mcp-publish.yml) builds + uploads
#              via PyPI Trusted Publishing (OIDC). No token needed.
#   (default)  MANUAL local fallback: `uv build` + `uv publish` using
#              PYPI_TOKEN. Use --test for TestPyPI.
#
# Flags:
#   --via-ci        publish by pushing the CI trigger tag (no token)
#   --test          upload to TestPyPI (needs TESTPYPI_TOKEN)
#   --dry-run       build + print the upload command; never uploads/pushes
#   --push | --yes  actually create+push the tag / actually upload (else safe)
#   --registries    just print the MCP-registry submission checklist
#
# Env: PYPI_TOKEN (manual path) · TESTPYPI_TOKEN (--test)
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

VIA_CI=0; TEST=0; PUSH=0; REG_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --via-ci) VIA_CI=1 ;;
    --test)   TEST=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --push|--yes) PUSH=1; ASSUME_YES=1 ;;
    --registries) REG_ONLY=1 ;;
    -h|--help) grep -E '^#' "$0" | sed -E 's/^# ?//'; exit 0 ;;
    *) die "unknown flag: $arg" ;;
  esac
done

MCP_VERSION="$(current_mcp_version)"
PYVER_FILE="$(grep -E '__version__' "$MCP_VERSION_FILE" | _extract_quoted)"

print_registry_checklist() {
  hr
  info "MCP community-registry submission — manual, owner-only steps."
  cat >&2 <<EOF
   PRECONDITION: structx-mcp ${MCP_VERSION} must be LIVE on PyPI first
     curl -s https://pypi.org/pypi/structx-mcp/json | jq -r .info.version

   Already scaffolded IN-REPO (nothing more to author):
     • mcp/server.json     — official-registry manifest (io.github.struct-x-ai/structx-mcp, pypi pkg, env vars)
     • mcp/smithery.yaml   — Smithery stdio configSchema + commandFunction
     • mcp/README.md        — carries the <!-- mcp-name: ... --> ownership marker (ships in the wheel)
     • mcp/RELEASE.md       — ready-to-paste mcp.so field block

   A maintainer MUST DO by hand (a repo-scoped agent cannot — each needs a web click
   or the org owner's GitHub identity):
     1. Smithery (highest reach): sign in at https://smithery.ai with GitHub →
        Add server → point at struct-x-ai/struct-x-sdks + mcp/smithery.yaml
     2. Official registry (feeds PulseMCP + aggregators automatically):
          brew install mcp-publisher
          mcp-publisher login github          # org owner identity
          cd mcp && mcp-publisher publish   # uses server.json
          curl "https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.struct-x-ai/structx-mcp"
     3. mcp.so: paste the field block from RELEASE.md at https://mcp.so/submit

   Full detail: RELEASE.md  and  mcp/RELEASE.md
EOF
}

if [ "$REG_ONLY" = "1" ]; then
  info "structx-mcp version: $MCP_VERSION"
  print_registry_checklist
  exit 0
fi

valid_semver "$MCP_VERSION" || die "could not read a valid version from $MCP_PYPROJECT"
[ "$MCP_VERSION" = "$PYVER_FILE" ] || die "version drift: pyproject.toml=$MCP_VERSION vs _version.py=$PYVER_FILE — run bump_version.sh mcp <ver> first."
info "structx-mcp version: $MCP_VERSION"
warn "structx-mcp depends on structx-sdk>=0.5.0 — publish the Python SDK FIRST."

# --- CI path ---------------------------------------------------------------
if [ "$VIA_CI" = "1" ]; then
  [ "$TEST" = "1" ] && die "--test is a manual-upload option; it doesn't apply to --via-ci."
  local_tag="${MCP_TAG_PREFIX}${MCP_VERSION}"
  info "CI publish path → tag ${local_tag} triggers .github/workflows/structx-mcp-publish.yml"
  if [ "$PUSH" != "1" ] && [ "$DRY_RUN" != "1" ]; then
    confirm "Push tag ${local_tag} to origin and let CI publish structx-mcp ${MCP_VERSION} to PyPI?" || die "aborted."
  fi
  push_tag "$local_tag" "structx-mcp ${MCP_VERSION}"
  ok "Verify (~3 min): curl -s https://pypi.org/pypi/structx-mcp/${MCP_VERSION}/json | jq -r .info.version"
  print_registry_checklist
  exit 0
fi

# --- manual local path -----------------------------------------------------
command -v uv >/dev/null 2>&1 || die "uv not found — the manual path needs the repo's uv toolchain."
if [ "$TEST" = "1" ]; then
  REPO_NAME="TestPyPI"; PUBLISH_URL="https://test.pypi.org/legacy/"; TOKEN_VAR="TESTPYPI_TOKEN"
else
  REPO_NAME="PyPI"; PUBLISH_URL=""; TOKEN_VAR="PYPI_TOKEN"
fi
warn "Manual local upload to ${REPO_NAME}. The canonical path is --via-ci (CI + OIDC)."
if [ "$DRY_RUN" != "1" ]; then
  require_env "$TOKEN_VAR" "Export it first, e.g.  export ${TOKEN_VAR}=pypi-XXXX"
fi
TOKEN="${!TOKEN_VAR:-<${TOKEN_VAR}>}"

cd "${REPO_ROOT}/mcp"
run rm -rf dist build
run uv build
run uvx twine check "dist/*"
if [ "$TEST" = "1" ]; then
  run uv publish --publish-url "$PUBLISH_URL" --token "$TOKEN" "dist/*"
else
  run uv publish --token "$TOKEN" "dist/*"
fi

ok "Published structx-mcp ${MCP_VERSION} to ${REPO_NAME} (or dry-ran)."
ok "Verify: curl -s https://pypi.org/pypi/structx-mcp/${MCP_VERSION}/json | jq -r .info.version"
print_registry_checklist
