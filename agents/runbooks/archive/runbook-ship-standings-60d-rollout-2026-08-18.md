# Runbook — Rollout: 45d → 60d for the timeline and the leaderboard (2026-08-18)

_Created: 2026-08-18_
_Context: August approved the 60d foothold — one snapshot per day, the player-page timeline and the ship standings both to 60d, floor to 20 games, and no live 45d left anywhere._
_QA: reviewed 2026-08-18 against the tree — 28 assertions checked, 3 corrected; see QA Notes below._

## QA Notes

_Reviewed 2026-08-18 against `/home/august/code/battlestats/.claude/worktrees/sixty-day-window` (linked worktree). 28 assertions checked, 3 corrected._

### Resolved
- **Step 5 omitted the gate that makes the task a no-op** -> actual: `snapshot_ship_top_players_task` returns without computing unless `SHIP_BADGE_SNAPSHOT_ENABLED=1` (`server/warships/tasks.py:1401`; pinned by the deploy at `server/deploy/deploy_to_droplet.sh:445-448`) -> added as an explicit precondition, so a silent run is not misread as a failure.
- **"force-warm the grid" was under-specified, and the obvious way to do it is now wrong** -> actual: `warm_realm_top_ships_task` became a **pure dispatcher** on 2026-08-12 (`server/warships/tasks.py:1570-1578`) — it computes nothing and re-queues the work, which is the exact lag Step 5 exists to avoid -> Step 5 now names the synchronous shell path, both treemap modes, and the **15** tier×type buckets (`_badge_tiers()` 8/9/10 × the five `SHIP_LEADERBOARD_TYPES`, `data.py:6663`), plus the note that `wr_pct=50` materializes the 25% bucket too.
- **"the comment block above it (`signals.py:240-249`)"** -> actual: the block runs `241-249`; line 240 is blank (`server/warships/signals.py:240`) -> corrected.
- **Interpretation picked — the FE `ShipLeaderboard` test fixtures.** Their 45-day bounds (`window_start: '2026-06-18'`, `window_end: '2026-08-02'`, `client/app/components/__tests__/ShipLeaderboard.test.tsx:117`) exist to prove the header *derives* the window rather than hardcoding it, so the literal 45 is arbitrary to the assertion. Moving them to 60-day bounds satisfies "no live 45d anywhere" without weakening the test, since the assertion still compares derived text against the fixture's own span. Recorded in Step 3 rather than left to implementation.

### Unverified
- The per-realm snapshot runtimes quoted in Step 5 (130–215s / 360–460s / 110–220s) are projections from 45d journal measurements, not observations at 60d. The first live rebuild is the measurement.
- The expected post-rollout coverage (na ~346, eu ~402, asia ~398) comes from the model in the study runbook; live counts additionally include the treemap top-25 union, which ran 1–3 ships higher at 45d.
- `SHIP_BADGE_SNAPSHOT_ENABLED` and both new pins were confirmed in the deploy script only. The live `/etc/battlestats-server.env` is read at Step 6, not before.

## Purpose

The execution plan for the decision recorded in
`runbook-ship-standings-60d-foothold-2026-08-18.md`. That runbook is the
evidence; this one is the procedure — what changes, in what order, what breaks
if the order is wrong, and how to prove it landed.

Read it before starting, and again at the verification step. It is written to be
resumable: every step states its own precondition.

## What moves

| # | Lever | From | To | Where |
|---|---|---|---|---|
| 1 | Player-page battle-history window | 45d (`fortyfive`) | 60d (`sixty`) | already committed on this branch |
| 2 | Ship-standings window | 45 | 60 | `SHIP_LEADERBOARD_WINDOW_DAYS`, `server/deploy/deploy_to_droplet.sh:791` |
| 3 | Per-player battle floor | 15 (code default, unpinned) | 20 (new prod pin) | `SHIP_BADGE_MIN_BATTLES` |
| 4 | Snapshot cadence | 2×/day/realm | 1×/day/realm | `server/warships/signals.py:254-256` |

Levers 2 and 3 are a pair. At 60d with the floor left at 15, coverage jumps
(NA 344 → 382 boards) while the thin-sample share of every pool stays at ~49%
— the outcome the study said to avoid. **20@60d is the same bar as 15@45d**
(0.333 games/day), so this is a window widen at constant quality, not a
tightening. Do not ship one without the other.

The population guard stays at 20 (CV 10, sub 12). It only needed relaxing under
the floor-25 option, which is not what shipped.

## Preconditions

- Rollup depth ≥ 60 days. `BattleEvent` and `PlayerDailyShipStats` both start
  2026-06-13; today is 2026-08-18, so 66 days. **Met.**
- The branch `worktree-sixty-day-window` is rebased on main and green
  (`tsc` clean, 41 FE tests, 173 backend tests as of the rebase).
- `main` and the worktree are both at VERSION 5.3.10.

## Step 1 — Cadence: two runs a day become one

`server/warships/signals.py:254-256`:

```python
hour=f"{realm_hour},{(realm_hour + 12) % 24}",   # ->  hour=str(realm_hour)
```

Firing hours become **na 02:30 / eu 06:30 / asia 10:30 UTC**, still striped and
still pairwise distinct.

Three things move with it, and skipping any of them leaves a contradiction in
the tree:

1. The comment block above it (`signals.py:241-249`) explains a mod-12
   distinctness requirement that **no longer applies** under a 24h period, and
   calls these "~12s aggregations" — a figure measured at a 14d window against a
   3.18M-row `BattleEvent`, now 14.4M rows at 45d and 100–350s per run. Rewrite
   both claims.
2. The `PeriodicTask` `description` (`signals.py:271`) says "every-12h recompute
   of the trailing N-day board".
3. **`server/warships/tests/test_periodic_schedule_topology.py:204-255`** —
   class `ShipSnapshotFiresTwiceADayTests`, whose
   `test_ship_snapshot_fires_twice_per_day` asserts exactly two comma-separated
   hour segments 12h apart. It will fail. Rename the class, invert the
   assertion to one firing per realm per day, and keep
   `test_ship_snapshot_firings_dont_collide_across_realms` — under a 24h period
   it still guards the striping property, and its comment about eu/asia
   colliding mod 12 becomes historical.

Schedule rows are registered from `post_migrate`, so the change lands when the
deploy runs `manage.py migrate --noinput`
(`server/deploy/deploy_to_droplet.sh:862`). The superseded `CrontabSchedule`
row is left orphaned by `get_or_create`; harmless, since `update_or_create`
repoints the `PeriodicTask`.

## Step 2 — The two prod pins

`server/deploy/deploy_to_droplet.sh`:

- **:791** `set_env_value SHIP_LEADERBOARD_WINDOW_DAYS 45` → `60`, and its
  comment block (:785-790) still reads "Raised 30 -> 45 on 2026-07-24 as the
  first foothold" — extend the history rather than replacing it.
- **new line** `set_env_value SHIP_BADGE_MIN_BATTLES 20`, adjacent to the window
  pin with a comment naming why the two move together (rate equivalence above).
  This variable has never been pinned; the code default 15 was live.

Both are read at task-call time, so no code default changes.

## Step 3 — Docs: no live 45d anywhere

Historical prose stays historical — "45d shipped v4.4.0", the retention
timeline, this tranche's own before/after tables. What must not survive is a
doc stating 45 as the **current** value.

| File | What changes |
|---|---|
| `agents/runbooks/ops-env-reference.md:144-145` | "45d (live) and 60d are stepping stones" → 60d live; the UI-window cap list "week/month/45d/year" → 60d |
| `agents/runbooks/ops-env-reference.md:159` | `SHIP_BADGE_MIN_BATTLES` (15) → prod pins **20** since 2026-08-18; code default 15 |
| `agents/runbooks/ops-env-reference.md` | **Gap:** no entry exists for `SHIP_LEADERBOARD_WINDOW_DAYS` at all, though it is prod-pinned. Add one. |
| `agents/runbooks/runbook-battle-history-archive-prune-2026-06-17.md:29` | UI-window cap list "week/month/45d/year" → 60d |
| `agents/runbooks/runbook-ship-list-rollup-source-2026-08-14.md:4,20,62,86,92` | the roadmap line "45 today, a stop at 60 soon" is now "60 since 2026-08-18"; the rest are historical measurements — leave, but date them |
| `agents/runbooks/runbook-ship-leaderboard-window-30d-2026-06-29.md` | add a superseded-by pointer to this tranche |
| `agents/runbooks/runbook-ship-leaderboard-architecture-2026-06-18.md` | its threshold list still says floor 15 / weights 0.5-0.35-0.15 (already stale before this change) |
| `agents/work-items/{battle-history-window-bracket-plan,battle-history-window-bracket-spec,client-locale-toggle-spec,data-capture-utility-audit-2026-08-05,db-growth-capacity-2026-08-05,doc-estate-findings-2026-08-15,i18n-terminology-research}.md` | 45d mentions; most are historical context — reconcile only the ones asserting a live value |
| `server/warships/data.py:7326,7342,7377` · `client/app/components/ShipLeaderboard.tsx:651` | comments naming prod 45 |
| `client/app/components/__tests__/ShipLeaderboard*.test.tsx` | fixtures use 45-day bounds to prove the header *derives* its window; move to 60-day bounds so no 45 remains, keeping the derivation assertion intact |

`CLAUDE.md` contains no 45 reference; nothing to do there.

## Step 4 — Gate, version, deploy

1. Release gate (`./run_test_suite.sh`, or the `release-gate` skill). A patch may
   skip it; this one touches a scheduler, two prod pins, and a test class, so
   run it.
2. Merge the branch to `main`. Verify HEAD before merging — a fast-forward from
   the root checkout has hijacked a branch here before.
3. `./scripts/release.sh patch` → **5.3.11**. Both main and the worktree read
   5.3.10 now; confirm before running, since the script bumps the local VERSION
   blind.
4. `./server/deploy/deploy_to_droplet.sh battlestats.online` — carries both env
   pins and runs the migrate that re-registers the schedule.
5. `./client/deploy/deploy_to_droplet.sh battlestats.online` — **mandatory after
   every bump**, backend-only changes included: `NEXT_PUBLIC_APP_VERSION` is
   captured at build time, and this release also carries the 60d timeline.

## Step 5 — Make the new window real

The env pin alone does not move a single board: every standings surface is
served from the snapshot, and the snapshot is only rebuilt on its schedule.
Until it is rebuilt, boards keep serving 45d data **relabelled 60d** — the same
mislabelling failure the rollup runbook records at 40d/45d.

**Precondition:** `SHIP_BADGE_SNAPSHOT_ENABLED=1`, or the task returns without
computing anything (`tasks.py:1401`). The deploy pins it
(`deploy_to_droplet.sh:445-448`), so this holds after Step 4 — but check it
before concluding a silent run was a failure.

For each realm in `na`, `eu`, `asia`, one at a time:

```
snapshot_ship_top_players_task.apply_async(args=[realm], queue='background')
```

Then **force-warm the grid directly** rather than waiting for the chained warm —
during the 45d rollout the queued warm sat behind a backlogged `background`
queue for 20+ minutes while the landing treemap served the old window.

Note that `warm_realm_top_ships_task` has been a **pure dispatcher** since
2026-08-12 (`tasks.py:1570-1578`): it computes nothing and fans the work back
onto the same queue, which is exactly the lag being avoided. So warm
synchronously in a droplet shell instead — both treemap modes, then all
**15 tier×type buckets** (`_badge_tiers()` = 8/9/10 × the five
`SHIP_LEADERBOARD_TYPES`), T10 first:

```
compute_realm_top_ships(realm, limit=25, mode='random')   # and mode='ranked'
compute_realm_ships_by_tier_type(realm, tier, ship_type, wr_pct=None, use_cache=False)
compute_realm_ships_by_tier_type(realm, tier, ship_type, wr_pct=50,  use_cache=False)
```

The `wr_pct=50` call materializes both the 50% and 25% buckets. Budget
~20–135s per bucket under load.

Expect roughly 130–215s (na), 360–460s (eu), 110–220s (asia) per snapshot at
60d. `manage.py shell < file` sets `argv[1]='shell'`, so pass the realm through
an environment variable, never `sys.argv`.

## Step 6 — Verification

- `/etc/battlestats-server.env` carries `SHIP_LEADERBOARD_WINDOW_DAYS=60` and
  `SHIP_BADGE_MIN_BATTLES=20`.
- Each realm's `ship-top-player-snapshot-{realm}` `PeriodicTask` has a **single**
  crontab hour, and the three are distinct.
- A `/ship` board reports `window_days=60` — allow up to 15 minutes for its
  Redis read-cache to turn over after a rebuild.
- Board coverage lands near the modelled numbers: **na ~346, eu ~402,
  asia ~398** ranked ships. A materially lower count means the rebuild ran
  before the env pin reached the worker.
- Player page: the Activity tab shows a `60d` pill and a 60-bar strip; the
  footer reads 5.3.11.
- No badge holder shows fewer than 20 battles.

## Rollback

Both pins are env values read at task-call time: set them back to 45/15, redeploy
the backend, and re-run the three snapshots — no code revert needed for the
standings half. The cadence and the timeline rename are code, so reverting those
means reverting the release commit and redeploying both halves.

## Known traps

- **The mislabelling window.** Between the env pin landing and the snapshot
  rebuild finishing, boards are 45d data labelled 60d. Keep Step 5 immediately
  after Step 4; do not stop at the deploy.
- **The queued warm lags.** Force-warm the grid; do not trust the chain.
- **`/ship` has its own 15-minute Redis read-cache** that a snapshot rebuild does
  not invalidate.
- **The floor and the window are a pair.** Shipping the window alone leaves the
  board wider *and* thinner.

## Related

- `runbook-ship-standings-60d-foothold-2026-08-18.md` — the measurements behind
  every number here.
- `runbook-ship-leaderboard-architecture-2026-06-18.md` — the pipeline this
  rollout perturbs.
- `runbook-ship-list-rollup-source-2026-08-14.md` — the 40d-labelled-45d
  mislabelling precedent that Step 5 exists to prevent.

## Outcome — executed 2026-08-19 (UTC), v5.3.11

Released `v5.3.11`, both halves deployed, all six verification checks passed.

| Check | Result |
|---|---|
| Env pins | `SHIP_LEADERBOARD_WINDOW_DAYS="60"`, `SHIP_BADGE_MIN_BATTLES="20"` live in `/etc/battlestats-server.env` |
| Beat cadence | one hour per realm — na `2`, eu `6`, asia `10`, minute `30`, descriptions read "daily recompute of the trailing 60-day board" |
| Snapshots | window `2026-06-20..2026-08-19` (60d) on all three realms — na 349 ships / 5,102 rows (120.7s), eu 403 / 5,962 (451.5s), asia 397 / 5,846 (205.4s) |
| Coverage vs model | na 349 (predicted ~346), eu 403 (~402), asia 397 (~398) — inside the treemap-union margin |
| Floor | Shimakaze NA board: 15 rows, minimum 22 battles — no row under 20 |
| Surfaces | `/ship` board, landing treemap (`limit=25`), and the player timeline all report `window_days=60`, `captured_on=2026-08-19`; footer 5.3.11 |

### Two things worth knowing for the next widen

1. **The EU rollup lost the fast path at the moment of the widen.**
   `ship_pop_rollup_covers_window` (`data.py:7367`) requires a row for **every**
   date in the half-open window. Widening to 60d pulled `2026-06-20` into the
   window, and EU had no `ShipPopDailyAgg` row for that day, so *every* EU
   tier×type bucket silently fell back to the raw `BattleEvent` scan — correct
   but minutes per bucket, with the warmer's 30-minute budget in front of it.
   Fixed by rebuilding the one realm-day: `rollup_ship_pop_daily('eu', date(2026,6,20))`
   → 164,733 source rows into 1,333 agg rows in 43.9s, after which all three
   realms returned `covers_window: True` and buckets dropped to ~50–200s.
   **Check `ship_pop_rollup_covers_window` for all three realms BEFORE the next
   widen, not after.** The gap is at the *oldest* edge of the new window, which
   is exactly the day a widen newly requires.

2. **The queue cannot be trusted for the rebuild.** The EU snapshot dispatched to
   `background` was never picked up — the queue was 26 deep with 3 unacked
   behind other work. Revoked it and ran `compute_ship_top_player_snapshot`
   synchronously on the droplet instead. For a rollout, go synchronous from the
   start; the queue is for the nightly path.

All 30 tier×type buckets across eu + asia warmed to completion (na was already
current through its own chain); final sweep confirms `window_days=60`,
`captured_on=2026-08-19` on na T10, eu T8, and asia T8/T9 buckets. Nothing left
pending.
