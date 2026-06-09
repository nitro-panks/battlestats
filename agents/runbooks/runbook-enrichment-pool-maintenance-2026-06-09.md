# Runbook — Enrichment Pool Maintenance (2026-06-09)

## Why

Enrichment's "99% caught up / N pending" only ever measured the **visible** `pending`
queue. Two classes of eligible players were parked invisibly and never re-evaluated:

- **`empty` false-negatives** — accounts that were **private at fetch time** (WG
  `ships/stats` returned no ships → `battles_json=[]` → `status='empty'`). They later go
  public, but `_candidates()` excludes `empty` (it wants `battles_json IS NULL`) **and** a
  `reclassify` pass keeps them `empty` (its rule is `battles_json==[] → empty`). So these
  were invisible to *both* the crawler and reclassify.
- **`skipped_*` drift** — players who were hidden / below the battle floor / inactive /
  below the WR floor at classification time, then changed (un-hid, crossed 500 battles,
  WR recovered, returned to activity). Their row state now passes the filter, but
  `enrichment_status` was never recomputed.

Both fixes existed but were **manual, unscheduled** one-shots, so the parked sets silently
re-grew every clan crawl. See `agents/work-items/player-enrichment-map-2026-06-08.md`
§11–§12 and the `project_enrichment_misses_elite_empty_falseneg` memory.

## What is automated — `enrichment_pool_maintenance_task`

A daily **DB-only** task (`enrichment-pool-maintenance`, **08:17 UTC**, queue `background`,
single-flight lock, kill switch `ENRICHMENT_POOL_MAINTENANCE_ENABLED`). It issues **no WG
calls**, so unlike enrichment's fetch arm it is **crawl-safe and never defers**. It only
relabels/re-queues rows; the self-chaining `enrich_player_data_task` does the actual WG
fetching on its next crawl-free window. Two idempotent passes:

### Pass 1 — `retry_empty_enrichments --apply --retry-after-days 14`

Re-surfaces `empty` false-negatives into `pending` (`status→pending`, `battles_json→NULL`).
Index-backed on `enrichment_status`, touches only the ~500-row `empty` set — sub-second.

### Pass 2 — per-realm `reclassify_enrichment_status --realm <r> --recent-hours 25`

**Incremental** drift rescue: recomputes `enrichment_status` only for rows fetched within
25h. Drift-relevant fields (`is_hidden` / `pvp_battles` / `pvp_ratio` /
`days_since_last_battle`) only change on a WG re-fetch, which bumps `last_fetch` — so the
recent set holds every row that could have **newly** drifted. Index-backed by
`player_last_fetch_idx` (migration 0067; the planner `BitmapAnd`s it with the realm/battles
index — verified via `EXPLAIN`). **~2.5 min/realm under crawl load** (~7–8 min total), vs
~36 min for the full catalog.

> **Why incremental is sufficient for the *active* population.** The daily active-snapshot
> engine (`save_player(core_only=True)`) refreshes `is_hidden`/`pvp_battles`/`pvp_ratio`/
> `days_since_last_battle` **and** bumps `last_fetch` on every active player each day
> (clan_crawl.py:203–238 — `core_only` only skips efficiency/achievements, not core stats).
> So an active threshold-crosser / un-hidden / WR-recovered player lands in the 25h window
> within a sweep cycle and gets reclassified to `pending` the next morning. Active drift
> self-clears; no full pass needed for it.

## What is NOT automated — the supervised full `reclassify`

Run the full `reclassify_enrichment_status` (no `--recent-hours`) **manually, under
observation** — it is ~10–15 min/realm (~36 min total) on the 1-vCPU PG. It is needed only
for the residue the incremental pass can't see:

- **The one-time pre-existing backlog** — rows last fetched >25h ago that accumulated while
  reclassify never ran. Prod-sized 2026-06-09 at **~230K rows** (na 879s / eu 639s / asia
  668s dry-run): ~71K `→enriched` benign corrections, ~47K `→pending` newly-eligible
  rescues. Clear it once (pause/partition the crawl, watch `system_load15` via the DO
  Prometheus endpoint — `reference_do_db_cpu_metrics_endpoint`). After that, the daily
  incremental keeps the active set current and only inactive residue accrues.
- **Pure-calendar inactivity crossings** — a player crossing the 365-day inactive line with
  no re-fetch never bumps `last_fetch`, so the incremental pass misses them. A periodic
  (e.g. monthly) supervised full pass mops these up.

## The convergence guard (why scheduling `retry_empty` is safe)

The one-shot `retry_empty_enrichments` re-queues **every** matching `empty` row. On a
schedule that would be an **unbounded re-fetch loop**: a *genuinely* empty account (no ships
/ still private / transient WG outage) re-empties on re-fetch → re-queued next run →
re-fetched → re-empties… forever, burning the scarce shared WG budget.

The guard is `--retry-after-days N` (default **14** via `ENRICHMENT_EMPTY_RETRY_AFTER_DAYS`).
Enrichment bumps `battles_updated_at` on every empty write (`enrich_player_data.py`
`_mark_empty`), so the cooldown re-queues only empties whose last attempt is **older than N
days, or never attempted**: a now-public account enriches and leaves the pool; a
genuinely-empty one is re-fetched at most once per N days. `--retry-after-days 0` (the
command default) disables the cooldown for manual one-shot drains.

> **Observed 2026-06-09:** right after the prior day's empty drain, the daily re-queue set
> is **0** — the cooldown correctly excludes rows re-attempted <14d ago. The guard working,
> not a bug.

## Env knobs

| Var | Default | Meaning |
|-----|---------|---------|
| `ENRICHMENT_POOL_MAINTENANCE_ENABLED` | `1` | Master kill switch (task no-ops at 0; schedule registered disabled). |
| `ENRICHMENT_EMPTY_RETRY_AFTER_DAYS` | `14` | Empty re-queue cooldown — re-fetch a stuck `empty` at most once per N days. |
| `ENRICHMENT_RECLASSIFY_RECENT_HOURS` | `25` | Incremental reclassify window (`last_fetch >= now - N h`). |
| `ENRICHMENT_POOL_MAINTENANCE_STATEMENT_TIMEOUT` | `120` | Per-statement Postgres `statement_timeout` (seconds) — blast-radius cap. |
| `ENRICH_MIN_PVP_BATTLES` / `ENRICH_MIN_WR` | `500` / `48.0` | Shared eligibility thresholds; reclassify + retry read the same vars. |

Task limits: `soft_time_limit=900s` / `time_limit=1080s`; single-flight lock 30 min (must
outlive the hard limit so a slow run can't lose its lock mid-pass).

## Operate

```bash
# Fire the daily task by hand (background worker):
cd server && python manage.py shell -c \
  "from warships.tasks import enrichment_pool_maintenance_task as t; print(t())"

# Incremental reclassify, one realm (what the task runs):
cd server && python manage.py reclassify_enrichment_status --realm na --recent-hours 25 --dry-run

# Empty re-queue, manually:
cd server && python manage.py retry_empty_enrichments --apply --retry-after-days 14

# --- SUPERVISED full reclassify (heavy: ~36 min, one-time backlog / calendar drift) ---
cd server && python manage.py reclassify_enrichment_status --realm na --dry-run   # size first
cd server && python manage.py reclassify_enrichment_status --realm na             # apply; repeat eu, asia
```

Verify the schedule + index after deploy:

```bash
cd server && python manage.py shell -c \
  "from django_celery_beat.models import PeriodicTask as P; \
   r=P.objects.get(name='enrichment-pool-maintenance'); print(r.enabled, r.crontab)"
# index: SELECT indexname FROM pg_indexes WHERE indexname='player_last_fetch_idx';
```

> **Deploy note:** the `post_migrate` handler re-creates the PeriodicTask with
> `enabled = ENRICHMENT_POOL_MAINTENANCE_ENABLED` on every migrate — a manual DB
> `enabled=False` toggle is overwritten on the next deploy; gate via the env flag for a
> durable off.

## What this does NOT fix

- **Enrichment still hard-defers under multi-day clan crawls** (the WG-fetch arm + its
  feeder `incremental_player_refresh`). This maintenance keeps the pool *correct* during a
  crawl, but the freshly-surfaced backlog only drains in crawl-free windows. The durable
  throughput fix is the planned WG token-bucket limiter so enrichment + crawl coexist — a
  larger, separate change.
- **The one-time ~230K backlog + calendar-drift residue** — supervised full reclassify (above).
- **Source-side empty handling** — enrichment still records a transient `ships/stats`
  failure as `empty` at write time; the cooldown retry compensates rather than preventing it.

## Related

- `agents/work-items/player-enrichment-map-2026-06-08.md` §11–§12 (the data-level map)
- `runbook-enrichment-crawler-2026-04-03.md`, `runbook-daily-active-snapshots-2026-06-09.md`
- `runbook-na-crawl-restart-loop-starves-refresh-2026-06-05.md` (crawl-deferral context)
- Memory: `project_enrichment_misses_elite_empty_falseneg`, `project_coverage_ceiling_daily_active`,
  `reference_wg_ships_stats_no_bulk`, `reference_do_db_cpu_metrics_endpoint`
</content>
