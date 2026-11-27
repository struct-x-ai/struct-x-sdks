# shellcheck shell=bash
# Shared helpers for the scripts/release/* release-automation scripts.
#
# This file is SOURCED, never executed. It provides: repo-root discovery,
# logging, a DRY_RUN-aware command runner, loud env-var + semver guards, the
# per-artifact version readers/writers, a CHANGELOG stub prepender, and a
# git-tag pusher. All helpers are dependency-light (bash + coreutils + git +
# the repo's uv/npm/go) and safe to re-run.
#
# Design notes:
#   * We never use `sed -i` (BSD vs GNU incompatibility) — every in-place edit
#     goes through a temp file + mv.
#   * Every mutating helper is idempotent: writing a version already present,
#     or a CHANGELOG stub whose heading already exists, is a no-op.
#   * DRY_RUN=1 makes run()/push_tag() print the command instead of executing.

set -euo pipefail

# --- repo root -------------------------------------------------------------
# Resolve the repo root from this file's location so the scripts work no
# matter what the caller's CWD is (and inside a git worktree).
_COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "${_COMMON_DIR}" rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "${REPO_ROOT}" ] || REPO_ROOT="$(cd "${_COMMON_DIR}/../.." && pwd)"

# Artifact paths (source of truth — verified against the repo layout).
PY_VERSION_FILE="${REPO_ROOT}/python/src/structx_sdk/_version.py"
PY_CHANGELOG="${REPO_ROOT}/python/CHANGELOG.md"
NODE_PKG_JSON="${REPO_ROOT}/node/package.json"
NODE_VERSION_TS="${REPO_ROOT}/node/src/version.ts"
NODE_CHANGELOG="${REPO_ROOT}/node/CHANGELOG.md"
GO_VERSION_FILE="${REPO_ROOT}/go/version.go"
GO_CHANGELOG="${REPO_ROOT}/go/CHANGELOG.md"
GO_MODULE_PATH="github.com/struct-x-ai/struct-x-sdks/go"
GO_TAG_PREFIX="go"            # module lives at go → tags are go/vX.Y.Z
MCP_PYPROJECT="${REPO_ROOT}/mcp/pyproject.toml"
MCP_VERSION_FILE="${REPO_ROOT}/mcp/src/structx_mcp/_version.py"
MCP_CHANGELOG="${REPO_ROOT}/mcp/CHANGELOG.md"

# CI tag prefixes (must match the publish-workflow triggers exactly).
PY_TAG_PREFIX="sdk-python-v"
NODE_TAG_PREFIX="sdk-node-v"
MCP_TAG_PREFIX="structx-mcp-v"

# --- logging ---------------------------------------------------------------
_c() { if [ -t 2 ]; then printf '\033[%sm' "$1" >&2; fi; }
info() { _c "0;36"; printf '»  %s\n' "$*" >&2; _c "0"; }
ok()   { _c "0;32"; printf '✓  %s\n' "$*" >&2; _c "0"; }
warn() { _c "0;33"; printf '⚠  %s\n' "$*" >&2; _c "0"; }
die()  { _c "1;31"; printf '✗  %s\n' "$*" >&2; _c "0"; exit 1; }
hr()   { printf '─%.0s' $(seq 1 60) >&2; printf '\n' >&2; }

# --- command runner --------------------------------------------------------
# run <cmd...> : echo the command; execute it unless DRY_RUN=1.
DRY_RUN="${DRY_RUN:-0}"
run() {
  if [ "${DRY_RUN}" = "1" ]; then
    _c "0;90"; printf '   [dry-run] %s\n' "$*" >&2; _c "0"
  else
    _c "0;90"; printf '   $ %s\n' "$*" >&2; _c "0"
    "$@"
  fi
}

# --- guards ----------------------------------------------------------------
# require_env VAR [hint] : fail LOUDLY (exit 1) if VAR is unset/empty.
require_env() {
  local var="$1" hint="${2:-}"
  if [ -z "${!var:-}" ]; then
    _c "1;31"; printf '✗  Required env var %s is not set.\n' "$var" >&2; _c "0"
    [ -n "$hint" ] && printf '   %s\n' "$hint" >&2
    exit 1
  fi
}

valid_semver() {
  [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.-]+)?$ ]]
}

# bump_semver <version> <patch|minor|major>
bump_semver() {
  local ver="${1%%[-+]*}" level="$2" ma mi pa
  IFS='.' read -r ma mi pa <<<"$ver"
  case "$level" in
    patch) pa=$((pa + 1)) ;;
    minor) mi=$((mi + 1)); pa=0 ;;
    major) ma=$((ma + 1)); mi=0; pa=0 ;;
    *) die "bump_semver: level must be patch|minor|major, got '$level'" ;;
  esac
  echo "${ma}.${mi}.${pa}"
}

# resolve_version <current> <arg> : arg is patch|minor|major or an explicit X.Y.Z
resolve_version() {
  local current="$1" arg="$2"
  case "$arg" in
    patch|minor|major) bump_semver "$current" "$arg" ;;
    *) valid_semver "$arg" || die "Not a valid semver or bump level: '$arg'"; echo "$arg" ;;
  esac
}

# confirm <prompt> : return 0 if user answers y (or ASSUME_YES=1), else 1.
confirm() {
  if [ "${ASSUME_YES:-0}" = "1" ]; then return 0; fi
  local reply
  _c "1;33"; printf '?  %s [y/N] ' "$*" >&2; _c "0"
  read -r reply || reply=""
  [[ "$reply" =~ ^[Yy]$ ]]
}

# --- version readers -------------------------------------------------------
_extract_quoted() { sed -E 's/.*["'"'"']([0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.-]+)?)["'"'"'].*/\1/'; }
current_py_version()   { grep -E '^__version__'   "$PY_VERSION_FILE"   | _extract_quoted; }
current_go_version()   { grep -E 'Version =' "$GO_VERSION_FILE" | head -1 | _extract_quoted; }
current_mcp_version()  { grep -E '^version = '    "$MCP_PYPROJECT"     | head -1 | _extract_quoted; }
current_node_version() { grep -E '"version"'      "$NODE_PKG_JSON"     | head -1 | _extract_quoted; }
current_node_ts_version() { grep -E 'VERSION'     "$NODE_VERSION_TS"   | head -1 | _extract_quoted; }

# --- in-place edit helper --------------------------------------------------
# sub_in_file <file> <extended-regex-sed-expr> : temp-file + mv (no sed -i).
sub_in_file() {
  local file="$1" expr="$2" tmp
  tmp="$(mktemp)"
  sed -E "$expr" "$file" >"$tmp"
  mv "$tmp" "$file"
}

# --- version writers (idempotent) ------------------------------------------
set_py_version() {
  local v="$1"
  [ "$(current_py_version)" = "$v" ] && { ok "python already at $v"; return; }
  sub_in_file "$PY_VERSION_FILE" "s/(__version__ = \")[^\"]*(\")/\1${v}\2/"
  ok "python  → $v  ($PY_VERSION_FILE)"
}
set_go_version() {
  local v="$1"
  [ "$(current_go_version)" = "$v" ] && { ok "go already at $v"; return; }
  sub_in_file "$GO_VERSION_FILE" "s/(Version = \")[^\"]*(\")/\1${v}\2/"
  ok "go      → $v  ($GO_VERSION_FILE)"
}
set_mcp_version() {
  local v="$1"
  set_versioned_file "$MCP_VERSION_FILE" "s/(__version__ = \")[^\"]*(\")/\1${v}\2/" "$(current_mcp_version)" "$v" "mcp _version.py"
  # pyproject static version line (anchored at column 0)
  sub_in_file "$MCP_PYPROJECT" "s/^(version = \")[^\"]*(\")/\1${v}\2/"
  ok "mcp     → $v  ($MCP_PYPROJECT + _version.py)"
}
set_node_version() {
  local v="$1"
  sub_in_file "$NODE_PKG_JSON"   "s/(\"version\"[[:space:]]*:[[:space:]]*\")[^\"]*(\")/\1${v}\2/"
  sub_in_file "$NODE_VERSION_TS" "s/(VERSION = ')[^']*(')/\1${v}\2/"
  ok "node    → $v  (package.json + src/version.ts)"
}
# helper used by set_mcp_version to keep _version.py write idempotent
set_versioned_file() {
  local file="$1" expr="$2" cur="$3" want="$4" _label="$5"
  [ "$cur" = "$want" ] || sub_in_file "$file" "$expr"
}

# --- CHANGELOG stub prepender (idempotent) ---------------------------------
# prepend_changelog <changelog> <version> : insert a dated stub above the
# first real release heading (after any "## [Unreleased]" block). No-op if a
# "## [<version>]" heading already exists.
prepend_changelog() {
  local file="$1" version="$2" date insert_line tmp stub
  date="$(date +%Y-%m-%d)"
  if grep -qE "^## \[${version//./\\.}\]" "$file"; then
    ok "changelog already has [$version] ($(basename "$(dirname "$file")")/$(basename "$file"))"
    return
  fi
  # insertion line = first "## [" heading that is NOT [Unreleased]; else EOF+1.
  insert_line="$(awk '/^## \[/ && $0 !~ /^## \[Unreleased\]/ {print NR; exit}' "$file")"
  if [ -z "$insert_line" ]; then
    insert_line="$(($(wc -l <"$file") + 1))"
  fi
  stub="$(mktemp)"
  {
    printf '## [%s] — %s\n\n' "$version" "$date"
    printf '### Added\n- _Describe user-facing changes here before release._\n\n'
  } >"$stub"
  tmp="$(mktemp)"
  head -n "$((insert_line - 1))" "$file" >"$tmp"
  cat "$stub" >>"$tmp"
  tail -n "+${insert_line}" "$file" >>"$tmp"
  mv "$tmp" "$file"
  rm -f "$stub"
  ok "changelog + [$version] stub ($(basename "$(dirname "$file")")/$(basename "$file"))"
}

# --- git tag pusher (the canonical CI-triggering publish action) -----------
# push_tag <tag> <message> : create + push an annotated tag. Idempotent —
# refuses to clobber an existing tag. DRY_RUN prints the commands.
push_tag() {
  local tag="$1" msg="$2"
  if git -C "$REPO_ROOT" rev-parse -q --verify "refs/tags/${tag}" >/dev/null 2>&1; then
    warn "tag ${tag} already exists locally — skipping create (bump the version for a re-release)."
    return 0
  fi
  run git -C "$REPO_ROOT" tag -a "$tag" -m "$msg"
  run git -C "$REPO_ROOT" push origin "$tag"
  if [ "${DRY_RUN}" = "1" ]; then
    ok "(dry-run) would push tag ${tag} — CI would then build + publish."
  else
    ok "pushed tag ${tag} — CI will build + publish."
  fi
}
