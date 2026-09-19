# Runbook — Ship-standings tier extension: which tiers can we add? (2026-09-15)

_Created: 2026-09-15_
_Context: the first visitor feedback submission the site has ever received (#6, `feature_suggestion`, en, eu, 2026-09-15T08:27:24Z) asks for more tiers: "Can you add other tiers of ships as well, please? E.g. T11." This runbook answers which tiers the data can actually carry, on the premise — set by the operator on 2026-09-15 — that the leaderboard window moves 60 → 90 days._
_QA: every figure measured on the production database on 2026-09-15/16 UTC with the live thresholds, via `server/scripts/spike_ship_board_tier7.py` (read-only; nothing written). Warmer timings read from the droplet journal for 2026-09-15. Reproduce with the commands in Validation._

## Purpose

Decides which of tiers 6, 7 and 11 can join `SHIP_BADGE_TIERS` (live: `8,9,10`),
at what window, and in what order. It measures depth per tier **and per
tier×type** — the axis the product actually surfaces as `/ships/<bucket>` — and
prices the two operational constraints that gate the change: the
`ShipPopDailyAgg` coverage gate and the percentile warmer's budget.

Read it before flipping `SHIP_LEADERBOARD_WINDOW_DAYS` or `SHIP_BADGE_TIERS`.
It supersedes the tier-only verdict in
`runbook-ship-standings-tier7-spike-2026-09-07.md`, which judged T7 alone
against an 85d proxy; the real 90d window and two further candidate tiers are
measured here.

## Method

`spike_ship_board_tier7.py` recomputes `compute_ship_top_player_snapshot`'s
ranking in memory (same prior, weights, population floors and win-rate gate,
read from the live env) and reports depth split by tier. Two changes from the
2026-09-07 run:

- **90d is real now, not a proxy.** Earliest `BattleEvent` is 2026-06-13, so a
  full 90-day window exists. The 85d proxy is retired.
- **New `SPIKE_BY_TYPE=1` block** reports ranked counts per (tier, ship_type).
  A tier's aggregate depth answers only half the question: every bucket is one
  indexable route and one warm-loop iteration, so a three-hull bucket is a thin
  page no matter how the tier reads in aggregate.

Live thresholds as read from `/etc/battlestats-server.env` on 2026-09-15:
window **60**, `SHIP_BADGE_TIERS=8,9,10`, `SHIP_BADGE_MIN_BATTLES=20`,
population 20 (CV 10, sub 12), prior 50 @ 0.5, weights 0.6/0.25/0.15,
`SHIP_BADGE_MIN_WIN_RATE=50`, list size 15.

Roster by tier and type (production `Ship` table, 2026-09-15):

| tier | BB | CA | DD | CV | Sub | total |
|---|---|---|---|---|---|---|
| 6 | 21 | 33 | 23 | 9 | 6 | 92 |
| 7 | 34 | 49 | 28 | **0** | **0** | 111 |
| 8 | 60 | 70 | 40 | 22 | 8 | 200 |
| 9 | 67 | 63 | 43 | **0** | **0** | 173 |
| 10 | 54 | 73 | 49 | 21 | 15 | 212 |
| 11 | 7 | 8 | 6 | 3 | **0** | 24 |

## Findings

### The bar, and all three candidates clear it at 90d

The quality bar is **T8 as it ships today**: the shallowest tier the product has
already accepted, measured at the live 60d window and floor 20. Candidates are
read at 90d/floor 20.

| realm | metric | T8 @60d (bar) | T8 @90d | T7 @90d | T6 @90d | T11 @90d |
|---|---|---|---|---|---|---|
| na | ranked ships | 98/200 | 125/200 | 67/111 | 73/92 | 20/24 |
| na | pool median | 56.5 | 74 | 60 | 56 | 55.5 |
| na | pool p25 | 35 | 37 | 36 | 31 | 42 |
| na | rows within 5 of floor | 28% | 26% | 32% | 29% | 19% |
| na | short boards (<15) | 13/98 | 18/125 | 8/67 | 9/73 | 3/20 |
| eu | ranked ships | 126/200 | 150/200 | 85/111 | 82/92 | 23/24 |
| eu | pool median | 87.0 | 111.0 | 86 | 104.5 | 175 |
| eu | pool p25 | 42 | 47 | 41 | 52 | 109 |
| eu | rows within 5 of floor | 26% | 20% | 27% | 24% | 12% |
| eu | short boards | 9/126 | 13/150 | 9/85 | 2/82 | 0/23 |
| asia | ranked ships | 124/200 | 141/200 | 66/111 | 70/92 | 23/24 |
| asia | pool median | 65.0 | 82 | 74.5 | 87 | 98 |
| asia | pool p25 | 34 | 45 | 38 | 30 | 55 |
| asia | rows within 5 of floor | 23% | 21% | 27% | 27% | 18% |
| asia | short boards | 16/124 | 11/141 | 1/66 | 9/70 | 2/23 |

Against the live bar, pool medians land at: T7 **106% / 99% / 115%**, T6
**99% / 120% / 134%**, T11 **98% / 201% / 151%** (na / eu / asia). No board is
emptied by the win-rate gate on any tier, realm or window.

Two honest qualifications. Against T8 measured at the *same* 90d window the
candidates are shallower (T7 81/77/91%, T6 76/94/106%, T11 75/158/120%), because
the widen lifts T8 too. And na is the thin realm on every candidate; eu is the
deep one.

**T11 is the strongest candidate on every quality axis except raw count.** Its
floor-adjacent row share is the lowest measured anywhere (12-19% against T8's
20-26%), its pools are the deepest relative to the bar, and 20-23 of its 24
hulls rank. Superships are played by a self-selected, high-engagement
population; that is exactly the shape a 15-row board wants.

### T11 also clears at the *current* 60d window

Unlike T7 and T6, T11 does not need the widen: at 60d/floor 20 it ranks
17 / 23 / 20 hulls (na / eu / asia) with pool medians 47 / 112 / 68 and
floor-adjacent shares 20% / 15% / 23%, already at or better than T8's live
numbers on eu and asia and 83% of them on na. If the window flip slips, T11
remains shippable; T7 and T6 are the ones whose case rests on 90 days.

### Per type: where the grid breaks

Ranked / roster at 90d, floor 20, na · eu · asia:

| tier | Battleships | Cruisers | Destroyers | Carriers | Submarines |
|---|---|---|---|---|---|
| 6 | 19·20·19 / 21 | 22·26·20 / 33 | 18·21·17 / 23 | 9·9·9 / 9 | 5·6·5 / 6 |
| 7 | 21·28·20 / 34 | 27·35·29 / 49 | 19·22·17 / 28 | **no hulls** | **no hulls** |
| 11 | 7·7·7 / 7 | 6·8·8 / 8 | 4·5·5 / 6 | 3·3·3 / 3 | **no hulls** |

- **T7 ships two structurally empty buckets per realm** (`/ships/t7-carriers`,
  `/ships/t7-submarines`). This is not new: T9 has the same hole today, and
  `ShipLeaderboard.tsx:517-525` short-circuits it to an easter egg. But that
  predicate is hardcoded to `tier === 9`; T7 and T11 would fetch, return empty,
  and render "No ranked ships" on an indexable route.
- **T11 carriers is the one genuinely thin bucket.** Three hulls, and on na
  their pools are tiny (median 11 at 90d, 2 of 3 boards short of 15 rows).
  eu 76 and asia 82 are fine. A three-row page on one realm is the price of the
  tier; the alternative is a per-type exclusion, which nothing in the code
  currently supports.
- **T6 is the only candidate with a complete five-type grid**, and its
  submarine and carrier pools are the deepest in the study (sub pool medians
  250 / 352.5 / 249; CV 45 / 129 / 131). Submarines live at T6; the shipped
  tiers under-represent them (8 hulls at T8, 15 at T10, none at T9).

### Floor 30 remains disqualified

Raising `SHIP_BADGE_MIN_BATTLES` to 30 alongside the widen costs, at 90d:
T7 67→49 boards on na, 85→68 on eu, 66→52 on asia; T6 73→50 / 82→69 / 70→48;
T11 20→17 / 23→23 / 23→19; and it cuts T8 itself 125→95 on na. Pool medians
fall below what each tier has *today* at 60d/floor 20. **Hold floor 20.**
Shipping a tier and gutting it in the same deploy is the failure mode.

### Blocker 1: the 90d rollup coverage gate fails right now

`ship_pop_rollup_covers_window(realm, 'random', today-90, today)` returns
**False on all three realms** (60d returns True). `ShipPopDailyAgg` starts
2026-06-20; a 90-day window opening 2026-06-18 is two days short.

The source rows exist: `PlayerDailyShipStats` holds 79,028 rows for 06-17,
102,208 for 06-18 and 248,613 for 06-19 (earliest PDSS date 2026-06-13). The
rollup was simply created on 06-20.

This is load-bearing, not cosmetic. With the gate False every tier×type bucket
falls back to the raw `BattleEvent` scan (19.19s vs 0.23s on the rollup path,
measured in v5.7.2), on warmers that are already near budget. Run
`rollup_ship_pop_daily_catchup(realm, window_days=90)` on each realm **before**
flipping the env value; it is idempotent and skips days already rolled.

**The gap also closes by itself on 2026-09-18.** The window start marches
forward daily: once it reaches 2026-06-20 the rollup covers it with no backfill
at all. So the catch-up is required only for a flip before 09-18, and optional
after. Flipping first and backfilling second is the one ordering that is wrong,
because every bucket raw-scans until the catch-up lands.

`SHIP_POP_ROLLUP_WINDOW_DAYS` derives from `SHIP_LEADERBOARD_WINDOW_DAYS`, and
`SHIP_POP_ROLLUP_RETENTION_DAYS` is `max(100, window + 15)`, so both follow the
flip automatically to 90 and 105. Battle-history retention (105d prod) already
sustains a 90-day rolling read.

Note the shape of those early days: 102k PDSS rows on 06-18 against 366k on
06-20. The oldest third of the 90d window was captured while the observation
floor was still ramping, so the widen buys ~+61-65% more qualifying rows
(na 18,649 → 30,803; eu 38,413 → 63,266; asia 30,625 → 49,286), not +50% by
day count alone.

### Blocker 2: the percentile warmer, not the all-view warmer, is the constraint

Only one of the two warm loops matters. The all-view loop
(`warm_ships_bucket_task`, one task per bucket) reads the rollup and completed
in **0.02-0.97s per bucket** on asia across 2026-09-15; it would not notice a
fourth tier. The percentile loop is the whole cost.

`warm_realm_ships_pct_task` walks every tier×type bucket serially: 15 buckets
today, soft limit 1620s, hard 1800s, lock TTL 2400s. Measured 2026-09-15 at the
**60d** window:

| realm | duration | buckets warmed |
|---|---|---|
| na | 1334.1s | 14 (1 skipped) |
| eu | 1384.1s | 14 (1 skipped) |
| asia | 1228.2s | 14 (1 skipped) |

That is 76-85% of the soft limit, a mean of ~88-99s per bucket including the 5s
pause. **The mean is not the marginal cost**, and the difference decides the
sequencing. Individually timed, the single heaviest bucket (T10 Battleships, 37
hulls na/eu, 40 asia) ran 20.1 / 23.5 / 32.4 / 50.1 / 74.7 / 168.0s across six
runs since 2026-09-14: a 8x spread on one bucket, driven by DB contention, not
by bucket shape. A new tier's buckets are also smaller than the mean bucket:
per-bucket cost is dominated by hulls x qualifying players, and T11 brings 24
hulls against the 585 already ranked.

Bounding the marginal cost by hull share rather than by bucket count:

| tier added | hulls | share of current target set | est. added warmer time per realm | 15 → n buckets |
|---|---|---|---|---|
| 11 | 24 | +4.1% | ~+60-110s (~+5-8%) | 20 |
| 6 | 92 | +15.7% | ~+200-320s (~+16-24%) | 20 |
| 7 | 111 | +19.0% | ~+250-380s (~+19-29%) | 20 |

So the honest reading is: **T11 fits inside today's budget; T7 or T6 lands at or
past the 1620s soft limit on eu even before the widen.** The earlier "one tier
pushes past the hard limit" estimate assumed added buckets cost the mean, which
the per-bucket timings do not support.

Two unknowns keep this a watch item rather than a settled number. The 90d widen
grows the qualifying row set 61-65% (measured in the spike: na 18,649 → 30,803;
eu 38,413 → 63,266; asia 30,625 → 49,286), and the widen's effect on the warmer
has never been measured. And the 8x contention spread on a single bucket means a
budget that fits on a quiet night can still soft-limit on a busy one.

Therefore: measure the warmer for two nights after the window flip before adding
any tier, and split `warm_realm_ships_pct_task` per tier before T7 or T6. The
codebase's own precedent is fan-out, not a bigger budget: one task per tier,
exactly as `startup_warm` was split in v5.6.1 and the enrichment reclassify in
v5.1.9. Per-tier tasks also make a slow tier cost one tier. If a budget rise is
chosen instead, `soft_time_limit`, `time_limit` and
`SHIP_PCT_WARM_LOCK_TIMEOUT` move together.

### Cost and blast radius per tier

The snapshot target set is 585 hulls today (T8+T9+T10, plus the treemap union):

| tier | hulls added | snapshot target growth | warm buckets added (per realm, per view) | indexable routes added | of which empty |
|---|---|---|---|---|---|
| 11 | 24 | +4.1% | 5 | 5 | 1 (subs) |
| 7 | 111 | +19.0% | 5 | 5 | 2 (CV, subs) |
| 6 | 92 | +15.7% | 5 | 5 | 0 |

Code touched by any tier addition:

- `server/deploy/deploy_to_droplet.sh:481` — `SHIP_BADGE_TIERS`. **One switch
  drives three things**: the board API gate (`views.py:1953`), both warm loops
  (`tasks.py:1706`, `tasks.py:1944`), and the profile **badges** themselves
  (`data.py:6302`, used at 6382 and 6446). Boards without badges, or badges at a
  narrower tier set than boards, is a code change to split `_badge_tiers()`, not
  an env flip.
- `client/app/lib/entityRoutes.ts:81` — `SHIP_BUCKET_TIERS`; propagates to
  `ShipLeaderboard.tsx`, `sitemap.ts` and the OG cards. 15 routes become 20, 25
  or 30.
- `client/app/components/ShipLeaderboard.tsx:517-525` — the structurally-empty
  predicate is hardcoded to tier 9. Generalize it before adding T7 (CV, sub) or
  T11 (sub).
- `client/app/lib/__tests__/entityRoutes.test.ts` iterates `SHIP_BUCKET_TIERS`
  and needs no edit, but its bucket-count assertions do.

### Open product question: what to call tier 11

Wargaming labels these ships **superships** and renders the tier as a star, not
as "XI". A pill reading `11` beside `8 9 10` is defensible and matches our
`Ship.tier` data, but it is not the name players use. Decide the label before
the slug ships: `/ships/t11-battleships` is a public, indexable surface, and
changing it later costs redirects.

## Implementation — T11 shipped 2026-09-15

Step 2 of the recommendation was taken first, ahead of the window flip, because
T11 is the one candidate that clears at the live 60d window. The operator chose
**one env switch, badges included** (T11 #1-#3 holders now carry supership
badges) and the **`11` / `t11-battleships`** label.

What changed:

- `server/deploy/deploy_to_droplet.sh:481,483` — `SHIP_BADGE_TIERS=8,9,10,11`.
  This is the whole backend change: the snapshot target set, the
  `/api/fetch/ships-by-tier-type` gate, both warm loops and the profile badges
  all read it.
- `client/app/lib/entityRoutes.ts` — `SHIP_BUCKET_TIERS` gains 11, and the
  tier-9-shaped knowledge about hull-less buckets becomes `isShiplessBucket`,
  a single exported predicate covering T9 carriers, T9 submarines and T11
  submarines. `allShipBucketSegments()` now filters those out, so the sitemap
  advertises **17** buckets rather than 20 (it advertised 15 before, two of them
  empty T9 pages).
- `client/app/components/ShipLeaderboard.tsx` — the fetch gate, the emitted
  bucket and the share URL move from `isEasterEgg` to `isShipless`, so no
  request is issued for a bucket the game has no hulls for. The two T9 easter
  eggs still render; T11 submarines get a plain sentence.
- Tests: bucket counts in `entityRoutes.test.ts` and `siteOrigin.test.ts` now
  derive from `SHIP_BUCKET_TIERS`, and `test_top_ships_warm_bucket_split.py`
  pins `PROD_TIERS = [8, 9, 10, 11]` so the dispatch assertions follow the
  production shape. Backend suite 1311 passed; frontend 770 passed; build and
  lint clean.

Not taken: the window is still 60, the percentile warmer is still one task per
realm, and the rollup backfill has not been run. Steps 0, 1, 3, 4 and 5 stand as
written.


### Post-deploy verification, 2026-09-16 03:0x-03:5x UTC

Released as **v5.9.0**. Backend and frontend deployed; the droplet's
`/etc/battlestats-server.env` reads `SHIP_BADGE_TIERS=8,9,10,11`.

The na snapshot rebuilt in 47.4s: `ships_qualified` 358, `ships_total` **611**
(was 588 on the same night's pre-T11 run), `badges` 1068, `ranked_rows` 5226.
The 23-hull delta is tier 11 entering scope.

Ranked T11 ships per bucket, read from the public API after all three realms
rebuilt, against what this study predicted at 60d/floor 20:

| realm | BB | CA | DD | CV | predicted |
|---|---|---|---|---|---|
| na | 7 | 5 | 4 | 1 | 7 / 5 / 4 / 1 |
| eu | 7 | 8 | 5 | 3 | 7 / 8 / 5 / 3 |
| asia | 7 | 8 | 2 | 3 | 7 / 8 / 2 / 3 |

Every bucket matches the spike exactly: 60 boards across three realms. The
instrument is therefore validated against production, not merely plausible.

**One trap, worth remembering before the next tier opens.** The API gate opened
at deploy time, but the snapshot that ranks the new tier runs later. Probing a
T11 bucket in that gap computed a payload from the pre-T11 snapshot and cached
it under the *current* generation key (`captured_on` had already advanced on the
02:31 nightly run), so `na T11 Battleship` served one ship while the snapshot
held seven. Nothing detects this: the payload is well-formed, on the right
generation, and simply short.

Sequence a tier addition as: deploy, rebuild the snapshot per realm, **then**
warm or read the new buckets. If a bucket was read too early, force it:

```python
from warships.data import compute_realm_ships_by_tier_type as c
for t in ('Battleship', 'Cruiser', 'Destroyer', 'AirCarrier'):
    print(t, len(c('na', tier=11, ship_type=t, mode='random', use_cache=False)['ships']))
```

Snapshot dispatch used for the rollout (striped 0 / 900 / 1800s so three realms
do not contend; the background worker had a 16-message backlog and took ~8
minutes to reach the first one):

```python
from warships.tasks import snapshot_ship_top_players_task
for i, realm in enumerate(('na', 'eu', 'asia')):
    snapshot_ship_top_players_task.apply_async(kwargs={'realm': realm}, countdown=i * 900)
```

## Recommendation

**All three tiers are addable on the data. Sequence them behind the warmer fix,
and lead with T11.**

0. **Backfill the rollup** to 90 days on all three realms; confirm the coverage
   gate returns True. Read-only until here; this is the first write. Skippable
   only if the flip waits until 2026-09-18, when the gate passes on its own.
1. **Flip `SHIP_LEADERBOARD_WINDOW_DAYS` to 90**
   (`server/deploy/deploy_to_droplet.sh:793`, `set_env_value`, which writes the
   quoted form `SHIP_LEADERBOARD_WINDOW_DAYS="90"`). One lever. Watch
   `warm_realm_ships_pct_task` for two nights and record the new per-realm cost;
   that number sizes everything below.
2. **Add T11** (DONE 2026-09-15, ahead of the flip: see Implementation). It answers the visitor's request verbatim, costs +4% on the
   snapshot and an estimated +5-8% on the percentile warmer, matches or beats
   the live quality bar on every realm, and would ship even if the window had
   stayed at 60. Generalize the empty-bucket predicate in the same change;
   decide the label. If step 1 leaves the warmer above ~1450s on any realm, do
   step 3 first.
3. **Split the percentile warmer per tier** (fan-out). Required before T7 or T6:
   either adds 16-29% to a task already at 76-85% of its soft limit.
4. **Add T7.** Largest hull count of the three and two dead routes, but the
   deepest conventional tier available.
5. **T6 is optional and last.** It is the only candidate that completes the
   five-type grid, and the only way submarines and carriers get a mid-tier
   board; it is also 92 more hulls on the nightly snapshot.

Do **not** raise `SHIP_BADGE_MIN_BATTLES` with any of these steps.

## Validation

Re-run the depth study (read-only; ~3-6 min per realm for both windows):

```bash
ssh -o ConnectTimeout=15 root@battlestats.online \
  'cd /opt/battlestats-server/current/server \
   && set -a && . /etc/battlestats-server.env 2>/dev/null && . /etc/battlestats-server.secrets.env 2>/dev/null && set +a \
   && SPIKE_WINDOWS=60,90 SPIKE_TIERS=6,7,8,11 SPIKE_FLOORS=20,30 SPIKE_BY_TYPE=1 \
      /opt/battlestats-server/venv/bin/python manage.py shell' \
  < server/scripts/spike_ship_board_tier7.py 2>&1 | grep -v "Loading environment"
```

A full run should be launched detached on the droplet (`setsid nohup … >
/tmp/out.txt`) and polled; an interactive ssh pipe can be cut mid-realm. When
killing a stray spike, kill the PID: never `pkill -f "manage.py shell"`, which
also matches the run you want to keep.

Coverage gate and warmer budget:

```bash
# gate — True for 60, False for 90 until the catch-up runs (or until 2026-09-18).
# manage.py shell takes the script on STDIN; there is no -c form here.
ssh root@battlestats.online 'cd /opt/battlestats-server/current/server \
  && set -a && . /etc/battlestats-server.env 2>/dev/null && . /etc/battlestats-server.secrets.env 2>/dev/null && set +a \
  && /opt/battlestats-server/venv/bin/python manage.py shell' <<'PY'
from datetime import timedelta
from django.utils import timezone as tz
from warships.data import ship_pop_rollup_covers_window as g
t = tz.now().date()
for r in ("na", "eu", "asia"):
    for w in (60, 90):
        print(r, w, g(r, "random", t - timedelta(days=w), t))
PY

# warmer budget — whole-task and per-bucket. The unit name matters; there is no
# bare `celery*` unit on this droplet.
ssh root@battlestats.online 'journalctl -u battlestats-celery-background --since "3 days ago" \
  --no-pager -o cat | grep -E "warm_realm_ships_pct_task\[[^]]+\] succeeded" | tail'
ssh root@battlestats.online 'journalctl -u battlestats-celery-background --since "3 days ago" \
  --no-pager -o cat | grep -E "warm_ships_by_pct_task\[[^]]+\] succeeded" | tail'
```

## Follow-ups

- [ ] Run `rollup_ship_pop_daily_catchup(realm, window_days=90)` on na, eu and
      asia; re-check the gate before the window flip. Unnecessary if the flip
      waits for 2026-09-18.
- [ ] Flip the window; capture the post-flip per-bucket warmer cost, now with
      20 buckets rather than 15 (T11 added 2026-09-15).
- [ ] Read the first `warm_realm_ships_pct_task` run at 20 buckets against the
      1228-1384s/15-bucket baseline; the estimate was +5-8%.
- [ ] Split `warm_realm_ships_pct_task` per tier; required before T7 or T6, and
      before T11 if the post-flip warmer exceeds ~1450s on any realm.
- [x] T11 label decided: `11`, slug `t11-<type>` (operator, 2026-09-15).
- [x] Generalize the tier-9 empty-bucket predicate (`isShiplessBucket`, shipped
      with T11 on 2026-09-15).
- [ ] Reply to feedback #6 now that T11 is live on all three realms; it is the first submission the
      site has received and the requester named that tier specifically.
- [ ] Archive `runbook-ship-standings-tier7-spike-2026-09-07.md` once the tier
      decision lands; this runbook supersedes its verdict.

## Related

- `runbook-ship-standings-tier7-spike-2026-09-07.md` (superseded verdict; the
  per-tier instrument this study extends)
- `runbook-ship-standings-75d-spike-2026-09-03.md` (window decision, floor
  question)
- `archive/runbook-ship-standings-60d-rollout-2026-08-18.md` (rollout procedure and the
  rollup-coverage backfill)
- `runbook-shareable-ship-leaderboard-2026-08-20.md` (the `/ships/[bucket]`
  routes that grow with every tier)
- `runbook-ship-leaderboard-architecture-2026-06-18.md`
