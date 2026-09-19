# Runbook — Ship-standings 60d foothold: window, floor, and rebuild cost (2026-08-18)

_Created: 2026-08-18_
_Context: the 45d→60d window foothold came up for its next step, and with it two questions — what per-player battle floor the data supports at 60d, and why a nightly rebuild of a trailing window costs minutes when the day-over-day delta is small._
_QA: all figures below are measured, not modelled from proportion; the modelling instrument was calibrated against the live snapshot before use (see Validation)._

## QA Notes

_Reviewed 2026-08-18 against `/home/august/code/battlestats/.claude/worktrees/sixty-day-window` (linked worktree; `.git` file → `.git/worktrees/sixty-day-window`). 28 assertions checked (24 against the repo, 4 against the production database — the latter marked below), 4 corrected._

### Resolved
- **"`top-ships-warmer-{realm}` fires an hour after the snapshot hour"** -> actual: `realm_hour = (SHIP_BADGE_SNAPSHOT_HOUR + 1 + REALM_CRAWL_CRON_HOURS[realm]) % 24` with offsets `{'eu': 0, 'na': 6, 'asia': 12}` (`server/warships/signals.py:213-214`, `signals.py:12`), giving **na 09:05 / eu 03:10 / asia 15:15 UTC** — an hour after the snapshot only if the crawl offset is 0 -> lever 1 now states the real times and flags that `signals.py:205-207` makes the same wrong claim. The load-bearing point (an independent daily entry exists, so halving the snapshot cadence orphans no warm) is unaffected.
- **"Precedent exists — v5.3.9 moved the ship list the same way"** -> actual: that move went to `ShipPopDailyAgg`, a per-(realm, mode, ship, day) pre-aggregate (`server/warships/data.py:7152-7164`), not to `PlayerDailyShipStats` -> lever 2 rewritten: the snapshot ranks per player, so it cannot use `ShipPopDailyAgg`, and its only rollup option is `PlayerDailyShipStats` (~13.4M rows vs `BattleEvent`'s ~14.4M) — a comparable scan, not a coarser one. The precedent is weaker than the original text implied; the "size of the win is unmeasured" caveat now carries the argument.
- **"`BattleEvent`'s only time index is a BRIN on `detected_at`"** -> actual: `battle_event_detected_brin` is the only index *leading* on `detected_at`; `battle_event_player_time_idx` is a btree on `(player_id, detected_at DESC)` and so cannot serve a window scan across all players -> lever 3 reworded to say "leading on". **Source: `pg_indexes` on the production database, not the repo** — a fresh checkout cannot reproduce this from migrations; re-read it with `SELECT indexname, indexdef FROM pg_indexes WHERE tablename='warships_battleevent'`.
- **"the backfire the 2026-06-29 lever study found at 30d"** -> actual: the finding is recorded in `agents/runbooks/runbook-ship-badges-rolling-2026-06-14.md:65-73` (its 2026-06-29 update), not in the window-30d runbook, and that entry does **not** state which window it was modelled against — the 14→30 widen landed the same day -> citation corrected, "at 30d" dropped, ambiguity stated inline. The Related section's attribution of the weight/gate study was corrected to the same file.
- **Follow-ups extended by two items QA surfaced:** the `signals.py:245` "~12s" figure inherits from `runbook-ship-badges-rolling-2026-06-14.md:75-95`, whose sizing table was measured at a **14d** window against a **3.18M-row** `BattleEvent` (now 14.4M at 45d), and that runbook's adjacent claim that the compute path "does not wrap its read in `_elevated_work_mem()`" is now false (`server/warships/data.py:6186`); separately it cites the retention pin at `deploy_to_droplet.sh:705`, now `:780`.

### Unverified
- Every measurement in Parts 1 and 2 — coverage tables, thin-sample shares, scan timings, journal runtimes, the 2026-06-13 data-depth floor, and the Umami traffic figures. These came from read-only queries against the production database, the droplet journal, and the Umami database during this session; they cannot be re-derived from the repository. Re-run the commands in Validation to refresh them.
- "prod sets nothing" for `SHIP_BADGE_MIN_BATTLES`: confirmed only in that `server/deploy/deploy_to_droplet.sh` contains no `set_env_value` for it. The live `/etc/battlestats-server.env` was not read.

### Open Questions
1. **Does the second daily snapshot run earn its keep?** Lever 1 (halving the cadence) is the cheapest saving on the table and is otherwise blocked only on intent: both runs write the same `captured_on`, so the second buys intra-day freshness of badges and `/ship` boards, nothing else. Keeping it means paying roughly double at 60d (EU ~360–460s per run). Blocks lever 1.

## Purpose

Two studies from one session, kept together because the second is the cost side
of the first. Read this before changing `SHIP_BADGE_MIN_BATTLES`,
`SHIP_LEADERBOARD_WINDOW_DAYS`, or the ship-snapshot Beat cadence — and before
planning the 90d foothold, which has a hard data-depth blocker documented here.

**Nothing described here was armed.** No env value was set, no schedule was
edited, no snapshot was rebuilt. Both are prod levers awaiting a decision.

## Scope note: there are two different "60 days"

They are unrelated numbers and the distinction matters throughout:

- **Player-page battle-history window** — the Activity-tab pills and trend
  strip. Moved 45d→60d on branch `worktree-sixty-day-window` (rename, not add:
  `fortyfive`→`sixty` end to end). Not deployed.
- **Ship-standings window** — `SHIP_LEADERBOARD_WINDOW_DAYS`, `data.py:5948`,
  pinned to **45** in `server/deploy/deploy_to_droplet.sh:791`. Drives the
  `/ship` boards and the profile badges. **Unchanged.**

Everything below concerns the second one.

## Part 1 — What battle floor the data supports

### The lever

`SHIP_BADGE_MIN_BATTLES` (`data.py:6064`, default **15**, read from the
environment at task-call time so it re-tunes without a redeploy; prod sets
nothing). It is the per-player minimum battles **on one ship** to enter that
ship's qualifying pool.

Two neighbours that are **not** this lever and must be left alone:
`SHIP_LIST_MIN_BATTLES` (`data.py:6668`, 50 — gates ships, not players) and
`SHIP_LIST_WR_PCT_PLAYER_MIN_BATTLES` (`data.py:6682`, 15 — a different
surface).

### Method

Modelled on `PlayerDailyShipStats` (mode=random, tiers 8/9/10, per realm,
`is_hidden=false`), one grouped scan per realm covering both windows via
`FILTER` clauses, returning a per-ship histogram rather than raw rows.

### Coverage — ships holding a board

| realm | 45d/15 (live) | 60d/15 | 60d/20 | 60d/25 | 60d/30 |
|---|---|---|---|---|---|
| na | 344 | 382 | 346 | 312 | 275 |
| eu | 395 | 429 | 402 | 378 | 351 |
| asia | 397 | 425 | 398 | 372 | 335 |

- **60d/20 is coverage-neutral** (+2/+7/+1) and needs no other change.
- **60d/25** costs 32/17/25 boards, all from the popularity tail (median rank
  ~326–386 of 507); **zero of the 100 most-played ships** in any realm.
- **45d/25 — the window NOT moved — costs NA 70 boards (−20%).** This is the
  same backfire the 2026-06-29 lever sweep recorded — raising the floor is a
  sample-reliability knob that pays for itself in delisted ships, costing
  "20–30 ships of coverage"
  (`runbook-ship-badges-rolling-2026-06-14.md:65-73`; that sweep does not state
  which window it ran against, and the 14→30 widen landed the same day). A
  floor raise is only affordable if the window moves with it.

### What the floor buys

On the live 45d/15 board: **25.6% of NA podium (badge) rows are held on fewer
than 25 battles**, and 42% of all board rows are under 25.

The finding that makes the question answerable: **widening the window does not
improve sample quality on its own.** Thin-sample share of the eligible pool is
49.9% at 45d/15 and 48.8% at 60d/15 — a wider window recruits marginal players
at nearly the same rate it deepens existing ones. Floor 20 halves it (~26%);
floor 25 eliminates it by definition.

### Rate equivalence

Games per day of window, which is what distinguishes a real raise from a relabel:

| setting | rate |
|---|---|
| 15/45 (today) | 0.333 |
| 20/60 | 0.333 — the identical bar |
| 25/60 | 0.417 — the first actual raise |
| 25/90 | 0.278 — **looser than today** |

### Traffic weighting

Umami, trailing 60d: 244 ship pageviews across 52 ships. At 60d/25 exactly one
visited board is lost, and it is where the popularity proxy and real traffic
**disagree**: **Le Fantasque (T8 DD, NA)** carries 9.4% of all ship-page views
yet falls to pool 16 against a guard of 20. EU loses no visited board; ASIA
loses 3 views' worth.

Limit: 244 views over 60 days is a thin sample — one shared link moves 9.4%.

### The pairing that removes the cost of floor 25

The population guard — `SHIP_BADGE_MIN_SHIP_POPULATION` (`data.py:6065`, 20;
CV 10, sub 12 via `_CV`/`_SUB`) — not the floor, is what actually delists a
ship. Sweeping **only** the general guard (CV and sub held at their live
values, since they are independent env vars):

| realm | today | g20 | g18 | g16 | g15 |
|---|---|---|---|---|---|
| na | 344 | 312 | 322 | 334 | 339 |
| eu | 395 | 378 | 384 | 392 | 395 |
| asia | 397 | 372 | 384 | 387 | 392 |

Floor 25 + guard 15 holds coverage flat while every ranked player has ≥25 games.
Le Fantasque NA needs guard ≤16 to survive (pool 48 → 30 → 16 at floors
15/20/25).

### The 90d step is not yet computable

`BattleEvent` **and** `PlayerDailyShipStats` both start **2026-06-13** — 66 days
of depth. That is the floor left by the old 32d retention era, before the raise
to 92d on 2026-07-20 and 105d on 2026-07-24. Depth accrues forward one day at a
time:

- 90 days of real depth arrives **~2026-09-11**, capping at 105d ~2026-09-26.
- Running a 90d window before then produces a **66-day board labelled 90d**,
  silently.

Pick the 90d floor in September on measured data, not by proportion — and note
from the rate table that 25@90d is a loosening, not a tightening.

## Part 2 — What a rebuild actually costs

### Correction: the 636s figure is stale

636s came from a one-off **synchronous** run on the droplet on 2026-07-24 (NA,
45d, under load) and is not representative. Real cost today, from the
`battlestats-celery-background` journal:

| realm | observed runs (45d) | projected at 60d |
|---|---|---|
| na | 98.5s / 120.8s / 161.3s | ~130–215s |
| eu | 268.7s / 346.6s / 347.1s | ~360–460s |
| asia | 81.3s / 142.6s / 166.6s | ~110–220s |

Beat fires each realm **twice daily**, striped (na 02:30/14:30, eu 06:30/18:30,
asia 10:30/22:30) — six runs a day, ~20 minutes of background-worker time.

### Where the time goes

Read-only, same filters the task uses:

| realm | scan (measured) | live task | scan share |
|---|---|---|---|
| na | 109.8s @ 60d, BattleEvent | 98–120s | ~all of it |
| eu | 189.4s @ 45d, BattleEvent | 269–347s | ~55–70% |

On NA the aggregation is the task. On EU roughly a third is everything else —
the treemap compute at the top, the ~5.9k-row write, ~2.4k `cache.delete()`
invalidations. Python scoring over ~40k rows is noise.

### Why "it is only a fractional delta" does not make it cheap

EU, `PlayerDailyShipStats`, same grouping:

| what | time | rows out |
|---|---|---|
| full 60-day window scan | 25.5s | 1,260,209 |
| one day (an incremental night's add) | 19.3s | 64,834 |
| one day aging out | 4.1s | 3,861 |

**A single day costs nearly what the entire window costs.** The full window goes
sequential scan + hash-aggregate; one day goes btree index + random heap access.
The delta is fractional in rows and barely fractional in time.

That is fixable — a BRIN on `date` would make a one-day slice near-sequential,
since rows cluster by insert date — so an incremental design is not ruled out.
But the delta is not free by default, and the running state such a design must
maintain is ~1.26M (ship, player) rows per realm, refreshed by ~69k upserts a
night plus an `updated_at` sweep to catch late-arriving corrections to
historical daily rows (capture is asynchronous; those rows do change).

Run-to-run variance for identical work (EU 269s vs 347s; asia 81s vs 167s) says
this job is I/O-bound on a 4 GB managed PG whose working set — 2.7 GB heap per
table plus indexes — does not comfortably cache.

### Levers, cheapest first

1. **Halve the cadence.** Both daily runs write the same `captured_on`; the
   second only refreshes intra-day. The dependency was checked: the task chains
   `queue_realm_top_ships_warm(realm)` (`tasks.py:1435`) and
   `warm_all_ship_pop_avg_damage_task`, but **both already have their own daily
   Beat entries** (`top-ships-warmer-{realm}` an hour after the snapshot hour;
   `ship-pop-bulk-warm-{realm}` at 00:10/00:30/00:50), so the chain is a second
   trigger, not the only one. Halving does not orphan either warm. Cost halves
   for one schedule edit. Product call: how fresh must badges be?
2. **Source switch to `PlayerDailyShipStats`.** The v5.3.9 ship-list move is a
   weaker precedent than it looks: that one went to **`ShipPopDailyAgg`**
   (`data.py:7152-7164`), a per-(realm, mode, ship, day) pre-aggregate, and the
   coarse granularity is why it won big. The snapshot ranks **per player**, so
   it cannot use `ShipPopDailyAgg`; its only rollup option is
   `PlayerDailyShipStats` at ~13.4M rows against `BattleEvent`'s ~14.4M — a
   comparable scan, not a coarser one. The rollup still beat `BattleEvent` in
   every paired read taken here, but by 1.3x on NA and far more on EU, and that
   spread is cache state rather than signal. **Treat the size of the win as
   unmeasured** pending a controlled back-to-back A/B on one realm. Bucketing
   caveat: rollup rows are UTC-date buckets; `BattleEvent` uses `detected_at`.
3. **Index work on `BattleEvent`.** The only index **leading** on
   `detected_at` is a BRIN (`battle_event_detected_brin`); the btree that also
   covers time leads on `player_id` (`battle_event_player_time_idx`), so it
   cannot serve a window scan across all players. The query also filters `mode`
   and joins player for realm/`is_hidden`. Uncertain payoff on a cache-starved
   DB — measure first.
4. **Incremental window table.** Feasible, most complex, and worth building only
   if 1 and 2 leave the job still hurting the background queue.

At 60d the scan grows about a third. Lever 1 alone more than pays for the
window widening.

## Validation

- **Instrument calibrated before use:** the rollup model at 45d/floor 15 gives
  344/395/397 ships (na/eu/asia) against the live `ShipTopPlayerSnapshot` at
  `captured_on=2026-08-18` of 347/398/398. The 1–3 ship gap is the treemap
  top-25 union, which the model omits.
- **Costs read from the live journal**, not inferred: `journalctl -u
  battlestats-celery-background.service --since <date> -g
  'snapshot_ship_top_players'`.
- **Scan timings** are read-only aggregates run against prod under
  `SET LOCAL statement_timeout`, mirroring the task's own filters.

## Follow-ups

- `signals.py:245` still calls these "~12s aggregations" — off by an order of
  magnitude, and it sits directly above the schedule anyone would edit. It
  inherits from `runbook-ship-badges-rolling-2026-06-14.md:75-95`, whose sizing
  table ("three ~12 s bursts/night") was measured at a **14d** window against a
  **3.18M-row** `BattleEvent`; the table is now 14.4M rows at 45d. Both need
  the same correction, and that runbook's adjacent claim that the compute path
  "does not wrap its read in `_elevated_work_mem()`" is also now false
  (`data.py:6186`). Own branch.
- The same runbook cites the retention pin at `deploy_to_droplet.sh:705`; it is
  now at `:780`. Line drift, not a value change (105 is correct).
- If the ship-standings window moves to 60d: rebuild the snapshot per realm,
  then **force-warm the grid directly** rather than relying on the queued warm
  (the queued path lagged 20+ minutes behind a backlogged `background` queue
  during the 45d rollout), and expect `/ship` boards to trail up to 15 minutes
  on their Redis read-cache.
- Re-measure the floor question in September once 90d of depth exists.
- The 11 doc files still describing the player-page window as 45d are
  reconciled with that branch, not this runbook.

## Related

- `runbook-ship-leaderboard-architecture-2026-06-18.md` — how the pipeline fits
  together end to end.
- `runbook-ship-badges-rolling-2026-06-14.md` — the rolling-nightly decision,
  the threshold sweep that set floor 15 / pop 20 / prior 50, **and** the
  2026-06-29 weight/gate study that shipped 0.60/0.25/0.15 +
  `SHIP_BADGE_MIN_WIN_RATE` (lines 54-73), plus the prod sizing table this
  runbook supersedes.
- `runbook-ship-leaderboard-window-30d-2026-06-29.md` — the 14→30 window widen.
