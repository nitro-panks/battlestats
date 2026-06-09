# Runbook — Enrichment Pool Maintenance (2026-06-09)

## Why

Enrichment's "99% caught up / N pending" only ever measured the **visible** `pending`
queue. Two classes of eligible players were parked invisibly and never re-evaluated:

- **`skipped_*` drift** — players who were hidden / below the battle floor / inactive /
  below the WR floor at classification time, then changed (un-hid, crossed 500 battles,
  WR recovered, returned to activity). Their row state now passes the filter, but
  `enrichment_status` was never recomputed, so the crawler never sees them.
- **`empty` false-negatives** — accounts that were **private at fetch time** (WG
  `ships/stats` returned no ships → `battles_json=[]` → `status='empty'`). They later go
  public, but `_candidates()` excludes `empty` (it wants `battles_json IS NULL`) **and** a
  `reclassify` pass keeps them `empty` (its rule is `battles_json==[] → empty`). So 85% of
  the elite misses were invisible to *both* the crawler and reclassify.

Both fixes (`reclassify_enrichment_status`, `retry_empty_enrichments`) existed but were
**manual, unscheduled** one-shots, so the parked sets silently re-grew every clan crawl.
See `agents/work-items/player-enrichment-map-2026-06-08.md` §11–§12 and the
`project_enrichment_misses_elite_empty_falseneg` memory.

## What this adds

A daily, **DB-only** maintenance pass that keeps the `pending` pool honest. It issues
**no Wargaming calls**, so unlike enrichment itself it is **crawl-safe and never defers** —
it runs to completion even mid-crawl. It only relabels rows / re-queues; the
self-chaining `enrich_player_data_task` does the actual WG fetching on its next crawl-free
window.

- **Task** `warships.tasks.enrichment_pool_maintenance_task` (queue `background`,
  single-flight lock). Two idempotent passes:
  1. `reclassify_enrichment_status --realm <r>` **per realm** (smaller per-realm
     transactions on the 1-vCPU managed PG) — recomputes `enrichment_status` from current
     row state, rescuing `skipped_*` drift back to `pending`.
  2. `retry_empty_enrichments --apply --retry-after-days N` — re-queues `empty`
     false-negatives (`status→pending`, `battles_json→NULL`) so they re-enter the
     candidate pool.
- **Schedule** `enrichment-pool-maintenance` (signals.py) — daily crontab **08:17 UTC**,
  one run, all realms handled inside the task. `enabled` follows the kill switch.
- Sets a Postgres session `statement_timeout` around the passes (RESET in `finally` so it
  can't leak to the next task on the worker's connection). No-op on the sqlite test
  harness (`connection.vendor` guard).

## The convergence guard (why scheduling `retry_empty` is now safe)

The one-shot `retry_empty_enrichments` re-queues **every** matching `empty` row. Scheduling
that as-is would be an **unbounded re-fetch loop**: a *genuinely* empty account (no ships /
still private / transient WG outage) re-empties on re-fetch → gets re-queued next run →
re-fetched → re-empties… forever, burning the scarce shared WG budget every day.

The guard is `--retry-after-days N` (default **14** via `ENRICHMENT_EMPTY_RETRY_AFTER_DAYS`).
When enrichment writes an `empty` row it bumps `battles_updated_at` to that attempt time
(`enrich_player_data.py` `_mark_empty`). The cooldown re-queues only empties whose last
attempt is **older than N days, or never attempted**:

- A player who went **public** within the window → re-fetched → enriches → leaves the pool
  (converges — the win).
- A **genuinely-empty** row → re-fetched at most **once per N days**, not every run. WG burn
  is bounded to `~empties / N` per day.

`--retry-after-days 0` (the default for the command) disables the cooldown and preserves the
original one-shot behavior for manual drains.

## Env knobs

| Var | Default | Meaning |
|-----|---------|---------|
| `ENRICHMENT_POOL_MAINTENANCE_ENABLED` | `1` | Master kill switch (task no-ops at 0; schedule registered disabled). |
| `ENRICHMENT_EMPTY_RETRY_AFTER_DAYS` | `14` | Cooldown — re-fetch a stuck `empty` at most once per N days. |
| `ENRICHMENT_POOL_MAINTENANCE_STATEMENT_TIMEOUT` | `180` | Per-pass Postgres `statement_timeout` (seconds). |
| `ENRICH_MIN_PVP_BATTLES` / `ENRICH_MIN_WR` | `500` / `48.0` | Shared with the crawler — define eligibility; reclassify + retry read the same vars. |

## Operate

```bash
# Manual run of either pass (dry-run defaults are safe):
cd server && python manage.py reclassify_enrichment_status --dry-run
cd server && python manage.py retry_empty_enrichments                      # dry run, sizes the backlog
cd server && python manage.py retry_empty_enrichments --apply --retry-after-days 14

# Fire the whole maintenance task by hand (background worker):
cd server && python manage.py shell -c \
  "from warships.tasks import enrichment_pool_maintenance_task as t; print(t())"

# Disable:
#   set ENRICHMENT_POOL_MAINTENANCE_ENABLED=0 in /etc/battlestats-server.env and restart
#   the background worker + beat, or toggle the PeriodicTask 'enrichment-pool-maintenance'.
```

Verify the schedule registered after deploy:

```bash
cd server && python manage.py shell -c \
  "from django_celery_beat.models import PeriodicTask as P; \
   r=P.objects.get(name='enrichment-pool-maintenance'); print(r.enabled, r.crontab)"
```

## What this does NOT fix

- **Enrichment still hard-defers under multi-day clan crawls** (the WG-fetch arm and its
  feeder `incremental_player_refresh`). This maintenance keeps the *pool* correct during a
  crawl, but the freshly-surfaced backlog only drains in crawl-free windows. The durable
  throughput fix is the planned WG token-bucket limiter so enrichment + crawl coexist —
  tracked separately, a larger change.
- **Source-side empty handling** — enrichment still records a transient `ships/stats`
  failure as `empty` at write time. The cooldown retry compensates; a cleaner fix would
  distinguish transient/privacy misses from genuine no-ship accounts and not park them.

## Related

- `agents/work-items/player-enrichment-map-2026-06-08.md` §11–§12 (the data-level map)
- `runbook-enrichment-crawler-2026-04-03.md` (the enrichment crawler itself)
- `runbook-na-crawl-restart-loop-starves-refresh-2026-06-05.md` (crawl-deferral context)
- Memory: `project_enrichment_misses_elite_empty_falseneg`, `reference_wg_ships_stats_no_bulk`
</content>
</invoke>
