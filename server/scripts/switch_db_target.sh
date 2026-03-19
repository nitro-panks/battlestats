#!/usr/bin/env bash

set -euo pipefail

usage() {
	cat <<'EOF'
Usage: ./server/scripts/switch_db_target.sh <cloud|local> [--dry-run] [--skip-check]

Switch the active Django/Celery database target by replacing:
- server/.env
- server/.env.secrets

with the target-specific files for cloud or local Postgres, then restart the backend services.
EOF
}

if [[ $# -lt 1 ]]; then
	usage
	exit 1
fi

TARGET="$1"
shift

DRY_RUN=0
SKIP_CHECK=0

while [[ $# -gt 0 ]]; do
	case "$1" in
		--dry-run)
			DRY_RUN=1
			;;
		--skip-check)
			SKIP_CHECK=1
			;;
		-h|--help)
			usage
			exit 0
			;;
		*)
			echo "Unknown option: $1" >&2
			usage
			exit 1
			;;
	esac
	shift
done

case "$TARGET" in
	cloud|local)
		;;
	*)
		echo "Target must be 'cloud' or 'local'." >&2
		usage
		exit 1
		;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
SERVER_DIR="$ROOT_DIR/server"

ACTIVE_ENV="$SERVER_DIR/.env"
ACTIVE_SECRETS="$SERVER_DIR/.env.secrets"
TARGET_ENV="$SERVER_DIR/.env.$TARGET"
TARGET_SECRETS="$SERVER_DIR/.env.secrets.$TARGET"

for required_file in "$TARGET_ENV" "$TARGET_SECRETS"; do
	if [[ ! -f "$required_file" ]]; then
		echo "Missing required file: $required_file" >&2
		exit 1
	fi
done

run_cmd() {
	echo "+ $*"
	if [[ "$DRY_RUN" -eq 1 ]]; then
		return 0
	fi
	"$@"
}

copy_file() {
	local source_path="$1"
	local target_path="$2"
	echo "+ cp $source_path $target_path"
	if [[ "$DRY_RUN" -eq 1 ]]; then
		return 0
	fi
	cp "$source_path" "$target_path"
}

copy_file "$TARGET_ENV" "$ACTIVE_ENV"
copy_file "$TARGET_SECRETS" "$ACTIVE_SECRETS"

if [[ "$TARGET" == "local" ]]; then
	run_cmd docker compose --profile local-db up -d db
	run_cmd docker compose up -d server task-runner task-scheduler
else
	run_cmd docker compose up -d server task-runner task-scheduler
	run_cmd docker compose --profile local-db stop db
fi

if [[ "$SKIP_CHECK" -eq 0 ]]; then
	run_cmd sleep 3
	run_cmd docker compose exec -T server python manage.py check
fi

run_cmd docker compose ps

echo
if [[ "$DRY_RUN" -eq 1 ]]; then
	echo "Requested target: $TARGET (dry-run; active files unchanged)"
	SUMMARY_ENV_FILE="$TARGET_ENV"
else
	echo "Active target: $TARGET"
	SUMMARY_ENV_FILE="$ACTIVE_ENV"
fi

grep -E '^(DB_HOST|DB_PORT|DB_NAME|DB_USER|DB_SSLMODE|DB_SSLROOTCERT)=' "$SUMMARY_ENV_FILE" || true