# Runbook: Flower + RabbitMQ Observability On The Droplet

**Created**: 2026-04-02 (planned) · **Deployed**: 2026-06-24 · **Status**: LIVE
**Scope**: A persistent Flower instance + the RabbitMQ management UI on the
production droplet, so operators see queue depth, worker liveness, and per-task
history without hand-combining `rabbitmqctl`, `celery inspect`, and `journalctl`.

## What's actually deployed (2026-06-24)

| Piece | Where | Exposure |
| --- | --- | --- |
| Flower 2.0.1 | `battlestats-flower.service`, `127.0.0.1:5555` | `https://battlestats.online/flower` — nginx home-IP allowlist + Flower basic-auth |
| RabbitMQ management UI | `rabbitmq_management` plugin, `127.0.0.1:15672` | SSH tunnel only (ufw blocks 15672; no nginx block — see "RabbitMQ exposure") |
| Task events | `worker_send_task_events=True` in `server/battlestats/celery.py` | n/a — makes Flower's task history populate |

The earlier (2026-04-02) plan assumed Flower in the **app** venv via
`celery -A battlestats flower`. The shipped design differs deliberately:

- **Dedicated venv** `/opt/battlestats-flower/venv`, not the app venv — Flower is
  decoupled from the app release cycle (a `pip install -r requirements.txt` on
  deploy can't disturb it, and it has no Django/app import dependency).
- **Standalone invocation** `celery --broker=<url> flower …` (no `-A`) — Flower 2.x
  ships **no `flower` console-script**; it's a `celery` subcommand. The dedicated
  venv has only `celery` + `flower`, not the app, so `-A battlestats` is unavailable
  (and unnecessary — Flower discovers workers over the control bus and reads queue
  lengths via `broker_api`).
- **Own env file** `/etc/battlestats-flower.env` (root:battlestats 640), not the
  shared app env.

## Files / units

- `/opt/battlestats-flower/venv` — dedicated venv (`flower`, `celery`, `tornado`).
- `/opt/battlestats-flower/flower.db` — persistent task history (survives restarts).
- `/etc/battlestats-flower.env` — `FLOWER_BROKER`, `FLOWER_BROKER_API`,
  `FLOWER_BASIC_AUTH` (the operator login), `FLOWER_PURGE_OFFLINE_WORKERS`.
- `/etc/systemd/system/battlestats-flower.service` — re-asserted by
  `deploy_to_droplet.sh` whenever the venv + env exist (guarded, so a fresh box
  without the one-time provisioning doesn't get a failing unit).
- nginx: `location /flower { allow 130.44.131.215; deny all; proxy_pass http://127.0.0.1:5555; … }`
  in `sites-available/battlestats-client.conf` — same allowlist pattern as `/umami`.

## What the deploy script does for you

`server/deploy/deploy_to_droplet.sh` now:

1. `configure_local_rabbitmq()` → `rabbitmq-plugins enable rabbitmq_management`
   (idempotent; persisted in `enabled_plugins`, survives the routine broker restart).
2. (Re)asserts `battlestats-flower.service` from the dedicated venv + env when both
   are present, then `enable --now` + `try-restart`.

It does **not** create the venv, env file, RabbitMQ monitoring user, or nginx block.
Those are the one-time provisioning below (needed on a fresh droplet / rebuild).

## One-time provisioning (fresh box / rebuild)

```bash
# 1. RabbitMQ read-only monitoring user (Flower's broker_api; `guest` is deleted by deploy)
RMQ_PASS=$(openssl rand -hex 16)
rabbitmqctl add_user flower "$RMQ_PASS"
rabbitmqctl set_user_tags flower monitoring
rabbitmqctl set_permissions -p / flower '^$' '^$' '.*'    # read-only

# 2. dedicated venv
python3 -m venv /opt/battlestats-flower/venv
/opt/battlestats-flower/venv/bin/pip install --upgrade pip wheel flower
chown -R battlestats:battlestats /opt/battlestats-flower

# 3. env file (reuse the app's broker URL; de-quote it — systemd strips quotes, raw grep doesn't)
B=$(grep -hoP '^CELERY_BROKER_URL=\K.*' /etc/battlestats-server.env /etc/battlestats-server.secrets.env | tail -1); B=${B%\"}; B=${B#\"}
umask 027
cat > /etc/battlestats-flower.env <<EOF
FLOWER_BROKER=$B
FLOWER_BROKER_API=http://flower:${RMQ_PASS}@127.0.0.1:15672/api/
FLOWER_BASIC_AUTH=admin:$(openssl rand -hex 16)
FLOWER_PURGE_OFFLINE_WORKERS=300
EOF
chown root:battlestats /etc/battlestats-flower.env && chmod 640 /etc/battlestats-flower.env

# 4. the systemd unit is written by the next deploy; or hand-write it (see /etc/systemd/system/battlestats-flower.service)

# 5. nginx — add inside the 443 server block of sites-available/battlestats-client.conf,
#    just before `location / {` (mirror the /umami block; rotate the allow IP if home IP changes):
#      location /flower {
#          allow 130.44.131.215; deny all;
#          proxy_pass http://127.0.0.1:5555;
#          proxy_http_version 1.1;
#          proxy_set_header Host $host;
#          proxy_set_header X-Real-IP $remote_addr;
#          proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
#          proxy_set_header X-Forwarded-Proto $scheme;
#          proxy_set_header Upgrade $http_upgrade;
#          proxy_set_header Connection "upgrade";
#      }
#    then: nginx -t && systemctl reload nginx
```

## Access

- **Flower (daily driver):** from the home network (allow-listed IP), browse
  `https://battlestats.online/flower` and log in with `FLOWER_BASIC_AUTH`.
- **RabbitMQ UI / Flower without the allowlist:** SSH tunnel —
  `ssh -N -L 5555:127.0.0.1:5555 -L 15672:127.0.0.1:15672 root@battlestats.online`,
  then `http://localhost:5555/flower` and `http://localhost:15672`
  (login `flower:<pass from /etc/battlestats-flower.env>`).

## RabbitMQ exposure (why it's tunnel-only)

The RabbitMQ management SPA only proxies cleanly under a subpath with
`management.path_prefix` in `rabbitmq.conf` — which needs a broker restart. Deploys
already restart the broker, so it could ride along, but it wasn't worth the extra
public surface: **Flower already surfaces queue depth** (via `broker_api`), so the
raw RabbitMQ UI is for occasional deep broker introspection, which the SSH tunnel
covers. If browser access is wanted later, prefer a `rabbitmq.` subdomain (root path,
no prefix gymnastics) over a subpath.

## Security model

ufw allows only 22/80/443; 5555 and 15672 never reach the internet directly. Public
access to Flower is gated by the nginx **home-IP allowlist** (`deny all` otherwise),
and then by Flower's **own basic-auth** — IP at the edge, credentials at the app, the
same two-layer model as `/umami`. The RabbitMQ `flower` user is **read-only**
(`'^$' '^$' '.*'`). Rotate the allow IP in `battlestats-client.conf` if home IP changes.

## Validation

```bash
ssh root@battlestats.online 'systemctl is-active battlestats-flower; ss -ltnp | grep 5555'
# Flower under the prefix (expect 200 with auth, 401 without):
ssh root@battlestats.online 'curl -s -o /dev/null -w "%{http_code}\n" -u "$(grep -oP "^FLOWER_BASIC_AUTH=\K.*" /etc/battlestats-flower.env)" http://127.0.0.1:5555/flower/'
# allowlist enforces (expect 403 from a non-allowed IP):
ssh root@battlestats.online 'curl -sk -o /dev/null -w "%{http_code}\n" -H "Host: battlestats.online" https://127.0.0.1/flower/'
# cross-check Flower against raw broker state:
ssh root@battlestats.online 'rabbitmqctl list_queues name messages_ready messages_unacknowledged consumers'
```

Flower's Workers tab should list `default`, `hydration`, `background`, `crawls`,
`floor`; the Tasks tab populates from `worker_send_task_events`. If the Tasks tab is
empty after a deploy, confirm `worker_send_task_events=True` is in `celery.py` and the
workers were restarted (runtime `celery -b <url> control enable_events` is a temporary
fallback, lost on restart).

## Notes / constraints

- Droplet is 2 vCPU / 8 GB (+2 GB swap); Flower adds a small Python process + the
  persistent DB. Measured fine at load ~0.7. The binding constraint remains the
  managed Postgres, not observability.
- Flower makes backlog *visible*; it doesn't reduce it. Queue-pressure tuning lives in
  the floor/enrichment runbooks.
- Sentry (centralized error capture for Django + Celery) is the planned next
  observability layer — deferred; needs a project DSN + `sentry-sdk` in requirements.
