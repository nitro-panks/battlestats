# Runbook: Flower Observability On The Droplet

**Created**: 2026-04-02
**Status**: Planned
**Scope**: Run a persistent Flower instance against the production Celery stack so operators can see queue depth, active tasks, worker state, and basic task history without manually combining `rabbitmqctl`, `celery inspect`, and `journalctl`.

## Goal

Add a production-safe Flower instance for the current bare-droplet backend runtime.

The current stack already exposes enough raw state to diagnose Celery manually:

1. systemd units for `battlestats-celery`, `battlestats-celery-hydration`, `battlestats-celery-background`, and `battlestats-beat`
2. RabbitMQ on `127.0.0.1:5672`
3. Redis on `127.0.0.1:6379`
4. Celery inspect commands working from `/opt/battlestats-server/current/server`

Flower should sit on top of that existing runtime rather than replace any part of it.

## Current Production Shape

As of 2026-04-02, the backend droplet is running:

1. Django gunicorn on `127.0.0.1:8888`
2. Next.js client as its own systemd service
3. RabbitMQ as an apt-managed system service
4. Redis as an apt-managed system service
5. three dedicated Celery workers via systemd:
   - `default` queue, concurrency `3`
   - `hydration` queue, concurrency `3`
   - `background` queue, concurrency `2`
6. Celery beat as its own systemd service

Observed live queue state during this review:

1. `default`: `0` ready, `0` unacked
2. `hydration`: `0` ready, `0` unacked
3. `background`: `145` ready, `2` unacked

That is the exact operational gap Flower would fill: a quick visual answer to whether backlog is concentrated in one queue, what tasks are running, and whether workers are draining normally.

## Recommended Production Model

Run Flower as a fourth application-side systemd unit on the droplet, bound to localhost and exposed through the existing reverse-proxy layer.

Recommended runtime shape:

1. process name: `battlestats-flower.service`
2. bind address: `127.0.0.1`
3. port: `5555`
4. broker: reuse `CELERY_BROKER_URL` from `/etc/battlestats-server.env`
5. auth: require authentication from day one
6. exposure: reverse proxy via nginx, not a public raw port

This matches the current operational style of the droplet:

1. long-lived Python processes are managed by systemd
2. secrets come from `/etc/battlestats-server.env` and `/etc/battlestats-server.secrets.env`
3. the app is served behind a local-only process boundary plus reverse proxy

## Security Decision

Do **not** expose Flower openly on the public internet without auth.

Flower exposes task names, task arguments, worker hostnames, and queue state. In this project, task payloads can include player names, realm values, and operational internals. Treat it as operator-only infrastructure.

Minimum acceptable security model:

1. bind Flower to `127.0.0.1`
2. require Flower basic auth
3. reverse proxy it through nginx only after auth is configured

Preferred security model:

1. bind Flower to `127.0.0.1`
2. require Flower basic auth from secrets env
3. restrict access to an admin-only hostname or path
4. optionally add IP allow-listing at nginx if the operator IPs are stable

If there is no immediate desire to expose it on the public domain, the lowest-risk first step is:

1. run Flower locally on the droplet at `127.0.0.1:5555`
2. access it temporarily through SSH port forwarding

That yields observability value before any nginx or public-routing change.

## What Needs To Change

### 1. Install Flower Into The Backend Runtime

Flower should be installed into the same venv used by gunicorn and Celery at `/opt/battlestats-server/venv`.

Implementation options:

1. add `flower` to `server/requirements.txt`
2. keep it as an explicit deploy-time install in the droplet scripts

Recommended approach: add it to `server/requirements.txt` so new releases and new droplets stay consistent.

### 2. Add Flower Environment Variables

Add env-backed defaults to `/etc/battlestats-server.env` and secrets to `/etc/battlestats-server.secrets.env`.

Recommended non-secret env vars:

```bash
FLOWER_HOST=127.0.0.1
FLOWER_PORT=5555
FLOWER_URL_PREFIX=/flower
FLOWER_PERSISTENT=True
FLOWER_DB=/opt/battlestats-server/shared/flower/flower.db
```

Recommended secret env var:

```bash
FLOWER_BASIC_AUTH=admin:strong-password-here
```

If multiple operators need access, Flower supports comma-separated auth pairs.

### 3. Add A systemd Unit

Add a dedicated service alongside the existing Celery units in both:

1. `server/deploy/bootstrap_droplet.sh`
2. `server/deploy/deploy_to_droplet.sh`

Recommended unit shape:

```ini
[Unit]
Description=Battlestats Flower dashboard
After=network.target rabbitmq-server.service battlestats-celery.service
Requires=rabbitmq-server.service

[Service]
Type=simple
User=battlestats
Group=battlestats
WorkingDirectory=/opt/battlestats-server/current/server
EnvironmentFile=/etc/battlestats-server.env
EnvironmentFile=/etc/battlestats-server.secrets.env
ExecStart=/bin/bash -lc 'exec "/opt/battlestats-server/venv/bin/celery" -A battlestats flower --address="${FLOWER_HOST:-127.0.0.1}" --port="${FLOWER_PORT:-5555}" --url_prefix="${FLOWER_URL_PREFIX:-/flower}" --basic_auth="${FLOWER_BASIC_AUTH}" --persistent="${FLOWER_PERSISTENT:-True}" --db="${FLOWER_DB:-/opt/battlestats-server/shared/flower/flower.db}"'
Restart=always
RestartSec=5
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
```

Notes:

1. the service should use the same application user as the rest of the backend stack
2. persistent mode is useful so basic task history survives service restarts
3. `shared/flower/` should be created during bootstrap/deploy and owned by `battlestats`

### 4. Restart List And Enablement

Both droplet scripts should treat Flower as a first-class service.

Needed changes:

1. `systemctl enable battlestats-flower`
2. include `battlestats-flower` in deploy restart lists
3. include Flower in post-deploy status checks where useful

### 5. Expose Flower Safely

There are two viable access models.

#### Option A: SSH Tunnel First

No nginx change required.

Operator flow:

```bash
ssh -L 5555:127.0.0.1:5555 root@battlestats.online
```

Then browse:

```text
http://127.0.0.1:5555/flower
```

Pros:

1. lowest risk
2. fastest to ship
3. avoids public exposure mistakes

Cons:

1. only available while tunneled
2. less convenient for routine monitoring

#### Option B: Reverse Proxy Through Nginx

Add an nginx location for a path or subdomain.

Path-based example:

```nginx
location /flower/ {
    proxy_pass http://127.0.0.1:5555/flower/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

If nginx is already terminating TLS for the production domain, this is the most convenient long-term operator setup.

Path caveat: Flower must be started with a matching `--url_prefix=/flower`, or assets and links will break.

Recommended first public exposure is still path-based behind auth, because it keeps the deployment simple and aligned with the existing app host.

## Validation Checklist

Flower should not be considered production-ready until all of the following work.

### Service validation

```bash
ssh root@battlestats.online 'systemctl status battlestats-flower --no-pager'
ssh root@battlestats.online 'journalctl -u battlestats-flower -n 100 --no-pager'
ssh root@battlestats.online 'ss -ltnp | grep 5555'
```

Expected:

1. service is active
2. it is bound to `127.0.0.1:5555`
3. no auth or broker connection errors appear in logs

### Functional validation

Verify that Flower shows:

1. workers: `default`, `hydration`, `background`
2. active queues for each worker
3. live task execution
4. background backlog when the crawler is active

Useful live validation commands to cross-check Flower against reality:

```bash
ssh root@battlestats.online 'rabbitmqctl list_queues name messages_ready messages_unacknowledged consumers'
ssh root@battlestats.online 'cd /opt/battlestats-server/current/server && set -a && . /etc/battlestats-server.env && . /etc/battlestats-server.secrets.env && set +a && /opt/battlestats-server/venv/bin/celery -A battlestats inspect active'
ssh root@battlestats.online 'cd /opt/battlestats-server/current/server && set -a && . /etc/battlestats-server.env && . /etc/battlestats-server.secrets.env && set +a && /opt/battlestats-server/venv/bin/celery -A battlestats inspect stats'
```

Flower should roughly match these raw sources.

### Reverse-proxy validation

If exposed through nginx:

```bash
curl -I https://battlestats.online/flower/
```

Expected:

1. authenticated access is required
2. the page and static assets load correctly behind the prefix
3. the root app and API routes remain unaffected

## Risks And Constraints

### 1. Security risk if publicly exposed without auth

This is the main risk. Flower is an operator tool, not a public app surface.

### 2. Mild extra memory pressure

Flower itself is not large, but the droplet currently runs within a real 4 GB ceiling and already hosts:

1. gunicorn
2. three Celery workers
3. Celery beat
4. Redis
5. RabbitMQ
6. Next.js client

Expect Flower to add another modest Python process and a small persistent store if enabled. This is likely acceptable, but it should still be measured after deployment.

### 3. Background queue pressure is the real bottleneck, not observability

Flower will make backlog visible; it will not reduce it.

Current observed state already shows the main issue clearly:

1. `background` queue carries the backlog
2. `default` and `hydration` are clear

So Flower is useful operationally, but it is not itself a queue-performance fix.

## Recommended Implementation Order

### Phase 1: private-only Flower

1. add `flower` to backend dependencies
2. add env vars and secrets
3. add `battlestats-flower.service`
4. enable and restart it on deploy
5. validate via SSH tunnel only

This delivers queue visibility with minimal risk.

### Phase 2: optional nginx exposure

1. choose `/flower/` path or dedicated subdomain
2. add reverse proxy config
3. keep Flower auth enabled even behind nginx
4. optionally add IP allow-listing

### Phase 3: operationalize usage

1. document standard checks for backlog, active tasks, and stuck workers
2. add Flower to the droplet monitoring checklist
3. decide whether screenshots or task-history export are needed for incident reviews

## Concrete Next Steps

If the goal is to get value quickly without overdesigning it, the next implementation slice should be:

1. add `flower` to `server/requirements.txt`
2. add `FLOWER_HOST`, `FLOWER_PORT`, `FLOWER_URL_PREFIX`, `FLOWER_PERSISTENT`, `FLOWER_DB`, and `FLOWER_BASIC_AUTH` handling to both droplet scripts
3. add a `battlestats-flower.service` systemd unit in both droplet scripts
4. create `/opt/battlestats-server/shared/flower/`
5. deploy and validate Flower through an SSH tunnel first
6. only after that, decide whether nginx exposure is worth the extra surface area

That is the smallest safe vertical slice for production Flower in this repository.
