#!/usr/bin/env bash
#
# release.sh — orchestrate a coordinated release across the four publishable
# artifacts, in dependency order (Python SDK → MCP → Node SDK → Go SDK).
#
# It does NOT bump versions — run bump_version.sh first, review the diff, and
# edit the CHANGELOG stubs. This script then: checks version consistency,
# (optionally) runs each target's tests, guards behind a confirmation, and
# calls the per-target publish_*.sh scripts.
#
# By DEFAULT it uses the canonical CI path (pushes the sdk-python-v* /
# structx-mcp-v* / sdk-node-v* tags so GitHub Actions publishes, and the
# go/v* tag for the module proxy). --local switches PyPI/npm to the manual
# token-based upload path instead.
#
# SAFETY: nothing is pushed/uploaded unless you pass --yes (or --push). Without
# it, every step is a dry run that prints exactly what it would do.
#
# Usage:
#   release.sh [py node go mcp | all]   [flags]
# Flags:
#   --local        manual token upload for PyPI/npm (needs PYPI_TOKEN/NPM_TOKEN)
#                  instead of the default CI-tag path
#   --test         with --local, target TestPyPI for the Python packages
#   --skip-tests   don't run the per-target test suites
#   --dry-run      force dry run (default when --yes is absent)
#   --yes | --push actually publish (push tags / upload)
#
# Examples:
#   release.sh all --dry-run           # see the full plan, touch nothing
#   release.sh py mcp --yes            # publish SDK then MCP via CI tags
#   release.sh all --local --test      # dry/really upload to TestPyPI + npm dry
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${HERE}/_common.sh"

TARGETS=(); LOCAL=0; TEST=0; SKIP_TESTS=0; PUSH=0
for arg in "$@"; do
  case "$arg" in
    py|node|go|mcp) TARGETS+=("$arg") ;;
    all) TARGETS=(py mcp node go) ;;
    --local) LOCAL=1 ;;
    --test) TEST=1 ;;
    --skip-tests) SKIP_TESTS=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --yes|--push) PUSH=1; ASSUME_YES=1 ;;
    -h|--help) grep -E '^#' "$0" | sed -E 's/^# ?//'; exit 0 ;;
    *) die "unknown arg: $arg" ;;
  esac
done
[ "${#TARGETS[@]}" -gt 0 ] || TARGETS=(py mcp node go)
# de-dupe while preserving the dependency order py → mcp → node → go
ordered=(); for t in py mcp node go; do for u in "${TARGETS[@]}"; do [ "$t" = "$u" ] && { ordered+=("$t"); break; }; done; done
TARGETS=("${ordered[@]}")

[ "$TEST" = "1" ] && [ "$LOCAL" != "1" ] && die "--test only applies with --local (TestPyPI is a manual-upload target)."

# Default to dry-run unless the caller opted into publishing.
if [ "$PUSH" != "1" ]; then DRY_RUN=1; fi

hr
info "Release plan"
info "  targets : ${TARGETS[*]}"
info "  path    : $( [ "$LOCAL" = 1 ] && echo 'manual local upload (--local)' || echo 'CI tag push (canonical)' )"
info "  mode    : $( [ "$DRY_RUN" = 1 ] && echo 'DRY RUN (nothing published)' || echo 'LIVE — will publish' )"
hr

# --- version report (the "confirm versions" step) --------------------------
declare -A VER
VER[py]="$(current_py_version)"
VER[node]="$(current_node_version)"
VER[go]="$(current_go_version)"
VER[mcp]="$(current_mcp_version)"
for t in "${TARGETS[@]}"; do info "version  ${t}  = ${VER[$t]}"; done

# consistency checks for the dual-source artifacts
if printf '%s\n' "${TARGETS[@]}" | grep -qx node; then
  [ "$(current_node_version)" = "$(current_node_ts_version)" ] || die "node: package.json != src/version.ts — bump_version.sh node first."
fi
if printf '%s\n' "${TARGETS[@]}" | grep -qx mcp; then
  [ "$(current_mcp_version)" = "$(grep -E '__version__' "$MCP_VERSION_FILE" | _extract_quoted)" ] || die "mcp: pyproject.toml != _version.py — bump_version.sh mcp first."
fi

# --- tests -----------------------------------------------------------------
run_tests() {
  local t="$1"
  case "$t" in
    py)   run bash -c "cd '${REPO_ROOT}/python' && uv run --extra dev pytest -q" ;;
    mcp)  run bash -c "cd '${REPO_ROOT}/mcp' && uv run --extra dev pytest -q" ;;
    node) run bash -c "cd '${REPO_ROOT}/node' && npm ci && npm run typecheck && npm test" ;;
    go)   run bash -c "cd '${REPO_ROOT}/go' && go test ./..." ;;
  esac
}
if [ "$SKIP_TESTS" = "1" ]; then
  warn "Skipping tests (--skip-tests). CI reruns them on the tag path anyway."
else
  info "Running per-target test suites…"
  for t in "${TARGETS[@]}"; do info "tests: $t"; run_tests "$t"; done
  ok "Tests passed (or dry-ran)."
fi

# --- guard -----------------------------------------------------------------
if [ "$DRY_RUN" != "1" ]; then
  hr
  confirm "PUBLISH ${TARGETS[*]} now ($( [ "$LOCAL" = 1 ] && echo local || echo 'CI tags' ))? This is irreversible on PyPI/npm." \
    || die "aborted — nothing published."
fi

# --- per-target publish ----------------------------------------------------
# Children are self-safe: EXEC_FLAG is --dry-run unless the caller passed
# --yes/--push, so we call them DIRECTLY (not through run()) to surface each
# child's own output even on a dry run.
EXEC_FLAG="$( [ "$PUSH" = "1" ] && echo "--yes" || echo "--dry-run" )"

publish_one() {
  local t="$1"; local -a args
  case "$t" in
    py)
      args=("${HERE}/publish_python.sh")
      [ "$LOCAL" = 1 ] || args+=(--via-ci)
      [ "$LOCAL" = 1 ] && [ "$TEST" = 1 ] && args+=(--test)
      args+=("$EXEC_FLAG") ;;
    mcp)
      args=("${HERE}/publish_mcp.sh")
      [ "$LOCAL" = 1 ] || args+=(--via-ci)
      [ "$LOCAL" = 1 ] && [ "$TEST" = 1 ] && args+=(--test)
      args+=("$EXEC_FLAG") ;;
    node)
      args=("${HERE}/publish_node.sh")
      [ "$LOCAL" = 1 ] || args+=(--via-ci)
      args+=("$EXEC_FLAG") ;;
    go)
      args=("${HERE}/publish_go.sh" "${VER[go]}" "$EXEC_FLAG") ;;
  esac
  info "→ ${args[*]}"
  "${args[@]}"
}

for t in "${TARGETS[@]}"; do hr; info "── publish: $t ──"; publish_one "$t"; done
hr
ok "release.sh done ($( [ "$DRY_RUN" = 1 ] && echo 'DRY RUN — nothing published' || echo 'published' ))."
if printf '%s\n' "${TARGETS[@]}" | grep -qx mcp; then
  info "MCP registry submission is manual — see: ${HERE}/publish_mcp.sh --registries"
fi
