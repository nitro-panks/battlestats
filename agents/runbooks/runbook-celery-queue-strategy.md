# Runbook: Celery Queue Strategy

**Created**: 2026-04-03
**Status**: Active - initial implementation slice landed
**Depends on**: `runbook-backend-droplet-deploy.md`, `runbook-cache-audit.md`, `spec-multi-realm-eu-support.md`

## Question

Does the current queue, routing, and worker model support battlestats task-based operations appropriately when long-running crawlers coexist with request-driven refresh work?

## Short Answer

The current production topology is directionally correct, and the first queue-strategy tightening slice is now implemented.

What is already good:

1. long-running crawlers and periodic warmers are isolated away from interactive request paths
2. ranked and efficiency hydrations already have their own queue
3. duplicate dispatch, stale crawl recovery, and upstream-protection rules are materially better than a naive single-queue Celery setup
4. local Docker now includes a dedicated hydration worker, so local queue topology matches the production three-queue model more closely

What is still suboptimal:

1. some request-driven work still remains on the `default` queue by design, so interactive work is improved but not perfectly stratified
2. the `background` queue is still carrying multiple very different maintenance classes with no further split

The answer is therefore:

1. **appropriate for current production stability and materially better than before this tranche**
2. **still not the final optimal shape if the goal is maximum isolation between all classes of interactive and maintenance work**

## Implementation Outcome

The initial runbook slice was implemented on 2026-04-03.

### Changes made

1. added explicit `background` routing for `ensure_crawl_all_clans_running_task`
2. added explicit `background` routing for `warm_all_clan_tier_distributions_task`
3. moved these request-driven heavier refreshes into `hydration`
   - `update_battle_data_task`
   - `update_clan_members_task`
   - `update_player_clan_battle_data_task`
   - `update_clan_battle_summary_task`
4. kept lighter entity refreshes such as `update_player_data_task` and `update_clan_data_task` on `default`
5. updated local `docker-compose.yml` to run a dedicated `hydration` worker in addition to `default` and `background`
6. corrected the local default worker name from `hydration@%h` to `default@%h`
7. added focused automated coverage for the queue-routing contract

### Why this slice

This keeps the current three-queue model intact while removing the highest-value contention points:

1. long-running non-interactive work stays on `background`
2. heavy request-driven upstream refreshes move off the shared `default` lane
3. lightweight request-adjacent entity refreshes remain on `default`
4. local topology now exercises the same queue split that production uses

## Current Runtime Topology

### Production queue model

Production deploy scripts create three dedicated workers:

1. `default` queue, concurrency `3`
2. `hydration` queue, concurrency `3`
3. `background` queue, concurrency `2`

Production also runs a dedicated Beat process.

The intended production mapping is visible in:

1. `server/battlestats/settings.py` via `CELERY_TASK_ROUTES`
2. `server/deploy/bootstrap_droplet.sh`
3. `server/deploy/deploy_to_droplet.sh`

### Routing rules in code

Current explicit task routing:

1. `background`
   - `crawl_all_clans_task`
   - `ensure_crawl_all_clans_running_task`
   - `incremental_player_refresh_task`
   - `incremental_ranked_data_task`
   - `refresh_efficiency_rank_snapshot_task`
   - landing warmers and hot-cache warmers
   - random landing queue refill tasks
   - bulk cache loader
   - `enrich_player_data_task` — self-chaining player enrichment crawler. Re-seeded every 15 min by the `player-enrichment-kickstart` Beat schedule (no-op if a batch is already running). A DO Functions migration (2026-04-04) was reverted on 2026-04-08; see `archive/spec-serverless-background-workers-2026-04-04.md`.
   - `startup_warm_caches_task`
   - `warm_all_clan_tier_distributions_task`
2. `hydration`
   - `update_battle_data_task`
   - `update_clan_members_task`
   - `update_ranked_data_task`
   - `update_player_clan_battle_data_task`
   - `update_player_efficiency_data_task`
   - `update_clan_battle_summary_task`
3. `default`
   - lightweight request-adjacent entity refreshes that are still intentionally left on the general lane

After the implementation slice, request-adjacent tasks still landing on `default` are primarily:

1. `update_player_data_task`
2. `update_clan_data_task`

## How Request-Driven Work Enters The System

The API layer enqueues refresh work directly from view and data paths.

### Player detail path

Player detail access can enqueue:

1. `update_player_data_task`
2. `update_battle_data_task`
3. `update_clan_data_task`
4. `update_clan_members_task`
5. `queue_ranked_data_refresh()` which routes to `hydration`
6. `queue_efficiency_data_refresh()` which routes to `hydration`
7. `queue_clan_battle_data_refresh()` which now resolves to `hydration`

### Clan detail and clan-adjacent paths

Clan access can enqueue:

1. `update_clan_data_task`
2. `update_clan_members_task`
3. `queue_clan_battle_summary_refresh()` now resolves to `hydration`

### Landing and cache behavior

Landing surfaces use cache-first behavior and enqueue warmers onto `background`, which is good because they do not need to preempt request-driven entity refreshes.

## Current Strengths

### 1. Crawlers are properly isolated from interactive traffic in production

This is the biggest architectural success in the current model.

Full clan crawls, incremental refreshes, enrichment, startup warming, and landing warmers all run on `background`, not on the same queue as request-driven refreshes. That prevents the worst class of failure where a long crawl blocks page-driven work.

### 2. There is already real upstream-protection logic, not just queue naming

The code does more than separate queues:

1. `prefetch-multiplier=1` improves fairness and reduces head-of-line hoarding
2. `acks_late` and `reject_on_worker_lost` improve recovery semantics
3. dispatch helpers use Redis-backed `cache.add(...)` guards to deduplicate enqueues
4. broker-failure cooldown keys prevent tight dispatch retry loops
5. crawl locks and heartbeats prevent duplicate full crawls and allow watchdog recovery
6. cross-realm crawl mutex limits concurrent full crawls
7. enrichment defers when crawls are active to avoid WG API contention
8. periodic schedules are staggered by realm to avoid synchronized spikes
9. RabbitMQ `consumer_timeout` is disabled via `advanced.config` to prevent channel kills on long-held unacked messages (required by `acks_late`)
10. systemd consumer watchdog runs every 5 min to detect and recover zombie workers (process alive, 0 consumers)

This is already a mature bounded-load posture.

### 3. Ranked and efficiency hydration were correctly recognized as a separate class

The `hydration` queue is the right idea. Ranked and efficiency refreshes are both request-adjacent and externally expensive enough that they should not compete directly with generic `default` work.

That separation is a good baseline for future refinement.

## Current Weaknesses

### Finding 1: `default` is still a mixed-priority lane

The current `default` queue is carrying too many request-driven task types with different cost and urgency profiles.

Examples:

1. `update_player_data_task` is request-adjacent and directly affects player detail freshness
2. `update_clan_members_task` can be materially heavier than a simple player refresh
3. `update_player_clan_battle_data_task` is request-triggered but still shares the same lane
4. `update_clan_battle_summary_task` is user-visible but not necessarily as urgent as the first player refresh on a page load

These are all reasonable tasks to enqueue, but they are not equal-priority work.

The current system therefore isolates **long-running background work** correctly, but it does **not** yet isolate **high-urgency interactive work** from **moderate-cost interactive work**.

### Finding 2: `hydration` was previously too narrow for the current product surface

Before this tranche, only ranked and efficiency refreshes were routed to `hydration`.

That gap is now reduced. The following task classes were moved into `hydration` because they behave more like heavy request-driven hydration than like generic default-lane work:

1. `update_battle_data_task`
2. `update_clan_members_task`
3. `update_player_clan_battle_data_task`
4. `update_clan_battle_summary_task`

The remaining question is whether any additional request-driven tasks should leave `default`, not whether `hydration` should stay as narrowly scoped as before.

### Finding 3: `background` carries the heaviest sustained load

`background` combines full crawls, incremental refreshes, landing warmers, startup warmers, bulk cache loads, and continuous enrichment. The enrichment lane was briefly migrated to DigitalOcean Functions (2026-04-04) but was reverted on 2026-04-08 because DO Functions egress from a rotating IP pool that cannot be whitelisted by the Wargaming `application_id`. Enrichment runs on the `background` queue via self-chaining `enrich_player_data_task` (17-20 min per batch).

The `background` workload is:

1. full crawls
2. periodic incremental refreshes (35-78 min per realm)
3. landing warmers
4. startup warmers
5. bulk cache loads
6. enrichment batches (17-20 min, self-chaining)

At `c=2` concurrency, warmer/crawl contention can cause starvation during heavy crawl windows.

**Operational note (2026-04-12):** Long-running tasks on `background` are vulnerable to RabbitMQ `consumer_timeout` (default 30 min) when combined with `CELERY_TASK_ACKS_LATE = True`. This was the root cause of a zombie worker incident. The deploy script now disables `consumer_timeout` via `advanced.config` and runs a consumer watchdog timer. See `runbook-incident-celery-zombie-worker-2026-04-12.md`.

### Finding 4: local Docker queue topology was not matching production

Production deploy scripts create dedicated `default`, `hydration`, and `background` workers.

Before this tranche, local `docker-compose.yml` started only:

1. one worker bound to `default`
2. one worker bound to `background`

That meant local queue validation was weaker than it should have been.

This is now corrected: local Docker includes a dedicated `hydration` worker.

## Assessment

### Is the current strategy appropriate?

Yes, for the most important current safety property:

1. long-running crawler and warmer work does not share a queue with user-facing request refreshes in production

That is the core requirement, and the production design satisfies it.

### Is the current strategy optimal?

No.

The biggest remaining issue is not crawler-vs-user contention. That part is mostly handled.

The biggest remaining issue is **residual user-triggered task contention within the interactive side of the system**, not crawler isolation.

The current architecture is best described as:

1. **good separation between maintenance work and interactive work**
2. **insufficient separation inside interactive work itself**

## Recommended Direction

Prefer queue separation before broker priority tuning.

RabbitMQ priorities can help, but clearer routing boundaries are easier to reason about operationally and easier to validate in code review.

### Recommended target model

#### Option A: keep 3 queues, refine routing

This is the smallest safe improvement.

1. keep `background` for crawls, warmers, startup warm, enrichment, and periodic maintenance
2. keep `default` only for lightweight request-adjacent entity refreshes
3. expand `hydration` to include all heavy user-triggered upstream/data refreshes

Likely candidates to move from `default` to `hydration`:

1. `update_battle_data_task`
2. `update_player_clan_battle_data_task`
3. `update_clan_battle_summary_task`
4. possibly `update_clan_members_task`

This is the best near-term change if the goal is better user-facing freshness without materially increasing system complexity.

#### Option B: move to 4 queues

This is the cleaner long-term shape if maintenance contention becomes visible.

1. `default`: lightweight request-adjacent tasks
2. `hydration`: heavy user-triggered refreshes
3. `background-maintenance`: landing warms, hot caches, bulk loads, startup warm
4. `background-crawl`: full crawls, enrichment, incremental refreshes

This makes the distinction explicit between:

1. freshness maintenance
2. corpus expansion / crawler work

That split is only worth the extra operational complexity if background backlog is measurably hurting freshness work.

## Recommended Next Steps

1. Measure whether `default` now stays responsive during clan/player load bursts after moving battle, clan-member, clan-battle, and summary refreshes to `hydration`.
2. Keep `background` as a single lane unless there is evidence that crawls are delaying warmers in a way users can feel.
3. If warm-latency during crawl windows becomes a real issue, split `background` into crawler and maintenance queues instead of relying on broker priorities alone.
4. Leave Redis lock, dedupe, watchdog, and cooldown mechanics in place; those are part of the reason the current system already behaves reasonably under load.

## Validation

Validated in this tranche:

1. focused queue-routing tests pass: `warships/tests/test_task_routing.py`
2. updated Django files show no editor errors
3. updated `docker-compose.yml` validates with `docker compose config`
4. canonical queue documentation in `CLAUDE.md` was updated to reflect the new routing model

## Decision Summary

If the question is whether the current queue strategy is reckless or fundamentally broken, the answer is no.

If the question is whether the current queue strategy is the optimal final arrangement for the task mix battlestats now has, the answer is also no.

The highest-value refinement is:

1. keep the crawler/background isolation model
2. tighten the interactive routing model
3. avoid adding more queues unless measured contention justifies it
