# Runbook — Tier-7 ship-standings spike: is T7 deep enough at 90d? (2026-09-07)

_Created: 2026-09-07_
_Context: the 90d window becomes fully covered around 2026-09-11. The question ahead of that cutover is whether the widened window also buys enough depth to extend the standings down a tier: `SHIP_BADGE_TIERS` has been 8,9,10 since the badge feature shipped._
_QA: every figure is measured on the production database on 2026-09-07 (22:2x UTC, outside the 02:00 snapshot stripe) with the live thresholds; nothing was written. Re-run with the script in Validation._

## Purpose

Answers "does T7 have the population to carry a board" with measured depth, not
a ship count, by ranking T7 and T8 side by side under identical thresholds.
Read this before flipping `SHIP_BADGE_TIERS`, and alongside
`runbook-ship-standings-75d-spike-2026-09-03.md`, which owns the window
decision and left the battle floor open.

## Method

`server/scripts/spike_ship_board_tier7.py` recomputes
`compute_ship_top_player_snapshot`'s ranking in memory — same prior, weights,
population floors and win-rate gate, read from the live env — and reports every
depth statistic **split by tier**, so T7 is judged against T8 (the shallowest
tier currently shipped) rather than against an absolute count. Differences from
the sibling window spike:

- **No treemap union.** `compute_ship_top_player_snapshot` unions the top-25
  treemap ships into its target set; those are overwhelmingly T10 and would
  contaminate a per-tier split. Targets here are exactly `Ship.tier in {7,8}`:
  110 T7 hulls, 200 T8 hulls.
- **Both battle floors from one aggregate.** The grouped query runs at
  `battles >= 20` and the floor-30 view re-filters those same rows in Python,
  which returns exactly what a second aggregate would. This answers the 75d
  spike's open floor question for these two tiers at no extra DB cost.
- **85d is a proxy for 90d.** Earliest `BattleEvent` on every realm is
  2026-06-13, so on 2026-09-07 only 86 days exist. B=85 is the deepest honest
  window today. Sizing the gap: 60→85 is a 42% widen and moved T7 pool medians
  +15% (na), +13% (eu), +21% (asia); the remaining five days are worth low
  single digits, not a change of verdict.

Live thresholds as read from `/etc/battlestats-server.env`: window 60,
`SHIP_BADGE_TIERS=8,9,10`, `SHIP_BADGE_MIN_BATTLES=20`, population 20 (CV 10,
sub 12), prior 50 @ 0.5, weights 0.6/0.25/0.15, `SHIP_BADGE_MIN_WIN_RATE=50`,
list size 15.

## Findings

### T7 at 85d lands at the depth T8 has today

The decisive comparison is not T7 vs T8 at the same window; it is **T7 at the
proposed window vs T8 at the window we actually ship**, because T8's live board
is the quality bar the product has already accepted.

| realm | T7 @85d ranked | pool med | T8 alone @60d ranked | pool med | T7 as % of that bar |
|---|---|---|---|---|---|
| na | 66/110 (60%) | 57 | 100/200 (50%) | 55 | 104% |
| eu | 84/110 (76%) | 84.5 | 126/200 (63%) | 91.5 | 92% |
| asia | 66/110 (60%) | 64.5 | 123/200 (62%) | 72 | 90% |

**Read the denominator carefully.** That bar is **T8 alone**, measured with this
instrument (no treemap union, T8 targets only). It is NOT the pooled 60d medians
of 74 / 121 / 104 recorded in the 75d spike, which average T8, T9 and T10
together plus the treemap ships and are therefore dominated by the deep T10
population. The claim here is "T7 at 90d matches T8 as it ships today," not
"T7 matches the board as a whole."

Against T8 measured at the *same* 85d window, T7 pools run 74-79% as deep
(na 57 vs 72, eu 84.5 vs 107, asia 64.5 vs 86), but the 25th percentile — the
thin end, where boards actually get bad — is within 10%: T7 35/39/37 vs T8
36/44/44. The tier is shallower in its head, not in its tail.

Other quality axes at 85d/floor 20, T7 vs T8:

| axis | na T7 / T8 | eu T7 / T8 | asia T7 / T8 |
|---|---|---|---|
| #1's battles, median | 50.5 / 55 | 59.5 / 65 | 40.5 / 52 |
| ranked rows within 5 of the floor | 32% / 25% | 27% / 20% | 28% / 21% |
| short boards (<15 rows after the WR gate) | 7/66 / 17/123 | 9/84 / 16/146 | 4/66 / 13/138 |
| boards emptied by the WR gate | 0 | 0 | 0 |
| marginal ships (pool ≤ floor+3) | 4 | 9 | 3 |

asia's T7 #1-battles median falls 46.5 → 40.5 from 60d to 85d. That is
dilution, not decay: the widen ranks ten more ships, and the newcomers arrive
with shallower leaders that pull the median down. Every other realm rises.

Short boards are proportionally *fewer* on T7 (6-11% vs 9-14%): the T7 ships
that clear the population floor clear it with a real pool behind them. The one
axis that is consistently worse is rows near the battle floor (≈7 points more),
which is the same sample-sensitivity that flips roughly 30% of #1s per window
step. Expect T7 leaders to churn slightly more than T8's.

### Volume: about a fifth more ranked ships

T7 adds 66 / 84 / 66 boards (na / eu / asia) against the 345 / 394 / 396 ships
ranked at the live 60d snapshot — **+19% / +21% / +17%**. Roughly 40 T7 hulls
per realm stay unranked: 105-107 of the 110 have *some* qualifying pool, but
only 60-76% clear the population floor of 20. A T7 tier×type bucket page will
therefore list fewer ships than its T8 equivalent, and premium/event hulls with
thin populations will be absent.

### The floor decision decides T7

The 75d spike left `SHIP_BADGE_MIN_BATTLES` open: holding at 20, or raising to
30 to keep the per-day bar constant across the widen. T7 is where that choice
bites hardest.

| realm | T7 @85d floor 20 | T7 @85d floor 30 | T8 @85d floor 20 | T8 @85d floor 30 |
|---|---|---|---|---|
| na | 66 (pool med 57) | 44 (49) | 123 (72) | 90 (46.5) |
| eu | 84 (84.5) | 66 (53) | 146 (107) | 125 (66) |
| asia | 66 (64.5) | 52 (46) | 138 (86) | 118 (54) |

Floor 30 deletes a third of the T7 boards on na and a fifth elsewhere, and cuts
pool medians below what the tier has *today* at 60d/floor 20. It costs T8 as
well (na 123 → 90). **Floor 20 is indicated on both tiers**; raising it works
directly against the depth the 90d widen exists to buy.

### Cost

Aggregate cost for the T7+T8 target set (310 ships), one grouped `BattleEvent`
query per realm-window: na 94.6s @60d / 78.9s @85d, eu 66.0 / 60.6, asia 60.6 /
67.3. For scale, the 75d spike measured the live T8-10 target set at 171-201s
(na), so T7's 110 hulls are not the expensive part of the snapshot; the T10
population is. Two second-order costs matter more:

- The nightly snapshot's target set grows by 110 hulls per realm (~+22%).
- Every tier×type warm loop goes 15 → 20 buckets per realm, for both the
  all-view and the percentile view (`warm_realm_ships_pct_task`, 27-minute soft
  limit, 5s inter-bucket pause). That warmer has soft-limited before; it is the
  binding operational constraint on this change, not the snapshot.

## Decision (recommendation, not yet taken)

**T7 is deep enough at floor 20. Ship it AFTER the 90d cutover, not with
it.** Reasoning:

1. T7 at 90d sits at 90-104% of T8's current depth on all three realms, and its
   thin end (pool p25) is within 10% of T8's. The product has already accepted
   boards at that depth.
2. The two changes load the same instrument in different ways and must be
   separable. Adding T7 puts no churn on existing boards (the T8-10 target sets
   are untouched), but it takes every tier×type warm loop from 15 to 20 buckets
   per realm per view — on a warmer that has soft-limited before. Flip the
   window, confirm two clean warm nights, then flip the tier: if the warmer
   soft-limits, the cause is unambiguous. One lever at a time.
3. Sequencing costs nothing. T7's coverage is population-bound, not
   window-bound, past 90d: it will read the same in a week.
4. Do NOT pair the tier extension with a `SHIP_BADGE_MIN_BATTLES` rise to 30.
   That combination ships a tier and then guts it in the same breath.

Blast radius if taken: `SHIP_BADGE_TIERS=7,8,9,10` at
`server/deploy/deploy_to_droplet.sh:481` opens the API and both warmers
automatically (`views.py:1959` gates on `_badge_tiers()`); the frontend needs
`SHIP_BUCKET_TIERS` at `client/app/lib/entityRoutes.ts:81`, which propagates to
`ShipLeaderboard.tsx`, the sitemap, and the OG cards — 15 indexable
`/ships/[bucket]` routes become 20.

## Validation

Re-run (read-only; measured 2.5-3 min per realm for both windows, sequential):

```bash
ssh -o ConnectTimeout=15 root@battlestats.online \
  'cd /opt/battlestats-server/current/server \
   && set -a && . /etc/battlestats-server.env 2>/dev/null && . /etc/battlestats-server.secrets.env 2>/dev/null && set +a \
   && SPIKE_WINDOWS=60,85 SPIKE_TIERS=7,8 SPIKE_FLOORS=20,30 /opt/battlestats-server/venv/bin/python manage.py shell' \
  < server/scripts/spike_ship_board_tier7.py 2>&1 | grep --line-buffered -v "Loading environment"
```

`SPIKE_TIERS` takes any tier list (add 6 to test one tier lower);
`SPIKE_WINDOWS` two windows; `SPIKE_FLOORS` two battle floors from one
aggregate. After 2026-09-11, re-run with `SPIKE_WINDOWS=60,90` to replace the
85d proxy with the real thing.

## Follow-ups

- [ ] ~2026-09-11: re-run at `SPIKE_WINDOWS=60,90` and confirm the T7 numbers
      hold. Flip the window first; hold the tier extension for a separate
      change once the warmers have two clean nights.
- [ ] Before flipping: confirm `ship_pop_rollup_covers_window` for 90d on all
      three realms (`archive/runbook-ship-standings-60d-rollout-2026-08-18.md`), and
      note that the rollup coverage gate is per-window, not per-tier — T7 rides
      the same check.
- [ ] If T7 ships: watch `warm_realm_ships_pct_task` for soft-limits on the
      first two nights (20 buckets, 27-minute budget).
- [ ] Archive this runbook once the tier decision is made and stable.

## Related

- `runbook-ship-standings-75d-spike-2026-09-03.md` (window decision; owns the
  floor question this spike answers for T7/T8)
- `archive/runbook-ship-standings-60d-rollout-2026-08-18.md` (rollout procedure,
  rollup-coverage backfill)
- `runbook-shareable-ship-leaderboard-2026-08-20.md` (the `/ships/[bucket]`
  routes that would go 15 → 20)
- `runbook-ship-leaderboard-architecture-2026-06-18.md`
