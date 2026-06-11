# Player Data Acquisition — Feed Timing Review & Optimization Plan

**Date:** 2026-06-08 · **Author role:** data architect / systems engineer
**Scope:** the scheduled processes that acquire player/clan data from the Wargaming
API and write it to Postgres. Derived/cache warmers are inventoried but are not the
optimization target.
**Source of truth:** the live `django_celery_beat.PeriodicTask` table on prod
(battlestats.online), 56 enabled tasks, captured 2026-06-08, cross-referenced with
`server/warships/signals.py`.

> **Correction (2026-06-11):** this analysis repeatedly calls the managed Postgres
> "1 vCPU" (§F1, §4 table, §scheduling). The DB was actually resized to **2 vCPU /
> 4 GB** on 2026-05-28 (before this doc was written). The thundering-herd /
> peak-overlap *findings* still hold qualitatively, but any sizing math anchored to a
> single core should be revisited against the 2-core headroom before being acted on.
> Current sizing: `agents/runbooks/ops-infra-resources.md`.

---

> **✅ Correction & reconciliation (2026-06-08, code review + implementation).**
> Every code-level claim below was verified against `signals.py` / `tasks.py` /
> `deploy_to_droplet.sh` before implementation; all findings held up. Three places
> where the original analysis was imprecise are corrected here, and the doc is
> reconciled with what actually shipped:
>
> 1. **F2 was a live bug, and the existing test did not catch it.**
>    `ObservationFloorRunsFourTimesADayTests` runs at the *code default* cadence
>    (`BATTLE_OBSERVATION_FLOOR_CYCLE_MINUTES` unset → 360 = 6h → 4×/day), **not**
>    prod's 180. The ASIA wrap-drop only manifests at 180min, so it was invisible
>    to CI. The fix adds a direct 180min helper test
>    (`RealmCrontabHelperTests.test_180min_floor_asia_wraps_to_8`).
> 2. **F5 is not a "trivial crontab change."** `REALM_CRAWL_CRON_HOURS` is shared
>    by the ship-top-player snapshot and the clan-tier-dist warmer (which the plan
>    keeps at 14:30), so the dict could not be edited directly. Shipped as a
>    crawl-only per-realm hour override (`CLAN_CRAWL_SCHEDULE_HOUR_ASIA`, default
>    22) that leaves the snapshot/tier-dist families untouched.
> 3. **Stale comment reconciled.** `signals.py` still described the floor as
>    "every 6h / fires 4× per day"; updated to the configurable-cadence + mod-1440
>    even-striping reality.
>
> **Shipped in this tranche:** Phase 1 (F1 minute lanes, F2 wrap fix, F5 ASIA crawl
> move, comment reconciliation) + F3 (PlayerActivityHourly + nightly aggregate) +
> Phase 2 self-chaining floor (flag-gated, default OFF). Phase 3 (`FLOOR_HOURS`
> 8→~5) remains a sequenced env step pending backlog drain.

---

## 1. Executive summary

Four issues limit how timely and evenly we acquire player data:

- **A thundering herd at 00:00 UTC (inside NA's peak):** 9 NA periodic tasks fire at
  minute=0, several at exactly 00:00:00. Same pattern (smaller) at every even hour.
  This is the proximate cause of the documented 03:00 / 23:00 DB-CPU peaks on the
  1-vCPU managed Postgres. **(F1)**
- **ASIA's clan crawl runs inside ASIA's activity peak.** Measured activity (§4.1)
  puts ASIA's peak at 12:00–15:00 UTC; `daily-clan-crawl-asia` started 15:00 UTC, so
  the crawl throttled ASIA's floor into coexist mode exactly when fresh battles were
  landing. NA's crawl (09:00) and EU's (03:00) sit correctly in their quiet windows. **(F5)**
- **ASIA's floor cadence was uneven.** After the 2026-06-08 6h→3h change, ASIA got 7
  cycles/day with one 6h gap (21:15→03:15 UTC) vs NA/EU's 8 evenly-spaced — the
  stripe math didn't wrap fire-times past midnight for the largest offset. **(F2)**
- **The schedule is uniformly striped, not aligned to the (now-measured) activity
  curve.** We have the input — `BattleObservation.last_battle_time` is a full
  datetime — so timeliness can be optimized; we just hadn't persisted/consumed it. **(F3/F4)**

**Staged plan:** (1) de-pile the herd, fix the ASIA wrap, move the ASIA crawl out of
peak (no-regret); (2) persist the activity curve and weight floor/refresh density to
each realm's measured post-peak window; (3) tune cadence vs the 8h staleness gate so
the higher frequency converts into fresher data.

## 2. Resource topology (the binding constraints)

Optimization is bounded by two shared resources, not by worker slots:

| Resource | Limit | Who draws on it |
|---|---|---|
| Wargaming API | ~10 req/s shared budget; `ships/stats` single-account (un-bulkable); `407 REQUEST_LIMIT_EXCEEDED` when exceeded | floor, player-refresh, ranked-refresh, clan-crawl, enrichment |
| Postgres | 1 vCPU, managed; prior disk/CPU saturation incidents | every task (reads + writes); analytical warmers use elevated `work_mem` |

Celery workers (default `-c3`, background `-c3`, hydration `-c5`, crawls `-c1`) mean
≤3 of a family run concurrently — but they all converge on the one DB and the one WG
budget. So the lever is **spreading work in time**, not adding workers.

## 3. Acquisition feeds — current state

All per-realm and striped via `REALM_INTERVAL_OFFSETS = {na:0, eu:1, asia:2}`.

| Feed | Task | Cadence (per realm) | Writes | Notes |
|---|---|---|---|---|
| Battle-history floor | `ensure_daily_battle_observations_task` | every 3h (`_CYCLE_MINUTES=180`) | `BattleObservation`→`BattleEvent` | LIMIT 7,500 normal / 3,000 coexist; `_HOURS=8` staleness gate |
| Player refresh | `incremental_player_refresh_task` | every 3h | Player core PvP stats | tiered staleness 12/24/72h; bounded LIMIT/cycle |
| Ranked refresh | `incremental_ranked_data_task` | every 2h | `Player.ranked_json` + `ranked_last_season_id` | feeds random-first routing |
| Clan crawl | `crawl_all_clans_task` | daily (EU 03:00, NA 09:00, **ASIA 22:00 after fix**) | Clan, Player (discovery), aggregates | core-only (R2) → ~6× cheaper; holds realm lock → floor coexists |
| Crawl watchdog | `ensure_crawl_all_clans_running_task` | every 5 min | — | restarts zombie crawls |
| Enrichment | `enrich_player_data_task` | kickstart every 15 min, self-chaining | efficiency/achievements/CB summary | the model for adaptive, capacity-filling cadence |

> ⚠ **Baseline caveat:** the floor's 3h cadence + LIMIT 7,500 cycle wall-time was
> still under validation at writing. If a cycle runs >stride the stride is unstable;
> Phase 3 timing must be re-checked against the confirmed cycle-time.

Freshness a player experiences today: battle history < `_HOURS` = 8h; core stats
12/24/72h tiered; ranked ≤ ~2h.

## 4. Activity curve & the current schedule

### 4.1 Measured activity curve (what should drive timing)

Distinct players bucketed by the UTC hour of their last battle
(`BattleObservation.last_battle_time`), 2-day window, ~10–13k players/realm:

| Realm | Peak window (UTC) | Busiest hour | Quiet window (UTC) | Shape |
|---|---|---|---|---|
| NA | 01:00–05:00 | 02:00 (10%) | ~09:00–17:00 | sharp single evening peak |
| EU | 14:00–15:00 & 20:00–21:00 | 15:00 (7%) | ~02:00–07:00 | broad/bimodal |
| ASIA | 12:00–15:00 | 14:00 (7%) | ~20:00–04:00 | broad afternoon/evening peak |

Capture should be densest in the ~4h after each peak; the crawl parked in each
realm's quiet window. NA crawl 09:00 ✓ and EU crawl 03:00 ✓ were well-placed; ASIA
crawl 15:00 ✗ landed in ASIA's peak (→ moved to 22:00).

### 4.2 The minute=0 stack (F1)

Every hour-multiple striped family anchored NA on minute 0 (only the floor passed a
`base_minute`). At 00:00 the stack was 9-deep (player-refresh + ranked-refresh +
landing + correlation + distribution + recent-players + recent-clans +
recently-viewed + hot-entity), at NA's peak, on the 1-vCPU DB.

## 5. Findings

- **F1 — minute=0 thundering herd → DB-CPU peaks.** Synchronized read/write spike at
  00:00/12:00. Pure scheduling fix.
- **F2 — ASIA floor cadence uneven (fixable).** `_realm_crontab_for_cycle` walked
  `start = base + offset*stride` forward with `while t < 1440` and did not wrap past
  midnight. At 180min with ASIA's offset (base_minute 75 + 120 = 195/03:15), the 8th
  fire landed at 24:15 and was dropped → 7 cycles, 6h hole 21:15→03:15.
- **F3 — activity signal exists but was unused.** Hour-resolution last-battle time is
  stored; nothing consumed a curve to shape the schedule. Persist as a rolling
  histogram.
- **F4 — schedule uniformly striped, not activity-weighted.**
- **F5 — ASIA clan crawl ran in ASIA's peak.** Move to its quiet window.
- **F6 — floor cadence (3h) outruns its staleness gate (8h).** The extra frequency
  drains the stale backlog faster but does not yet tighten steady-state freshness
  below 8h. To convert frequency into freshness, lower `_HOURS` toward the cadence.

## 6. Optimization plan (as shipped)

### Phase 1 — No-regret fixes (shipped)

- **De-pile the herd (F1):** each striped family now passes a distinct `base_minute`
  lane so no two hour-multiple families share NA minute 0. Lanes: hot-entity :07,
  player-refresh :05, floor :15, ranked-refresh :25, recent-clans :20,
  recent-players :35, correlation :45, distribution :50, landing :55,
  recently-viewed :02. Guarded by `MinuteLaneDePileTests`.
- **Fix the ASIA floor wrap (F2):** `_realm_crontab_for_cycle` now emits exactly
  `1440 // cycle_minutes` fires wrapping modulo 1440, so every realm gets its full
  cycle count and ASIA's overnight hole closes. Guarded by
  `test_180min_floor_asia_wraps_to_8`.
- **Move the ASIA crawl out of peak (F5):** crawl-only hour override
  `CLAN_CRAWL_SCHEDULE_HOUR_ASIA` (default 22), leaving NA/EU and the
  snapshot/tier-dist families untouched.
- **Reconcile the stale floor comment** in `signals.py`.

### F3 — Persist the activity curve (shipped)

- `PlayerActivityHourly` model (`realm`, `hour` 0-23, `player_count`, `window_days`,
  `computed_at`; unique `(realm, hour)`) — migration `0066_playeractivityhourly`.
- `aggregate_player_activity_curve_task`: nightly (04:00 UTC) rebuild from
  `BattleObservation.last_battle_time` over `ACTIVITY_CURVE_WINDOW_DAYS` (default 7),
  grouped by realm + `ExtractHour`, counting distinct players. Idempotent
  delete-and-replace per realm; single-flight lock. No-op unless
  `ACTIVITY_CURVE_ENABLED=1`. Tests in `test_activity_curve_aggregate.py`.

### Phase 2 — Self-chaining, peak-weighted floor (shipped, flag-gated OFF)

`ensure_daily_battle_observations_task` gains an opt-in self-chaining mode
(`BATTLE_OBSERVATION_FLOOR_SELF_CHAIN_ENABLED=1`, per-realm allowlist
`..._REALMS` csv for NA-first rollout), mirroring `enrich_player_data_task`: after a
sweep, when no crawl is competing and the remaining stale pool exceeds
`..._THRESHOLD` (default 500), re-dispatch via `apply_async(countdown=interval)`. The
interval is biased shorter during the realm's busy hours by reading
`PlayerActivityHourly` (no-op when the curve is empty). Bounded by the per-realm
single-flight lock + min-interval; the fixed Beat schedule stays as the backstop.

### Phase 3 — Convert frequency into freshness (pending)

After the post-R2 backlog drains and steady-state 180min/7500 cycle wall-time is
confirmed (`benchmark_observation_floor`), lower `BATTLE_OBSERVATION_FLOOR_HOURS`
8 → ~5 stepwise, re-benchmarking WG budget + cycle wall-time at each step. Pure env.

## 7. Sequencing & guardrails

Phase 1 + F3 + the (default-off) Phase 2 code land together; Phase 2 is enabled
NA-first behind its flag and benchmarked before EU/ASIA; Phase 3 is a sequenced env
bump gated on backlog drain. Every step: zero `407 REQUEST_LIMIT_EXCEEDED`; DB
`system_load15` within headroom; floor cycle wall-time inside its stride; instant
rollback via env knobs (all behavior changes are flag/env-gated).

## 8. Verification

```bash
cd server
python -m pytest warships/tests/test_periodic_schedule_topology.py \
  warships/tests/test_activity_curve_aggregate.py -q          # F1/F2/F3 guards
python manage.py makemigrations --check --dry-run warships    # 0066 present
```

After deploy: `observation-floor-asia` shows hour `0,3,6,9,12,15,18,21` minute `15`
(hole closed); `daily-clan-crawl-asia` shows hour `22`; DB `system_load15` falls at
the 00:00/12:00 boundaries; `check_enrichment_crawler.sh` shows no 407 spike.

## 9. New env knobs (default-safe)

| Env | Default | Effect |
|---|---|---|
| `CLAN_CRAWL_SCHEDULE_HOUR_ASIA` | 22 | ASIA clan-crawl UTC hour (out of peak) |
| `ACTIVITY_CURVE_ENABLED` | 0 (1 in prod deploy) | gates the nightly activity-curve aggregate |
| `ACTIVITY_CURVE_WINDOW_DAYS` | 7 | trailing window for the histogram |
| `ACTIVITY_CURVE_HOUR` / `_MINUTE` | 4 / 0 | aggregate schedule (UTC quiet window) |
| `BATTLE_OBSERVATION_FLOOR_SELF_CHAIN_ENABLED` | 0 | enable adaptive floor self-chaining |
| `BATTLE_OBSERVATION_FLOOR_SELF_CHAIN_REALMS` | "" (all) | per-realm allowlist for staged rollout |
| `BATTLE_OBSERVATION_FLOOR_SELF_CHAIN_THRESHOLD` | 500 | min remaining stale pool to keep chaining |
| `BATTLE_OBSERVATION_FLOOR_SELF_CHAIN_INTERVAL` | 120 | base re-dispatch countdown (s), curve-biased |

## 10. Sources

Prod config: `server/.env.cloud`, `server/deploy/deploy_to_droplet.sh`. Code:
`server/warships/{signals,tasks,models}.py`,
`management/commands/ensure_daily_battle_observations.py`. Live `PeriodicTask` table
+ activity sampled from prod 2026-06-08. Related:
`analysis-update-process-cost-map-2026-06-06.md`,
`runbook-battle-observation-floor-2026-05-02.md`,
`runbook-db-cpu-saturation-2026-05-24.md`,
`runbook-periodic-task-topology-2026-04-11.md`.
