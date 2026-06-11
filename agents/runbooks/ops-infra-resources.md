# Ops: Infrastructure Resources

**Lifecycle:** evergreen · **Owner:** platform · **Last verified:** 2026-06-11 (live `doctl` + `pg_settings`)

Authoritative current production resource sizing. This is the single source of truth
for "how big is the box" questions; **re-verify against live infra before quoting in
capacity math** (slugs/limits change on resize). This doc supersedes any "1-vCPU"
claim in older runbooks — those describe the **pre-2026-05-28** state, not today's.

## TL;DR for agents (read this before sizing anything)

- **Managed Postgres is 2 vCPU / 4 GB RAM** — resized from `db-amd-1vcpu-2gb`
  (1 vCPU / 2 GB) on **2026-05-28**. `system_load15` saturates around **2**, not 1.
  **Do NOT plan against a 1-vCPU DB budget** — that is the most common stale assumption.
- **App droplet is 2 vCPU / 8 GB RAM** (+ 2 GB swap).
- DB **CPU** is a light watch-item, not a capacity blocker. DB **disk is not a
  constraint** (60 GB plan, ~22 GB used).

## App droplet (`battlestats-droplet`, nyc3)

| Resource | Value |
| --- | --- |
| vCPU | **2** |
| RAM  | **8 GB** (+ 2 GB swap) |
| Disk | 90 GB (~35% used) |

Co-hosts everything except the DB: nginx, gunicorn (Django), Next.js (3001), Umami
(3002), all Celery workers + Beat, RabbitMQ, Redis. Also shares the host with the
`oturu` project — status sweeps should include both.

## Managed Postgres (`db-postgresql-nyc3-11231`, nyc3)

| Resource | Value |
| --- | --- |
| Plan slug | `db-s-2vcpu-4gb` |
| vCPU | **2** |
| RAM | **4 GB** |
| Disk | 60 GB (~21.6 GB used) |
| Engine | PostgreSQL **18**, single node |
| `max_connections` | 100 (**~97 usable**; DO reserves a few for management) |
| `shared_buffers` | ~780 MB (`99840 × 8kB`) |
| `effective_cache_size` | ~2.3 GB (`299648 × 8kB`) |
| `work_mem` (default) | 4 MB (analytical queries raise to `ANALYTICAL_WORK_MEM`=8 MB via `SET LOCAL`) |
| `max_parallel_workers` | 8 (`_per_gather` = 2) |

`shared_buffers` / `max_connections` are not operator-configurable on DO managed PG —
plan within these. History: the May 2026 disk/CPU incidents
(`runbook-db-cpu-saturation-2026-05-24.md`) were on the old 1-vCPU / 2-GB plan; the
resize + a disk bump to 60 GB followed.

## Connection budget (well within ~97)

gunicorn 5 (`cpu*2+1`, capped) + Celery default 3 + hydration 3 + background 3 +
crawls 1 + startup warmer 1 + Beat ≈ **~17 max**. `CONN_MAX_AGE=300` +
`CONN_HEALTH_CHECKS` keep them alive/healthy. The 97-connection limit is the binding
DB constraint, not core count — leave concurrency as-is unless a new bottleneck appears.

## How to re-verify

```bash
# DB plan + storage
doctl databases get 5449f4d9-a924-4158-af2d-0614a8cfd485 -o json | \
  python3 -c "import sys,json;d=json.load(sys.stdin)[0];print(d['size'],d['storage_size_mib'],d['version'])"
# DB tuning (no creds in this repo — pull from server/.env.secrets.cloud)
psql "$DB_URL" -c "SELECT name,setting,unit FROM pg_settings WHERE name IN \
  ('max_connections','shared_buffers','work_mem','effective_cache_size','max_parallel_workers');"
# Droplet
doctl compute droplet list --format Name,Memory,VCPUs,Disk
ssh root@battlestats.online 'nproc; free -h; df -h /'
```

Live DB load: scrape the DO Prometheus endpoint (watch `system_load15` vs **2**) —
see `reference_do_db_cpu_metrics_endpoint` (auto-memory) /
`runbook-db-cpu-saturation-2026-05-24.md`.

**Never commit the DB connection-string password** to any file in this repo.
