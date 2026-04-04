#!/bin/bash
# Deploy battlestats functions to DigitalOcean.
#
# Copies the Django server code into function packages that need it,
# deploys via doctl, then cleans up the copies.
#
# Usage: ./functions/deploy.sh [--include pkg/fn] [extra doctl flags...]
#   e.g. ./functions/deploy.sh
#        ./functions/deploy.sh --include enrichment/enrich-batch

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVER_SRC="$REPO_ROOT/server"

# Functions that need the Django server code
DJANGO_FUNCTIONS=(
    "packages/enrichment/enrich-batch"
)

cleanup() {
    for fn_dir in "${DJANGO_FUNCTIONS[@]}"; do
        rm -rf "$SCRIPT_DIR/$fn_dir/server"
    done
}
trap cleanup EXIT

echo "=== Copying server code into function packages ==="
for fn_dir in "${DJANGO_FUNCTIONS[@]}"; do
    dest="$SCRIPT_DIR/$fn_dir/server"
    echo "  $fn_dir/server"
    mkdir -p "$dest"

    # Copy only what's needed — skip tests, migrations, static, logs, envs
    rsync -a --delete \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='.env*' \
        --exclude='logs/' \
        --exclude='staticfiles/' \
        --exclude='media/' \
        --exclude='scripts/' \
        --exclude='warships/tests/' \
        --exclude='warships/management/commands/test_*' \
        --exclude='warships/agentic/' \
        --exclude='requirements-agentic.txt' \
        --exclude='Pipfile*' \
        --exclude='gunicorn.conf.py' \
        "$SERVER_SRC/" "$dest/"
done

echo "=== Deploying to DigitalOcean Functions ==="
doctl serverless deploy "$SCRIPT_DIR" \
    --remote-build \
    --env "$SCRIPT_DIR/.env" \
    "$@"

echo "=== Deploy complete ==="
