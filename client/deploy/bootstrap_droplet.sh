#!/usr/bin/env bash

set -euo pipefail

HOST="${1:-}"
DEPLOY_USER="${DEPLOY_USER:-root}"
APP_ROOT="${APP_ROOT:-/opt/battlestats-client}"
APP_USER="${APP_USER:-battlestats}"
NGINX_SERVER_NAME="${NGINX_SERVER_NAME:-_}"
API_ORIGIN="${API_ORIGIN:-http://127.0.0.1:8888}"

if [[ -z "${APP_ORIGIN:-}" ]]; then
  if [[ "${NGINX_SERVER_NAME}" != "_" ]]; then
    APP_ORIGIN="https://${NGINX_SERVER_NAME%% *}"
  else
    APP_ORIGIN="https://tamezz.com"
  fi
fi

if [[ -z "${HOST}" ]]; then
  echo "Usage: $0 <droplet-ip-or-hostname>" >&2
  exit 1
fi

ssh "${DEPLOY_USER}@${HOST}" \
  APP_ROOT="${APP_ROOT}" \
  APP_USER="${APP_USER}" \
  NGINX_SERVER_NAME="${NGINX_SERVER_NAME}" \
  API_ORIGIN="${API_ORIGIN}" \
  APP_ORIGIN="${APP_ORIGIN}" \
  'bash -s' <<'REMOTE'
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y ca-certificates curl gnupg nginx rsync

if ! command -v node >/dev/null 2>&1; then
  install -d -m 0755 /etc/apt/keyrings
  curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg
  echo 'deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main' > /etc/apt/sources.list.d/nodesource.list
  apt-get update
  apt-get install -y nodejs
fi

if ! id -u "${APP_USER}" >/dev/null 2>&1; then
  useradd --system --home "${APP_ROOT}" --shell /usr/sbin/nologin "${APP_USER}"
fi

install -d -o "${APP_USER}" -g "${APP_USER}" "${APP_ROOT}"
install -d -o "${APP_USER}" -g "${APP_USER}" "${APP_ROOT}/releases"

if [ ! -f /etc/battlestats-client.env ]; then
  cat > /etc/battlestats-client.env <<EOF
BATTLESTATS_API_ORIGIN=${API_ORIGIN}
BATTLESTATS_APP_ORIGIN=${APP_ORIGIN}
# NEXT_PUBLIC_GA_MEASUREMENT_ID=
EOF
fi

cat > /etc/systemd/system/battlestats-client.service <<EOF
[Unit]
Description=Battlestats Next.js client
After=network.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_ROOT}/current/client
Environment=NODE_ENV=production
EnvironmentFile=/etc/battlestats-client.env
ExecStart=/usr/bin/env npm start -- --hostname 127.0.0.1 --port 3001
Restart=always
RestartSec=5
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/nginx/sites-available/battlestats-client.conf <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name ${NGINX_SERVER_NAME};

  location /api/ {
    proxy_pass http://127.0.0.1:8888;
    proxy_http_version 1.1;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
  }

    location / {
        proxy_pass http://127.0.0.1:3001;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
EOF

ln -sfn /etc/nginx/sites-available/battlestats-client.conf /etc/nginx/sites-enabled/battlestats-client.conf
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl daemon-reload
systemctl enable nginx battlestats-client
systemctl restart nginx

if [ -d "${APP_ROOT}/current/client" ]; then
  systemctl restart battlestats-client
fi
REMOTE

echo "Droplet bootstrap complete for ${DEPLOY_USER}@${HOST}"
