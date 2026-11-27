#!/usr/bin/env bash
#
# publish_python.sh — release the Python SDK (structx-sdk) to PyPI.
#
# TWO paths:
#   --via-ci   (recommended, canonical)  cut + push the `sdk-python-v<ver>`
#              tag; GitHub Actions (sdk-python-publish.yml) then builds +
#              tests + uploads via PyPI Trusted Publishing (OIDC). No token
#              needed here — CI holds the trust relationship.
#   (default)  MANUAL local fallback: `uv build` + `uv publish` using
#              PYPI_TOKEN from the environment. Use when CI/OIDC is
#              unavailable, or with --test to push to TestPyPI first.
#
# Flags:
#   --via-ci        publish by pushing the CI trigger tag (no token)
#   --test          upload to TestPyPI instead of PyPI (needs TESTPYPI_TOKEN)
#   --dry-run       build + print the upload command; never uploads/pushes
#   --push | --yes  actually create+push the tag / actually upload (else safe)
#
# Env:
#   PYPI_TOKEN      PyPI API token (pypi-...) for the manual path
#   TESTPYPI_TOKEN  TestPyPI API token, required with --test
#
# The canonical, repo-standard path is --via-ci. This script never conflicts
# with the publish workflow: --via-ci *triggers* it; the manual path is an
# explicit local alternative you opt into.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

VIA_CI=0; TEST=0; PUSH=0
for arg in "$@"; do
  case "$arg" in
    --via-ci) VIA_CI=1 ;;
    --test)   TEST=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --push|--yes) PUSH=1; ASSUME_YES=1 ;;
    -h|--help) grep -E '^#' "$0" | sed -E 's/^# ?//'; exit 0 ;;
    *) die "unknown flag: $arg" ;;
  esac
done

VERSION="$(current_py_version)"
valid_semver "$VERSION" || die "could not read a valid version from $PY_VERSION_FILE"
info "structx-sdk (Python) version: $VERSION"

# --- CI path ---------------------------------------------------------------
if [ "$VIA_CI" = "1" ]; then
  [ "$TEST" = "1" ] && die "--test is a manual-upload option; it doesn't apply to --via-ci."
  local_tag="${PY_TAG_PREFIX}${VERSION}"
  info "CI publish path → tag ${local_tag} triggers .github/workflows/sdk-python-publish.yml"
  if [ "$PUSH" != "1" ] && [ "$DRY_RUN" != "1" ]; then
    confirm "Push tag ${local_tag} to origin and let CI publish structx-sdk ${VERSION} to PyPI?" || die "aborted."
  fi
  push_tag "$local_tag" "structx-sdk ${VERSION}"
  ok  "Verify (~3 min): curl -s https://pypi.org/pypi/structx-sdk/${VERSION}/json | jq -r .info.version"
  exit 0
fi

# --- manual local path -----------------------------------------------------
command -v uv >/dev/null 2>&1 || die "uv not found — the manual path needs the repo's uv toolchain."

if [ "$TEST" = "1" ]; then
  REPO_NAME="TestPyPI"; PUBLISH_URL="https://test.pypi.org/legacy/"; TOKEN_VAR="TESTPYPI_TOKEN"
  VERIFY_URL="https://test.pypi.org/pypi/structx-sdk/${VERSION}/json"
else
  REPO_NAME="PyPI"; PUBLISH_URL=""; TOKEN_VAR="PYPI_TOKEN"
  VERIFY_URL="https://pypi.org/pypi/structx-sdk/${VERSION}/json"
fi

warn "Manual local upload to ${REPO_NAME}. The canonical path is --via-ci (CI + OIDC)."
# Fail LOUDLY before doing any work if the token is missing (real uploads only).
if [ "$DRY_RUN" != "1" ]; then
  require_env "$TOKEN_VAR" "Export it first, e.g.  export ${TOKEN_VAR}=pypi-XXXX  (create at https://pypi.org/manage/account/token/)"
fi
TOKEN="${!TOKEN_VAR:-<${TOKEN_VAR}>}"

cd "${REPO_ROOT}/python"
run rm -rf dist build
run uv build
run uvx twine check "dist/*"

if [ "$TEST" = "1" ]; then
  run uv publish --publish-url "$PUBLISH_URL" --token "$TOKEN" "dist/*"
else
  run uv publish --token "$TOKEN" "dist/*"
fi

ok "Published structx-sdk ${VERSION} to ${REPO_NAME} (or dry-ran)."
ok "Verify: curl -s ${VERIFY_URL} | jq -r .info.version"
