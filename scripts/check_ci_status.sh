#!/usr/bin/env bash
#
# Advisory gate: report CI status on the current HEAD before deploy.
#
# Usage:
#   ./scripts/check_ci_status.sh              # exits 0 and reports CI status
#   REQUIRE_CI_CHECK=1 ./scripts/...         # fail deploys when CI is not green
#   SKIP_CI_CHECK=1 ./scripts/...            # bypass the check entirely
#
# Called by both deploy scripts as a pre-deploy check.

set -euo pipefail

if [[ "${SKIP_CI_CHECK:-}" == "1" ]]; then
  echo "⚠  SKIP_CI_CHECK=1 — bypassing CI gate"
  exit 0
fi

require_ci_check="${REQUIRE_CI_CHECK:-0}"

REPO="nitro-panks/battlestats"
HEAD_SHA="$(git rev-parse HEAD)"
SHORT_SHA="${HEAD_SHA:0:8}"

echo "Checking CI status for ${SHORT_SHA}..."

# Query GitHub Actions for the CI workflow status on this commit
STATUS_JSON="$(curl -sf -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/${REPO}/actions/runs?head_sha=${HEAD_SHA}&per_page=5" 2>/dev/null || echo '{}')"

CI_CONCLUSION="$(echo "${STATUS_JSON}" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    for run in d.get('workflow_runs', []):
        if run.get('name') == 'CI':
            print(run.get('conclusion') or run.get('status') or 'unknown')
            sys.exit(0)
    print('not_found')
except Exception:
    print('error')
" 2>/dev/null || echo 'error')"

case "${CI_CONCLUSION}" in
  success)
    echo "CI passing on ${SHORT_SHA}"
    exit 0
    ;;
  failure)
    echo "CI warning: failing on ${SHORT_SHA}"
    echo "Review the failing checks before deploying."
    echo "https://github.com/${REPO}/actions"
    if [[ "${require_ci_check}" == "1" ]]; then
      echo "REQUIRE_CI_CHECK=1 is set, so deploy remains blocked."
      exit 1
    fi
    exit 0
    ;;
  in_progress|queued|waiting|pending|requested)
    echo "CI warning: still running on ${SHORT_SHA} (status: ${CI_CONCLUSION})"
    echo "Wait for CI to complete if you need a clean signal before deploying."
    echo "https://github.com/${REPO}/actions"
    if [[ "${require_ci_check}" == "1" ]]; then
      echo "REQUIRE_CI_CHECK=1 is set, so deploy remains blocked."
      exit 1
    fi
    exit 0
    ;;
  not_found)
    echo "CI warning: no CI run found for ${SHORT_SHA}"
    echo "Push the commit or trigger CI if you need validation before deploying."
    if [[ "${require_ci_check}" == "1" ]]; then
      echo "REQUIRE_CI_CHECK=1 is set, so deploy remains blocked."
      exit 1
    fi
    exit 0
    ;;
  *)
    echo "CI warning: could not determine CI status (got: ${CI_CONCLUSION})"
    echo "Check manually: https://github.com/${REPO}/actions"
    if [[ "${require_ci_check}" == "1" ]]; then
      echo "REQUIRE_CI_CHECK=1 is set, so deploy remains blocked."
      exit 1
    fi
    exit 0
    ;;
esac
