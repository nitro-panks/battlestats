# Runbook: Post-Deploy And Post-Bounce Operations

_Created: 2026-04-05_

## Purpose

Define the complete post-redeploy and post-bounce operating sequence for the production droplet, including which processes are automatic, which remain manual, how to verify them, and how to run follow-up warming without swamping the server.

This runbook is the source of truth for:

1. post-backend-deploy verification,
2. post-client-deploy verification,
3. post-bounce behavior,
4. targeted cache invalidation and rewarming,
5. bounded warm sequencing on the 4 GB production droplet.

## Current Production Behavior

Production currently behaves this way after a backend redeploy or manual service bounce:

1. systemd services restart,
2. the active backend release must be verified explicitly,
3. Beat resumes periodic schedules,
4. Best-player snapshots may be materialized during backend deploy unless explicitly disabled,
5. startup cache warmers do not run automatically because `WARM_CACHES_ON_STARTUP=0` on the droplet.

As implemented in the repo now:

1. backend deploy automatically runs `scripts/post_deploy_operations.sh <host> verify --skip-client --expect-backend-release ...`,
2. client deploy automatically runs `scripts/post_deploy_operations.sh <host> verify --skip-backend --expect-client-release ...`,
3. heavier post-deploy snapshot, invalidation, landing warm, and best-entity warm steps remain manual and opt-in.

That means a bounce is intentionally lighter-weight than a full cache repopulation. Cached data already in Redis remains available, periodic warmers resume on schedule, and any deploy-specific refreshes must be triggered manually and narrowly.

Validated against code and live production state on 2026-04-05:

1. backend deploy restarts `redis-server` and `rabbitmq-server` in addition to gunicorn and the Celery services,
2. client deploy kills stale `next-server` processes before restarting `battlestats-client`,
3. the startup warm chain is exactly `landing -> hot entities -> bulk load -> distributions -> correlations`,
4. the most recent smoke test task was not green, so smoke verification remains an explicit required step.

## Why The Sequence Must Stay Narrow

The droplet is memory-constrained enough that broad concurrent warming is a reliability risk.

Relevant constraints already documented in the deploy and OOM runbooks:

1. Gunicorn, three Celery worker groups, Beat, Redis, RabbitMQ, and Next.js already consume most of the available RAM.
2. Full startup warming chains include landing payloads, hot entities, bulk entity loads, distributions, and correlations.
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
- [ ] smoke verification is run after targeted warm steps complete

### Only check when the deploy changed landing ranking, landing payloads, or cache-key semantics

- [ ] affected Best-player snapshots rebuilt
- [ ] affected landing-player or landing-clan caches invalidated
- [ ] landing warm run serially for each affected realm
- [ ] best-entity warm run only if the changed surface depends on deeper entity hydration
- [ ] no heavy warmer locks remain active before smoke verification

### Backend redeploy

These are required after every backend redeploy.

| Step | Why it matters | How it normally runs | Verification | Current status as of 2026-04-05 |
|---|---|---|---|---|
| Active backend release switch | New code is not live until `/opt/battlestats-server/current` points at the new release | Deploy script attempts this automatically | `readlink -f /opt/battlestats-server/current` | Required a manual fix during the CB rollout; verify explicitly every time |
| Gunicorn restart | Makes the new Django code serve traffic | Deploy script | `systemctl is-active battlestats-gunicorn` | Active |
| Celery default restart | Restarts user-facing task queue on the new code | Deploy script | `systemctl is-active battlestats-celery` | Active |
| Celery hydration restart | Restarts heavier request-driven refresh tasks on the new code | Deploy script | `systemctl is-active battlestats-celery-hydration` | Active |
| Celery background restart | Restarts warmers and long-running background tasks on the new code | Deploy script | `systemctl is-active battlestats-celery-background` | Active |
| Celery beat restart | Resumes periodic schedule execution on the new code | Deploy script | `systemctl is-active battlestats-beat` | Active |
| Redis restart | Restores cache backend and task-lock storage on the new rollout | Deploy script | `systemctl is-active redis-server` | Active |
| RabbitMQ restart | Restores Celery broker connectivity on the new rollout | Deploy script | `systemctl is-active rabbitmq-server` | Active |
| Migrations | Keeps DB schema aligned with code | Deploy script | deploy output and healthy app startup | Completed during deploy |
| Collectstatic | Publishes current static assets for Django-side static references | Deploy script | deploy output | Completed during deploy |
| Django check | Prevents shipping obviously broken server config | Deploy script | deploy output | Completed during deploy |
| Best-player snapshot materialization | Keeps Best-player landing payloads off the request path | Deploy script unless disabled | query `LandingPlayerBestSnapshot` rows | Present for `na` and `eu` across `overall`, `ranked`, `efficiency`, `wr`, and `cb` |

### Client redeploy

These are required after every client redeploy.

| Step | Why it matters | How it normally runs | Verification | Current status as of 2026-04-05 |
|---|---|---|---|---|
| New client release switch | New frontend build is not live until `current` points at the new release | Client deploy script | `readlink -f /opt/battlestats-client/current` | Previously completed during CB rollout |
| Stale Next process cleanup | Reclaims memory from orphaned node processes | Client deploy script | `ps` or healthy restart behavior | Handled by deploy path |
| Client restart | Serves the new Next.js build | Client deploy script | `systemctl is-active battlestats-client` | Active |
| Nginx health | Keeps public HTTP routing intact | Existing service, not redeployed each time | `systemctl is-active nginx` | Active |

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

If startup warming were enabled, the sequence in `startup_warm_all_caches` would run sequentially for each realm:

1. `warm_landing_page_content`
2. `warm_hot_entity_caches`
3. `bulk_load_entity_caches`
4. `warm_player_distributions`
5. `warm_player_correlations`

That chain is deliberately disabled on production because it is broader and heavier than most deploys need.

## Manual Post-Deploy Operations By Change Type

### No payload or ranking change

If the deploy only changes internal behavior and does not affect landing payloads, ranking logic, or cache-key semantics:

1. verify active release,
2. verify services are active,
3. run smoke checks,
4. stop there.

Do not run extra warmers just because a deploy happened.

### Best-player ranking, snapshot, or landing payload change

Run only the affected realms and only the affected sorts.

Preferred order:

1. materialize `LandingPlayerBestSnapshot` for affected `realm + sort`,
2. invalidate only the affected landing-player caches,
3. run landing-page warm for one realm at a time,
4. run best-entity warm only if the changed surface actually depends on deeper player/clan entity hydration,
5. smoke test after the targeted warms settle.

### Clan ranking or clan payload change

Preferred order:

1. invalidate only the affected landing-clan caches,
2. warm landing page content for one realm at a time,
3. run clan-only best-entity warming if the change affects clan detail or Best-clan candidate hydration,
4. avoid forcing player-side best-entity warming unless the change depends on player payload freshness.

### Distribution or correlation change

Only run these manually if the deploy changed those exact surfaces:

1. `warm_player_distributions`
2. `warm_player_correlations`

Do not pair them with landing best-entity warming unless required.

## Bounded Operating Plan

Use this order to keep the droplet stable.

### Safe serial plan

1. finish backend deploy,
2. verify `/opt/battlestats-server/current`,
3. verify backend services,
4. finish client deploy,
5. wait 60-120 seconds for steady-state service recovery,
6. run any required snapshot rebuilds first,
7. run cache invalidation second,
8. warm one realm at a time,
9. prefer landing warm before entity warm,
10. leave unrelated heavy warmers alone,
11. run smoke verification last.

### Concurrency rules

1. Do not run `na` and `eu` heavy warms in parallel.
2. Do not run `startup_warm_all_caches` manually after a routine deploy.
3. Do not combine bulk cache load, distribution warm, correlation warm, and best-entity warm unless recovering from a broad cache outage.
4. Prefer `force_refresh=False` for best-entity warming unless correctness requires forced upstream refresh.
5. Treat smoke testing as the last gate, not something to run while heavy warms are still active.

## Implemented Entrypoints

The deploy-scoped tooling now ships in two layers.

### Shell entrypoint

Use `scripts/post_deploy_operations.sh` from the repo root.

Supported subcommands:

1. `verify`
2. `snapshots`
3. `invalidate`
4. `warm-landing`
5. `warm-best-entities`
6. `smoke`

Representative usage:

```bash
./scripts/post_deploy_operations.sh YOUR_DROPLET_IP verify --realm na --realm eu
./scripts/post_deploy_operations.sh YOUR_DROPLET_IP snapshots --realm na --sort cb
./scripts/post_deploy_operations.sh YOUR_DROPLET_IP invalidate --realm na --players --include-recent
./scripts/post_deploy_operations.sh YOUR_DROPLET_IP warm-landing --realm na --include-recent
./scripts/post_deploy_operations.sh YOUR_DROPLET_IP warm-best-entities --realm na --player-limit 25 --clan-limit 25
./scripts/post_deploy_operations.sh YOUR_DROPLET_IP smoke --base-url http://127.0.0.1:8888
```

The shell wrapper is responsible for:

1. remote release-target verification,
2. systemd service verification,
3. invoking the Django-side command for app-scoped operations,
4. invoking the smoke script in JSON mode.

### Django entrypoint

App-scoped operations live behind:

```bash
python manage.py run_post_deploy_operations <operation> [options]
```

Supported operations:

1. `verify`
2. `snapshots`
3. `invalidate`
4. `warm-landing`
5. `warm-best-entities`

This command returns structured JSON so the shell wrapper can fail closed without scraping freeform logs.

## Operational Implementation Plan

The current runbook is operator-accurate, but too much of the workflow still depends on manual SSH and ad hoc shell commands.

The implementation goal is:

1. make the checklist executable,
2. keep heavy follow-up steps opt-in,
3. fail closed when release activation or service health is wrong,
4. preserve the current bounded-load behavior.

### Phase 1: Automate release and service verification

Implement a single verification entrypoint that checks:

1. backend `current` release target,
2. client `current` release target,
3. required systemd services,
4. Best-player snapshot presence,
5. active warmer locks.

This phase should be verification-only. It should not invalidate caches or warm anything.

This phase also needs to drive a code fix in the deploy paths themselves: verification is not enough if release activation can still silently drift. The implementation should harden activation first, then verify it.

### Phase 2: Add opt-in targeted follow-up operations

Implement an explicit post-deploy operations command or script that can run narrow follow-up steps serially.

Required capabilities:

1. rebuild Best-player snapshots for selected `realm + sort`,
2. invalidate landing-player caches for selected realms,
3. invalidate landing-clan caches for selected realms,
4. warm landing payloads one realm at a time,
5. optionally warm best-entity caches one realm at a time,
6. print concise machine-readable status after each step.

This phase must remain opt-in. Backend or client deploy should not automatically run heavy warms by default.

### Phase 3: Add smoke verification as a final gate

Implement a final smoke-verification step that runs only after targeted warm steps complete.

Required behavior:

1. run after verification and any requested follow-up operations,
2. fail the overall post-deploy plan if smoke checks fail,
3. emit a short summary that can be pasted into deploy notes or a runbook log.

### Phase 4: Wire deploy scripts to the new plan conservatively

Once the verification and targeted follow-up tooling exists, wire the deploy scripts to use it in a bounded way.

Safe default behavior:

1. backend deploy runs verification automatically,
2. client deploy runs client-side verification automatically,
3. heavy follow-up operations remain disabled by default and require explicit flags,
4. smoke verification can be opt-in until it is proven stable enough to gate every deploy.

Client and backend deploys should both converge on the same release-activation standard:

1. switch `current` atomically,
2. verify that `readlink -f` matches the intended release,
3. fail the deploy if activation verification fails.

## Code Changes Required

| File | Change |
|---|---|
| `server/deploy/deploy_to_droplet.sh` | Harden release activation so `current` is switched atomically and verified every time, then call a shared post-deploy verifier. Fail immediately if the active backend release target or required backend services do not match expectations. Keep heavy warming behind explicit flags. |
| `client/deploy/deploy_to_droplet.sh` | Replace the current unchecked `ln -sfn` activation with the same atomic-and-verified activation model used by the backend deploy, then call the shared verifier for client symlink and service health. |
| `scripts/post_deploy_operations.sh` | New shell entrypoint for the operational plan. Support subcommands or flags for `verify`, `snapshots`, `invalidate`, `warm-landing`, `warm-best-entities`, and `smoke`. Ensure serial realm execution and readable summaries. |
| `server/warships/management/commands/run_post_deploy_operations.py` | New management command for app-scoped operations that belong inside Django: snapshot rebuilds, landing cache invalidation, landing warm, best-entity warm, and lock/status reporting. Keep outputs structured JSON so the shell wrapper can compose them safely. |
| `server/scripts/smoke_test_site_endpoints.py` | Review and harden smoke output so the post-deploy wrapper can reliably distinguish pass, fail, and partial failure without scraping ambiguous logs. This is the current smoke-script location in the repo and in the existing VS Code task. |
| `agents/runbooks/runbook-backend-droplet-deploy.md` | Update after implementation so it documents the new default verifier and any new opt-in flags for targeted post-deploy operations. |
| `agents/runbooks/runbook-client-droplet-deploy.md` | Update after implementation so it documents client-side verification behavior and how the shared post-deploy plan is invoked. |
| `agents/runbooks/runbook-daily-data-refresh-schedule-2026-04-05.md` | Keep the boundary clear: daily refresh remains steady-state, while the new post-deploy tooling remains deploy-scoped and intentionally narrow. |

## Guardrails For The Code Changes

1. Do not add automatic full startup warming to deploy scripts.
2. Do not run `na` and `eu` heavy warm steps in parallel.
3. Do not make best-entity warming mandatory for every deploy.
4. Do not make smoke testing block deploy by default until the current exit-code-15 flake is understood and stabilized.
5. Prefer structured JSON summaries from operational commands over ad hoc log scraping.

## Acceptance Criteria For The Implementation

The implementation is complete when all of the following are true:

1. backend deploy can prove the active backend release and required backend services without manual SSH follow-up,
2. client deploy can prove the active client release and required client services without manual SSH follow-up,
3. targeted post-deploy snapshot, invalidation, and warm steps can be executed from a single documented entrypoint,
4. the operational entrypoint runs heavy steps serially and does not broaden default load,
5. smoke verification can be invoked as the final step with a clear pass/fail summary,
6. the deploy and daily-refresh runbooks still describe non-overlapping responsibilities.

## QA Findings For The Implementation Plan

This implementation-plan section was reviewed against the current repo layout and deploy tooling on 2026-04-05.

Findings applied in this revision:

1. corrected the smoke script path from `scripts/smoke_test_site_endpoints.py` to `server/scripts/smoke_test_site_endpoints.py`, which is the actual file tracked in the repo,
2. added explicit client deploy activation hardening because the current client deploy still uses an unchecked `ln -sfn` switch,
3. tightened the backend deploy plan so it fixes activation drift instead of only verifying after the fact,
4. clarified that both deploy scripts should share the same atomic-and-verified activation standard before the post-deploy verifier is trusted.

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

### Snapshot coverage verification

```bash
cd /opt/battlestats-server/current/server
set -a
source /etc/battlestats-server.env
source /etc/battlestats-server.secrets.env
set +a
/opt/battlestats-server/venv/bin/python manage.py shell -c '
from warships.models import LandingPlayerBestSnapshot
print(list(LandingPlayerBestSnapshot.objects.order_by("realm", "sort").values_list("realm", "sort")))
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
    _landing_best_entity_warm_lock_key,
    _landing_page_warm_lock_key,
)
keys = [
    _landing_page_warm_lock_key("na"), _landing_page_warm_lock_key("eu"),
    _hot_entity_cache_warm_lock_key("na"), _hot_entity_cache_warm_lock_key("eu"),
    _bulk_cache_load_lock_key("na"), _bulk_cache_load_lock_key("eu"),
    _distribution_warm_lock_key("na"), _distribution_warm_lock_key("eu"),
    _correlation_warm_lock_key("na"), _correlation_warm_lock_key("eu"),
    _landing_best_entity_warm_lock_key("na"), _landing_best_entity_warm_lock_key("eu"),
]
print({key: bool(cache.get(key)) for key in keys})
'
```

## Current Operational Status Snapshot

As of 2026-04-05 after the CB ranking rollout:

1. backend services are active,
2. client and nginx are active,
3. `LandingPlayerBestSnapshot` rows exist for `na` and `eu` across all five Best-player sorts,
4. startup warmers are disabled on production (`WARM_CACHES_ON_STARTUP=0`),
5. manual landing warm for the CB rollout completed earlier for `na` and `eu`,
6. the clan-member `None` upstream crash path was fixed and the NA clan-only best-entity warm completed,
7. the EU clan-only verification no longer hit the old `AttributeError`, but concise completion output remained expensive to obtain because the realm-specific warm cascaded into additional hydration work,
8. deploy scripts now auto-run a bounded release-and-service verifier after backend and client rollout,
9. the smoke script now supports JSON output for deploy tooling, but smoke verification still remains opt-in until the broader flake history is retired.

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