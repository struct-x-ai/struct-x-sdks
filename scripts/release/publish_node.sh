#!/usr/bin/env bash
#
# publish_node.sh — release the Node/TypeScript SDK (@struct-x/sdk) to npm.
#
# TWO paths:
#   --via-ci   (recommended, canonical)  cut + push the `sdk-node-v<ver>`
#              tag; GitHub Actions (sdk-node-publish.yml) then typechecks +
#              tests + builds + `npm publish --access public --provenance`
#              using the repo's NPM_TOKEN secret. Provenance attestation is
#              CI-only (needs OIDC), so this is the preferred path.
#   (default)  MANUAL local fallback: `npm ci` + build + `npm publish
#              --access public` using NPM_TOKEN from the environment. No
#              provenance (OIDC unavailable off-CI).
#
# Flags:
#   --via-ci        publish by pushing the CI trigger tag (no token)
#   --dry-run       run `npm publish --dry-run` + `npm pack --dry-run`; no upload/push
#   --push | --yes  actually create+push the tag / actually publish (else safe)
#
# Env:
#   NPM_TOKEN   npm granular access token (Read and write, scoped to @struct-x)
#               (create at https://www.npmjs.com/settings/<user>/tokens)
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

VIA_CI=0; PUSH=0
for arg in "$@"; do
  case "$arg" in
    --via-ci) VIA_CI=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --push|--yes) PUSH=1; ASSUME_YES=1 ;;
    -h|--help) grep -E '^#' "$0" | sed -E 's/^# ?//'; exit 0 ;;
    *) die "unknown flag: $arg" ;;
  esac
done

VERSION="$(current_node_version)"
TS_VERSION="$(current_node_ts_version)"
valid_semver "$VERSION" || die "could not read a valid version from $NODE_PKG_JSON"
[ "$VERSION" = "$TS_VERSION" ] || die "version drift: package.json=$VERSION vs src/version.ts=$TS_VERSION — run bump_version.sh node <ver> first (the test suite asserts the User-Agent)."
info "@struct-x/sdk (Node) version: $VERSION"

# --- CI path ---------------------------------------------------------------
if [ "$VIA_CI" = "1" ]; then
  local_tag="${NODE_TAG_PREFIX}${VERSION}"
  info "CI publish path → tag ${local_tag} triggers .github/workflows/sdk-node-publish.yml"
  if [ "$PUSH" != "1" ] && [ "$DRY_RUN" != "1" ]; then
    confirm "Push tag ${local_tag} to origin and let CI publish @struct-x/sdk ${VERSION} to npm?" || die "aborted."
  fi
  push_tag "$local_tag" "@struct-x/sdk ${VERSION}"
  ok "Verify (~3 min): npm view @struct-x/sdk version"
  exit 0
fi

# --- manual local path -----------------------------------------------------
command -v npm >/dev/null 2>&1 || die "npm not found."
warn "Manual local publish to npm (no provenance — that is CI-only). Canonical path is --via-ci."

# Fail LOUDLY up-front (before the slow npm ci/build) if the token is missing
# on a real publish.
if [ "$DRY_RUN" != "1" ]; then
  require_env NPM_TOKEN "Export it first, e.g.  export NPM_TOKEN=npm_XXXX  (granular access token, Read and write, scoped to @struct-x)"
fi

cd "${REPO_ROOT}/node"
run npm ci
run npm run build
run npm pack --dry-run

if [ "$DRY_RUN" = "1" ]; then
  run npm publish --access public --dry-run
  ok "Dry run only — nothing published."
  exit 0
fi

NPMRC="$(mktemp)"
trap 'rm -f "$NPMRC"' EXIT
printf '//registry.npmjs.org/:_authToken=%s\n' "$NPM_TOKEN" >"$NPMRC"
NPM_CONFIG_USERCONFIG="$NPMRC" run npm publish --access public

ok "Published @struct-x/sdk ${VERSION} to npm."
ok "Verify: npm view @struct-x/sdk@${VERSION} version"
