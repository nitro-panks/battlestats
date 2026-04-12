# Backend Droplet Deploy Runbook

This runbook deploys the Django backend on a bare Ubuntu DigitalOcean droplet while keeping the existing DigitalOcean managed Postgres database as the system of record.

## Production shape

- Django runs under gunicorn on `127.0.0.1:8888`.
- Celery worker and Celery beat run as systemd services.
- Redis and RabbitMQ run locally on the droplet via apt-managed services.
- Database traffic goes to the existing DigitalOcean managed Postgres target using the cloud env files from `server/.env.cloud` and `server/.env.secrets.cloud`.
- The DigitalOcean CA certificate is installed on the droplet and referenced with an absolute `DB_SSLROOTCERT` path.

## One-time bootstrap

From the repo root:

```bash
chmod +x server/deploy/bootstrap_droplet.sh server/deploy/deploy_to_droplet.sh
EXTRA_ALLOWED_HOSTS=battlestats.online,www.battlestats.online \
./server/deploy/bootstrap_droplet.sh YOUR_DROPLET_IP
```

The bootstrap installs:

- Python 3 with venv support
- Redis
- RabbitMQ
- release directories under `/opt/battlestats-server`
- systemd units:
  - `battlestats-gunicorn`
  - `battlestats-celery`
  - `battlestats-beat`

## Deploy

When backend changes are ready:

```bash
EXTRA_ALLOWED_HOSTS=battlestats.online,www.battlestats.online \
./server/deploy/deploy_to_droplet.sh YOUR_DROPLET_IP
```

That deploy does all of the following:

- uploads `server/.env.cloud`
- uploads `server/.env.secrets.cloud`
- uploads `server/ca-certificate.crt`
- syncs the `server/` directory to a timestamped release
- wires each release `server/logs` path to `${APP_ROOT}/shared/logs` and ensures `django.log` is writable by the app user before Django management commands run
- defaults `ENABLE_AGENTIC_RUNTIME=0` on the droplet so the core site boots without LangGraph, CrewAI, or the top-level `agents/` tree
- updates the remote env files to use the absolute CA cert path, droplet-local Redis/RabbitMQ values, the explicit domain/IP allow-list you passed in, and the agentic runtime flag
- installs Python dependencies into `/opt/battlestats-server/venv`
- runs `manage.py migrate`
- runs `manage.py collectstatic --noinput`
- runs `manage.py check`
- flips `/opt/battlestats-server/current` to the new release with an atomic symlink move and verifies that the active target matches the new release path
- restarts gunicorn, celery worker, and celery beat
- runs `manage.py materialize_landing_player_best_snapshots` automatically after the new release is active unless explicitly disabled

What the deploy does not guarantee on its own:

- it does not perform a broad post-deploy cache repopulation on production,
- it does not replace targeted cache invalidation and warming for landing or ranking changes,
- it should not be treated as proof that `/opt/battlestats-server/current` is correct unless you verify it directly.

To deploy the optional agentic runtime on purpose, enable it explicitly:

```bash
DEPLOY_AGENTIC_RUNTIME=1 \
EXTRA_ALLOWED_HOSTS=battlestats.online,www.battlestats.online \
./server/deploy/deploy_to_droplet.sh YOUR_DROPLET_IP
```

With `DEPLOY_AGENTIC_RUNTIME=1`, the deploy also syncs the top-level `agents/` directory and installs `server/requirements-agentic.txt`.

## Remote config files

The deploy uses these droplet files:

- `/etc/battlestats-server.env`
- `/etc/battlestats-server.secrets.env`
- `/etc/ssl/certs/battlestats-do-ca-certificate.crt`

The deploy script populates them from the existing repo cloud target files, so the backend continues using the established managed Postgres connection details instead of a second config source.

The deploy also enforces droplet memory tuning for the Django and Celery process set:

- `/etc/sysctl.d/99-battlestats-memory.conf` sets `vm.swappiness=10` so the kernel prefers keeping hot gunicorn and Celery workers in RAM, using swap as a transient safety net rather than an eager spill target.
- `/etc/battlestats-server.env` carries Celery concurrency and recycling defaults sized for the 4 GB droplet: default queue `3`, hydration queue `3`, background queue `2`.
- `/etc/battlestats-server.env` also carries migration guardrails for multi-realm population: `MAX_CONCURRENT_REALM_CRAWLS=1`, `CLAN_CRAWL_RATE_LIMIT_DELAY=0.25`, and `CLAN_CRAWL_CORE_ONLY_RATE_LIMIT_DELAY=0.10`.
- Celery workers are restarted from systemd units that read those env vars and apply `--max-memory-per-child` to recycle unusually large worker children before memory drift accumulates.
- Before restarting services, the deploy clears realm-scoped clan-crawl Redis keys so an interrupted EU resume crawl does not remain blocked behind stale locks after a rollout.

The deploy now also hardens several previously observed backend rollout failures:

- it does not rely on a plain in-place `ln -sfn` for `current`; it performs an atomic symlink replacement and verifies the active release target,
- it ensures the shared Django file-log target exists and is writable before management commands and service startup, which prevents release-local `server/logs/django.log` permission drift from blocking gunicorn.

### RabbitMQ and Celery hardening (added 2026-04-12)

The deploy script now provisions three additional resilience mechanisms:

1. **RabbitMQ `advanced.config`** — Disables `consumer_timeout` (default 30 min in RabbitMQ 3.12). Without this, long-running tasks using `CELERY_TASK_ACKS_LATE = True` get their AMQP channel killed with `PRECONDITION_FAILED`, leaving the worker in a zombie state (process alive, 0 consumers). Created during `configure_local_rabbitmq()`.

2. **Celery consumer watchdog** — A systemd timer (`battlestats-celery-watchdog.timer`) runs `/usr/local/bin/battlestats-celery-watchdog.sh` every 5 minutes. The script checks each queue's consumer count via `rabbitmqctl list_queues` and restarts any service with 0 consumers. Logs events via `logger -t battlestats-watchdog`.

3. **`Wants=` instead of `Requires=`** — Celery worker systemd units use `Wants=redis-server.service rabbitmq-server.service` instead of `Requires=`. This prevents cascading service failures when Redis or RabbitMQ restarts. `After=` is retained for boot ordering.

See `runbook-incident-celery-zombie-worker-2026-04-12.md` for the full incident that motivated these changes.

Automatic Best-player snapshot materialization is enabled by default. Optional deploy-time controls:

- `AUTO_MATERIALIZE_LANDING_PLAYER_BEST_SNAPSHOTS=0` disables the post-deploy snapshot rebuild.
- `MATERIALIZE_LANDING_PLAYER_BEST_SNAPSHOT_REALMS=na,eu` scopes the rebuild to specific realms.
- `MATERIALIZE_LANDING_PLAYER_BEST_SNAPSHOT_SORTS=ranked,wr` scopes the rebuild to specific Best-player sorts.

## Post-Deploy And Post-Bounce Follow-Up

Use [agents/runbooks/runbook-post-deploy-post-bounce-operations-2026-04-05.md](agents/runbooks/runbook-post-deploy-post-bounce-operations-2026-04-05.md) as the canonical checklist after backend deploy or manual service bounce.

Important current production behavior:

1. `WARM_CACHES_ON_STARTUP=0` on the droplet, so a bounce does not auto-run the full startup warmer chain.
2. For ranking or landing payload changes, follow-up invalidation and rewarming must be targeted and manual.
3. Heavy warmers should run serially, one realm at a time.

What the deploy now does automatically after a successful backend rollout:

1. verifies that `/opt/battlestats-server/current` matches the intended release,
2. verifies `battlestats-gunicorn`, `battlestats-celery`, `battlestats-celery-hydration`, `battlestats-celery-background`, `battlestats-beat`, `redis-server`, and `rabbitmq-server`,
3. runs Django-side post-deploy verification for the realms in `POST_DEPLOY_VERIFY_REALMS`.

Current default:

- `POST_DEPLOY_VERIFY_REALMS=na,eu`

Targeted follow-up remains manual via the shared wrapper:

```bash
./scripts/post_deploy_operations.sh YOUR_DROPLET_IP snapshots --realm na --sort cb
./scripts/post_deploy_operations.sh YOUR_DROPLET_IP invalidate --realm na --players --include-recent
./scripts/post_deploy_operations.sh YOUR_DROPLET_IP warm-landing --realm na --include-recent
./scripts/post_deploy_operations.sh YOUR_DROPLET_IP warm-best-entities --realm na --player-limit 25 --clan-limit 25
```

When you are serving the app from a custom domain, pass the root domain and any aliases as a comma-separated `EXTRA_ALLOWED_HOSTS` value so Django accepts the incoming `Host` header.

## Service checks

Useful remote checks:

```bash
ssh root@YOUR_DROPLET_IP 'systemctl status battlestats-gunicorn --no-pager'
ssh root@YOUR_DROPLET_IP 'systemctl status battlestats-celery --no-pager'
ssh root@YOUR_DROPLET_IP 'systemctl status battlestats-beat --no-pager'
ssh root@YOUR_DROPLET_IP 'journalctl -u battlestats-gunicorn -n 100 --no-pager'
ssh root@YOUR_DROPLET_IP 'curl -s http://127.0.0.1:8888/api/player/Mebuki/ | head'
```

### Celery health (consumer counts)

`systemctl status` is **not sufficient** to verify Celery worker health. A worker can show `active (running)` while having 0 AMQP consumers (zombie state). Always check consumer counts:

```bash
ssh root@YOUR_DROPLET_IP 'rabbitmqctl -q list_queues name consumers messages'
```

Expected output: each of `default`, `hydration`, `background` should have ≥1 consumer.

### Watchdog status

```bash
ssh root@YOUR_DROPLET_IP 'systemctl status battlestats-celery-watchdog.timer --no-pager'
ssh root@YOUR_DROPLET_IP 'journalctl -t battlestats-watchdog --no-pager -n 20'
```

Any `ALERT` entries in the watchdog journal indicate the watchdog caught and recovered a zombie worker.

For clan-chart regressions specifically, verify that a stale clan shell does not suppress a plot built from already-present members. A healthy post-deploy check is that `/api/fetch/clan_data/<clan_id>:active` returns real rows for populated clans rather than `[]` with `X-Clan-Plot-Pending: true` indefinitely.
