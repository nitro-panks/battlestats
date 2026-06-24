# Runbook — Inline ship-list win-rate-percentile filter (top 50% / 25%)

**Date:** 2026-06-23 (cold-compute strategy reversed same day — see below)
**Status:** active (shipped)
**Owner:** data
**Area:** landing inline ship leaderboard (`ShipLeaderboard.tsx`), `compute_realm_ships_by_tier_type`, `warm_realm_ships_pct_task` (nightly pre-warm) + `warm_ships_by_pct_task` (lazy fallback)

## What shipped

The inline ship list (landing page, under the treemap — pick a Tier + Type, ships
ranked by win rate) gained a **WR filter** in the filter bar, right of the SS type
pill: `WR  [All] [50%] [25%]`.

- **All** is the prior behavior — each ship's stats (battles, avg damage,
  kills/battle, win rate) are realm-wide aggregates over the rolling trailing
  `SHIP_LEADERBOARD_WINDOW_DAYS` (14) window.
- **50% / 25%** re-pool each ship's stats over only the **top N% of that ship's
  players by window win rate**, answering "how are good/great players doing with
  these ships?".
- **The list defaults to 50%** (not All) — the landing leads with the good-player
  view. The default landing bucket is pre-warmed (below) so this is instant.

**Load-bearing constraint:** the filter NEVER changes *which* ships are listed —
only the displayed numbers narrow. Ship membership still gates on the ship's
**full-population** battles ≥ `SHIP_LIST_MIN_BATTLES` (50), identical to the
all-view. The list re-orders by the subset win rate (desc) and every column stays
click-sortable on the new numbers.

The filter applies to the **ship list only**, not the drilled-in per-player board;
the WR pills are hidden while a ship board is open.

**Selection persistence (v2.15.0, 2026-06-24).** The Tier / Type / WR selection is
saved to `localStorage` under `bs-ship-leaderboard` (`{tier, type, wrPct}`) on every
change and restored on a return visit. Restore runs in a mount effect — *after* SSR —
so the first client render still matches the server's default markup (no hydration
mismatch); the list fetch is gated on restore so it fires once with the restored
bucket instead of flashing the default T10/BB/50 first. Each field is validated on
read (`readStoredShipLbPrefs` in `ShipLeaderboard.tsx`), so a stale/malformed value
falls back to the component default rather than fetching garbage. `wrPct: null`
("All") is a real stored value, distinct from absent. Treemap drill-downs also set
tier/type, so those persist too.

## Backend

`compute_realm_ships_by_tier_type(realm, tier, ship_type, mode, wr_pct=None, player_min_battles=None, use_cache=True)`
(`server/warships/data.py`):

- `wr_pct=None` → unchanged cheap path (one `BattleEvent` GROUP BY ship → ~31
  rows). **Hot path is untouched** — landing visitors and the daily warmer never
  pay the heavier query.
- `wr_pct in (50, 25)` → heavy path: one per-`(ship, player)` `BattleEvent`
  aggregation over the window (47k rows for the busiest NA T10 bucket), then for
  each ship rank its players by win rate (tie-break battles, then player id),
  keep the top `ceil(n·pct/100)` (≥1), and re-pool. `100` is an internal
  equivalence hatch (ignores the floor, pools everyone → reproduces the cheap
  all-path exactly; pinned by `test_pct_100_no_floor_matches_the_all_path_exactly`).
- **Two distinct floors** (kept separate in code + docs):
  - `SHIP_LIST_MIN_BATTLES` (50) — gates whether a *ship* is listed, on
    full-population battles. Unchanged by this filter.
  - `SHIP_LIST_WR_PCT_PLAYER_MIN_BATTLES` (env, default 15) — a *player* needs
    this many window battles to enter the ranking, so "top 25% by WR" reflects
    players with a real sample, not tiny-sample 100%-WR tourists. If the floor
    leaves a ship's ranked population empty, it **falls back to full-population
    stats** rather than dropping the ship (never violate the constraint).

Endpoint: `GET /api/realm/<realm>/ships?tier=&type=&wr_pct=50|25`. Unsupported
`wr_pct` values fall through to the all-view. Payload gains a top-level `wr_pct`
field (`null` for all).

### Cold-compute handling — pre-warm ALL buckets nightly; lazy is the fallback

> **Decision reversed 2026-06-23 (August).** The feature first shipped warming
> *only* the default bucket and leaving every other tier×type lazy (queue + ~20s
> "crunching" poll on first view). In practice that put the ~15–28s crunch on the
> visitor for **every** non-default bucket — too much burden. The new design
> pre-warms **every** tier×type percentile bucket per realm, serially with a short
> DB-breather pause, so visitors essentially never trigger the crunch. The lazy
> queue+poll path below is **retained only as a rare rotation-gap fallback** (a
> visitor hitting a bucket in the gap between the window rotating and the warm
> landing, or after a warm failure).

The percentile recompute is **~15–28s** for popular T10 buckets (PDSS is no
faster — same 14-day scan), which is **over the client's 15s fetch timeout**, so
the recompute is never on the request thread.

**Nightly pre-warm — `warm_realm_ships_pct_task` (`server/warships/tasks.py`):**

- Walks **every** tier×type bucket for the realm (the `_badge_tiers()` × five
  `SHIP_LEADERBOARD_TYPES` grid; 15 buckets in prod), **default bucket first** so
  the primary landing view warms before the rest, computing each at `wr_pct=50`
  (one per-(ship,player) query that **materializes both 50 & 25**, written to two
  window-keyed fresh keys, 26h TTL).
- **Load discipline on the shared 2-vCPU Postgres** (the binding constraint —
  this is the cost the original lazy decision was avoiding, now accepted with
  guards):
  - **skip-if-warm** — a bucket whose fresh key already exists for the current
    window is skipped. The repeated triggers (the nightly top-ships Beat + the
    2×/day snapshot, all of which chain this warmer) therefore collapse to **one
    real pass per window**; only the post-rotation pass does heavy work. (Empty
    buckets write no fresh key, so they re-run their *cheap* candidate query each
    pass — no per-player aggregation.) An ACKS_LATE redelivery after a mid-loop
    crash also resumes where it stopped.
  - **per-bucket pause** — `time.sleep(SHIP_LIST_WR_PCT_WARM_PAUSE_SECONDS)`
    (default 5s, env-tunable, 0 disables) between heavy buckets.
  - **per-bucket lock** — grabs the lazy task's per-bucket lock before computing,
    so a visitor opening a still-cold bucket mid-pass doesn't double-run the query.
  - **per-realm lock** — `SHIP_LIST_WR_PCT_WARM_LOCK_TIMEOUT` (default 40 min,
    must outlive the task's 30 min hard `time_limit`). The realms are hour-striped
    upstream (Beat + snapshot triggers are ≥~1h apart vs a ~5–8 min warm), so two
    realms never overlap on the DB — a per-realm lock is correct; **no global
    cross-realm lock** (it would just skip a realm's warm for the cycle).
- Dedicated `SHIP_PCT_WARM_TASK_OPTS` (30 min hard / 27 min soft) — far more
  headroom than the shared `TASK_OPTS` 540s, since the serial walk of ~15 heavy
  buckets can run several minutes.
- **Chained from `warm_realm_top_ships_task`** via `queue_realm_ships_pct_warm`
  (lock + dispatch dedup). `warm_realm_top_ships_task` fires from the nightly
  top-ships Beat **and** after each ship snapshot, so the pct warm runs right
  after the window rotates. The top-ships task still warms the **default bucket
  inline** (instant primary view); the chained full warmer skip-if-warms it, so
  it's never recomputed.

**Lazy fallback (retained) — `warm_ships_by_pct_task`:**

- On a cold percentile fresh key the read path queues `warm_ships_by_pct_task`
  (background queue, per-bucket lock + dispatch dedup → a burst of pollers
  enqueues at most one warm) and returns a `pending: True` payload (`ships: []`) +
  sets `X-Ships-WR-Pending: true`. One run materializes BOTH 50 & 25.
- Percentile keys carry **NO durable `:published` fallback** (unlike the
  all/treemap keys): the all-only treemap warmer can't refresh a pct published
  key, so it would serve a frozen window forever. A cold pct key re-queues + polls.
- Client (`ShipLeaderboard.tsx`, **unchanged by this change**): the percentile
  fetch uses `ttlMs:0` and polls every ~3s up to ~16× (~48s budget) while
  `data.pending`, showing a one-time "Crunching stats for the top N%…" message.
  With the nightly pre-warm in place this path is rarely exercised.

**Shared cache-key contract.** Both the writer (`compute_realm_ships_by_tier_type`)
and the warmer's skip-if-warm check (`ship_pct_bucket_cache_key`) build the fresh
key through one helper, `_ships_by_fresh_cache_key`. This is load-bearing: if the
two key strings drifted, skip-if-warm would always read "cold" and silently
recompute all buckets every trigger (the 2–3× daily PG load the design avoids).
Pinned by `test_pct_bucket_cache_key_matches_what_compute_writes`.

## Cost / ops notes

- Added daily DB load: **~15 heavy queries per realm, once per window rotation**
  (the full nightly pre-warm). The repeated triggers (Beat + 2×/day snapshot)
  collapse to one real pass per window via skip-if-warm; the second/third trigger
  re-confirms warmth cheaply. The per-bucket 5s pause spreads the ~15-bucket walk
  over ~5–8 min wall-clock. This is the cost the original lazy design declined;
  the per-realm hour-striping + pause keep it off the DB's saturation edge, and
  the standing load monitor (alarm on sustained `load15 > 2.3`) is the backstop —
  if it bites, raise `SHIP_LIST_WR_PCT_WARM_PAUSE_SECONDS`.
- Both pct warmers run on the `background` worker (`battlestats-celery-background`),
  off the user-facing lanes.
- Knobs: `SHIP_LIST_WR_PCT_PLAYER_MIN_BATTLES` (default 15) — per-player sample
  floor; does not affect the listed set. `SHIP_LIST_WR_PCT_WARM_PAUSE_SECONDS`
  (default 5) — inter-bucket DB breather. `SHIP_LIST_WR_PCT_WARM_LOCK_TIMEOUT`
  (default 2400s).

## Tests

- Backend: `server/warships/tests/test_realm_ships_by_tier_type.py`
  - `RealmShipsByTierTypeWrPctTests` — top-25/50 re-pooling math, **ship-set
    equivalence to the all-view**, player-floor fallback, the 100% equivalence
    hatch, the cold→pending+queue serve, warm-then-ready, the view honoring /
    ignoring `wr_pct` + the pending header, and
    `test_pct_bucket_cache_key_matches_what_compute_writes` (the shared-key
    contract that makes skip-if-warm correct).
  - `RealmShipsPctWarmTests` — the nightly all-buckets warmer
    (`warm_realm_ships_pct_task`): fills both 50 & 25 fresh keys, skip-if-warm
    idempotence, and the per-realm lock preventing a concurrent run. Also
    `RealmShipsByTierTypeWarmTests` asserts the top-ships warm chains
    `queue_realm_ships_pct_warm`.
- Frontend: `client/app/components/__tests__/ShipLeaderboard.test.tsx` (WR-percentile
  describe) — unchanged by this change (the lazy pending/poll path it covers now
  fires only on the rare rotation-gap fallback).

## Validation (local, 2026-06-23, cloud DB + dockerized redis/rabbitmq)

End-to-end verified: cold 25% → `pending` + header → `warm_ships_by_pct_task`
computed na/T10/Cruiser in ~31s → re-request ready (31 ships, same set), sibling
50% instant from the same warm. Screenshots confirmed All / crunching / top-50 /
top-25 render with the same ship set, WR + damage climbing as the percentile
narrows (Sicilia 56.6%→ Aki 70.3%, Slava 118k→140k avg dmg at top-25%).

## Validation — nightly pre-warm (prod, 2026-06-23, v2.14.1)

After deploying the all-buckets pre-warm, `warm_realm_ships_pct_task` was
triggered per realm (serially): na `warmed=11 skipped=4`, eu `15/0`, asia `15/0`
(prod `_badge_tiers()` = {8,9,10} → 15 buckets/realm). Acceptance test — a
previously-lazy bucket now serves instantly with **no** `X-Ships-WR-Pending`
header and `pending: null`:
- na T10 Cruiser 25% → 31 ships, Svea 70.6%; T10 DD 50% → 27 ships, Småland 68.8%;
  T10 CV 25% → 8 ships, Essex 73.0%.
- eu T10 Cruiser 25% → 36 ships, San Martín 79.2%; asia T10 BB 50% → 35 ships,
  Sicilia 66.3%.
Droplet load15 stayed ~0.7 across the three serial warms (alarm threshold 2.3).
Going forward the warm runs automatically, chained off the nightly top-ships Beat
+ the 2×/day snapshot (skip-if-warm → one real pass/window).
