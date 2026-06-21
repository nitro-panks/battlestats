# Runbook: Give Player-Visit Core Refresh Its Own Lane

**Created**: 2026-06-17
**Status**: SHIPPED — `update_player_data_task` + `update_clan_data_task` are routed to `hydration` in `CELERY_TASK_ROUTES` (`server/battlestats/settings.py`), completing the interactive-lane consolidation. The "Question"/"Diagnosis" below describe the pre-fix `default`-fallthrough state for context; the observation floor has also since moved to its own dedicated `floor` worker, so it no longer camps a `default` slot either.
**Depends on**: `runbook-celery-queue-strategy.md`, `runbook-player-refresh-latency-2026-06-10.md`, `runbook-backend-droplet-deploy.md`
**Supersedes/advances**: the "Option A — keep 3 queues, refine routing" recommendation in `runbook-celery-queue-strategy.md` (Findings 1 + Recommended Direction)

## Question

When a user loads a player (or clan) profile, the on-visit refresh sometimes feels like it "gets stuck behind things in the queue and lags until it clears." Is that real, and what is the smallest safe change that gives player-initiated refreshes a snappy, isolated lane?

## Short Answer

It is real, but narrow. The system already has a dedicated request-driven lane (`hydration`) and **most** on-visit refreshes route to it. The exception is the two most user-visible tasks:

- `update_player_data_task` — the core player-profile refresh (drives the "Updating…" pill and headline stats)
- `update_clan_data_task` — the core clan refresh

Both still fall through to the shared **`default`** queue (no explicit route → default). On `default` they share `-c 3` slots with bursty, multi-minute, non-interactive work — most importantly the observation-floor sweep `ensure_daily_battle_observations_task`, which runs its work **inline** (`call_command(...)`) and therefore camps a `default` slot for the full sweep, plus the clan-crawl dispatchers and a couple of warmers. When the floor is mid-sweep occupying slots, a user's core refresh waits for a free slot.

**Fix (this slice):** route `update_player_data_task` and `update_clan_data_task` to `hydration`, completing the consolidation that `runbook-celery-queue-strategy.md` already recommended. After it, *every* on-visit refresh shares one isolated, request-only lane, fully decoupled from the floor/crawl/warmers. Optionally bump `CELERY_HYDRATION_CONCURRENCY` from 3 → 5 (env-only, I/O-bound work).

**Caveat — not everything the user perceives is the queue.** A separate, already-diagnosed component of the perceived "~1 min" is a **frontend artifact**: the profile-wide pending pill polls every ~6s, so even a 2s backend refresh shows the pill for a poll cycle or two (see `project_player_page_loading_pill_diagnosis` memory / `runbook-player-refresh-latency-2026-06-10.md`). This slice addresses the *real backend contention* (intermittent, correlated with floor cycles), not the steady-state pill duration.

## Diagnosis (evidence)

### How a player-page visit fans out today

`server/warships/views.py` (player + clan detail paths) enqueues up to ~8 tasks. Their routing (`server/battlestats/settings.py:293` `CELERY_TASK_ROUTES`):

| Task | Queue today | Lane character |
|---|---|---|
| `update_battle_data_task` | `hydration` | request-only ✅ |
| `update_ranked_data_task` | `hydration` | request-only ✅ |
| `update_player_clan_battle_data_task` | `hydration` | request-only ✅ |
| `update_player_efficiency_data_task` | `hydration` | request-only ✅ |
| `update_clan_members_task` | `hydration` | request-only ✅ |
| `update_clan_battle_summary_task` | `hydration` | request-only ✅ |
| **`update_player_data_task`** | **`default`** | ⚠️ shared with floor/crawl/warmers |
| **`update_clan_data_task`** | **`default`** | ⚠️ shared with floor/crawl/warmers |

The two core refreshes have **no entry** in `CELERY_TASK_ROUTES`, so they fall through to `default`. This is the residual gap named in `runbook-celery-queue-strategy.md` (Finding 1, lines 173–186; the heavier siblings were already moved to `hydration` in the 2026-04-03 slice).

### What else lives on `default`, and why it blocks

`default` runs at `-c 3` with `--prefetch-multiplier=1` (`server/deploy/deploy_to_droplet.sh`, `server/docker-compose.yml`). Blocking is slot-based, not strict FIFO — a user refresh runs immediately *if a slot is free*, but waits if all 3 are occupied. The occupants that cause that:

1. **`ensure_daily_battle_observations_task` (the observation floor)** — `server/warships/tasks.py:1765`. No queue override in `CELERY_TASK_ROUTES`, no queue in its `PeriodicTask` registration (`server/warships/signals.py:809`), no queue in `CRAWL_TASK_OPTS` → **lands on `default`**. It executes `call_command("ensure_daily_battle_observations", limit=3000, …)` **inline** within the task body (`tasks.py:1870`), walking up to `BATTLE_OBSERVATION_FLOOR_LIMIT` (3000) active players with paced WG calls and **self-chaining** (`tasks.py:1967`). The result is a recurring multi-minute occupant of one `default` slot, striped per realm on a 6-hourly cadence and self-re-dispatching while a stale backlog remains.
2. **Clan-crawl dispatchers** — `ensure_crawl_all_clans_running_task` (every 5 min) and `dispatch_clan_crawl_task` are *deliberately* on `default` (lightweight, must not camp the single-slot `crawls` queue; see route comments at `settings.py:302–311`). Individually cheap, but they add to slot churn.
3. **A few warmers / nightly tasks** with no route also fall to `default` (e.g. `snapshot_ship_top_players_task`, `warm_realm_top_ships_task`, `materialize_landing_player_best_snapshots_task`, `warm_recently_viewed_players_task`).

So the user-visible core refresh competes for 3 slots against an inline floor sweep + crawl dispatchers + warmers. That is the intermittent "stuck behind the queue" stall. The charts (battles/ranked/CB) stay snappy precisely because they're already on `hydration`; it's specifically the core stats that lag.

### Why moving the two tasks is safe

Every non-test caller of `update_player_data_task` and `update_clan_data_task` is the **request path** (`server/warships/views.py` and the `data.py` hydration dispatch at `data.py:4405`). There is **no bulk/batch loop** that fans these tasks out (confirmed by grep; a code comment at `tasks.py:707` notes `update_player_data_task` is dispatched only from the request path). Therefore moving them to `hydration` cannot drag batch load into the request lane — it only finishes the lane the `hydration` queue was built for.

## Implementation Steps

This is a backend-only change. Keep it a single small slice.

### Step 1 — Route the two core tasks to `hydration`

Edit `server/battlestats/settings.py`, in the `CELERY_TASK_ROUTES` dict (the `hydration` block, ~lines 325–331). Add:

```python
    # Core on-visit refreshes. These are dispatched ONLY from the request path
    # (views.py player/clan detail + data.py hydration dispatch); no bulk/batch
    # loop fans them out (see tasks.py:707). Routing them to `hydration` finishes
    # the interactive-lane consolidation started 2026-04-03 and removes the last
    # contention point with the inline observation-floor sweep + crawl dispatchers
    # + warmers that share the `default` lane. See
    # runbook-interactive-refresh-lane-2026-06-17.md.
    'warships.tasks.update_player_data_task': {'queue': 'hydration'},
    'warships.tasks.update_clan_data_task': {'queue': 'hydration'},
```

### Step 2 — Extend the routing-contract test

Edit `server/warships/tests/test_task_routing.py`,
`test_heavy_request_driven_refreshes_route_to_hydration`. Add to `expected_tasks`:

```python
        "warships.tasks.update_player_data_task",
        "warships.tasks.update_clan_data_task",
```

(Optionally add a one-line assertion/comment that these are the *core* on-visit refreshes, so the intent survives.)

### Step 3 (optional) — Raise hydration concurrency

`hydration` now carries the full per-visit fan-out (~8 tasks/visit, all I/O-bound on WG/DB waits, not CPU). The binding backpressure is the global WG token-bucket limiter and the 2-vCPU Postgres, **not** the app box's CPU. A modest bump helps overlap waiting:

- Production: change the existing `set_env_value CELERY_HYDRATION_CONCURRENCY 3` line in `server/deploy/deploy_to_droplet.sh` (currently line ~562) to `5`. Env knobs live in this deploy script, **not** in `.env.cloud` (see `project_hot_players_cap_cost_model` memory). The systemd unit consumes it as `-c "${CELERY_HYDRATION_CONCURRENCY:-3}"` (line ~668).
- This is reversible by unsetting the env / reverting to 3. Do **not** raise `default`/`background` in the same change — keep the variable count to one.

Decision: ship Steps 1–2 first (zero-infra, the actual fix). Treat Step 3 as a follow-up tuning knob to apply only if hydration depth is observed to back up under concurrent visitors.

### Step 4 — Do NOT add a 5th worker / do NOT move the floor

Considered and rejected for this slice:
- **New dedicated `interactive` worker** — adds a 5th Celery process (~150–300 MB + a Django import) on a 2 vCPU / 8 GB box for marginal gain over reusing `hydration`. Reconsider only if `hydration` itself becomes contended.
- **Move the floor to `background`** — it would then fight the enrichment backlog and snapshot engine; the floor is freshness-critical. Leaving it on a now-lighter `default` (after the two core tasks leave) is strictly better. (`background` carrying the heaviest sustained load is Finding 3 in the queue-strategy runbook.)

## QA / Validation

Run before committing the *implementation* (this runbook's commit only needs Doc QA below):

1. **Routing contract test (sqlite harness):**
   ```bash
   cd server
   DB_ENGINE=sqlite3 DJANGO_SECRET_KEY=test \
     python -m pytest warships/tests/test_task_routing.py --nomigrations -q
   ```
   Expect: `test_heavy_request_driven_refreshes_route_to_hydration` passes with the two new entries.
2. **Views still enqueue correctly (no caller assumed a queue):**
   ```bash
   DB_ENGINE=sqlite3 DJANGO_SECRET_KEY=test \
     python -m pytest warships/tests/test_views.py --nomigrations -q
   ```
   `views.py` calls `.delay()` (no `queue=` kwarg), so routing is resolved by `CELERY_TASK_ROUTES`; tests patch `update_*_task.delay` and assert dispatch, not queue — they remain green.
3. **Local Docker topology sanity:** `docker compose config` validates; the `hydration` worker already exists locally (`docker-compose.yml`). Confirm `update_player_data_task` lands on the hydration worker:
   ```bash
   docker compose up -d
   # tail the hydration worker; load a cold player profile via the client; confirm
   # "update_player_data_task ... received" appears in the hydration worker log,
   # not the default worker log.
   ```
4. **Release gate (before release):** run the lean gate; `patch` may skip it but routing + views are covered above.

### Post-deploy verification (production)

1. Deploy backend: `./server/deploy/deploy_to_droplet.sh battlestats.online`.
2. Confirm the running unit consumes `hydration` and (if Step 3) shows `-c 5`:
   ```bash
   ssh root@battlestats.online 'systemctl show battlestats-celery-hydration -p ExecStart | tr " " "\n" | grep -E "\-Q|\-c"'
   ```
3. Inspect queue depths during a floor window (when `default` is busiest) and confirm a fresh profile load refreshes promptly:
   ```bash
   ssh root@battlestats.online 'rabbitmqctl list_queues name messages consumers | grep -E "default|hydration"'
   curl -sS -D- -o /dev/null "https://battlestats.online/api/player/<known-stale-player>"
   # X-Player-Refresh-Pending should clear within a poll cycle or two even mid-floor.
   ```
4. Spot-check that the floor itself is unaffected: `./server/scripts/check_enrichment_crawler.sh` is enrichment-specific; for the floor use `/observation` (benchmark readout) over the next 1–2 snapshots to confirm coverage didn't regress (the floor still has its own `default` slot, now less crowded).

## Risks & Rollback

| Risk | Likelihood | Mitigation |
|---|---|---|
| `hydration` becomes the new contention point under many concurrent visitors | Low–Med | All hydration tasks are I/O-bound and ultimately serialized by the WG token-bucket limiter; Step 3 concurrency bump available; revert routing is one-line |
| A non-request caller secretly relied on `default` | Very Low | Grep-confirmed no such caller; routing change is transparent to `.delay()` callers |
| Floor coverage regresses | Very Low | Floor unchanged; `default` is *less* crowded after the move, so the floor sweep gets slots more readily |

**Rollback:** revert the two lines in `CELERY_TASK_ROUTES` (and the test additions), redeploy backend. No data migration, no state. If Step 3 was applied, unset `CELERY_HYDRATION_CONCURRENCY` (reverts to `-c 3`).

## Pre-commit / Doctrine reconciliation

Per `agents/knowledge/agentic-team-doctrine.json` and `CLAUDE.md`:

1. **Durable docs** — when the implementation lands, reconcile `runbook-celery-queue-strategy.md` (move `update_player_data_task` / `update_clan_data_task` out of its "still on `default`" list, lines 107–110; mark the Option-A residual as closed) and update the queue-routing description in `CLAUDE.md`'s Celery section if it enumerates lane membership.
2. **Test coverage** — Step 2 keeps the routing contract under test.
3. **Contract docs** — no API/payload change; `runbook-api-surface.md` untouched.
4. **Registry** — add this runbook to `agents/doc_registry.json` (done with this commit).

## References

- `runbook-celery-queue-strategy.md` — the lane doctrine; this runbook is its Option-A next slice
- `runbook-player-refresh-latency-2026-06-10.md` — latency tiers, frontend-pill component
- `server/battlestats/settings.py:293` — `CELERY_TASK_ROUTES`
- `server/warships/tasks.py:689` (`update_player_data_task`), `:647` (`update_clan_data_task`), `:1765` (`ensure_daily_battle_observations_task`)
- `server/warships/tests/test_task_routing.py` — routing contract
- `server/deploy/deploy_to_droplet.sh`, `server/docker-compose.yml` — worker `-Q`/`-c` config
