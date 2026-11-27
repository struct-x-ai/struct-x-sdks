#!/usr/bin/env bash
#
# bump_version.sh — set the version of one or all SDK/MCP artifacts and
# prepend a dated CHANGELOG stub. Local file edits ONLY; never tags,
# builds, publishes, or touches git. Safe + idempotent to re-run.
#
# Usage:
#   scripts/release/bump_version.sh <py|node|go|mcp|all> <X.Y.Z|patch|minor|major>
#
# The version arg is either an explicit semver (X.Y.Z) or a bump LEVEL
# (patch|minor|major) applied to each target's *own* current version.
#
# Where each version lives (source of truth):
#   py    python/src/structx_sdk/_version.py         (hatch dynamic-version reads this)
#   node  node/package.json + node/src/version.ts (must match — test asserts User-Agent)
#   go    go/version.go                               (const Version; git tag carries the real version)
#   mcp   mcp/pyproject.toml + .../_version.py (static; must match)
#
# NOTE: the four artifacts are on INDEPENDENT version lines
# (py/node 0.5.x · go 0.2.x · mcp 0.4.x). Prefer a bump LEVEL with `all`
# (e.g. `all patch`) so each line advances from its own current version.
# Passing an explicit X.Y.Z with `all` forces every artifact to the same
# number and prints a warning.
#
# Examples:
#   bump_version.sh py 0.5.0        # set the Python SDK to 0.5.0
#   bump_version.sh node minor      # 0.4.0 → 0.5.0 for the Node SDK
#   bump_version.sh all patch       # bump each artifact's own patch level
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

[ "$#" -eq 2 ] || die "usage: bump_version.sh <py|node|go|mcp|all> <X.Y.Z|patch|minor|major>"
TARGET="$1"; SPEC="$2"

case "$TARGET" in
  py|node|go|mcp|all) : ;;
  *) die "unknown target '$TARGET' (want: py|node|go|mcp|all)" ;;
esac
case "$SPEC" in
  patch|minor|major) : ;;
  *) valid_semver "$SPEC" || die "version must be X.Y.Z or patch|minor|major, got '$SPEC'" ;;
esac

if [ "$TARGET" = "all" ] && valid_semver "$SPEC"; then
  warn "Setting py+node+go+mcp all to $SPEC — these are independent version lines."
  warn "For a coordinated release you usually want a LEVEL: 'bump_version.sh all patch'."
fi

bump_py()   { local v; v="$(resolve_version "$(current_py_version)"   "$SPEC")"; info "python  $(current_py_version)  → $v"; set_py_version "$v";  prepend_changelog "$PY_CHANGELOG"   "$v"; }
bump_go()   { local v; v="$(resolve_version "$(current_go_version)"   "$SPEC")"; info "go      $(current_go_version)  → $v"; set_go_version "$v";  prepend_changelog "$GO_CHANGELOG"   "$v"; \
              warn "go: remember the git tag carries the real version — run publish_go.sh $v (tag ${GO_TAG_PREFIX}/v${v})."; }
bump_mcp()  { local v; v="$(resolve_version "$(current_mcp_version)"  "$SPEC")"; info "mcp     $(current_mcp_version)  → $v"; set_mcp_version "$v"; prepend_changelog "$MCP_CHANGELOG"  "$v"; }
bump_node() { local v; v="$(resolve_version "$(current_node_version)" "$SPEC")"; info "node    $(current_node_version)  → $v"; set_node_version "$v"; prepend_changelog "$NODE_CHANGELOG" "$v"; }

hr
case "$TARGET" in
  py)   bump_py ;;
  node) bump_node ;;
  go)   bump_go ;;
  mcp)  bump_mcp ;;
  all)  bump_py; bump_node; bump_go; bump_mcp ;;
esac
hr

# Post-bump sanity: node's two version sources must agree.
if [ "$TARGET" = "node" ] || [ "$TARGET" = "all" ]; then
  if [ "$(current_node_version)" != "$(current_node_ts_version)" ]; then
    die "node version drift: package.json=$(current_node_version) vs version.ts=$(current_node_ts_version)"
  fi
fi

ok "Version bump complete. Review the diff, edit the CHANGELOG stub(s), then run the matching publish_*.sh (or release.sh)."
info "Nothing was committed, tagged, built, or published."
