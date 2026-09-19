# Runbook — Rollout: 60d → 90d for the timeline and the leaderboard (2026-09-19)

_Created: 2026-09-19_
_Context: August called the 90d move. It is the end of the 30 → 45 → 60 → 90 walk, not another foothold: 90d was always the intended rolling window and capture depth only reached it this week._
_QA: every number below was measured on prod on 2026-09-19 before the flip; the two that were not are marked Unverified._

## Purpose

The execution plan for the last step of the ship-standings window walk, and for
the timeline pill row that goes with it. `archive/runbook-ship-standings-60d-rollout-2026-08-18.md`
is the precedent this forks; the evidence for the floor decision lives in
`runbook-ship-standings-tier7-spike-2026-09-07.md` and
`runbook-ship-standings-75d-spike-2026-09-03.md`.

Read it before starting and again at verification. Every step states its own
precondition, so it is resumable.

## What moves

| # | Lever | From | To | Where |
|---|---|---|---|---|
| 1 | Ship-standings window | 60 | 90 | `SHIP_LEADERBOARD_WINDOW_DAYS`, `server/deploy/deploy_to_droplet.sh:793` |
| 2 | Per-player battle floor | 20 | **20 (held)** | `SHIP_BADGE_MIN_BATTLES`, `deploy_to_droplet.sh:801` |
| 3 | Player-timeline pills | Day/Week/Month/45d/60d/75d | Day/Week/Month/**90d** | `VISIBLE_WINDOWS`, `client/app/components/BattleHistoryCard.tsx` |
| 4 | Trend-strip domain | 75 | 90 | `STRIP_DOMAIN_DAYS` + `STRIP_FETCH_WINDOW`, same file |
| 5 | Battle-history window enum | — | `ninety` added | `BATTLE_HISTORY_WINDOWS`, `server/warships/views.py` |
| 6 | Archive retention code default | 92 | 105 | `ARCHIVE_RETENTION_DAYS_DEFAULT`, `server/warships/incremental_battles.py` |

Lever 6 is hygiene, not a live change: prod already pins
`BATTLE_HISTORY_ARCHIVE_RETENTION_DAYS=105`. At a 90d window a 92-day default
leaves two days of slack, so any environment running on the default would serve
a short window the first time a prune ran early.

### The floor does NOT move with the window

The 45 → 60 step set `SHIP_BADGE_MIN_BATTLES` to 20 on the rule "the floor
tracks the window so the bar per day stays constant" (0.333 games/day). That
rule gives **30** at 90d, and 30 was measured to destroy what the widen buys:
on T7 at 85d it cut ranked hulls from 66 to 44 per realm and pushed pool
medians below what the tier carries at the narrower window
(`runbook-ship-standings-tier7-spike-2026-09-07.md`). Depth is the goal here;
a floor that scales with the window cancels it. **Held at 20, deliberately.**

## Preconditions (all verified on prod 2026-09-19)

- **Capture depth.** `PlayerDailyShipStats` earliest date `2026-06-13` — 98 days.
  A 90d window is real depth, not a short board wearing a 90d label.
- **Rollup coverage.** `ship_pop_rollup_covers_window(realm, 'random', end-90, end)`
  returns `True` for na, eu and asia. **But the margin is one day**: `ShipPopDailyAgg`
  starts `2026-06-20`, and eu/asia sat on `captured_on = 2026-09-18`, which opens
  their window at exactly `2026-06-20`. A realm whose snapshot lags a further day
  drops EVERY tier×type bucket onto the raw `BattleEvent` scan — correct, minutes
  per bucket, and silent (`reference_rollup_coverage_gate_breaks_on_widen`). Step 1
  buys the margin.
- **Derived retention follows automatically.** `SHIP_POP_ROLLUP_RETENTION_DAYS`
  is `max(100, window + 15)`, so it becomes 105 at 90d without a pin.

## Step 1 — Buy rollup margin BEFORE the flip

Three realm-days, backfilled from `PlayerDailyShipStats` (which reaches back to
06-13, so the source exists):

```python
from datetime import date
from warships.data import rollup_ship_pop_daily
for realm in ('na', 'eu', 'asia'):
    for d in (date(2026, 6, 17), date(2026, 6, 18), date(2026, 6, 19)):
        print(realm, d, rollup_ship_pop_daily(realm, d))
```

Budget ~44s per realm-day (measured during the 60d rollout), so ~7 minutes.
Idempotent: a delete-and-replace upsert per realm-day.

**Do this first.** Flipping first and backfilling second is the one wrong order.

## Step 2 — Gate, version, deploy

- Release gate, then `./scripts/release.sh minor` (5.10.0 → 5.11.0: the pill row
  is a user-facing UX change).
- `./server/deploy/deploy_to_droplet.sh battlestats.online`.
- `./client/deploy/deploy_to_droplet.sh battlestats.online` — **mandatory**, even
  though the window itself is a backend env value: `NEXT_PUBLIC_APP_VERSION` is
  captured at build time and the pill row is client code.

## Step 3 — Make the new window real

The env pin moves no board on its own; every standings surface is served from
the snapshot. Until the snapshot is rebuilt, boards serve 60d data **labelled
90d**. Keep this step immediately after the deploy.

**Precondition:** `SHIP_BADGE_SNAPSHOT_ENABLED=1` (pinned by the deploy at
`deploy_to_droplet.sh:445-448`), or the task returns without computing.

Per realm, one at a time:

```python
snapshot_ship_top_players_task.apply_async(args=[realm], queue='background')
```

Then **force-warm the grid synchronously** rather than trusting the chained warm
— `warm_realm_top_ships_task` has been a pure dispatcher since 2026-08-12, which
is the lag being avoided. Both treemap modes, then all **20** tier×type buckets
(`_badge_tiers()` = 8/9/10/11 × the five `SHIP_LEADERBOARD_TYPES`), T10 first:

```python
compute_realm_top_ships(realm, limit=25, mode='random')   # and mode='ranked'
compute_realm_ships_by_tier_type(realm, tier, ship_type, wr_pct=None, use_cache=False)
compute_realm_ships_by_tier_type(realm, tier, ship_type, wr_pct=50,  use_cache=False)
```

`wr_pct=50` materializes the 25% bucket too. `manage.py shell < file` sets
`argv[1]='shell'`, so pass the realm through an environment variable.

**The tier-extension trap applies here too** (cost 30 min on 2026-09-15): a
bucket READ in the gap between deploy and snapshot caches a payload computed
over the OLD window under the CURRENT generation key, and nothing detects it
because the payload is well-formed and on the right `captured_on`. Order is
deploy → snapshot → warm/read.

**Why the force-warm must cover all 20 buckets, warm or not.** The fresh key is
`ships-by:{mode}:win{window_end}:…` (`_ships_by_fresh_cache_key`,
`data.py:6752`) — tagged on the window's END date, **not its length**. Two
consequences:

1. A rebuild that lands on the same `captured_on` does **not** rotate the key,
   so a payload a viewer cached at the old window in the deploy→snapshot gap
   survives the rebuild.
2. `warm_realm_ships_pct_task` skips any bucket whose fresh key already exists
   (`ship_pct_bucket_cache_key`), so the nightly warmer will **not** repair it —
   it will read the poisoned key as proof the bucket is warm.

`use_cache=False` writes through `_store_realm_ship_cache` unconditionally,
which is the overwrite. Do not substitute a warm-state check for it.

## Step 4 — Verification

- `/etc/battlestats-server.env` carries `SHIP_LEADERBOARD_WINDOW_DAYS=90` and
  `SHIP_BADGE_MIN_BATTLES=20`.
- A `/ship` board reports `window_days=90`. Allow 15 minutes for the board's own
  Redis read-cache, which a snapshot rebuild does not invalidate.
- Board coverage rises against the 60d baseline (na 349 / eu 403 / asia 397 at
  the 60d rollout, before T11). A materially LOWER count means the rebuild ran
  before the env pin reached the worker.
- `ship_pop_rollup_covers_window` still `True` on all three realms after the
  snapshot advances `captured_on`.
- Player page: the Activity tab shows Day / Week / Month / **90d** and a 90-bar
  strip; the footer reads 5.11.0.
- No badge holder shows fewer than 20 battles.

## The pct warmer: measured, not assumed

`warm_realm_ships_pct_task` walks every tier×type bucket serially — 20 buckets
since T11, soft limit 1620s, hard 1800s, lock TTL 2400s. The three most recent
full na runs were **1219s, 1263s, 1330s** (19 warmed, 1 skipped), i.e. 75-82% of
the soft limit before this widen. The obvious fear was that 90d adds ~50% of
scan on top and blows both limits.

**It does not.** Interleaved A/B on prod, na T10 Destroyer at `wr_pct=50`, cache
write stubbed so the run could not poison live keys:

| Window | Runs | Median |
|---|---|---|
| 60d | 98.2s, 90.6s, 58.5s | **90.6s** |
| 90d | 82.0s, 104.6s, 61.2s | **82.0s** |

The ranges overlap completely and the 90d median is the lower of the two: the
bucket's cost is dominated by contention and by the per-(ship, player) pooling
over the snapshot's player set, **not** by the number of days scanned. The
all-view is irrelevant either way at 0.6s per bucket.

So: no soft-limit raise ships with this rollout. Read the first two nights
against the 1219-1330s baseline instead.

### The strip fetch is the other thing the widen taxes, and it is free

`STRIP_FETCH_WINDOW` moved 75 → 90, and unlike the warmer this runs on the
**request thread, on every player-card mount**. Measured on prod the same day,
`_build_battle_history_payload(player, 'daily', w, 'random')`, three interleaved
runs each after the first (cold) call:

| Player | 75d | 90d |
|---|---|---|
| Wara39 (na) | 11ms | 14ms |
| lil_boots (na) | 12ms | 10ms |

The daily-rollup read is indexed on `(player, date)` and a player's row count
over the extra fortnight is trivial; the widen is inside the noise. The payload
does grow (Wara39: 40 → 50 non-empty days, 171 → 220 battles), which is the
point of the change.

```
ssh root@battlestats.online 'journalctl -u battlestats-celery-background --since "2 days ago" \
  --no-pager -o cat | grep -E "warm_realm_ships_pct_task\[[^]]+\] succeeded" | tail'
```

## Rollback

The standings half is two env values read at task-call time: set them back to
60/20, redeploy the backend, re-run the three snapshots. No code revert.

The timeline half is code. Reverting it means reverting the release commit and
redeploying the client. Note that the retired pills are still ACCEPTED by the
backend (`fortyfive`/`sixty`/`seventyfive` stay in `BATTLE_HISTORY_WINDOWS` for
bookmarked URLs), so a revert of the client alone is coherent.

## Known traps

- **The mislabelling window** between the env pin and the snapshot rebuild.
- **The rollup gate is silent.** A missing agg day costs minutes per bucket and
  raises nothing.
- **The queued warm lags**; warm synchronously.
- **`/ship` has its own 15-minute Redis read-cache.**
- **A stale timeline pref names a retired pill.** `isStickyWindow` gates on
  `VISIBLE_WINDOWS`, so `sixty` and `seventyfive` fall back to Month rather than
  stranding a reader on a pill that no longer renders. Covered by a test.
  The stale key is **not cleared**, deliberately: clearing it would need a
  migration pass over every scope in a reader's localStorage to remove a value
  that already reads as absent. The one consequence to remember is that a
  retired name re-entering `VISIBLE_WINDOWS` would resurrect old picks rather
  than start those readers at the default.

## Unverified

- Post-rollout board coverage at 90d is not predicted here. The 75d spike
  measured +25/+28/+23 ships at 60 → 75 with floor 20; 90d should exceed that,
  but the first rebuild is the measurement.
- Per-realm snapshot runtimes at 90d. At 60d they ran ~130-215s (na),
  ~360-460s (eu), ~110-220s (asia).

## Related

- `archive/runbook-ship-standings-60d-rollout-2026-08-18.md` — the procedure this forks.
- `runbook-ship-leaderboard-window-30d-2026-06-29.md` — the **Current value**
  banner, authoritative for the live window.
- `runbook-ship-standings-tier7-spike-2026-09-07.md` — the floor-30 measurement.
- `runbook-ship-standings-tier-extension-2026-09-15.md` — the read-before-snapshot
  trap, and the pct-warmer cost model.
- `runbook-ship-leaderboard-architecture-2026-06-18.md` — the pipeline this
  perturbs.
