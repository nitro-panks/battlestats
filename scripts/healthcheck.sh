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
        log "OK    ${label}  (${http_code}, ${size}B)"
        (( PASS++ )) || true
    else
        log "FAIL  ${label}  ${reason}  url=${url}"
        (( FAIL++ )) || true
    fi

    rm -f "$tmpfile"
}

# ── page routes (HTML) ───────────────────────────────────────────────────────

log "── run start ──────────────────────────────────────"

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

# ── summary ──────────────────────────────────────────────────────────────────

TOTAL=$((PASS + FAIL))
log "── run end ── ${PASS}/${TOTAL} passed, ${FAIL} failed ──"

if (( FAIL > 0 )); then
    exit 1
fi
