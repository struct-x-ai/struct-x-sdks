#!/usr/bin/env bash
#
# publish_go.sh — release the Go SDK.
#
# Go has NO publish workflow and NO registry upload: pkg.go.dev + the module
# proxy pick up a correctly-formatted git TAG automatically. Because the Go
# module lives in a monorepo subdirectory (go, module path
# github.com/struct-x-ai/struct-x-sdks/go), the tag MUST be prefixed with that
# subdirectory:
#
#     go/vX.Y.Z            ✅ correct (subdir-prefixed)
#     vX.Y.Z                   ❌ wrong (would version the repo root, not go)
#
# Consumers then install with:
#     go get github.com/struct-x-ai/struct-x-sdks/go@vX.Y.Z
#
# Pushing the tag IS the publish. So this script DEFAULTS TO DRY-RUN and only
# pushes with --push (or --yes). No token is required (auth is your git push
# credentials to origin).
#
# Usage:
#   publish_go.sh [X.Y.Z]        # version defaults to go/version.go
#   publish_go.sh --push         # actually create + push the tag
#   publish_go.sh 0.2.0 --push
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

PUSH=0; VERSION=""
for arg in "$@"; do
  case "$arg" in
    --push|--yes) PUSH=1; ASSUME_YES=1 ;;
    --dry-run) PUSH=0 ;;
    -h|--help) grep -E '^#' "$0" | sed -E 's/^# ?//'; exit 0 ;;
    -*) die "unknown flag: $arg" ;;
    *) VERSION="$arg" ;;
  esac
done

FILE_VERSION="$(current_go_version)"
[ -n "$VERSION" ] || VERSION="$FILE_VERSION"
valid_semver "$VERSION" || die "not a valid version: '$VERSION'"

if [ "$VERSION" != "$FILE_VERSION" ]; then
  warn "Tag version $VERSION != go/version.go ($FILE_VERSION)."
  warn "The const Version and the tag should agree — run: bump_version.sh go $VERSION"
fi

TAG="${GO_TAG_PREFIX}/v${VERSION}"      # e.g. go/v0.1.0
info "Go SDK version: $VERSION   →   tag: ${TAG}"
info "Module path: ${GO_MODULE_PATH}"

# Preflight: the module must at least build before we tag it.
if command -v go >/dev/null 2>&1; then
  ( cd "${REPO_ROOT}/go" && run go build ./... )
else
  warn "go toolchain not found — skipping build preflight."
fi

# Default is DRY-RUN unless the caller explicitly opts into pushing.
if [ "$PUSH" != "1" ]; then
  DRY_RUN=1
  warn "DRY-RUN (default). Re-run with --push to actually create + push the tag."
fi

if [ "$DRY_RUN" != "1" ]; then
  confirm "Create + push tag ${TAG} to origin? This publishes the Go SDK to the module proxy." || die "aborted."
fi

push_tag "$TAG" "structx go sdk ${VERSION}"

hr
ok "Install line for consumers / docs:"
printf '   go get %s@v%s\n' "$GO_MODULE_PATH" "$VERSION" >&2
ok "Verify it resolved (may take a few min for the proxy to index):"
printf '   GOPROXY=https://proxy.golang.org go list -m %s@v%s\n' "$GO_MODULE_PATH" "$VERSION" >&2
printf '   open https://pkg.go.dev/%s@v%s\n' "$GO_MODULE_PATH" "$VERSION" >&2
