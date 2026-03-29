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

if [[ ! -d "${UMAMI_ROOT}" ]]; then
  git clone --depth 1 --branch v2.16.1 https://github.com/umami-software/umami.git "${UMAMI_ROOT}"
  chown -R "${APP_USER}:${APP_USER}" "${UMAMI_ROOT}"
else
  cd "${UMAMI_ROOT}"
  sudo -u "${APP_USER}" git fetch --tags
  sudo -u "${APP_USER}" git checkout v2.16.1
fi

cd "${UMAMI_ROOT}"

# ---------- Environment ----------

if [[ ! -f "${UMAMI_ROOT}/.env" ]]; then
  # Read DB connection from the battlestats server env
  source /etc/battlestats-server.env
  source /etc/battlestats-server.secrets.env

  DB_URL="postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/umami?sslmode=${DB_SSLMODE:-require}"

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

# ---------- Nginx location block ----------

NGINX_CONF="/etc/nginx/sites-available/battlestats-client.conf"

if ! grep -q 'location /umami' "${NGINX_CONF}"; then
  # Insert the umami location block before the catch-all "location /" block
  sed -i '/location \/ {/i \
  location /umami/ {\
    proxy_pass http://127.0.0.1:'"${UMAMI_PORT}"'/umami/;\
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
  echo "Nginx updated with /umami/ route."
else
  echo "Nginx already has /umami/ route."
fi

echo ""
echo "=========================================="
echo "Umami is live at https://${HOSTNAME:-battlestats.online}/umami/"
echo "Default login: admin / umami"
echo "=========================================="

REMOTE

echo "Umami bootstrap complete for ${DEPLOY_USER}@${HOST}"
