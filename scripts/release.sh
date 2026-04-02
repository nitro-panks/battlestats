#!/usr/bin/env bash
#
# Bump version, commit, tag, and push a release.
#
# Usage:
#   ./scripts/release.sh patch    # 1.2.0 → 1.2.1
#   ./scripts/release.sh minor    # 1.2.0 → 1.3.0
#   ./scripts/release.sh major    # 1.2.0 → 2.0.0
#
# The script will:
#   1. Read the current version from VERSION
#   2. Bump it according to the level argument
#   3. Write the new version to VERSION
#   4. Commit, tag (annotated), and push

set -euo pipefail

LEVEL="${1:-}"

if [[ ! "${LEVEL}" =~ ^(patch|minor|major)$ ]]; then
  echo "Usage: $0 <patch|minor|major>" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION_FILE="${REPO_ROOT}/VERSION"

if [[ ! -f "${VERSION_FILE}" ]]; then
  echo "Error: VERSION file not found at ${VERSION_FILE}" >&2
  exit 1
fi

CURRENT="$(cat "${VERSION_FILE}" | tr -d '[:space:]')"

IFS='.' read -r MAJOR MINOR PATCH <<< "${CURRENT}"

case "${LEVEL}" in
  major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
  minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
  patch) PATCH=$((PATCH + 1)) ;;
esac

NEW="${MAJOR}.${MINOR}.${PATCH}"

echo "Bumping version: ${CURRENT} → ${NEW} (${LEVEL})"

if [[ "${LEVEL}" =~ ^(minor|major)$ ]]; then
  echo ""
  echo "Running required release tests for ${LEVEL} release"
  "${REPO_ROOT}/scripts/run_release_gate.sh"
elif [[ "${LEVEL}" == "patch" ]]; then
  echo ""
  echo "Skipping release tests for patch release"
fi

printf '%s\n' "${NEW}" > "${VERSION_FILE}"

cd "${REPO_ROOT}"

# Show what will be tagged
echo ""
echo "Commits since v${CURRENT}:"
git log --oneline "v${CURRENT}..HEAD" 2>/dev/null || git log --oneline -5
echo ""

git add VERSION
git commit -m "chore: bump version to ${NEW}"
git tag -a "v${NEW}" -m "v${NEW}"
git push
git push origin "v${NEW}"

echo ""
echo "Released v${NEW}"
