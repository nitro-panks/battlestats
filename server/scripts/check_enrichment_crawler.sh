#!/usr/bin/env bash
# check_enrichment_crawler.sh — One-shot enrichment crawler status report
# Usage: ./server/scripts/check_enrichment_crawler.sh [host]
# Default host: battlestats.online

set -euo pipefail

HOST="${1:-battlestats.online}"

echo "========================================"
echo "  Enrichment Crawler Status Report"
echo "  Host: $HOST"
echo "  Time: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "========================================"

# Single SSH call to collect all data from the droplet
ssh "root@${HOST}" bash -s <<'REMOTE_SCRIPT'
set -euo pipefail

echo
echo "## Worker Health"
echo

# Parse systemctl for structured fields
STATUS_RAW=$(systemctl show battlestats-celery-background \
    --property=ActiveState,SubState,MemoryCurrent,MemoryPeak,MemorySwapCurrent,MemorySwapPeak,CPUUsageNSec,NRestarts,ActiveEnterTimestamp \
    2>/dev/null)

ACTIVE_STATE=$(echo "$STATUS_RAW" | grep '^ActiveState=' | cut -d= -f2)
SUB_STATE=$(echo "$STATUS_RAW" | grep '^SubState=' | cut -d= -f2)
MEM_CURRENT=$(echo "$STATUS_RAW" | grep '^MemoryCurrent=' | cut -d= -f2)
MEM_PEAK=$(echo "$STATUS_RAW" | grep '^MemoryPeak=' | cut -d= -f2)
SWAP_CURRENT=$(echo "$STATUS_RAW" | grep '^MemorySwapCurrent=' | cut -d= -f2)
SWAP_PEAK=$(echo "$STATUS_RAW" | grep '^MemorySwapPeak=' | cut -d= -f2)
CPU_NS=$(echo "$STATUS_RAW" | grep '^CPUUsageNSec=' | cut -d= -f2)
RESTARTS=$(echo "$STATUS_RAW" | grep '^NRestarts=' | cut -d= -f2)
STARTED=$(echo "$STATUS_RAW" | grep '^ActiveEnterTimestamp=' | cut -d= -f2-)

# Human-readable memory
fmt_mem() {
    local bytes=$1
    if [ -z "$bytes" ] || [ "$bytes" = "[not set]" ] || [ "$bytes" = "infinity" ]; then
        echo "n/a"
    elif [ "$bytes" -ge 1073741824 ]; then
        echo "$(awk "BEGIN {printf \"%.1f\", $bytes/1073741824}")G"
    elif [ "$bytes" -ge 1048576 ]; then
        echo "$(awk "BEGIN {printf \"%.0f\", $bytes/1048576}")M"
    else
        echo "${bytes}B"
    fi
}

# Worker uptime
if [ -n "$STARTED" ] && [ "$STARTED" != "" ]; then
    STARTED_EPOCH=$(date -d "$STARTED" +%s 2>/dev/null || echo "0")
    NOW_EPOCH=$(date +%s)
    if [ "$STARTED_EPOCH" -gt 0 ]; then
        UPTIME_SEC=$((NOW_EPOCH - STARTED_EPOCH))
        UPTIME_MIN=$((UPTIME_SEC / 60))
        UPTIME_HR=$((UPTIME_MIN / 60))
        REMAINING_MIN=$((UPTIME_MIN % 60))
        if [ "$UPTIME_HR" -gt 0 ]; then
            UPTIME_STR="${UPTIME_HR}h ${REMAINING_MIN}m"
        else
            UPTIME_STR="${UPTIME_MIN}m"
        fi
    else
        UPTIME_STR="unknown"
    fi
else
    UPTIME_STR="unknown"
fi

# CPU in seconds
if [ -n "$CPU_NS" ] && [ "$CPU_NS" != "0" ] && [ "$CPU_NS" != "[not set]" ]; then
    CPU_SEC=$(awk "BEGIN {printf \"%.0f\", $CPU_NS/1000000000}")
    CPU_MIN=$((CPU_SEC / 60))
    CPU_REM=$((CPU_SEC % 60))
    CPU_STR="${CPU_MIN}m ${CPU_REM}s"
else
    CPU_STR="n/a"
fi

# OOM risk assessment
MEM_PCT=""
OOM_RISK="low"
MAX_MEM_PER_CHILD=786432  # from --max-memory-per-child in KB
MAX_MEM_BYTES=$((MAX_MEM_PER_CHILD * 1024))
if [ -n "$MEM_PEAK" ] && [ "$MEM_PEAK" != "[not set]" ] && [ "$MEM_PEAK" != "infinity" ]; then
    # Compare peak to max-memory-per-child (per-worker limit)
    # With 2 workers, effective limit is ~1.5G before system pressure
    SYSTEM_LIMIT=$((MAX_MEM_BYTES * 2))
    if [ "$MEM_PEAK" -gt "$SYSTEM_LIMIT" ]; then
        OOM_RISK="CRITICAL"
    elif [ "$MEM_PEAK" -gt $((SYSTEM_LIMIT * 80 / 100)) ]; then
        OOM_RISK="HIGH"
    elif [ "$MEM_PEAK" -gt $((SYSTEM_LIMIT * 60 / 100)) ]; then
        OOM_RISK="moderate"
    fi
fi

printf "  %-22s %s\n" "State:" "${ACTIVE_STATE} (${SUB_STATE})"
printf "  %-22s %s\n" "Uptime:" "$UPTIME_STR"
printf "  %-22s %s\n" "Memory (current):" "$(fmt_mem "$MEM_CURRENT")"
printf "  %-22s %s\n" "Memory (peak):" "$(fmt_mem "$MEM_PEAK")"
printf "  %-22s %s\n" "Swap (current):" "$(fmt_mem "$SWAP_CURRENT")"
printf "  %-22s %s\n" "Swap (peak):" "$(fmt_mem "$SWAP_PEAK")"
printf "  %-22s %s\n" "CPU time:" "$CPU_STR"
printf "  %-22s %s\n" "Restarts:" "$RESTARTS"
printf "  %-22s %s\n" "OOM risk:" "$OOM_RISK"
printf "  %-22s %s\n" "max-memory-per-child:" "${MAX_MEM_PER_CHILD} KB ($(fmt_mem $MAX_MEM_BYTES))"

# --- Dump bounded journal windows once for all subsequent queries ---
# Using 24h instead of unbounded to avoid slow full-journal scans on large logs.
# BASELINE_ENRICHED accounts for batches completed before the 24h window.
# Update this value when the 24h window no longer covers the start of the crawl.
BASELINE_ENRICHED=0  # players enriched before the 24h journal window

JLOG_24H=$(mktemp)
JLOG_6H=$(mktemp)
JLOG_30M=$(mktemp)
JLOG_3M=$(mktemp)
trap 'rm -f "$JLOG_24H" "$JLOG_6H" "$JLOG_30M" "$JLOG_3M"' EXIT

journalctl -u battlestats-celery-background --since '24 hours ago' --no-pager 2>/dev/null > "$JLOG_24H"
journalctl -u battlestats-celery-background --since '6 hours ago' --no-pager 2>/dev/null > "$JLOG_6H"
journalctl -u battlestats-celery-background --since '30 min ago' --no-pager 2>/dev/null > "$JLOG_30M"
journalctl -u battlestats-celery-background --since '3 min ago' --no-pager 2>/dev/null > "$JLOG_3M"

# --- Redis locks ---
echo
echo "## Lock Status"
echo

CRAWL_LOCKS=$(redis-cli KEYS '*crawl*' 2>/dev/null | grep -v "^$" || true)
ENRICH_LOCKS=$(redis-cli KEYS '*enrich*' 2>/dev/null | grep -v "^$" || true)

if [ -z "$CRAWL_LOCKS" ]; then
    printf "  %-22s %s\n" "Crawl locks:" "none (good)"
else
    printf "  %-22s %s\n" "Crawl locks:" "HELD"
    echo "$CRAWL_LOCKS" | while read -r k; do echo "    $k"; done
fi

if [ -z "$ENRICH_LOCKS" ]; then
    printf "  %-22s %s\n" "Enrichment lock:" "none (not running?)"
else
    printf "  %-22s %s\n" "Enrichment lock:" "held (good — task is active)"
fi

# --- Batch history ---
echo
echo "## Batch History"
echo

JOURNAL_BATCHES=$(grep 'Enrichment pass complete' "$JLOG_24H" \
    | sed "s/.*\[/[/" | cut -d, -f1 | sed 's/\[//' | sed 's/\]//')

WINDOW_BATCHES=$(echo "$JOURNAL_BATCHES" | grep -c '.' 2>/dev/null || echo "0")
TOTAL_ENRICHED=$(( BASELINE_ENRICHED + WINDOW_BATCHES * 500 ))
TOTAL_BATCHES=$(( BASELINE_ENRICHED / 500 + WINDOW_BATCHES ))

RECENT_BATCHES=$(grep 'Enrichment pass complete' "$JLOG_6H" \
    | sed "s/.*\[/[/" | cut -d, -f1 | sed 's/\[//' | sed 's/\]//')

RECENT_COUNT=$(echo "$RECENT_BATCHES" | grep -c '.' 2>/dev/null || echo "0")

printf "  %-22s %s\n" "24h window batches:" "$WINDOW_BATCHES"
printf "  %-22s %s\n" "Total batches:" "$TOTAL_BATCHES (includes baseline)"
printf "  %-22s %s\n" "Total enriched:" "$TOTAL_ENRICHED"
printf "  %-22s %s\n" "Last 6h batches:" "$RECENT_COUNT"
printf "  %-22s %s\n" "Last 6h enriched:" "$((RECENT_COUNT * 500))"

if [ "$RECENT_COUNT" -gt 0 ]; then
    FIRST_RECENT=$(echo "$RECENT_BATCHES" | head -1)
    LAST_RECENT=$(echo "$RECENT_BATCHES" | tail -1)
    printf "  %-22s %s\n" "First (6h window):" "$FIRST_RECENT"
    printf "  %-22s %s\n" "Last (6h window):" "$LAST_RECENT"
fi

# --- Throughput ---
echo
echo "## Throughput"
echo

if [ "$RECENT_COUNT" -gt 1 ]; then
    FIRST_RECENT=$(echo "$RECENT_BATCHES" | head -1)
    LAST_RECENT=$(echo "$RECENT_BATCHES" | tail -1)
    FIRST_EPOCH=$(date -d "$FIRST_RECENT" +%s 2>/dev/null || echo "0")
    LAST_EPOCH=$(date -d "$LAST_RECENT" +%s 2>/dev/null || echo "0")
    if [ "$FIRST_EPOCH" -gt 0 ] && [ "$LAST_EPOCH" -gt "$FIRST_EPOCH" ]; then
        ELAPSED_SEC=$((LAST_EPOCH - FIRST_EPOCH))
        ELAPSED_MIN=$((ELAPSED_SEC / 60))
        ENRICHED_RECENT=$((RECENT_COUNT * 500))
        if [ "$ELAPSED_MIN" -gt 0 ]; then
            RATE_PER_HOUR=$((ENRICHED_RECENT * 60 / ELAPSED_MIN))
            AVG_BATCH_SEC=$((ELAPSED_SEC / (RECENT_COUNT - 1)))
            AVG_BATCH_MIN=$((AVG_BATCH_SEC / 60))
            AVG_BATCH_REM=$((AVG_BATCH_SEC % 60))
            printf "  %-22s %s\n" "Window:" "${ELAPSED_MIN} min"
            printf "  %-22s %s\n" "Avg batch interval:" "${AVG_BATCH_MIN}m ${AVG_BATCH_REM}s"
            printf "  %-22s %s\n" "Throughput:" "~${RATE_PER_HOUR} players/hour"

            # ETA
            REMAINING=$((194000 - TOTAL_ENRICHED))
            if [ "$REMAINING" -gt 0 ] && [ "$RATE_PER_HOUR" -gt 0 ]; then
                ETA_HOURS=$((REMAINING / RATE_PER_HOUR))
                ETA_DAYS=$(awk "BEGIN {printf \"%.1f\", $ETA_HOURS/24}")
                printf "  %-22s %s\n" "Est. remaining:" "~${REMAINING} players"
                printf "  %-22s %s\n" "Est. time to finish:" "~${ETA_HOURS}h (~${ETA_DAYS} days)"
            fi
        else
            echo "  Insufficient time span"
        fi
    else
        echo "  Could not parse timestamps"
    fi
else
    echo "  Need 2+ recent batches to estimate"
fi

# --- Errors ---
echo
echo "## Errors (last 6 hours)"
echo

ENRICH_ERRORS=$(grep -c "Enrichment pass complete.*errors.*[1-9]" "$JLOG_6H" 2>/dev/null) || ENRICH_ERRORS=0
WORKER_LOST=$(grep -c 'WorkerLostError' "$JLOG_6H" 2>/dev/null) || WORKER_LOST=0
SIGTERM_COUNT=$(grep -c 'signal 15 (SIGTERM)' "$JLOG_6H" 2>/dev/null) || SIGTERM_COUNT=0
OOM_KILLS=$(grep -c 'signal 9 (SIGKILL)' "$JLOG_6H" 2>/dev/null) || OOM_KILLS=0

printf "  %-22s %s\n" "Enrichment errors:" "$ENRICH_ERRORS"
printf "  %-22s %s\n" "Worker lost:" "$WORKER_LOST"
printf "  %-22s %s\n" "SIGTERM (graceful):" "$SIGTERM_COUNT"
printf "  %-22s %s\n" "SIGKILL (OOM):" "$OOM_KILLS"

# --- Live progress ---
echo
echo "## Live Progress"
echo

LIVE=$(grep 'Enriched ' "$JLOG_3M" | tail -3)
if [ -z "$LIVE" ]; then
    echo "  No enrichment activity in the last 3 minutes"
    echo
    echo "  Last known enrichment log:"
    grep 'Enriched \|Enrichment pass\|Enrichment re-dispatch' "$JLOG_24H" | tail -3 | sed 's/^/  /'
else
    echo "$LIVE" | sed 's/^/  /'
fi

# --- Clan crawl interference ---
echo
echo "## Clan Crawl Interference"
echo

CRAWL_PAGES=$(grep -c 'Page [0-9]*/[0-9]* —' "$JLOG_30M" || echo "0")
if [ "$CRAWL_PAGES" -gt 0 ]; then
    echo "  WARNING: Clan crawl activity in last 30 min ($CRAWL_PAGES page logs)"
    grep 'Page [0-9]*/[0-9]* —' "$JLOG_30M" | tail -3 | sed 's/^/  /'
else
    echo "  None detected (good)"
fi

# --- Periodic task status ---
echo
echo "## Periodic Crawl Tasks"
echo

cd /opt/battlestats-server/current/server
source /opt/battlestats-server/venv/bin/activate
set -a && source .env && source .env.secrets && set +a
python -c "
import django, os
os.environ['DJANGO_SETTINGS_MODULE'] = 'battlestats.settings'
django.setup()
from django_celery_beat.models import PeriodicTask
for t in PeriodicTask.objects.filter(name__icontains='crawl').order_by('name').values('name', 'enabled'):
    status = 'ENABLED' if t['enabled'] else 'disabled'
    print(f'  {status:>8}  {t[\"name\"]}')
" 2>/dev/null

echo
echo "========================================"
echo "  Report complete"
echo "========================================"
REMOTE_SCRIPT
