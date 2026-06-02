#!/usr/bin/env bash
#
# Bootstrap Umami analytics on the battlestats droplet.
#
# Usage:
#   ./umami/deploy/bootstrap_umami.sh battlestats.online
#
# Prerequisites:
#   - Node 20+ on the droplet
#   - PostgreSQL (uses the same managed DB as the Django backend)
#   - Nginx already configured by client/deploy/bootstrap_droplet.sh
#
# After first run, visit https://battlestats.online/umami/ to log in.
# Default credentials: admin / umami  (change immediately)

set -euo pipefail

HOST="${1:-}"
DEPLOY_USER="${DEPLOY_USER:-root}"
UMAMI_ROOT="${UMAMI_ROOT:-/opt/umami}"
APP_USER="${APP_USER:-battlestats}"
UMAMI_PORT="${UMAMI_PORT:-3002}"

if [[ -z "${HOST}" ]]; then
  echo "Usage: $0 <droplet-ip-or-hostname>" >&2
  exit 1
fi

ssh "${DEPLOY_USER}@${HOST}" \
  UMAMI_ROOT="${UMAMI_ROOT}" \
  APP_USER="${APP_USER}" \
  UMAMI_PORT="${UMAMI_PORT}" \
  'bash -s' <<'REMOTE'
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

# ---------- Clone or update Umami ----------

UMAMI_VERSION="${UMAMI_VERSION:-v2.20.2}"

if [[ ! -d "${UMAMI_ROOT}" ]]; then
  git clone --depth 1 --branch "${UMAMI_VERSION}" https://github.com/umami-software/umami.git "${UMAMI_ROOT}"
  chown -R "${APP_USER}:${APP_USER}" "${UMAMI_ROOT}"
else
  cd "${UMAMI_ROOT}"
  # shallow clones need the specific tag fetched before checkout
  sudo -u "${APP_USER}" git -C "${UMAMI_ROOT}" checkout -- yarn.lock 2>/dev/null || true
  sudo -u "${APP_USER}" git -C "${UMAMI_ROOT}" fetch --depth 1 origin tag "${UMAMI_VERSION}"
  sudo -u "${APP_USER}" git -C "${UMAMI_ROOT}" checkout "${UMAMI_VERSION}"
fi

cd "${UMAMI_ROOT}"

# ---------- Environment ----------

if [[ ! -f "${UMAMI_ROOT}/.env" ]]; then
  # Read DB connection from the battlestats server env
  source /etc/battlestats-server.env
  source /etc/battlestats-server.secrets.env

  # Umami connects with a LEAST-PRIVILEGE role scoped to the `umami` database only —
  # NOT the doadmin cluster superuser. A umami compromise must not reach the player
  # data in `defaultdb`. Provision the role once as doadmin:
  #   CREATE ROLE umami_app LOGIN PASSWORD '<secret>';
  #   ALTER DATABASE umami OWNER TO umami_app;   -- so it can run prisma migrations
  #   \c umami
  #   -- hand over existing tables PER-OBJECT. Do NOT use `REASSIGN OWNED BY doadmin`:
  #   -- it reassigns SHARED objects (databases/tablespaces) cluster-wide, not just this
  #   -- DB, and will silently hand other databases to umami_app. (See runbook hazard note.)
  #   DO $$ DECLARE r record; BEGIN
  #     FOR r IN SELECT tablename FROM pg_tables WHERE schemaname='public' LOOP
  #       EXECUTE format('ALTER TABLE public.%I OWNER TO umami_app', r.tablename); END LOOP;
  #     FOR r IN SELECT sequencename FROM pg_sequences WHERE schemaname='public' LOOP
  #       EXECUTE format('ALTER SEQUENCE public.%I OWNER TO umami_app', r.sequencename); END LOOP;
  #   END $$;
  # then store UMAMI_DB_USER / UMAMI_DB_PASSWORD in /etc/battlestats-server.secrets.env.
  # See agents/runbooks/runbook-umami-hardening-2026-06-02.md.
  UMAMI_DB_USER="${UMAMI_DB_USER:?UMAMI_DB_USER must be set (scoped role, not doadmin)}"
  UMAMI_DB_PASSWORD="${UMAMI_DB_PASSWORD:?UMAMI_DB_PASSWORD must be set}"

  DB_URL="postgresql://${UMAMI_DB_USER}:${UMAMI_DB_PASSWORD}@${DB_HOST}:${DB_PORT}/umami?sslmode=${DB_SSLMODE:-require}"

  cat > "${UMAMI_ROOT}/.env" <<EOF
DATABASE_URL=${DB_URL}
BASE_PATH=/umami
PORT=${UMAMI_PORT}
EOF
  chown "${APP_USER}:${APP_USER}" "${UMAMI_ROOT}/.env"
  chmod 640 "${UMAMI_ROOT}/.env"
  echo "Created ${UMAMI_ROOT}/.env — edit DATABASE_URL if needed."
else
  echo "${UMAMI_ROOT}/.env already exists, skipping."
fi

# ---------- Install & Build ----------

sudo -u "${APP_USER}" bash -c "cd ${UMAMI_ROOT} && npm install --legacy-peer-deps"
sudo -u "${APP_USER}" bash -c "cd ${UMAMI_ROOT} && npm run build"

# ---------- Systemd service ----------

cat > /etc/systemd/system/umami.service <<EOF
[Unit]
Description=Umami analytics
After=network.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${UMAMI_ROOT}
EnvironmentFile=${UMAMI_ROOT}/.env
Environment=NODE_ENV=production
ExecStart=/usr/bin/env npm start
Restart=always
RestartSec=5
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable umami
systemctl restart umami

echo "Umami service started on port ${UMAMI_PORT}."

# ---------- Nginx routing + dashboard IP allowlist ----------
#
# Two access tiers under /umami:
#   * /umami/script.js + /umami/api/send  — PUBLIC (every visitor's browser hits these).
#   * everything else (dashboard + admin API) — restricted to UMAMI_ALLOW_IP.
# An IP allowlist (not HTTP Basic auth) is used deliberately: umami authenticates its API
# with an `Authorization: Bearer` header, which collides with Basic auth's Authorization
# header and breaks login (JSON.parse error). An allowlist doesn't touch headers, so it can
# protect the admin API too. Rotate UMAMI_ALLOW_IP when the home IP changes.
# See runbook-umami-hardening-2026-06-02.md.

NGINX_CONF="/etc/nginx/sites-available/battlestats-client.conf"
NGINX_ENABLED="/etc/nginx/sites-enabled/battlestats-client.conf"
UMAMI_ALLOW_IP="${UMAMI_ALLOW_IP:?UMAMI_ALLOW_IP must be set (the home/office IP allowed to reach the dashboard)}"

# sites-enabled MUST be a symlink to sites-available, or edits here silently never go live.
if [[ -e "${NGINX_ENABLED}" && ! -L "${NGINX_ENABLED}" ]]; then
  echo "WARNING: ${NGINX_ENABLED} is a copy, not a symlink — config edits would not take effect." >&2
  echo "         Reconcile it to a symlink of ${NGINX_CONF} before re-running." >&2
fi

if ! grep -q 'location /umami {' "${NGINX_CONF}"; then
  # Insert the umami blocks before the catch-all "location /" block.
  sed -i '/location \/ {/i \
  location = /umami/script.js {\
    proxy_pass http://127.0.0.1:'"${UMAMI_PORT}"';\
    proxy_http_version 1.1;\
    proxy_set_header Host $host;\
    proxy_set_header X-Real-IP $remote_addr;\
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\
    proxy_set_header X-Forwarded-Proto $scheme;\
  }\
  location = /umami/api/send {\
    proxy_pass http://127.0.0.1:'"${UMAMI_PORT}"';\
    proxy_http_version 1.1;\
    proxy_set_header Host $host;\
    proxy_set_header X-Real-IP $remote_addr;\
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\
    proxy_set_header X-Forwarded-Proto $scheme;\
  }\
  location /umami {\
    allow '"${UMAMI_ALLOW_IP}"';\
    deny all;\
    proxy_pass http://127.0.0.1:'"${UMAMI_PORT}"';\
    proxy_http_version 1.1;\
    proxy_set_header Host $host;\
    proxy_set_header X-Real-IP $remote_addr;\
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\
    proxy_set_header X-Forwarded-Proto $scheme;\
    proxy_set_header Upgrade $http_upgrade;\
    proxy_set_header Connection "upgrade";\
  }\
' "${NGINX_CONF}"
  nginx -t && systemctl reload nginx
  echo "Nginx updated with /umami/ routes + dashboard IP allowlist (${UMAMI_ALLOW_IP})."
else
  echo "Nginx already has /umami/ routes."
fi

echo ""
echo "=========================================="
echo "Umami ${UMAMI_VERSION} is live at https://${HOSTNAME:-battlestats.online}/umami/"
echo "Dashboard + admin API restricted to ${UMAMI_ALLOW_IP}; collection endpoints are public."
echo "=========================================="

REMOTE

echo "Umami bootstrap complete for ${DEPLOY_USER}@${HOST}"
