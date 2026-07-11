# Runbook: Post-Deploy And Post-Bounce Operations

_Created: 2026-04-05_

> **Reconciled for 3.0 (landing featured-boards decommission).** The
> `run_post_deploy_operations` management command and its landing-coupled subcommands
> (`snapshots`, `invalidate`, `warm-landing`, `warm-best-entities`) were **removed in 3.0**;
> `scripts/post_deploy_operations.sh` now exposes only `verify` (release + systemd-service
> checks) and `smoke`. All Best-player/Best-clan ranking, `LandingPlayerBestSnapshot`
> materialization, and landing-payload warm/invalidate guidance below has been removed. This
> runbook remains the canonical post-deploy/post-bounce **verification** checklist. See
> `runbook-landing-featured-boards-decommission-2026-06-22.md`.

## Purpose

Define the complete post-redeploy and post-bounce operating sequence for the production droplet, including which processes are automatic, which remain manual, and how to verify them without swamping the server.

This runbook is the source of truth for:

1. post-backend-deploy verification,
2. post-client-deploy verification,
3. post-bounce behavior,
4. bounded, surface-scoped follow-up warming (distributions / correlations only) on the production droplet.

## Current Production Behavior

Production currently behaves this way after a backend redeploy or manual service bounce:

1. systemd services restart,
2. the active backend release must be verified explicitly,
3. Beat resumes periodic schedules,
4. startup cache warmers do not run automatically because `WARM_CACHES_ON_STARTUP=0` on the droplet.

As implemented in the repo now:

1. backend deploy automatically runs `scripts/post_deploy_operations.sh <host> verify --skip-client --expect-backend-release ...`,
2. client deploy automatically runs `scripts/post_deploy_operations.sh <host> verify --skip-backend --expect-client-release ...`,
3. any surface-scoped follow-up warm (distributions / correlations) remains manual and opt-in.

That means a bounce is intentionally lighter-weight than a full cache repopulation. Cached data already in Redis remains available, periodic warmers resume on schedule, and any deploy-specific refreshes must be triggered manually and narrowly.

Validated against code and live production state on 2026-04-05:

1. backend deploy restarts `redis-server` and `rabbitmq-server` in addition to gunicorn and the Celery services,
2. client deploy kills stale `next-server` processes before restarting `battlestats-client`,
3. the startup warm chain is `hot entities -> bulk load -> distributions -> correlations` (the landing-page warm was removed in 3.0),
4. the most recent smoke test task was not green, so smoke verification remains an explicit required step.

## Why The Sequence Must Stay Narrow

The droplet is memory-constrained enough that broad concurrent warming is a reliability risk.

Relevant constraints already documented in the deploy and OOM runbooks:

1. Gunicorn, three Celery worker groups, Beat, Redis, RabbitMQ, and Next.js already consume most of the available RAM.
2. Full startup warming chains include hot entities, bulk entity loads, distributions, and correlations.
3. These warmers can trigger large DB scans or upstream hydration bursts.
4. Running multiple realm-wide warmers together increases both memory pressure and upstream load.

The operating rule is therefore:

1. prefer the smallest realm-scoped, surface-scoped warm that fixes the changed surface,
2. run heavy operations serially,
3. avoid manual full startup warming unless a broader cache outage justifies it.

## Operator Checklist

Use this checklist during the actual operation.

### Always check

- [ ] backend release symlink points to the intended release
- [ ] `battlestats-gunicorn` is active
- [ ] `battlestats-celery` is active
- [ ] `battlestats-celery-hydration` is active
- [ ] `battlestats-celery-background` is active
- [ ] `battlestats-beat` is active
- [ ] `redis-server` is active
- [ ] `rabbitmq-server` is active
- [ ] client release symlink points to the intended release
- [ ] `battlestats-client` is active
- [ ] `nginx` is active
- [ ] smoke verification is run after any follow-up warm steps complete

### Only check when the deploy changed distribution or correlation surfaces

- [ ] `warm_player_distributions` / `warm_player_correlations` run serially for each affected realm
- [ ] no heavy warmer locks remain active before smoke verification

### Backend redeploy

These are required after every backend redeploy.

| Step                                 | Why it matters                                                                         | How it normally runs                                                       | Verification                                        | Current status as of 2026-04-05                                                                                                                                |
| ------------------------------------ | -------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- | --------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Active backend release switch        | New code is not live until `/opt/battlestats-server/current` points at the new release | Deploy script does this automatically with atomic `mv -T` and verification | `readlink -f /opt/battlestats-server/current`       | Automated — deploy exits non-zero if activation fails. Previously required manual fix during CB rollout (see `archive/runbook-stale-symlink-diagnosis-2026-04-05.md`). |
| Gunicorn restart                     | Makes the new Django code serve traffic                                                | Deploy script                                                              | `systemctl is-active battlestats-gunicorn`          | Active                                                                                                                                                         |
| Celery default restart               | Restarts user-facing task queue on the new code                                        | Deploy script                                                              | `systemctl is-active battlestats-celery`            | Active                                                                                                                                                         |
| Celery hydration restart             | Restarts heavier request-driven refresh tasks on the new code                          | Deploy script                                                              | `systemctl is-active battlestats-celery-hydration`  | Active                                                                                                                                                         |
| Celery background restart            | Restarts warmers and long-running background tasks on the new code                     | Deploy script                                                              | `systemctl is-active battlestats-celery-background` | Active                                                                                                                                                         |
| Celery beat restart                  | Resumes periodic schedule execution on the new code                                    | Deploy script                                                              | `systemctl is-active battlestats-beat`              | Active                                                                                                                                                         |
| Redis restart                        | Restores cache backend and task-lock storage on the new rollout                        | Deploy script                                                              | `systemctl is-active redis-server`                  | Active                                                                                                                                                         |
| RabbitMQ restart                     | Restores Celery broker connectivity on the new rollout                                 | Deploy script                                                              | `systemctl is-active rabbitmq-server`               | Active                                                                                                                                                         |
| Migrations                           | Keeps DB schema aligned with code                                                      | Deploy script                                                              | deploy output and healthy app startup               | Completed during deploy                                                                                                                                        |
| Collectstatic                        | Publishes current static assets for Django-side static references                      | Deploy script                                                              | deploy output                                       | Completed during deploy                                                                                                                                        |
| Django check                         | Prevents shipping obviously broken server config                                       | Deploy script                                                              | deploy output                                       | Completed during deploy                                                                                                                                        |

### Client redeploy

These are required after every client redeploy.

| Step                       | Why it matters                                                           | How it normally runs                       | Verification                                  | Current status as of 2026-04-05        |
| -------------------------- | ------------------------------------------------------------------------ | ------------------------------------------ | --------------------------------------------- | -------------------------------------- |
| New client release switch  | New frontend build is not live until `current` points at the new release | Client deploy script                       | `readlink -f /opt/battlestats-client/current` | Previously completed during CB rollout |
| Stale Next process cleanup | Reclaims memory from orphaned node processes                             | Client deploy script                       | `ps` or healthy restart behavior              | Handled by deploy path                 |
| Client restart             | Serves the new Next.js build                                             | Client deploy script                       | `systemctl is-active battlestats-client`      | Active                                 |
| Nginx health               | Keeps public HTTP routing intact                                         | Existing service, not redeployed each time | `systemctl is-active nginx`                   | Active                                 |

## Post-Bounce Behavior

### What a bounce does today

A bounce currently means service restart only.

For this runbook, "bounce" means restarting the existing production services in place. It does not imply a fresh deploy, cache invalidation, snapshot rebuild, or explicit warm job.

Because production has:

```env
WARM_CACHES_ON_STARTUP=0
```

the gunicorn `when_ready` hook does not dispatch `startup_warm_caches_task` after restart.

So after a bounce:

1. services come back,
2. existing Redis cache entries remain available until TTL expiry or invalidation,
3. Beat resumes its periodic jobs,
4. no full startup warm chain is automatically queued,
5. any deploy-specific cache invalidation requires explicit follow-up warming.

### What the disabled startup warm chain would have done

If startup warming were enabled, the sequence in `startup_warm_all_caches` would run sequentially for each realm (the `warm_landing_page_content` step was removed in 3.0):

1. `warm_hot_entity_caches`
2. `bulk_load_entity_caches`
3. `warm_player_distributions`
4. `warm_player_correlations`

That chain is deliberately disabled on production because it is broader and heavier than most deploys need.

## Manual Post-Deploy Operations By Change Type

### No payload or ranking change

If the deploy only changes internal behavior and does not affect landing payloads, ranking logic, or cache-key semantics:

1. verify active release,
2. verify services are active,
3. run smoke checks,
4. stop there.

Do not run extra warmers just because a deploy happened.

### Distribution or correlation change

Only run these manually if the deploy changed those exact surfaces:

1. `warm_player_distributions`
2. `warm_player_correlations`

Run them one realm at a time; do not pair them with the bulk cache load unless recovering from a broad cache outage.

## Bounded Operating Plan

Use this order to keep the droplet stable.

### Safe serial plan

1. finish backend deploy,
2. verify `/opt/battlestats-server/current`,
3. verify backend services,
4. finish client deploy,
5. wait 60-120 seconds for steady-state service recovery,
6. run any surface-scoped follow-up warm (distributions / correlations) only if the deploy changed that surface, one realm at a time,
7. leave unrelated heavy warmers alone,
8. run smoke verification last.

### Concurrency rules

1. Do not run `na` and `eu` heavy warms in parallel.
2. Do not run `startup_warm_all_caches` manually after a routine deploy.
3. Do not combine bulk cache load, distribution warm, and correlation warm unless recovering from a broad cache outage.
4. Treat smoke testing as the last gate, not something to run while heavy warms are still active.

## Implemented Entrypoints

### Shell entrypoint

Use `scripts/post_deploy_operations.sh` from the repo root.

Supported subcommands (as of 3.0):

1. `verify` — remote release-target + systemd-service verification
2. `smoke` — endpoint smoke test in JSON mode

Representative usage:

```bash
./scripts/post_deploy_operations.sh YOUR_DROPLET_IP verify --realm na --realm eu
./scripts/post_deploy_operations.sh YOUR_DROPLET_IP smoke --base-url http://127.0.0.1:8888
```

The shell wrapper is responsible for:

1. remote release-target verification,
2. systemd service verification,
3. invoking the smoke script in JSON mode.

### Django entrypoint (removed in 3.0)

The `python manage.py run_post_deploy_operations <operation>` command — and all five of its
operations (`verify`, `snapshots`, `invalidate`, `warm-landing`, `warm-best-entities`) — was
**removed in 3.0** with the landing featured-boards decommission (every operation was
landing/snapshot-coupled). Release + service verification now lives entirely in the shell
wrapper's `verify` subcommand; there is no longer a Django-side post-deploy command.

## Operational Implementation Plan (Completed)

All four phases of the original implementation plan are now complete:

1. **Phase 1 (release and service verification)**: Both deploy scripts use atomic `mv -T` activation with `readlink -f` verification. The shared `scripts/post_deploy_operations.sh verify` subcommand checks release targets and systemd services.
2. **Phase 2 (targeted follow-up operations)**: ~~`snapshots` / `invalidate` / `warm-landing` / `warm-best-entities` subcommands~~ — **removed in 3.0** with the landing decommission; these operations no longer exist.
3. **Phase 3 (smoke verification)**: `scripts/post_deploy_operations.sh smoke` runs `server/scripts/smoke_test_site_endpoints.py` in JSON mode with clear pass/fail.
4. **Phase 4 (wired into deploy scripts)**: Backend deploy auto-runs verification via `scripts/post_deploy_operations.sh verify`. Client deploy does the same for client-side checks.

### Deploy script ordering fix (2026-04-05)

The backend deploy script had a structural issue where `configure_local_rabbitmq()` ran before core release setup (`.env` symlinks, pip install, migrate, collectstatic, chown, activation). A RabbitMQ failure would abort the entire SSH block, leaving incomplete releases. This was fixed by moving `configure_local_rabbitmq()` after release activation but before service restart. See `archive/runbook-stale-symlink-diagnosis-2026-04-05.md` for full diagnosis.

## Guardrails

1. Do not add automatic full startup warming to deploy scripts.
2. Do not run `na` and `eu` heavy warm steps in parallel.
3. Do not make distribution/correlation warming mandatory for every deploy.
4. Do not make smoke testing block deploy by default until the current exit-code-15 flake is understood and stabilized.
5. Prefer structured JSON summaries from operational commands over ad hoc log scraping.

## Verification Commands

These checks were QA-reviewed against the current deploy scripts and live production shape on 2026-04-05.

### Release and service verification

```bash
ssh root@YOUR_DROPLET_IP '
  readlink -f /opt/battlestats-server/current &&
  systemctl is-active battlestats-gunicorn battlestats-celery battlestats-celery-hydration battlestats-celery-background battlestats-beat redis-server rabbitmq-server
'
```

```bash
ssh root@YOUR_DROPLET_IP '
  readlink -f /opt/battlestats-client/current &&
  systemctl is-active battlestats-client nginx
'
```

### Warmer lock verification

```bash
cd /opt/battlestats-server/current/server
set -a
source /etc/battlestats-server.env
source /etc/battlestats-server.secrets.env
set +a
/opt/battlestats-server/venv/bin/python manage.py shell -c '
from django.core.cache import cache
from warships.tasks import (
    _bulk_cache_load_lock_key,
    _correlation_warm_lock_key,
    _distribution_warm_lock_key,
    _hot_entity_cache_warm_lock_key,
)
keys = [
    _hot_entity_cache_warm_lock_key("na"), _hot_entity_cache_warm_lock_key("eu"),
    _bulk_cache_load_lock_key("na"), _bulk_cache_load_lock_key("eu"),
    _distribution_warm_lock_key("na"), _distribution_warm_lock_key("eu"),
    _correlation_warm_lock_key("na"), _correlation_warm_lock_key("eu"),
]
print({key: bool(cache.get(key)) for key in keys})
'
```

## Known Issue: systemd EnvironmentFile and AMQP URL quoting

**Symptom**: After a deploy, Celery workers log `Cannot connect to amqp://guest:**@rabbitmq:5672//` instead of connecting to `127.0.0.1`. All request-driven background tasks (CB aggregation, ranked hydration, battle data refresh) silently fail. The `X-*-Pending` headers are set but never resolve.

**Root cause**: systemd `EnvironmentFile` parsing treats unquoted `//` as a comment delimiter. The `CELERY_BROKER_URL=amqp://...@127.0.0.1:5672//` value gets truncated at the `//`, causing the env var to be empty. Workers fall back to the Django settings default (`amqp://guest:guest@rabbitmq:5672//` — the Docker-compose hostname).

**Fix**: `set_env_value()` in `bootstrap_droplet.sh` now wraps all values in double quotes (`3a61628`). If the issue recurs, manually quote the value:

```bash
# Check current state
grep CELERY_BROKER_URL /etc/battlestats-server.env
# If unquoted, fix it
sed -i 's|^CELERY_BROKER_URL=\(.*\)|CELERY_BROKER_URL="\1"|' /etc/battlestats-server.env
# Restart workers
systemctl restart battlestats-celery battlestats-celery-hydration battlestats-celery-background battlestats-beat
# Verify
journalctl -u battlestats-celery-hydration --since '10 sec ago' | grep 'Connected to'
```

**Detection**: Check for `Cannot connect to amqp://guest:**@rabbitmq:5672//` in any Celery worker journal. The `rabbitmq` hostname (vs `127.0.0.1`) is the tell.

## QA Findings

The runbook was reviewed against the current deploy scripts, the startup warmer command, and live production state on 2026-04-05.

Changes applied from QA:

1. added `redis-server` and `rabbitmq-server` to the required checks because backend deploy restarts both services,
2. clarified that snapshot materialization is default-on rather than unconditional,
3. clarified the exact meaning of a post-bounce state in this repo,
4. marked smoke verification as still outstanding instead of implying a fully green post-deploy state,
5. removed a duplicate checklist heading.

## Relationship To Daily Refresh Scheduling

This runbook governs deploy-time and bounce-time operations only.

The daily refresh runbook remains the source of truth for:

1. DO Functions backfill and steady-state scheduling,
2. clan-sync and enrichment windows,
3. daily or periodic background freshness goals.

The two documents must stay aligned on one critical distinction:

1. deploy-time warmers are narrowly targeted and operator-driven,
2. steady-state freshness is handled by periodic jobs and DO Functions,
3. a bounce is not a substitute for the daily refresh plan.
