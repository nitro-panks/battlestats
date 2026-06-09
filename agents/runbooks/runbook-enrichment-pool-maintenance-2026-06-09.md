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

Both fixes (`reclassify_enrichment_status`, `retry_empty_enrichments`) existed but were
**manual, unscheduled** one-shots, so the parked sets silently re-grew every clan crawl.
See `agents/work-items/player-enrichment-map-2026-06-08.md` §11–§12 and the
`project_enrichment_misses_elite_empty_falseneg` memory.

## What is automated (and what is NOT)

The two fixes have **very different prod cost**, so only the cheap one is on a cron:

| Fix | Cost (prod, 2026-06-09) | Disposition |
|-----|-------------------------|-------------|
| `retry_empty_enrichments` | index-backed on `enrichment_status`; touches only the small `empty` set (~500 rows) — sub-second | **Scheduled daily** |
| `reclassify_enrichment_status` (full catalog) | full per-realm scan: **na 879s / eu 639s / asia 668s ≈ 36 min total**, ~230K-row one-time change-set | **Supervised manual op** (not scheduled) |

### Scheduled: `enrichment_pool_maintenance_task`

- **Task** `warships.tasks.enrichment_pool_maintenance_task` (queue `background`,
  single-flight lock). Runs `retry_empty_enrichments --apply --retry-after-days N`.
- **DB-only** (no WG calls) and **index-backed**, so it is cheap and **crawl-safe** — it
  never defers, unlike enrichment's WG-fetch arm. It only relabels rows; the self-chaining
  `enrich_player_data_task` does the actual WG fetching on its next crawl-free window.
- **Schedule** `enrichment-pool-maintenance` (signals.py) — daily crontab **08:17 UTC**.
  `enabled` follows the kill switch.

### NOT scheduled: full `reclassify_enrichment_status`

Prod sizing (above) showed the full-catalog reclassify is ~10–15 min/realm on the 1-vCPU
managed PG, and there is **no scheduling trough** — the clan crawl is quasi-permanent
(~14-day passes striped across realms), so an unattended daily 36-min scan would stack
heavy load on the DB indefinitely. The change-set it produces (~230K rows: ~71K
`→enriched` benign corrections, ~47K `→pending` newly-eligible rescues) is a **one-time
accumulated backlog** from reclassify never having run — *not* daily drift. So:

- **One-time backlog clear** — run it once, deliberately, under observation (pause or
  partition the crawl, watch `system_load15` via the DO Prometheus endpoint — see
  `reference_do_db_cpu_metrics_endpoint`). Same playbook as the 2026-06-09 empty drain.
- **Recurring `skipped_*` drift** stays manual until an **incremental redesign**: filter
  reclassify to recently-fetched rows (`last_fetch` within ~25h — only those can have
  drifted). This is the durable automation path, but it **requires a `last_fetch` index**
  (currently unindexed → the filter would still seqscan). Index build + `--recent-hours`
  flag is the scoped follow-up that would let the drift rescue join the daily cron safely.

## The convergence guard (why scheduling `retry_empty` is safe)

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

`--retry-after-days 0` (the command default) disables the cooldown and preserves the
original one-shot behavior for manual drains.

> **Observed 2026-06-09:** immediately after the prior day's empty drain, the daily task's
> re-queue set is **0** — the cooldown correctly excludes rows re-attempted <14d ago. New
> empties become candidates only as they age past the cooldown or appear fresh. This is the
> guard working, not a bug.

## Env knobs

| Var | Default | Meaning |
|-----|---------|---------|
| `ENRICHMENT_POOL_MAINTENANCE_ENABLED` | `1` | Master kill switch (task no-ops at 0; schedule registered disabled). |
| `ENRICHMENT_EMPTY_RETRY_AFTER_DAYS` | `14` | Cooldown — re-fetch a stuck `empty` at most once per N days. |
| `ENRICH_MIN_PVP_BATTLES` / `ENRICH_MIN_WR` | `500` / `48.0` | Shared with the crawler — define eligibility; reclassify + retry read the same vars. |

## Operate

```bash
# Scheduled task (cheap) — fire by hand on the background worker:
cd server && python manage.py shell -c \
  "from warships.tasks import enrichment_pool_maintenance_task as t; print(t())"

# Empty re-queue, manually (dry-run sizes the backlog by WR band):
cd server && python manage.py retry_empty_enrichments                       # dry run
cd server && python manage.py retry_empty_enrichments --apply --retry-after-days 14

# --- SUPERVISED full reclassify (heavy: ~36 min, ~230K rows). Watch DB load. ---
cd server && python manage.py reclassify_enrichment_status --realm na --dry-run   # size first
cd server && python manage.py reclassify_enrichment_status --realm na             # apply, per realm
# repeat eu, asia. Prefer running with the crawl paused/partitioned.
```

Verify the schedule registered + enabled after deploy:

```bash
cd server && python manage.py shell -c \
  "from django_celery_beat.models import PeriodicTask as P; \
   r=P.objects.get(name='enrichment-pool-maintenance'); print(r.enabled, r.crontab)"
```

> **Deploy note:** the `post_migrate` handler re-creates this PeriodicTask with
> `enabled = ENRICHMENT_POOL_MAINTENANCE_ENABLED` on every migrate. A manual DB
> `enabled=False` toggle is therefore overwritten on the next deploy — gate via the env
> flag if you need it durably off.

## What this does NOT fix

- **Enrichment still hard-defers under multi-day clan crawls** (the WG-fetch arm and its
  feeder `incremental_player_refresh`). This maintenance keeps the *empty* slice of the pool
  correct during a crawl, but the freshly-surfaced backlog only drains in crawl-free
  windows. The durable throughput fix is the planned WG token-bucket limiter so enrichment +
  crawl coexist — tracked separately, a larger change.
- **`skipped_*` drift rescue is not yet automated** — see "NOT scheduled" above; needs the
  `last_fetch` index + incremental `--recent-hours` reclassify.
- **Source-side empty handling** — enrichment still records a transient `ships/stats`
  failure as `empty` at write time. The cooldown retry compensates; a cleaner fix would
  distinguish transient/privacy misses from genuine no-ship accounts and not park them.

## Related

- `agents/work-items/player-enrichment-map-2026-06-08.md` §11–§12 (the data-level map)
- `runbook-enrichment-crawler-2026-04-03.md` (the enrichment crawler itself)
- `runbook-na-crawl-restart-loop-starves-refresh-2026-06-05.md` (crawl-deferral context)
- Memory: `project_enrichment_misses_elite_empty_falseneg`, `reference_wg_ships_stats_no_bulk`,
  `reference_do_db_cpu_metrics_endpoint`, `db_disk_cpu_incident_2026-05-24`
</content>
