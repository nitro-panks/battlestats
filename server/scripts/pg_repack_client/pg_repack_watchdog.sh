#!/bin/bash
# Lives on the droplet so it survives the operator's session. Polls the managed
# cluster's own disk gauge and interrupts pg_repack if the volume crosses the
# abort line. SIGINT, not KILL: pg_repack's handler drops its half-built copy,
# which is what actually frees the space.
ABORT_AT="${ABORT_AT:-94}"
. /root/.pgrepack_metrics            # METRICS_USER / METRICS_PASS / METRICS_HOST
LOG=/root/pg_repack_watchdog.log
echo "$(date -u +%T) watchdog armed, abort at ${ABORT_AT}%" >> "$LOG"
seen=0
while true; do
  if pgrep -f "pg_repack-1.5.2/pg_repack" >/dev/null; then seen=1; elif [ "$seen" = 1 ]; then
    echo "$(date -u +%T) pg_repack exited; watchdog done" >> "$LOG"; break; fi
  pct=$(curl -s -m 15 -u "$METRICS_USER:$METRICS_PASS" "https://$METRICS_HOST:9273/metrics" \
        | grep -E '^disk_used_percent\{' | grep pgsql | awk '{print $NF}')
  if [ -n "$pct" ]; then
    echo "$(date -u +%T) disk_used_percent=$pct" >> "$LOG"
    if awk -v p="$pct" -v a="$ABORT_AT" 'BEGIN{exit !(p>=a)}'; then
      echo "$(date -u +%T) ABORT: ${pct}% >= ${ABORT_AT}% -> SIGINT pg_repack" >> "$LOG"
      pkill -INT -f "pg_repack-1.5.2/pg_repack" || true
    fi
  else
    echo "$(date -u +%T) metrics scrape failed" >> "$LOG"
  fi
  sleep 30
done
