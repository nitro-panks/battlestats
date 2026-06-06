#!/usr/bin/env bash
# check_battle_history_rollup.sh — One-shot battle-history rollup durability report
# Usage: ./server/scripts/check_battle_history_rollup.sh [host] [audit_days]
# Default host: battlestats.online, audit window: 30 days
#
# Read-only: runs the reconcile management command on the droplet and surfaces
# the latest nightly rollup-task log summary. Never mutates.
# Runbook: agents/runbooks/runbook-battle-history-rollup-durability-2026-06-06.md

set -euo pipefail

HOST="${1:-battlestats.online}"
AUDIT_DAYS="${2:-30}"

echo "========================================"
echo "  Battle-History Rollup Durability Report"
echo "  Host: $HOST"
echo "  Audit window: ${AUDIT_DAYS} days"
echo "  Time: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "========================================"

ssh "root@${HOST}" AUDIT_DAYS="${AUDIT_DAYS}" bash -s <<'REMOTE_SCRIPT'
set -euo pipefail

echo
echo "## Rollup Gate State"
echo

ENV_FILE=/opt/battlestats-server/current/server/.env
if [ -f "$ENV_FILE" ]; then
    grep -hoE 'BATTLE_HISTORY_(ROLLUP|RECONCILE)[A-Z_]*=[^ ]*' "$ENV_FILE" \
        | sed 's/^/  /' || echo "  (no BATTLE_HISTORY_ROLLUP/RECONCILE keys set — defaults apply)"
else
    echo "  (env file not found at $ENV_FILE)"
fi

echo
echo "## Reconciliation (BattleEvent vs PlayerDailyShipStats)"
echo

cd /opt/battlestats-server/current/server
source /opt/battlestats-server/venv/bin/activate
set -a && source .env && source .env.secrets && set +a

python manage.py reconcile_battle_history_rollup --audit-days "${AUDIT_DAYS}" 2>&1 \
    | sed 's/^/  /'

echo
echo "## Last Nightly Rollup Run (24h journal)"
echo

ROLLUP_LOG=$(journalctl -u battlestats-celery-background --since '24 hours ago' --no-pager 2>/dev/null \
    | grep -E 'roll_up_player_daily_ship_stats_task' | tail -4)
if [ -z "$ROLLUP_LOG" ]; then
    echo "  No rollup-task activity in the last 24h (sweeper disabled or idle?)"
else
    echo "$ROLLUP_LOG" | sed 's/^/  /'
fi

echo
echo "## Reconcile Task WARNs (24h journal)"
echo

RECON_WARN=$(journalctl -u battlestats-celery-background --since '24 hours ago' --no-pager 2>/dev/null \
    | grep -E 'battle-history rollup (hole|reconciliation)' | tail -6)
if [ -z "$RECON_WARN" ]; then
    echo "  No reconcile-task log lines in the last 24h (task disabled?)"
else
    echo "$RECON_WARN" | sed 's/^/  /'
fi

echo
echo "========================================"
echo "  Report complete"
echo "========================================"
REMOTE_SCRIPT
