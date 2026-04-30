#!/usr/bin/env bash
# healthcheck.sh — Hit every public endpoint on battlestats.online every 10 min (via cron).
# Validates HTTP 200 AND meaningful response body.
# Usage: ./scripts/healthcheck.sh [BASE_URL]
#   BASE_URL defaults to https://battlestats.online

set -euo pipefail

BASE="${1:-https://battlestats.online}"
LOG_DIR="/home/august/code/archive/battlestats/logs/healthcheck"
mkdir -p "$LOG_DIR"
LOGFILE="$LOG_DIR/healthcheck.log"

PASS=0
FAIL=0
TIMESTAMP=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

# Known test fixtures
PLAYER_NAME="lil_boots"
PLAYER_ID="1031615890"    # lil_boots (pinned warm player)
CLAN_ID="1000067803"      # RAIN
CLAN_SLUG="1000067803-rain"

# ── helpers ──────────────────────────────────────────────────────────────────

log() { echo "$TIMESTAMP  $1" >> "$LOGFILE"; }

# check URL STATUS_CODE BODY_GREP [MIN_BYTES]
#   BODY_GREP  — regex the body MUST match for a pass
#   MIN_BYTES  — minimum Content-Length (default 256)
check() {
    local label="$1" url="$2" body_grep="$3" min_bytes="${4:-256}"
    local tmpfile
    tmpfile=$(mktemp)

    local http_code
    http_code=$(curl -sS -o "$tmpfile" -w '%{http_code}' \
        --max-time 30 --connect-timeout 10 "$url" 2>/dev/null) || http_code="000"

    local size
    size=$(wc -c < "$tmpfile")

    local ok=true reason=""

    if [[ "$http_code" != "200" ]]; then
        ok=false; reason="HTTP $http_code"
    elif (( size < min_bytes )); then
        ok=false; reason="body too small (${size}B < ${min_bytes}B)"
    elif ! grep -qiP "$body_grep" "$tmpfile" 2>/dev/null; then
        ok=false; reason="missing expected content (grep: $body_grep)"
    fi

    if $ok; then
        (( PASS++ )) || true
    else
        log "FAIL  ${label}  ${reason}  url=${url}"
        (( FAIL++ )) || true
    fi

    rm -f "$tmpfile"
}

# ── page routes (HTML) ───────────────────────────────────────────────────────

# Landing page — must contain the search form / app shell
check "page:landing" \
    "$BASE/" \
    "battlestats|warships|player" \
    512

# Player detail — lil_boots is the pinned warm player
check "page:player" \
    "$BASE/player/${PLAYER_NAME}" \
    "${PLAYER_NAME}|player" \
    512

# Clan detail — use a known clan slug (RAIN)
check "page:clan" \
    "$BASE/clan/${CLAN_SLUG}" \
    "clan|rain" \
    512

# ── API: landing / discovery ─────────────────────────────────────────────────

check "api:landing-players-best" \
    "$BASE/api/landing/players?mode=best&limit=5&sort=overall&realm=na" \
    '"name"|"pvp_ratio"' \
    128

check "api:landing-clans-best" \
    "$BASE/api/landing/clans?mode=best&limit=5&sort=overall&realm=na" \
    '"clan_id"|"tag"' \
    128

check "api:landing-player-suggestions" \
    "$BASE/api/landing/player-suggestions?q=lil" \
    '"name"|"pvp_ratio"|\[\]' \
    2

check "api:landing-clan-suggestions" \
    "$BASE/api/landing/clan-suggestions?q=rain" \
    '"tag"|"clan_id"|\[\]' \
    2

# ── API: player data ─────────────────────────────────────────────────────────

check "api:player-detail" \
    "$BASE/api/player/${PLAYER_NAME}/?realm=na" \
    '"player_id"|"clan_tag"' \
    128

check "api:player-summary" \
    "$BASE/api/fetch/player_summary/${PLAYER_ID}?realm=na" \
    '"player_id"|"player_score"|"pvp_battles"' \
    32

# ── API: chart data (player-level) ──────────────────────────────────────────

check "api:tier-data" \
    "$BASE/api/fetch/tier_data/${PLAYER_ID}/?realm=na" \
    '\[|\{|"tier"' \
    16

check "api:type-data" \
    "$BASE/api/fetch/type_data/${PLAYER_ID}/?realm=na" \
    '\[|\{|"type"' \
    16

check "api:activity-data" \
    "$BASE/api/fetch/activity_data/${PLAYER_ID}/?realm=na" \
    '\[|\{|"date"|"battles"' \
    16

check "api:randoms-data" \
    "$BASE/api/fetch/randoms_data/${PLAYER_ID}/?all=true&realm=na" \
    '\[|\{|"ship_name"|"battles"' \
    16

# ── API: population / distributions ─────────────────────────────────────────

check "api:distribution-wr" \
    "$BASE/api/fetch/player_distribution/win_rate/?realm=na" \
    '"bins"|"bucket"|"count"' \
    128

check "api:correlation-tier-type" \
    "$BASE/api/fetch/player_correlation/tier_type/${PLAYER_ID}/?realm=na" \
    '"metric"|"tiles"|"x_labels"' \
    64

check "api:correlation-ranked" \
    "$BASE/api/fetch/player_correlation/ranked_wr_battles/${PLAYER_ID}/?realm=na" \
    '"metric"|"correlation"|"tiles"' \
    64

# ── API: clan data ───────────────────────────────────────────────────────────

check "api:clan-detail" \
    "$BASE/api/clan/${CLAN_ID}?realm=na" \
    '"clan_id"|"tag"|"members"' \
    128

check "api:clan-members" \
    "$BASE/api/fetch/clan_members/${CLAN_ID}?realm=na" \
    '"members"|"player_name"|\[' \
    32

# ── API: sitemap ─────────────────────────────────────────────────────────────

check "api:sitemap-entities" \
    "$BASE/api/sitemap-entities/?realm=na" \
    '"players"|"clans"|\[' \
    32

# ── droplet services ─────────────────────────────────────────────────────────

# Celery Beat dispatches every periodic task (player/ranked refresh, warmers,
# crawl schedules). If it dies, the site keeps serving cached data and every
# HTTP probe above still passes — silent failure. Ship it died undetected for
# 10 days on 2026-04-12 before we noticed manually.
check_systemd_unit() {
    local unit="$1"
    local state
    state=$(ssh -o BatchMode=yes -o ConnectTimeout=5 root@battlestats.online \
        "systemctl is-active $unit" 2>/dev/null) || state="unreachable"

    if [[ "$state" == "active" ]]; then
        (( PASS++ )) || true
    else
        log "FAIL  service:${unit}  state=${state}"
        (( FAIL++ )) || true
    fi
}

check_systemd_unit "battlestats-beat.service"

# ── celery queue depth ──────────────────────────────────────────────────────
#
# Pairs with the beat check above. If a worker hangs (zombie consumer, stuck
# task, crash loop), beat keeps dispatching but nothing drains — the queue
# climbs. Catch within 10 min instead of noticing manually days later.
#
# Thresholds chosen from observed steady-state on 2026-04-22:
#   default/hydration  — drain continuously, normally ≈0. Anything above ~100
#                        means consumers are absent or wedged.
#   background         — absorbs long-running tasks (incremental_player_refresh
#                        runs ~20–40 min, both -c 2 slots can be busy while the
#                        queue backs up to ~900). 2000 gives comfortable headroom
#                        before alerting.
check_queue_depth() {
    local queue="$1" threshold="$2"
    local depth
    depth=$(ssh -o BatchMode=yes -o ConnectTimeout=5 root@battlestats.online \
        "rabbitmqctl list_queues -q name messages --no-table-headers 2>/dev/null | awk -v q='$queue' '\$1==q {print \$2}'" \
        2>/dev/null) || depth=""

    if [[ -z "$depth" ]]; then
        log "FAIL  queue:${queue}  unreachable or missing"
        (( FAIL++ )) || true
        return
    fi

    if (( depth > threshold )); then
        log "FAIL  queue:${queue}  depth=${depth} > ${threshold}"
        (( FAIL++ )) || true
    else
        (( PASS++ )) || true
    fi
}

check_queue_depth "default"    100
check_queue_depth "hydration"  100
check_queue_depth "background" 2000
# crawls runs the multi-day clan crawl on its own worker (-c 1). Steady-state
# is 0 messages while the crawl is active, or 1 between Beat ticks. Anything
# above 5 means the crawl is queueing instead of running — investigate.
# See agents/runbooks/runbook-clan-crawl-blocker-2026-04-30.md.
check_queue_depth "crawls"     5

# ── summary ──────────────────────────────────────────────────────────────────

TOTAL=$((PASS + FAIL))

if (( FAIL > 0 )); then
    log "── ${FAIL}/${TOTAL} checks failed ──"
    exit 1
fi
