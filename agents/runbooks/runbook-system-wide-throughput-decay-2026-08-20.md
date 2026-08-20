# Runbook — `recapture_partial:asia` on 2026-08-19 is a system-wide throughput decay, not a recapture defect (2026-08-20)

_Created: 2026-08-20_
_Context: the 2026-08-19T11:34Z ops mail fired one condition, `recapture_partial:asia` (`scanned=18100` of `candidates=30000`, `advanced=537`, `yield_frac=0.0297`)._
_Status: **DIAGNOSIS ONLY. NOTHING ARMED, NOTHING CHANGED.** No env touched, no worker restarted, no deploy. The 2026-08-20 stripe had not fired at time of writing (04:41 UTC; Beat fires 10:10/10:30/10:50 UTC), so the next confirming observation is available later today._

## The one-paragraph version

The alert is real and it is worse than the ones before it, but the sweep is not the thing that broke. asia scanned 18,100 rows in 900.9s: **20.1 rows/s**, against its own 35 to 46 baseline and below even the contended 25.3 and 31.6 of Aug 13 and Aug 14. At that rate a 30,000 row pass needs roughly 1,493s, so the shortfall against a 900s budget is about **590s**. The same decay is visible on every realm and on other work entirely: NA fell from 86.4 to 48.6 rows/s over four days and is now under its own floor; the observation floor lost half of NA's daily observations in the same window. The prescribed recapture lever L1 buys about 45s and is therefore **dead as a response to this alert**. The evidence points at a shared resource ceiling. Worker slots are **falsified**. The database was the leading candidate until its second leg was tested like-for-like and **failed**: pure-DB aggregation warmers are flat over the same four days while WG-bound bulk work decayed, which is the opposite of what a DB constraint produces. **The mechanism is not yet identified**; the DB rests on one windowed sample, and the WG side has the right shape but numbers an order of magnitude too small.

## What the alert says, verified against the files

| realm | date | partial | scanned | duration_s | rows/s | flush_failed | cursor_stamped |
|---|---|---|---|---|---|---|---|
| na | 08-19 | false | 30000 | 616.6 | 48.6 | false | 30000 |
| eu | 08-19 | false | 30000 | 676.9 | 44.3 | false | 30000 |
| asia | 08-19 | **true** | **18100** | **900.9** | **20.1** | false | 18100 |

The snapshot is honest: `cursor_stamped == scanned`, `flush_failed: false`, `chunk_errors: 0`, `aborted: false`. Yield composition is unremarkable (`yield_frac` 0.0297, `no_data` 10, `hidden` 6), so the cursor did not rotate into an expensive or unusual slice. Per-chunk cost rose; the pool did not change shape.

## Free result: the 2026-08-16 truncation fix is confirmed exercised

This closes Step 1 of `project_recapture_resume_2026-08-17`. Aug 19 is a truncated asia pass that ran the full 900s and **wrote a complete, honest snapshot** instead of erasing it, with `duration_s` populated for the first time in an alerting context. The failure mode of `runbook-recapture-truncation-handler-crash-2026-08-16.md` did not recur.

One thing the fix now makes visible that nobody reported: **Aug 18's asia pass took 879.6s, or 97.7% of its 900s budget, and was not partial.** By the `/recapture` skill's own rule (`duration_s` above ~85% of the soft limit is a near-miss worth saying out loud) the wall was one day away and legible. The near-miss detector is still unbuilt; had it existed, it would have fired on Aug 18.

## The decay is system-wide, and asia is only where it broke first

Rows per second, recapture, reconstructed from `duration_s` and the journal:

| realm | Aug 16 | Aug 17 | Aug 18 | Aug 19 | own baseline |
|---|---|---|---|---|---|
| na | 86.4 | 79.8 | 65.7 | **48.6** | 66 to 85 |
| eu | 67.6 | 44.9 | 41.2 | 44.3 | (noisy) |
| asia | 38.7 | 38.8 | 34.1 | **20.1** | 35 to 46 |

NA's curve is monotonic and it has already crossed below its own baseline; it simply has enough headroom that a 30,000 row pass still fits. asia had the least headroom, so asia breached. **"asia truncated again" is the wrong headline.**

The observation floor shows the same period, and harder. `coverage_ratio_vs_7d`, per realm, from `benchmarks/observation-floor/*_0430Z.json`:

| realm | Aug 16 | Aug 17 | Aug 18 | Aug 19 |
|---|---|---|---|---|
| asia | 0.40 | 0.37 | 0.33 | 0.33 |
| eu | 0.36 | 0.36 | 0.31 | 0.31 |
| na | 0.31 | 0.33 | 0.35 | **0.21** |

NA's daily observations went 31,262 to **15,690** in the Aug 18 04:30 to Aug 19 04:30 window; its `obs_bulk_floor` fell to 12,412 against asia's 29,044 and EU's 39,480, and its captured events fell to 31,548 against EU's 97,262. The `ensure_daily_battle_observations_task` median went 737.4s to 903.1s while daily completions fell 75 to 58. Coverage near 0.3 to 0.4 is the expected steady state (see `project_coverage_ceiling_daily_active`); **NA at 0.21 is not**. That is a product-visible data-freshness regression and it is arguably more urgent than the alert that surfaced it.

## What is falsified

- **Worker-slot saturation.** This was the 08-13/08-14 mechanism (`project_recapture_asia_soft_limit_2026-08-14`) and it does not apply. `background` slot-seconds in the 09:50 to 11:30 window: Aug 16 **106%**, Aug 17 57%, Aug 18 48%, Aug 19 **54%**. The worst day is one of the least saturated, and the cleanest day was oversubscribed. Corroborating: on Aug 19 all three realms logged `received` within milliseconds of Beat, where the 08-13/08-14 signature was 20 to 30 minutes of receipt lag. The cause is inside the task body, not in the queue wait.
- **Pool composition.** Yield fractions 2.7% to 4.4%, `no_data` and `hidden` in single digits, `chunk_errors: 0`, stable across all realms and all four days.
- **Upstream / transport.** `chunk_errors: 0` on every realm, and an asia-only upstream fault cannot slow NA and EU.
- **A crash.** The journal shows `succeeded in 900.94s: {'status': 'partial'}`; the snapshot exists and is internally consistent.

## What is measured but too small to be the mechanism

The WG global token bucket (`warships/api/rate_limiter.py`, 9 req/s, burst 18, shared across every worker and gunicorn) is under rising pressure. Wait-budget exhaustion warnings per day:

| queue | Aug 16 | Aug 17 | Aug 18 | Aug 19 | Aug 20 (to 04:41) |
|---|---|---|---|---|---|
| background | 0 | 5 | 14 | 21 | 8 |
| floor | 522 | 565 | 1099 | 474 | 322 |

The trend on `background` is real and monotonic. It is nonetheless **not sized to explain this**: 21 exhaustions at an 8s budget is at most 168s of logged tail-wait across the entire background queue for the entire day, against a ~590s shortfall on one task. More decisively, the limiter **fails open** by design once the budget is spent, so the floor exhausting 474 to 1,099 times a day is not actually being held to 9 req/s. A bucket that fails open cannot be what throttles recapture to 20 rows/s. Keep this as a secondary signal; do not build the story on it.

## Candidate cause: the database. One line supports it; the second was tested and failed.

**1. Direct measurement.** Managed Postgres is 2 vCPU / 4 GB. Sampled 2026-08-20 04:41 and 04:42 UTC:

```
cpu_usage_iowait  49.99  then  42.02
cpu_usage_idle     2.34  then   7.98
system_load1       6.03  then   4.34
system_load5       4.58  then   5.25
system_load15      3.67  then   4.24
disk_used_percent 66.34
mem_used_percent  47.16
```

`reference_managed_pg_trouble_signs` puts the trouble threshold at iowait above ~25%; this is 42% to 50%. `system_load15` of 4.24 on 2 vCPU is roughly twice saturation. **Caveat, stated plainly: both samples fall inside the 04:30 rollup window**, so this is a windowed sample and not evidence of all-day saturation. It is a strong prior, not a proof.

**2. The read-side warmers. This leg was tested and it FAILED; recorded because the failure is informative.** The first pass compared full-day medians (n≈144) against an Aug 20 partial day (n≈27) and read a 40% to 60% rise. That is the same partial-population error as the discarded `incremental_player_refresh_task` medians. Recomputed like-for-like, restricted to 00:00 to 04:41 on every day:

| task | Aug 17 | Aug 18 | Aug 19 | Aug 20 |
|---|---|---|---|---|
| `snapshot_active_players_task` | 601.7s (n=14) | 554.1s (n=14) | 636.5s (n=10) | 547.2s (n=10) |
| `warm_hot_entity_caches_task` | 210.3s (n=14) | 173.7s (n=19) | 199.4s (n=11) | **294.2s (n=10)** |

`snapshot_active_players_task` is **flat**. `warm_hot_entity_caches_task` is up roughly 40% to 50% on Aug 20 alone, on n=10, one day. That is not corroboration; it is one weak signal.

**This inverts the reading.** Pure-DB aggregation work did **not** decay while WG-bound bulk work (recapture on all three realms, the observation floor) did. If the database were the shared constraint, the aggregation warmers would have moved first and hardest. They did not. So the DB hypothesis now rests on a **single windowed sample** taken inside the 04:30 rollup, and the evidence actually pushes back toward the WG side, where the difficulty is that the limiter's own numbers are too small and it fails open. **The mechanism is not identified.** See "What is measured but too small" above and step 2 below.

**Best-dated candidate trigger, unproven:** backend release `20260818210948` (v5.3.11) landed 2026-08-18 **21:09 UTC** and moved `SHIP_LEADERBOARD_WINDOW_DAYS` 45 to 60 across standings and the timeline, doubling the scan range on the largest tables. That deploy sits inside the exact window in which NA's floor observations halved, and one day before the worst recapture day. Two honest objections: NA's recapture decay had already begun on Aug 17, before the deploy, so a single 08-18 cause cannot produce the whole curve; and 5.4.0 through 5.4.2 all landed on 08-19, all frontend, one of which changes player-page fetch behaviour.

## What was measured and then discarded; do not resurrect it

Recording these so the next pass does not re-derive them and build on sand.

- **`incremental_player_refresh_task` medians (29.5s to 240.1s) are meaningless.** The distribution is bimodal: individual runs are either ~0s or 3,000 to 10,000s. The median crosses between modes and reads as an 8x regression that is not there.
- **A summed `coverage_ratio_vs_7d` is not a coverage ratio.** Summing the per-realm ratios produced a "1.06 falling to 0.84" figure that looks like the floor dropping below full daily coverage. It is three ratios added together. The per-realm table above is the correct read.
- **`grep -ciE "429|too many requests|REQUEST_LIMIT_EXCEEDED|407"` over a journal is worthless.** It returns tens of thousands of hits by matching those digit sequences inside UUIDs and durations. Any WG-throttle count needs an anchored pattern.
- **`/v2/databases/{id}/metrics/credentials` returns `not_found`.** The metrics basic-auth credentials are **account-level**: `GET /v2/databases/metrics/credentials`. The `:9273` scrape must then be run **from the droplet**; it is firewalled to trusted sources and returns empty from a workstation. `reference_do_db_cpu_metrics_endpoint` should be corrected on both points.
- **`benchmarks/db-size/` is stale**, newest file `db-size-20260622T130000Z.txt`. Note the convention differs from every sibling benchmark directory: `db-size-<TIMESTAMP>.txt` plus a `diff-<TIMESTAMP>.txt`, not `YYYY-MM-DD_HHMMZ_*.json`. A date-globbed JSON pattern returns nothing there and that absence is a pattern mismatch, not staleness; the staleness is separately confirmed by a plain `ls -1t`. It cannot answer the growth question.
- **Full-day medians must never be compared against a partial day.** This bit twice in one session: once on `incremental_player_refresh_task`, once on the read-side warmers where it briefly manufactured the DB hypothesis's second leg. Restrict both sides to the same wall-clock window before reading a trend.

## Recommended next steps, in order

1. **Do not pull L1.** `RECAPTURE_LAPSED_DELAY=0.05` buys about 45s against a ~590s gap. It cannot close this, it spends the one-lever-per-step budget, and it contaminates the measurement of whatever actually caused the rate collapse. L2b, L3 and L4 are equally beside the point if the constraint is the database. The lever ordering in `runbook-recapture-soft-limit-budget-2026-08-13.md` was sized for a 34 rows/s world that no longer exists.
2. **Count WG-side faults with an anchored pattern**, since the like-for-like recompute pushed the reading back toward the WG side. The client logs `HTTP request failed for endpoint '%s'` and `Error in response for endpoint '%s'` (`warships/api/client.py`); count those per day per queue, and look for `REQUEST_LIMIT_EXCEEDED`. The hypothesis worth testing: the floor's demand grew with the active-7d pool (206,829 to 227,194 over five days), the limiter's 8s budget is exhausted 474 to 1,099 times a day, it **fails open**, and the system therefore transacts above 9 req/s and meets Wargaming's own ceiling instead of ours. That would slow every WG-bound task and leave DB-only warmers untouched, which is exactly the observed pattern.
3. **Sample the DB during the recapture window today**, 10:00 to 11:30 UTC, from the droplet, and compare against the 04:41 numbers. This separates "the DB is saturated all day" from "the 04:30 rollup saturates it briefly," and it is the only thing that keeps the DB candidate alive or kills it. If it stays alive, rank `pg_stat_statements` by `shared_blks_read` and `total_exec_time`; do not reset it on prod (blocked by the auto-mode classifier), snapshot and diff over a window.
4. **Verify the 60d rollout's required post-deploy work actually completed.** `runbook-ship-standings-60d-rollout-2026-08-18.md` names a snapshot rebuild per realm plus a forced grid warm, and `reference_rollup_coverage_gate_breaks_on_widen` warns that if the new oldest day was not backfilled first, every bucket falls back to a raw scan. Commit `fe717e4` claims the warm completed and all buckets verified; confirm that against the live rollup coverage, not against the commit message.
5. **Only then** consider a reversible probe on `SHIP_LEADERBOARD_WINDOW_DAYS` (60 back to 45). That is a production lever and needs an explicit ack; one lever at a time, per `feedback_prod_levers_one_at_a_time`.
6. **Treat NA's floor coverage at 0.21 as its own item.** It is a freshness regression on the product's primary asset and it will not be fixed by anything in the recapture lever list.

## Detector gaps this pass re-confirms

Both were already open from 2026-08-16 and both would have helped here:

- **No near-miss condition.** `duration_s` above 85% of the soft limit. Aug 18's asia pass at 97.7% would have fired a day before the breach.
- **No "the task raised" condition**, and none for `flush_failed`.

A third is now visible: **no condition watches rows/s against a realm's own baseline.** Every realm degraded for four days in plain sight and only the one with the least headroom tripped a threshold.

## Related

`runbook-recapture-soft-limit-budget-2026-08-13.md`,
`runbook-recapture-truncation-handler-crash-2026-08-16.md`,
`runbook-ship-standings-60d-rollout-2026-08-18.md`,
`runbook-realm-schedule-striping-2026-08-15.md`,
`agents/work-items/db-growth-capacity-2026-08-05.md`.
