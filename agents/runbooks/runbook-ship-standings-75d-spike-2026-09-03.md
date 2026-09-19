# Runbook — Ship-standings 60d vs 75d spike, and the decision to wait for 90d (2026-09-03)

_Created: 2026-09-03_
_Context: with 75 days of battle history in the store, the question came up whether to widen `SHIP_LEADERBOARD_WINDOW_DAYS` 60→75 now, with no other changes, or hold for the 90d end state. A read-only spike recomputed the per-ship top-player boards at both windows on production and diffed them._
_QA: every figure below is measured on the production database on 2026-09-03 with the live thresholds; nothing was written. Re-run the script in Validation to refresh._

## Purpose

Records what a 15-day widen does to board coverage, pool depth, and leader
stability, and the decision that followed: **skip 75d; go 60→90 in one step
on or after 2026-09-11.** Read this before the 90d rollout, and before any
future proposal to advance the window in small steps.

## Method

`server/scripts/spike_ship_board_window.py` mirrors the ranking in
`compute_ship_top_player_snapshot` (`server/warships/data.py`) in memory:
same target set (badge tiers ∪ treemap top-25), same floors, prior, weights,
and win-rate gate, read from the live env. It computes both windows from the
same anchor (today) and reports coverage, depth, and overlap. Runs realms
sequentially; one `BattleEvent` group-aggregate per (realm, window).

Live thresholds at the time, as read by the script from `/etc/battlestats-server.env`
on the droplet on 2026-09-03 (window and floor pinned by `set_env_value` in
`server/deploy/deploy_to_droplet.sh`): window 60, `SHIP_BADGE_MIN_BATTLES` 20,
population 20 (CV 10, sub 12), tiers 8-10, prior 50 @ 0.5, weights
0.6/0.25/0.15, `SHIP_BADGE_MIN_WIN_RATE` 50. Earliest `BattleEvent` on every
realm is 2026-06-13, so 75d was fully covered and 90d was not.

## Findings

### Coverage: about 7% more ships, none lost

| realm | ships ranked 60d → 75d | new | lost | live snapshot (60d) |
|---|---|---|---|---|
| na | 345 → 370 | 25 | 0 | 345 |
| eu | 394 → 422 | 28 | 0 | 394 |
| asia | 396 → 419 | 23 | 0 | 396 |

A widen is monotone: every player's window total can only grow, so no ship
falls under the population floor. The live snapshot matched the modelled 60d
count exactly on all three realms, which calibrates the instrument.

Newcomers are almost entirely T8/T9 cruisers and destroyers whose pools sat at
15-19 and cleared 20 (typically to 20-30), plus a few carriers clearing the
CV floor of 10. Submarines gained nothing on any realm. Marginal ships (pool
within 3 of the floor) stayed flat at 11-19 per realm: the widen moves the
edge, it does not remove it.

### Depth: pools 30-40% deeper, floor-adjacent rows thinner

| realm | pool median | #1 battles median | ranked rows within 5 battles of floor |
|---|---|---|---|
| na | 74 → 96 | 51 → 60 | 21% → 19% |
| eu | 121 → 169 | 61.5 → 64.5 | 20% → 17% |
| asia | 104 → 143 | 59 → 65 | 19% → 16% |

Ranked rows total: na 5047 → 5440, eu 5834 → 6246, asia 5797 → 6180. Boards
shorter than 15 rows after the win-rate gate: roughly flat (29→26, 18→22,
31→29). Minimum #1 sample stayed at the floor (20) on every realm.

### Leaders: not preserved

| realm | same #1 | same top-3 set | mean top-3 overlap | top-15 Jaccard (median) | badge-tier #1 flips |
|---|---|---|---|---|---|
| na | 73% | 39% | 2.21 / 3 | 0.7 | 92 |
| eu | 69% | 32% | 2.14 / 3 | 0.6 | 122 |
| asia | 71% | 39% | 2.21 / 3 | 0.6 | 116 |

Many #1 flips are between low-sample records near the floor (EU Minnesota:
21 battles at 71% → 34 at 62%; ASIA Somers: 27 at 67% → 20 at 90%). The
composite is still sample-sensitive at 20 battles; the extra 15 days add
pool depth but also 15 more days of eligible hot streaks. This churn is the
cost of any step change in the window, not something specific to 75d.

### Cost and prerequisites

- Aggregation cost rose 15-85%: na 171s → 201s, eu 154s → 159s, asia 67s →
  124s (the asia pair is noisy). Comparable to the nightly snapshot's own cost.
- `ship_pop_rollup_covers_window(realm, 'random', today-75d, today)` returned
  True on all three realms, so the tier×type ship-list buckets would have
  stayed on the fast rollup path. No `ShipPopDailyAgg` backfill was needed
  (contrast the 45→60 rollout, where EU lacked one day and every bucket fell
  to the raw scan).
- Method gotcha: `BattleEvent.objects.aggregate(Min('detected_at'))` joined on
  player realm is a full scan, roughly 8 minutes per realm. The saved script
  uses an indexed `exists()` probe instead.

## Decision

**Wait for 90d. Do not ship 75d.** Reasoning, in the operator's framing (pool
as deep as possible, disruption from a changing window as small as possible):

1. Disruption is per step, not per day. Each window change reshuffles about
   30% of #1s and two thirds of podiums; 60→90 in one step will not be much
   worse than 60→75. Two steps in eight days would be two reshuffles, and the
   last one (45→60) landed 2026-08-19.
2. 75d is only the intermediate stop. Extrapolating the measured growth, 90d
   lands near pool medians na ~120 / eu ~200 / asia ~175, versus 96/169/143
   at 75d. Only the 90d step realises the pool the 105-day retention was
   sized for.
3. The cost of waiting is eight days of the current board; 90d becomes fully
   covered around 2026-09-11.

After 90d, further depth should come from the battle floor and the shrinkage
prior, not from more window changes.

## Open question for the 90d rollout

The 45→60 rollout paired the widen with `SHIP_BADGE_MIN_BATTLES` 15→20 so the
per-day bar stayed constant (0.333 games/day). Applying the same rule at 90d
gives a floor of 30, which cuts pools and works against the depth goal.
This spike held the floor at 20. **Decide the floor explicitly before the
90d rollout, and run the spike at both floors so the choice is measured.**
Env pin: `set_env_value SHIP_BADGE_MIN_BATTLES` in
`server/deploy/deploy_to_droplet.sh`.

## Validation

Re-run the spike (read-only, ~10-15 minutes for three realms, sequential):

```bash
ssh -o ConnectTimeout=15 root@battlestats.online \
  'cd /opt/battlestats-server/current/server \
   && set -a && . /etc/battlestats-server.env 2>/dev/null && . /etc/battlestats-server.secrets.env 2>/dev/null && set +a \
   && SPIKE_WINDOWS=60,90 /opt/battlestats-server/venv/bin/python manage.py shell' \
  < server/scripts/spike_ship_board_window.py 2>&1 | grep --line-buffered -v "Loading environment"
```

`SPIKE_WINDOWS` takes the two windows to compare; `SPIKE_REALMS` limits the
realms. To model a different floor, prefix `SHIP_BADGE_MIN_BATTLES=30` after
the env sourcing. `events_reach_90d=YES` on every realm is the go signal for
the data side; then check `ship_pop_rollup_covers_window` for the 90d window
(see `archive/runbook-ship-standings-60d-rollout-2026-08-18.md` for the backfill
call) and follow that runbook's rollout procedure.

## Follow-ups

- [ ] ~2026-09-10: run the 60 vs 90 spike at floor 20 and at floor 30; record
      both in the 90d rollout runbook.
- [ ] Confirm rollup coverage for the 90d window on all three realms before
      flipping `SHIP_LEADERBOARD_WINDOW_DAYS`.
- [ ] Archive this runbook once 90d is live and stable.

## Related

- `archive/runbook-ship-standings-60d-foothold-2026-08-18.md` (floor study, rebuild
  cost, the 2026-06-13 data-depth floor)
- `archive/runbook-ship-standings-60d-rollout-2026-08-18.md` (rollout procedure)
- `runbook-ship-leaderboard-architecture-2026-06-18.md`
- `runbook-ship-badges-rolling-2026-06-14.md` (weights, gate, prior)
