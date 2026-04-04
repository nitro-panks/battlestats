#!/bin/bash
# invoke-enrichment.sh — Trigger one enrichment function invocation.
#
# Each invocation processes multiple batches for up to ~14 minutes.
# Run this every 15 minutes via cron to maintain continuous enrichment.
#
# Usage:
#   ./functions/invoke-enrichment.sh           # fire-and-forget
#   ./functions/invoke-enrichment.sh --wait    # wait for result

set -euo pipefail

if [[ "${1:-}" == "--wait" ]]; then
    echo "Invoking enrichment (waiting for result)..."
    doctl serverless functions invoke enrichment/enrich-batch --full 2>&1
else
    echo "Invoking enrichment (async)..."
    RESULT=$(doctl serverless functions invoke enrichment/enrich-batch --no-wait 2>&1)
    ACTIVATION_ID=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['activationId'])" 2>/dev/null || echo "unknown")
    echo "Activation ID: $ACTIVATION_ID"
    echo "Check result: doctl serverless activations result $ACTIVATION_ID"
fi
