# Runbook: DB CPU / queue remediation + follow-ups (2026-05-25)

_Created: 2026-05-25_
_Context: Remediation day for the managed-Postgres CPU-saturation incident diagnosed in `runbook-db-cpu-saturation-2026-05-24.md`. This runbook records what was shipped + verified on 2026-05-25 (CPU cache fix, background-queue flood fixes, the chronic-CI fix that gated them, a ranked backfill, the crawl-watchdog routing fix) plus an eventing-health sweep._
_Status: **DONE.** All fixes deployed to prod and merged to `main` (PRs #8, #9, #10); `main` CI green again. Open follow-ups are non-urgent (see [Follow-ups](#follow-ups))._

## Purpose

Single record of the 2026-05-25 remediation so a future agent can see what changed, why, and how it was verified — without reconstructing it from three PRs and a long chat. The original diagnosis (alert data, root-cause hunt) lives in `runbook-db-cpu-saturation-2026-05-24.md`.

> **Note on the diagnosis runbook:** the detailed 2026-05-24 edits (CPU-axis findings, VACUUM FULL, daily-job enablement, fix direction) are committed on the **unpushed** branch `chore/battle-observation-retention-prune-2026-05-24` (commit `817a7d2`). The copy on `main` is the original pre-remediation version. Push/PR that branch to land those edits on `main`.

## Background

The cluster (`db-postgresql-nyc3-11231`, NYC3) had two distinct problems, often conflated:

- **Disk axis** — `BattleObservation` capture is append-only JSON with no retention; the table reached ~22 GB (almost all TOAST) and the cluster hit read-only/disk-full on 2026-05-24.
- **CPU axis** — continuous CPU-high alerts since 2026-05-01. Confirmed via `pg_stat_statements` to be `score_best_clans()` recomputed ~2,180×/day, **not** the observation writes. Two separate root causes.

## Remediation shipped (2026-05-25)

### 1. Disk axis — compaction job enabled + one-time reclaim
- **Daily compaction Beat job enabled:** `BATTLE_OBSERVATION_COMPACT_ENABLED=1` on the droplet; `prune-battle-observations` runs 12:30 UTC daily (NULLs stale `ships_stats_json`/`ranked_ships_stats_json`, keeps rows — `BattleEvent` FKs cascade).
- **One-time `VACUUM FULL warships_battleobservation`:** 9m 10s, **22 GB → 7.0 GB**, `defaultdb` **37 GB → 22 GB** (~15 GB returned to OS). Chosen over `pg_repack` because live heap was only 416 MB (tiny live data → short lock). **Note: DO managed Postgres cannot shrink storage (scale-up only)** — this is OS-headroom hygiene, not a path to downsizing the 60 GB volume.

### 2. CPU axis — cache `score_best_clans()` (PR #8, `8f6a5a9`)
- **Root cause:** `_build_best_landing_clans()` recomputed `score_best_clans()` live on every landing Best-mode cache miss (`landing.py`), with no single-flight lock, amplified by `LANDING_CLANS_DIRTY_KEY` thrashing (every clan write invalidated it). Its 3 aggregate queries scan the 11 GB `warships_player` + `playerexplorersummary` (~14–18.5s each), ~170 CPU-hours over a 5.7-day `pg_stat_statements` window. `EXPLAIN` showed they're already index-driven → the fix is **frequency reduction, not an index**.
- **Fix:** read-through cache of the full scored ranking keyed on `(realm, sort)` (independent of `limit` — it only slices the tail), TTL `SCORE_BEST_CLANS_CACHE_TTL` (default 3h). **Intentionally NOT wired to the dirty-key invalidation** (that was the storm).
- **Prod proof:** `score_best_clans(na, overall)` twice = **31.03 s cold → 0.002 s cached**. Landing warm durations dropped **346–423 s → 5–15 s**; concurrent scoring queries 4+ → ~1.

### 3. Background-queue flood (PR #9, `261c542`, release `20260524202731`)
The `background` queue had a ~1,650-message backlog (accumulated while the worker was pegged on the slow warms). Composition: **~48% `dispatch_tracked_player_polls_task`** (battle-tracking PoC, no-op on prod but beat-dispatched every 60s) + **~29% `warm_landing_page_content_task`** (dirty-key thrash).
- **Fix A:** gate the poll dispatcher's beat `enabled` on `BATTLE_TRACKING_PLAYER_NAMES` being set (`signals.py`).
- **Fix B:** debounce `_queue_landing_republish` with a per-realm cooldown key (`LANDING_REPUBLISH_COOLDOWN_SECONDS`, default 120s) that the warm task does **not** clear (`landing.py`). Coalesces the invalidation→republish→warm loop.
- **Ops:** purged the stale 1,650 backlog (`rabbitmqctl purge_queue background`; `acks_late` preserved in-flight).

### 4. Chronic CI failure fix (shipped in PR #8's squash)
`main` CI had been **red since ~2026-05-03**. Root cause: `warm_landing_page_content` materializes `Landing*Snapshot` rows via a `ThreadPoolExecutor`; those threads commit on separate DB connections **outside** the `TestCase` transaction, so the rows leak across tests and the recent-players surface serves a stale Tier-2 snapshot — 4 tests assert empty. **Only reproduces against real PG+Redis in the full suite** (passes on SQLite / in isolation), which is why the SQLite-based local release gate never caught it.
- **Fix:** `_clear_landing_snapshots()` helper, scoped to the affected read-tests/setups that do **NOT** spawn the threaded warm — the DELETE holds an uncommitted row lock that deadlocks a concurrent threaded warm in the same test (a hang I hit and diagnosed).
- **Reproduce locally:** PG15 + Redis containers, run the 4 release-gate files together, real DB engine (NOT `--nomigrations`, NOT SQLite).

### 5. Crawl-watchdog queue routing (PR #10, `7e2efb6`, release `20260525104439`)
`ensure_crawl_all_clans_running_task` was routed to the single-slot `crawls` queue (`CELERY_TASK_ROUTES`), shared with the days-long `crawl_all_clans_task`. While a crawl camps the `-c1` worker, the every-5-min watchdog piled up unconsumed (~269 deep) **and could never run to detect a zombie crawl holding that slot** — defeating its purpose.
- **Fix:** route the watchdog to `default` (already consumed by `-c3`). It self-gates when the crawl is healthy and can still clear a stale lock + restart. The literal "gate enqueue when lock held" was rejected — it would block zombie detection.
- Also fixed a silently-stale `test_task_routing.py` test (asserted crawl tasks → `background`; they're → `crawls` since the 2026-04-30 carve-out; not in the CI gate so the drift went unnoticed).

### 6. Ranked-data backfill — top-1500 active NA
`incremental_ranked_data --realm na --limit 1500 --known-limit 1500 --discovery-limit 300` with an isolated state file (`/tmp/ranked_top1500_na_state.json`, `--reset-state`, nohup'd). **Result: 1500/1500 succeeded, 0 errors.**

## Validation

- **`main` CI green** on all three merge commits (`8f6a5a9` #8, `261c542` #9, `7e2efb6` #10) — first green `main` in weeks.
- **CPU:** `score_best_clans` 31 s → 0.002 s cached; warm durations 5–15 s; ~1 scoring query active (was 4+ concurrent).
- **Queues (post-deploy):** `background` `ready=0`, poll task `enabled=False` with 0 dispatches; `crawls` draining (279 → 209) with new watchdog ticks landing on `default`.
- **Ranked:** 1500/1500 NA players refreshed, 0 errors.
- **Branches:** merged `perf/best-clans-scoring-cache-2026-05-24` and `chore/queue-backlog-hygiene-2026-05-24` deleted (remote + local).

## Eventing-health sweep (2026-05-25, late)

All workers + beat active, consumer watchdog firing, beat dispatching; `hydration`/`default` clean; `crawls` draining.

**Investigated anomaly — benign:** the `background` queue showed a high, slowly-climbing `messages_unacknowledged` (47 → 82) with `ready=0`. Diagnosed as **enrichment ETA-deferrals, not stuck messages**: `enrich_player_data_task` defers while a clan crawl runs (shared WG rate limit) and re-enqueues with `apply_async(countdown=300)`; Celery holds those future-ETA messages unacked until they fire. The 15-min kickstart spawns extra deferral chains (can't tell deferring from running), so they accumulate, then **collapse when the crawl finishes**. Worker pings OK, no OOM, no crashes. A restart flushes but doesn't durably fix (re-accumulates while the crawl runs) and isn't needed. **Decision: left as-is** (self-healing, low impact). Captured in auto-memory `background-unacked-enrichment-deferral` so it isn't re-investigated.

## Follow-ups

- **`warships_player` (~11 GB, 8.4 GB TOAST, ~80k dead tuples)** — remaining disk-axis compaction target. Investigate which JSON columns bloat (`battles_json`/`ranked_json`/`efficiency_json`) before acting; `VACUUM FULL` only helps if live data is also small.
- **Land the 2026-05-24 diagnosis-runbook edits** stranded on `chore/battle-observation-retention-prune-2026-05-24` (`817a7d2`).
- **Watch DO CPU-alert volume over ~24h** — the true confirmation the CPU fix held.
- **Optional (low priority):** dedup enrichment deferral chains so they don't slowly accumulate during multi-day crawls (self-clears today, so deferred).

## Related runbooks
- `runbook-db-cpu-saturation-2026-05-24.md` — original diagnosis + disk-axis remediation tool.
- `runbook-clan-crawl-blocker-2026-04-30.md` — the `crawls` queue carve-out this watchdog fix builds on.
- `runbook-incident-celery-zombie-worker-2026-04-12.md` — prior eventing incident.
