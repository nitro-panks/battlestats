# Runbook: Data Lifecycle Architecture

**Created:** 2026-06-21 · **Status:** active · **Lifecycle:** evergreen · **Owner:** platform

The single consolidated reference for how Battlestats **ingests → stores → evicts** persistent
data, what is bloating the managed Postgres, and the retention policy for every table. Prior docs are
incident- or feature-scoped (growth analysis, archive mechanism, CPU incident); this is the policy
baseline they implement. Re-verify the live numbers with the queries in **§7** before acting — they
drift within days.

---

## 1. The binding constraint & runway

The managed Postgres cluster (`db-postgresql-nyc3-11231`, **db-s-2vcpu-4gb**, PG 18) is the wall,
not the app droplet. **Do not read the droplet's `/dev/vda1` (87 GB, 33%) as headroom** — that is the
app server; the DB is a separate managed disk.

| Metric | Value (2026-06-21) | Source |
|---|---|---|
| Disk ceiling | **60 GiB** (`storage_size_mib=61440`), **autoscale OFF** | `doctl databases get <id>` |
| Disk **used** | **38.3 GB (62.5%)**, 22.9 GB free | DO Prometheus `:9273` `disk_used` |
| `pg_database_size` | 31 GB | `SELECT pg_database_size(...)` |
| Health | load15 1.53, iowait 3.4%, CPU idle ~59% | DO `:9273` — coping, not stressed |

The ~7 GB gap between `disk_used` (38.3 GB) and `pg_database_size` (31 GB) is WAL/temp/overhead — so
the real utilization is **38.3/60, not 31/60**. Measure the disk, not the DB size.

### Runway — separate one-time from steady-state (the analytical core)

The recent jump (23 GB on 2026-06-15 → 31 GB now) is **mostly one-time, not slope**. The 6-15 "23 GB"
was measured right after a VACUUM FULL, so the delta starts from a compacted floor and is dominated by:

- **~1.4 GB reclaimable bloat** on `playerexplorersummary` (see §4) — recovered, not grown.
- **~3.3 GB enrichment `battles_json` backfill** (`player` TOAST 7.7 → 11 GB) — **finite**, saturates when the eligible pool is enriched.
- **~1.7 GB `battleobservation` TOAST** — **coverage-bound**, decelerating as the observation floor saturates the active-player set.

The genuine **steady-state slope ≈ ~105 MB/day** (isolated in the 6-15 growth runbook), now partly
offset by the active battle-history archive timer. With 22.9 GB free and the backfills saturating,
the honest runway is **~6–9 months**, not the alarmist "3 weeks" a naive 6-day extrapolation implies.
The reclaim + retention work in this runbook buys further headroom.

---

## 2. Per-table lifecycle matrix

Ingest → store → evict for every persistent model. "TOAST" = out-of-line large-column storage.
Sizes are live 2026-06-21.

| Table | Total / TOAST | Rows | Ingest path | Retention / eviction | Gate |
|---|---|---|---|---|---|
| `warships_player` | 15 GB / **11 GB** | 1.07M | enrichment + floor + per-view refresh write `battles_json` (raw WG `ships/stats`) | **bounded** (1/realm-player); `battles_json` prunable on >180d-inactive | `prune_inactive_player_battles_json` (now timer-gated, §5) |
| `warships_battleobservation` | 9.3 GB / **8.5 GB** | 2.07M | floor / hot / on-render capture stores raw `ships_stats_json` + `ranked_ships_stats_json` | **keep=1 compaction ON** (NULL old payloads); rows persist (CASCADE FK) | `BATTLE_OBSERVATION_COMPACT_ENABLED=1 KEEP=1` |
| `warships_playerexplorersummary` | 2.3 GB / 0 | 0.73M | nightly efficiency-rank rewrite (~174K rows/day) | **1/player, no eviction**; **bloated** — see §4 | autovacuum+fillfactor (migration 0076) |
| `warships_playerdailyshipstats` | 1.6 GB / 0 | 3.66M | daily rollup of BattleEvents | **archive+delete >32d** | `BATTLE_HISTORY_ARCHIVE_ENABLED=1` |
| `warships_battleevent` | 1.3 GB / 0 | 3.73M | per-battle deltas from observation pairs | **archive+delete >32d** | `BATTLE_HISTORY_ARCHIVE_ENABLED=1` |
| `warships_playerachievementstat` | 1.2 GB / 0 | 4.25M | denormalized mirror of `achievements_json` | **no eviction** (read only by merge ops); idx>heap | — (future) |
| `warships_snapshot` | 739 MB / 0 | 3.65M | `snapshot_active_players_task` + per-view, ~190K rows/day | **NEW: downsample >90d to 1/player/ISO-week** (this assessment) | `SNAPSHOT_DOWNSAMPLE_ENABLED` (default off) |
| `warships_clan` | 70 MB | 0.12M | clan refresh / crawl | bounded (saturates) | — |
| `warships_shiptopplayersnapshot` | 18 MB | 51K | nightly ship leaderboard rebuild | **prune >`SHIP_BADGE_RETENTION_DAYS`** | `SHIP_BADGE_SNAPSHOT_ENABLED=1` |
| `warships_entityvisitevent` | 11 MB | 14.6K | per-page-view first-party/GA4 events | **NEW: monthly cleanup >180d** (this assessment) | `ENTITY_VISIT_CLEANUP_ENABLED` (default off) |
| `warships_entityvisitdaily` | 3 MB | 9K | rollup of visit events | rebuild-on-demand (bounded) | — |
| `warships_deletedaccount` | 3 MB | 31K | GDPR blocklist | never pruned (tiny) | — |
| `warships_hotplayer` | 1.3 MB | 2.4K | engagement queue maintenance | capped at `HOT_PLAYERS_MAX`; **queue disabled** | `HOT_PLAYERS_ENABLED=0` |
| `Ship`, `LandingPlayerBestSnapshot`, `PlayerActivityHourly`, `StreamerSubmission`, `MvPlayerDistributionStats` | <1 MB each | — | reference / materialized | fixed or rebuilt | — |

**Dropped 2026-06-15** (migration 0074, ~1.18 GB reclaimed): `PlayerWeekly/Monthly/YearlyShipStats`
rollups — all UI windows resolve to the daily layer.

---

## 3. TOAST anatomy — why two columns are 63% of the DB

`player.battles_json` (11 GB) + `battleobservation.ships_stats_json` (8.5 GB) = **~19.5 GB of TOAST**,
~63% of `pg_database_size`. Both store **raw Wargaming API payloads**, not derived data:

- **`player.battles_json`** — the raw `ships/stats` blob. **The frontend never reads it**
  (`PlayerSerializer.Meta.exclude` drops it; `/randoms` falls back to the derived `randoms_json`). It
  is an internal baseline that enrichment + floor + per-view refresh keep repopulating. Growth is
  **backfill-shaped** (more players get enriched) and saturates. Quick reclaim: prune the >180d-inactive
  tail (§5). Structural elimination is the biggest long-term win but the highest risk (§6).
- **`battleobservation.ships_stats_json`** — the same WG payload captured per observation, kept as the
  diff baseline for the BattleEvent pipeline. **keep=1 compaction is ON**: all but the latest payload
  per player is NULLed nightly. So live TOAST ≈ distinct-observed-players × ~21 KB — **coverage-bound,
  not unbounded**. Decelerating as the floor saturates the active set.

Neither is dead-tuple bloat; both are mostly live payload. The lever for #1 is *store less* (prune /
stop persisting); for #2 it is *already pulled* (keep=1).

---

## 4. Bloat anatomy — `playerexplorersummary` (the one quick win)

`pgstattuple` (2026-06-21): table 1601 MB, **tuple_percent 7.9%, dead 0.6%, free_percent 90.7%
(1385 MB free)**. Live tuple data is only ~120 MB (`sum(pg_column_size)`), yet the heap is 1527 MB —
**~1.4 GB is reclaimable physical bloat**, plus ~700 MB of index bloat (indexes 769 MB > heap-of-live).

**Mechanism:** the nightly efficiency-rank refresh rewrites ~174K rows/day. The changed columns
(`efficiency_rank_percentile`, `player_score`) are **indexed**, so updates can never be HOT — each
writes a new heap tuple + new index entries. Autovacuum reclaims the dead tuples to the free-space map
(dead% stays ~0.6%) but **never shrinks the file**: the high-water mark, inflated by an earlier
full-population pass, stays at 1.5 GB.

**Why this isn't fully self-healing, and the fix:**
- Migration **0076** adds the table to autovacuum tuning (it was omitted from 0073) and sets
  `fillfactor=90`. fillfactor leaves per-page headroom so a non-HOT new tuple more often lands in the
  same page — it **slows re-bloat** but **does not enable HOT** (the indexed columns still change), so
  it cannot stop it. This is a metadata-only ALTER; it does not reclaim the existing 1.4 GB.
- Reclaiming the existing bloat needs a **one-time `pg_repack`** (online, no long lock — verify it is
  in DO's allowed extensions first) or a **windowed `VACUUM FULL`** (ACCESS EXCLUSIVE — this is a
  UI-backing table, so a maintenance window). This is **gated** (§6).
- Re-bloat after a reclaim is **slow** (~174K rows/day churn on 0.73M rows, with 0076's tuning), so a
  one-shot reclaim + monitoring is sufficient; revisit a recurring repack only if it re-bloats past a
  threshold.

---

## 5. What this assessment shipped (safe code — applies on a deploy)

All landed on branch `data-lifecycle-assessment`; none mutate prod until August deploys, and the
destructive jobs stay **gated OFF** until an env flip.

1. **Migration `0076_explorersummary_storage_tuning.py`** — autovacuum reloptions + `fillfactor=90`
   on `warships_playerexplorersummary` (metadata-only; mirrors the 0073 pattern; sqlite no-op).
2. **`downsample_snapshots` command + `snapshot_retention.py`** — collapse Snapshot rows >90d to one
   per player per ISO-week (latest-date keeper; cumulative `battles`/`wins` trajectory preserved at
   week granularity). Safe because **no read path consumes Snapshot beyond ~29 days** (`data.py`'s
   28-day interval window + the 29-day `activity_json`); account-merge tolerates week-granularity old
   rows. Gated by `SNAPSHOT_DOWNSAMPLE_ENABLED` (default off); `--dry-run` always allowed. 5 unit tests.
3. **Three systemd timers** in `deploy_to_droplet.sh` (mirroring the archive timer), each **gated OFF**
   so they fire but no-op until flipped:
   - `battlestats-downsample-snapshots.timer` — weekly (Mon 04:30 UTC).
   - `battlestats-prune-battles-json.timer` — weekly (Sun 05:00 UTC), wrapper-gated on `PRUNE_BATTLES_JSON_ENABLED`.
   - `battlestats-cleanup-entity-visits.timer` — monthly (8th 05:30 UTC), wrapper-gated on `ENTITY_VISIT_CLEANUP_ENABLED`.

---

## 6. Gated next-steps (require August's go + a window — NOT done here)

1. **One-time `playerexplorersummary` reclaim (~2 GB):** `pg_repack` preferred (confirm DO allows the
   extension) over windowed `VACUUM FULL`. Reclaims ~1.4 GB heap + ~0.7 GB index. **Biggest immediate win.**
2. **Snapshot downsample first run:** `--dry-run` to confirm counts, then `SNAPSHOT_DOWNSAMPLE_ENABLED=1`
   for a real run; the weekly timer then maintains it. Destructive (deletes intra-week old rows).
3. **`battles_json` 11 GB structural fix:** the FE never reads it — long-term, stop persisting it or
   move to a per-ship baseline table. Biggest TOAST, **biggest risk** — design separately, do not attempt inline.
4. **Schedule the inactive-`battles_json` prune + entity-visit cleanup:** flip `PRUNE_BATTLES_JSON_ENABLED=1`
   / `ENTITY_VISIT_CLEANUP_ENABLED=1` once the first supervised `--dry-run` looks right.
5. **Disk alert / autoscale:** autoscale is OFF and the wall is hard at 60 GiB. Add an alert on
   `disk_used_percent > 80` (DO `:9273`) or enable storage autoscale as the outage backstop (cf. the
   2026-05-24 read-only incident).

---

## 7. Monitoring & re-verify recipe

**Disk (the real constraint)** — DO managed-PG Prometheus, recipe in memory `reference_do_db_cpu_metrics_endpoint`:
```bash
# creds: curl -s -H "Authorization: Bearer $DO_TOKEN" \
#   https://api.digitalocean.com/v2/databases/metrics/credentials
curl -su "$U:$P" "https://<metrics-host>:9273/metrics" \
  | grep -E '^disk_(used|total)\b|system_load15|cpu_usage_iowait'
# disk_used / disk_total -> % full; watch >80%.
```

**DB composition** (on the droplet, env-sourced psql):
```sql
SELECT c.relname,
       pg_size_pretty(pg_relation_size(c.oid))                      AS heap,
       pg_size_pretty(COALESCE(pg_relation_size(c.reltoastrelid),0))AS toast,
       pg_size_pretty(pg_total_relation_size(c.oid))                AS total,
       c.reltuples::bigint AS rows
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind='r'
ORDER BY pg_total_relation_size(c.oid) DESC LIMIT 15;
```

**Bloat check** (pgstattuple is installed):
```sql
SELECT round(free_percent::numeric,1) AS free_pct, pg_size_pretty(free_space) AS reclaimable
FROM pgstattuple('warships_playerexplorersummary');   -- free_pct >>10 => repack candidate
```

**Live kill-switch state** (2026-06-21): `BATTLE_OBSERVATION_COMPACT_ENABLED=1 KEEP=1`,
`BATTLE_HISTORY_ARCHIVE_ENABLED=1` (32d, timer 1st+15th), `SHIP_BADGE_SNAPSHOT_ENABLED=1`,
`FLOOR_REFRESH_BATTLES_JSON_ENABLED=0`, `HOT_PLAYERS_ENABLED=0`; new gates all default 0.

---

## 8. Related docs

- `runbook-db-growth-analysis-2026-06-15.md` — growth attribution + the ~105 MB/day slope isolation.
- `runbook-battle-history-archive-prune-2026-06-17.md` — the 32d archive+prune mechanism (implements the BattleEvent/PDSS rows of §2).
- `runbook-db-cpu-saturation-2026-05-24.md` — origin of keep=1 compaction + the disk-full read-only incident.
- `runbook-battle-history-data-operationalization-2026-06-16.md` — the keep-30d-and-operationalize decision for battle history.
- `runbook-daily-active-snapshots-2026-06-09.md` — the Snapshot engine this runbook adds a retention policy to.
- `runbook-db-size-optimization-2026-05-26.md` — superseded by this consolidation (candidate to archive).
