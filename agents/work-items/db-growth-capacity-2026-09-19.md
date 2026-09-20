# Work item: DB capacity re-assessment at the 90d cutover, and a compaction regression

_Created: 2026-09-19_
_Author role: DBA / capacity planning_
_Context: successor to `db-growth-capacity-2026-08-05.md`, which projected an ~68 GB plateau (~80% of the 84.17 GB volume) reached around 2026-11, with 80% crossed ~2026-10-06. Re-measured 45 days later, on the day the ship-standings window moved to 90d. The database is at that plateau roughly six weeks early._
_Method: read-only. Every session `SET statement_timeout`; `SET default_transaction_read_only=on`. No writes, no VACUUM, no reclamation, no service changes. Full-table `count(DISTINCT …)` on `warships_battleobservation` timed out at 60s and was replaced by bounded index-driven sampling rather than being retried with a longer budget._

## Evidence classes

Carried over from the 2026-08-05 document so the two can be read together.

| Tag | Meaning |
|---|---|
| **M** | Measured directly today by query |
| **S** | Sampled and extrapolated; sample size stated |
| **D** | Derived from a prior measurement plus today's |
| **A** | Assumed; stated as such and never load-bearing alone |

## TL;DR

1. **The current sizing does not hold.** `pg_database_size` is **59.06 GB** (M), up from 39.02 GB on 2026-08-05: **+20.04 GB in 45 days, ~445 MB/day** (D). Against the 84.17 GB volume, with the WAL gap at its measured 7.25 GB ceiling, `disk_used` is **≈66.3 GB ≈ 79%** (D). The August document put 80% at 2026-10-06 and the plateau in November; both arrive early.
2. **The 90d window is not a driver.** Battle-history retention did not move (105d, unchanged, and already sized for a 90d read). The only derived change was `SHIP_POP_ROLLUP_RETENTION_DAYS` 100 → 105 against a 99 MB table: roughly 5 MB (D). The constraint remains per-player stores that never age out, exactly as August concluded.
3. **A compaction regression is the single largest correctable driver.** `BATTLE_OBSERVATION_COMPACT_KEEP=1` is pinned in `/etc` **and** in the deploy script, is documented in three runbooks as the live value — **and is read by nothing**. The systemd unit that replaced the Celery task on 2026-08-06 passes only `--statement-timeout`; every other knob falls back to its argparse default, and `COMPACT_KEEP_PER_PLAYER_DEFAULT = 3`. Production has kept **three** JSON generations per player since 2026-08-06, not one. Measured: **mean 4.18 payload-bearing rows per recently-observed player, 394 of 400 sampled at ≥3** (S).
4. **That regression is worth ~12 GB of the table and two thirds of its slope** (S+D). `warships_battleobservation` is now **25.72 GB** (M), +9.88 GB since August = **220 MB/day**, the largest single line in the growth decomposition.
5. **Two of the four biggest growth lines stop by themselves within a fortnight.** `BattleEvent` and `PlayerDailyShipStats` (181 MB/day combined) have **never pruned once** — the 2026-09-15 archive run logged `skipped (no rows older than cutoff)` for both (M). Their floor is 2026-06-13 (M), so 105d depth lands 2026-09-26 and the 2026-10-01 timer run is the first with candidates. Post-fill slope is **~264 MB/day** (D).
6. **Reclaim is not the same as `disk_used` falling.** Every lever below frees space *inside* its table for reuse. Without `VACUUM FULL` (forbidden: the 2026-07-21 24-minute outage) or `pg_repack` (v1.5.2 available on the cluster, not installed), the volume does not shrink. Price the levers as **slope**, not as GB returned.
7. **Alerting was never armed, and nothing else will catch this.** Step 0 of the August remediation plan (DO disk alerts at 70% and 80%) is still open, and both thresholds are now behind us. The `doctl` token in `~/.config/doctl/config.yaml` and on the droplet both return 401, so `disk_used_percent` could not be read from the metrics endpoint today and the 79% figure is derived. **Storage autoscale is off permanently by operator decision (2026-09-20)** — unpredictable cost is disqualifying for this project — so the ceiling is a chosen property of the system and item 1's dates are hard deadlines, not points where the bill grows instead.

## Measured state, 2026-09-19 ~23:50 UTC

| Metric | 2026-08-05 | 2026-09-19 | Class |
|---|---|---|---|
| `pg_database_size(defaultdb)` | 39.02 GB | **59.06 GB** | M |
| All databases | 39.13 GB | 59.21 GB | M |
| Volume total | 84.17 GB | 84.17 GB | M (unchanged) |
| Non-database gap (WAL + temp + logs) | 7.25 GB | ~7.25 GB, ceiling ~8 | D — `pg_ls_waldir()` is `permission denied` for the app role |
| **Implied `disk_used`** | 46.27 GB (55.0%) | **≈66.3 GB (≈79%)** | D |
| Connections | — | 34 of 100, 5 active, 0 idle-in-tx, **0 blocked** | M |
| `max_wal_size` / `wal_keep_size` | 4013/4014 MB | 3999/3999 MB | M |
| Heap buffer-cache hit | — | **64.45%** | M |
| Index buffer-cache hit | — | 87.84% | M |

### Per-table footprint and the 45-day delta

| Table | 08-05 | 09-19 | Δ | MB/day | Class |
|---|---|---|---|---|---|
| `warships_battleobservation` | 15.84 GB | **25.72 GB** | +9.88 | **220** | M+D |
| `warships_player` | 10.99 GB | 12.35 GB | +1.36 | 30 | M+D |
| `warships_playerdailyshipstats` | 4.47 GB | 8.85 GB | +4.38 | 97 | M+D |
| `warships_battleevent` | 3.88 GB | 7.64 GB | +3.76 | 84 | M+D |
| `warships_snapshot` | 1.83 GB | 2.27 GB | +0.44 | 10 | M+D |
| `warships_playerachievementstat` | 1.37 GB | 1.50 GB | +0.13 | 3 | M+D |
| `warships_playerexplorersummary` | 0.31 GB | 0.33 GB | +0.02 | <1 | M+D |
| Everything else | 0.31 GB | 0.40 GB | +0.09 | 2 | M+D |
| **Total** | **39.02** | **59.06** | **+20.04** | **445** | D |

Two observations. First, `battleobservation` alone is half the slope, and the August document's hope that its rate was largely transient TOAST refill did not survive: 220 MB/day is lower than the 364 MB/day measured mid-refill, but it is a sustained rate, not a decay. Second, `PDSS` + `BattleEvent` ran at 181 MB/day against the runbook's 197 MB/day forecast for the pair — the forecast was good; it simply never modelled the observation table.

## The compaction regression

### What is true in production

```
/etc/battlestats-server.env:      BATTLE_OBSERVATION_COMPACT_KEEP="1"
deploy_to_droplet.sh:817:         set_env_value BATTLE_OBSERVATION_COMPACT_KEEP 1
deploy_to_droplet.sh:1220 (unit): ExecStart=… manage.py prune_battle_observations \
                                    --statement-timeout "${…_STATEMENT_TIMEOUT:-1800}"
journalctl 2026-09-19:            "Compacted 96,622 observation payloads in 49 batch(es)
                                   (keep_per_player=3, min_age_hours=0)."
```

The pin is correct, present in both authorities, and inert. `prune_battle_observations` takes `--keep-per-player`, defaulting to `COMPACT_KEEP_PER_PLAYER_DEFAULT = 3` (`incremental_battles.py:1565`); the unit does not pass it. The env name is read in exactly one place, `prune_battle_observations_task` (`tasks.py`), whose Beat registration was deliberately disabled on 2026-08-06 when the work moved onto the timer. The knob and its only reader were switched off in the same change.

Corrected in QA 2026-09-19: the sibling knobs did **not** all go inert. `--dormant-after-days` reads its env var **as its own argparse default** (`prune_battle_observations.py:102-104`), so the unit's `EnvironmentFile` carries it through and it works. `--keep-per-player`, forty lines earlier in the same argparse block, takes the module constant instead. The defect is an inconsistency inside one command, which is exactly why it was invisible. The knobs genuinely unread from the environment are `KEEP`, `MIN_AGE_HOURS`, `BATCH_SIZE`, `MAX_ROWS` and `SLEEP`; of those only `KEEP` is pinned in production, so it is the only one whose being ignored had a live effect.

### What it costs

| Quantity | Value | Class |
|---|---|---|
| Observation rows (estimate) | 3,921,436 | M (`reltuples`) |
| Rows carrying JSON | 28.8% of a 44,591-row sample → **~1.13 M** | S |
| Mean payload size | **16 kB** | S (1% sample) |
| Payload bytes held | **~18.1 GB** | S |
| Table TOAST | ~24.7 GB | M |
| Payload generations per recently-observed player | **mean 4.18, max 7; 394/400 at ≥3** | S (400 players) |

At `keep=1` the steady state is one generation per payload-bearing player instead of three. That is **~12 GB released into reusable space** and a per-player coefficient falling from ~48 kB to ~16 kB (S+D). It does not shrink the volume; it absorbs roughly 45 days of the post-fill slope and cuts the largest ongoing line by about two thirds.

**The generations it destroys cannot be re-fetched.** WG serves current cumulative stats only, so generations 2 and 3 are gone permanently once compacted. They exist as a diff baseline; `keep=1` is what every document says production has been running since August, and what it did run from 2026-05-26 to 2026-08-06.

### Why the drift checker cannot see this class

`check_env_drift.sh` compares the deploy script, `/etc`, and the docs. This value agrees in all three and is read by none of them, so it passes checks 1, 2 and 3 cleanly. A fourth check — *is every pinned key referenced by code that actually runs?* — is the only thing that would have caught it. Worth adding; the same shape would catch any knob orphaned by a Celery-to-timer migration.

## Projection

Central path, assuming the observation slope continues and nothing is changed (D):

| Milestone | Date | Basis |
|---|---|---|
| 105d window fills; `PDSS`+`BattleEvent` stop growing | **2026-09-26** | floor 2026-06-13 (M) |
| First archive run with candidates | **2026-10-01** | timer, 1st + 15th (M) |
| 80% of volume | **~now to 2026-09-26** | +1.2 GB of window fill |
| 90% of volume | **~2026-10-21** | 264 MB/day post-fill |
| Volume full (read-only outage) | **~2026-11-22** | 264 MB/day; autoscale OFF permanently by decision |

With `keep=1` restored, the ~12 GB of reusable space absorbs inserts for roughly 45 days and the slope falls to roughly **120-150 MB/day** (S+D), moving the 90% date into 2027. That is a reprieve, not a fix: the per-player coefficient is still unbounded in the player pool, which is the conclusion the August document reached and this one does not overturn.

## Efficiency findings

Ranked by value, all read-only measurements.

| # | Finding | Size | Class |
|---|---|---|---|
| 1 | **Compaction keeps 3 generations, not 1** (above) | ~12 GB + 2/3 of the largest slope | S+D |
| 2 | **`warships_playerachievementstat` has no reader.** Written by `data.py:594` (delete + `bulk_create` per refresh) and merged by `player_records.py`. Nothing selects it: no serializer, no view, and the client has **zero** occurrences of "achievement". Its indexes (821 MB) now exceed its heap (610 MB), and the delete-then-recreate pattern churns it continuously | 1.50 GB + ongoing WAL and dead-tuple churn | M |
| 3 | **Two large `PDSS` indexes are effectively unscanned**: `…_mode_5a941e36` (195 MB, **0 scans**) and `…_player_id_daed36c5` (190 MB, **8 scans**, likely redundant against a composite). This is the highest-write table in the schema at 20.5 M rows, so each one taxes every insert as well as the volume | ~385 MB + insert cost | M |
| 4 | **Heap buffer-cache hit is 64.45%** against 780 MB of `shared_buffers` on a 4 GB instance serving a 59 GB database. This is the memory-side face of the chronic random-access latency already recorded (11.5 ms vs 1.2 ms round-trip at ~37% iowait). RAM, not just disk, is undersized for the working set | — | M |
| 5 | **`battles_json` prune: the timer has fired weekly since 2026-06-21 and no-ops every time**, because `PRUNE_BATTLES_JSON_ENABLED=0` gates the command. Measured reclaim if armed is far smaller than August estimated: 997 of a 34,178-row sample are inactive >180d and hold JSON — **~2.9% of players, ~326 MB** (S), against the ~2 GB the August document projected | ~326 MB | S |
| 6 | `mv_player_distribution_stats` carries 18.3% dead tuples and takes 43% of its scans sequentially — by far the worst ratio in the schema, though it is small (120 MB) | — | M |

Healthy, for the record: 0 blocked queries, 0 idle-in-transaction, 34 of 100 connections, and sequential-scan share at 0.0-0.3% on every large table except the materialized view.

## Recommended sequence

One lever per acknowledgement, per standing practice. Nothing in this document has been applied.

1. **Restore `keep=1`** by passing `--keep-per-player "${BATTLE_OBSERVATION_COMPACT_KEEP:-1}"` in the unit's `ExecStart`, and change `COMPACT_KEEP_PER_PLAYER_DEFAULT` to 1 so the code default stops contradicting every document. Irreversible for generations 2 and 3. Largest single win.
2. **Re-arm alerting** (August's Step 0): DO disk alerts at 80% and 90%. Needs a working `doctl` token. This is the only warning that will ever fire: autoscale is off by decision, so nothing converts the November date into a bill instead of an outage.
3. **Decide the volume.** Even with lever 1, 84 GiB at ~264 MB/day pre-fix is thin. A resize is the only move that buys unconditional headroom, and it is the one that does not depend on any estimate in this document being right.
4. **Drop the two unscanned `PDSS` indexes** after confirming no planner regression on the ship-standings aggregations that read that table.
5. **Decide `playerachievementstat`'s fate.** If nothing is going to read it, stopping the write is worth more than the 1.5 GB: it removes a delete-and-recreate cycle from every player refresh.
6. Leave the `battles_json` prune alone, or arm it for tidiness. At ~326 MB it is not a capacity lever, and the weekly no-op timer is misleading enough to be worth either arming or removing.

## Related

- `db-growth-capacity-2026-08-05.md` — the predecessor; its method, its evidence classes, and its player-pool conclusion all still stand.
- `agents/runbooks/runbook-db-disk-remediation-2026-08-05.md` — the remediation plan whose Step 0 and Step 2 are still open, and whose item 13 predicted exactly this stale-default confusion.
- `agents/runbooks/runbook-env-value-authority-2026-08-05.md` — the drift procedure. Finding 3 above is a class it cannot catch.
- `agents/runbooks/runbook-db-table-audit-2026-07-19.md` — the F-series table audit.
