#!/usr/bin/env bash
#
# Hard gate: verify CI is passing on the current HEAD before allowing deploy.
#
# Usage:
#   ./scripts/check_ci_status.sh          # exits 0 if passing, 1 if failing
#   SKIP_CI_CHECK=1 ./scripts/...         # bypass (emergency deploys only)
#
# Called by both deploy scripts as a pre-deploy check.

set -euo pipefail

if [[ "${SKIP_CI_CHECK:-}" == "1" ]]; then
  echo "⚠  SKIP_CI_CHECK=1 — bypassing CI gate"
  exit 0
fi

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
    echo "DEPLOY BLOCKED: CI is failing on ${SHORT_SHA}"
    echo "Fix the failing tests before deploying."
    echo "https://github.com/${REPO}/actions"
    echo ""
    echo "To bypass in an emergency: SKIP_CI_CHECK=1 ./deploy_to_droplet.sh ..."
    exit 1
    ;;
  in_progress|queued|waiting|pending|requested)
    echo "DEPLOY BLOCKED: CI is still running on ${SHORT_SHA} (status: ${CI_CONCLUSION})"
    echo "Wait for CI to complete before deploying."
    echo "https://github.com/${REPO}/actions"
    echo ""
    echo "To bypass in an emergency: SKIP_CI_CHECK=1 ./deploy_to_droplet.sh ..."
    exit 1
    ;;
  not_found)
    echo "DEPLOY BLOCKED: No CI run found for ${SHORT_SHA}"
    echo "Push to main and wait for CI to complete before deploying."
    echo ""
    echo "To bypass in an emergency: SKIP_CI_CHECK=1 ./deploy_to_droplet.sh ..."
    exit 1
    ;;
  *)
    echo "DEPLOY BLOCKED: Could not determine CI status (got: ${CI_CONCLUSION})"
    echo "Check manually: https://github.com/${REPO}/actions"
    echo ""
    echo "To bypass in an emergency: SKIP_CI_CHECK=1 ./deploy_to_droplet.sh ..."
    exit 1
    ;;
esac
