#!/usr/bin/env bash
#
# Run a Wapiti security audit against the battlestats application.
#
# Usage:
#   ./scripts/security_audit.sh                          # Scan production (https://battlestats.online)
#   ./scripts/security_audit.sh http://localhost:8888     # Scan local backend
#   ./scripts/security_audit.sh --full                   # Deep scan with all modules
#
# Reports are saved to server/logs/security/ as timestamped HTML + JSON files.
# Intended to run weekly via cron or manually before releases.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

WAPITI_VENV="/home/august/.local/share/wapiti-venv"
WAPITI="${WAPITI_VENV}/bin/wapiti"

REPORT_DIR="${REPO_ROOT}/server/logs/security"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

DEFAULT_TARGET="https://battlestats.online"
FULL_SCAN=false

# Parse arguments
TARGET=""
for arg in "$@"; do
    case "${arg}" in
        --full) FULL_SCAN=true ;;
        -*) echo "Unknown option: ${arg}" >&2; exit 1 ;;
        *) TARGET="${arg}" ;;
    esac
done
TARGET="${TARGET:-${DEFAULT_TARGET}}"

if [[ ! -x "${WAPITI}" ]]; then
    echo "Error: wapiti not found at ${WAPITI}" >&2
    echo "Install with: python3 -m venv ${WAPITI_VENV} && ${WAPITI_VENV}/bin/pip install wapiti3" >&2
    exit 1
fi

mkdir -p "${REPORT_DIR}"

# Build the scan scope based on what we know about the app's attack surface.
# Wapiti crawls from the target URL, but we also seed it with known API paths
# so it doesn't miss endpoints that aren't linked from the HTML.
SEED_URLS=(
    "${TARGET}/"
    "${TARGET}/api/landing/players/?realm=na&mode=best"
    "${TARGET}/api/landing/clans/?realm=na&mode=best"
    "${TARGET}/api/landing/recent/"
    "${TARGET}/api/landing/player-suggestions/?q=test"
    "${TARGET}/api/stats/"
    "${TARGET}/api/players/explorer/?realm=na"
    "${TARGET}/api/sitemap-entities/"
)

# Modules to run. Default set covers the most relevant web app vulns
# without being excessively noisy against a production site.
if [[ "${FULL_SCAN}" == true ]]; then
    MODULES="all"
    SCAN_LABEL="full"
    DEPTH=5
    MAX_LINKS=500
else
    # Focused scan: XSS, SQL injection, SSRF, command injection, CRLF,
    # open redirects, CSP, cookie flags, HTTP headers, file inclusion
    MODULES="xss,sql,exec,ssrf,redirect,crlf,csp,cookieflags,http_headers,file"
    SCAN_LABEL="standard"
    DEPTH=3
    MAX_LINKS=200
fi

REPORT_BASE="${REPORT_DIR}/wapiti-${SCAN_LABEL}-${TIMESTAMP}"

echo "=== Wapiti Security Audit ==="
echo "Target:  ${TARGET}"
echo "Scan:    ${SCAN_LABEL}"
echo "Modules: ${MODULES}"
echo "Report:  ${REPORT_BASE}.html"
echo ""

# Build the wapiti command
WAPITI_CMD=(
    "${WAPITI}"
    --url "${TARGET}/"
    --scope url           # Stay on this domain
    --depth "${DEPTH}"
    --max-links-per-page "${MAX_LINKS}"
    --module "${MODULES}"
    --flush-session       # Don't reuse stale crawl data
    --format html
    --output "${REPORT_BASE}.html"
    --color
    --verbose 1
    --timeout 30
    --max-scan-time 1800  # 30 min hard cap
)

# Add seed URLs so wapiti tests known API endpoints
for seed in "${SEED_URLS[@]}"; do
    WAPITI_CMD+=(--start "${seed}")
done

# Exclude paths that would cause side effects or are not part of our app
WAPITI_CMD+=(
    -x "${TARGET}/umami/.*"
    -x "${TARGET}/static/.*"
    -x "${TARGET}/_next/.*"
)

echo "Starting scan at $(date '+%Y-%m-%d %H:%M:%S')..."
echo ""

"${WAPITI_CMD[@]}" 2>&1 | tee "${REPORT_BASE}.log"
EXIT_CODE=${PIPESTATUS[0]}

echo ""
echo "=== Scan Complete ==="
echo "Exit code: ${EXIT_CODE}"
echo "Report:    ${REPORT_BASE}.html"
echo "Log:       ${REPORT_BASE}.log"
echo ""

# Also generate a JSON report for programmatic consumption
if [[ ${EXIT_CODE} -eq 0 ]]; then
    echo "Generating JSON report..."
    JSON_CMD=(
        "${WAPITI}"
        --url "${TARGET}/"
        --scope url
        --depth "${DEPTH}"
        --max-links-per-page "${MAX_LINKS}"
        --module "${MODULES}"
        --format json
        --output "${REPORT_BASE}.json"
        --timeout 30
        --max-scan-time 1800
        --verbose 0
    )
    for seed in "${SEED_URLS[@]}"; do
        JSON_CMD+=(--start "${seed}")
    done
    JSON_CMD+=(-x "${TARGET}/umami/.*" -x "${TARGET}/static/.*" -x "${TARGET}/_next/.*")
    "${JSON_CMD[@]}" 2>/dev/null || true
    if [[ -f "${REPORT_BASE}.json" ]]; then
        echo "JSON report: ${REPORT_BASE}.json"
    fi
fi

# Gitignore the reports directory (contains potentially sensitive findings)
GITIGNORE="${REPORT_DIR}/.gitignore"
if [[ ! -f "${GITIGNORE}" ]]; then
    echo "*" > "${GITIGNORE}"
    echo "!.gitignore" >> "${GITIGNORE}"
fi

exit ${EXIT_CODE}
