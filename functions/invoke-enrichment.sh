#!/bin/bash
# invoke-enrichment.sh — Trigger enrichment function invocations.
#
# Each invocation processes multiple batches for up to ~14 minutes.
# Run this every 15 minutes via cron to maintain continuous enrichment.
#
# Usage:
#   ./functions/invoke-enrichment.sh               # 2 parallel partitions (fire-and-forget)
#   ./functions/invoke-enrichment.sh --wait         # 2 parallel partitions (wait for result)
#   ./functions/invoke-enrichment.sh --partitions 3 # 3 parallel partitions
#   ./functions/invoke-enrichment.sh --partitions 1 # single invocation (legacy mode)

set -euo pipefail

NUM_PARTITIONS="${ENRICH_NUM_PARTITIONS:-2}"
WAIT=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --wait) WAIT=true; shift ;;
        --partitions) NUM_PARTITIONS="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo "Launching $NUM_PARTITIONS enrichment partition(s)..."

for (( p=0; p<NUM_PARTITIONS; p++ )); do
    if [[ "$WAIT" == "true" ]]; then
        echo "Invoking partition $p/$NUM_PARTITIONS (waiting)..."
        doctl serverless functions invoke enrichment/enrich-batch \
            --param "partition:$p" --param "num_partitions:$NUM_PARTITIONS" \
            --full 2>&1 &
    else
        echo "Invoking partition $p/$NUM_PARTITIONS (async)..."
        RESULT=$(doctl serverless functions invoke enrichment/enrich-batch \
            --param "partition:$p" --param "num_partitions:$NUM_PARTITIONS" \
            --no-wait 2>&1)
        ACTIVATION_ID=$(echo "$RESULT" | python3 -c \
            "import json,sys; print(json.load(sys.stdin)['activationId'])" 2>/dev/null || echo "unknown")
        echo "  Partition $p activation: $ACTIVATION_ID"
    fi
done

if [[ "$WAIT" == "true" ]]; then
    echo "Waiting for all partitions to complete..."
    wait
fi

echo "All $NUM_PARTITIONS partition(s) launched."
