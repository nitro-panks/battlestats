#!/bin/bash
# Supervised pg_repack of warships_battleobservation. Extra args pass through,
# so `--dry-run` exercises the exact same connection and flags as the real run.
set -euo pipefail
set -a
. /etc/battlestats-server.env
. /etc/battlestats-server.secrets.env
set +a
export PGPASSWORD="$DB_PASSWORD"
export PGSSLMODE="${DB_SSLMODE:-require}"
[ -n "${DB_SSLROOTCERT:-}" ] && export PGSSLROOTCERT="$DB_SSLROOTCERT"
export PGAPPNAME="pg_repack-supervised"
#  -k  doadmin is not a superuser
#  -D  never terminate the sessions blocking us; give up instead
#  --no-order  plain rewrite (this table has no CLUSTER key, and needs none)
exec /usr/local/lib/pg_repack-1.5.2/pg_repack \
  -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
  -k -D --no-order --wait-timeout=60 \
  -t public.warships_battleobservation "$@"
