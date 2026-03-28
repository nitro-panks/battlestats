#!/usr/bin/env bash
# Collect stack metrics for load test baseline and during-test snapshots.
# Usage:
#   ./tests/load/collect_metrics.sh baseline    # One-shot idle snapshot
#   ./tests/load/collect_metrics.sh monitor 15  # Repeat every 15s until Ctrl-C

set -euo pipefail

MODE="${1:-baseline}"
INTERVAL="${2:-15}"
OUTDIR="tests/load/results/$(date +%Y%m%d-%H%M%S)-${MODE}"
mkdir -p "$OUTDIR"

collect_snapshot() {
    local tag="$1"
    echo "--- Snapshot: $tag ---"

    # Docker stats (CPU, memory, net I/O)
    docker stats --no-stream --format \
        "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}\t{{.PIDs}}" \
        > "$OUTDIR/docker-stats-${tag}.txt" 2>&1 || true
    cat "$OUTDIR/docker-stats-${tag}.txt"

    # Redis INFO stats
    docker exec battlestats-redis redis-cli INFO stats 2>/dev/null \
        | grep -E "keyspace_hits|keyspace_misses|instantaneous_ops_per_sec|used_memory_human|connected_clients" \
        > "$OUTDIR/redis-${tag}.txt" 2>/dev/null || echo "Redis unavailable" > "$OUTDIR/redis-${tag}.txt"
    echo "  Redis: $(cat "$OUTDIR/redis-${tag}.txt" | tr '\n' ' ')"

    # RabbitMQ queue depths
    curl -s -u guest:guest "http://localhost:15672/api/queues/%2F" 2>/dev/null \
        | python3 -c "
import sys, json
try:
    qs = json.load(sys.stdin)
    for q in qs:
        print(f\"  {q['name']}: messages={q.get('messages',0)} consumers={q.get('consumers',0)} unacked={q.get('messages_unacknowledged',0)}\")
except: print('  RabbitMQ unavailable')
" > "$OUTDIR/rabbitmq-${tag}.txt" 2>&1 || echo "  RabbitMQ unavailable" > "$OUTDIR/rabbitmq-${tag}.txt"
    cat "$OUTDIR/rabbitmq-${tag}.txt"

    # PostgreSQL active connections (if local db is running)
    docker exec battlestats-db psql -U django -d battlestats -c \
        "SELECT count(*) AS active_connections FROM pg_stat_activity WHERE state = 'active';" \
        > "$OUTDIR/pg-${tag}.txt" 2>/dev/null || echo "  PG unavailable (may be cloud)" > "$OUTDIR/pg-${tag}.txt"
    echo "  PG: $(cat "$OUTDIR/pg-${tag}.txt" | tr '\n' ' ')"

    echo ""
}

if [ "$MODE" = "baseline" ]; then
    echo "Collecting baseline snapshot..."
    collect_snapshot "baseline"
    echo "Saved to $OUTDIR/"
elif [ "$MODE" = "monitor" ]; then
    echo "Monitoring every ${INTERVAL}s (Ctrl-C to stop)..."
    i=0
    while true; do
        collect_snapshot "$(printf '%04d' $i)"
        i=$((i + 1))
        sleep "$INTERVAL"
    done
else
    echo "Usage: $0 {baseline|monitor} [interval_seconds]"
    exit 1
fi
