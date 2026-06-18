# Runbook — Ship-leaderboard warm-before-evict (2026-06-18)

**Status:** dated-active · **Kind:** runbook · **Section:** feature-recovery ·
**Section:** backend (caching)

After the nightly ship snapshot recomputes leaders, the landing **most-played-ships treemap**
(`realm_top_ships`) and the inline **tier-type ship list** (`realm_ships_by_tier_type`) now **warm
the new numbers and keep the previous numbers served until the new ones are ready** — instead of
blanking on a cold synchronous aggregation during the window-rotation gap. This is the backend half
of the "treemap + list go offline on refresh" symptom diagnosed in
`runbook-landing-shipleaderboard-refresh-blank-2026-06-18.md`.

Related: `runbook-ship-leaderboard-architecture-2026-06-18.md` (feature end-to-end),
`runbook-landing-shipleaderboard-refresh-blank-2026-06-18.md` (diagnosis),
`runbook-leaderboard-updates.md` (the warmer), `landing.py` published-cache idiom.

## The problem (what blanked)

Both surfaces cache under a **window-end-date-tagged** Redis key:

- `{realm}:top-ships:{mode}:win{date}:{limit}`
- `{realm}:ships-by:{mode}:win{date}:t{tier}:{type}`

`date` = `window_end` from `latest_ship_snapshot_window(realm)` = the latest
`ShipTopPlayerSnapshot.captured_on` = **today's UTC date**. When the first snapshot run past UTC
midnight lands a new `captured_on`, the served cache key **rotates to a cold `win{newdate}` key**
(the 12:30 re-run reuses the same date, so rotation is **once per UTC day**). The previous day's warm
key still holds valid data but is **orphaned** — no reader computes that key anymore. Until the
scheduled `warm_realm_top_ships_task` ran **~1h later** (`signals.py`), every request paid the full
synchronous `BattleEvent … Sum("battles_delta")` GROUP-BY **in-request** — multi-second, with no
skeleton on the client → "offline."

The window-date-in-key design is deliberate: it self-heals treemap↔snapshot **alignment** (the key
changes exactly when the window advances, so the treemap can never serve a window the snapshot has
moved past). The fix preserves that invariant — see the tradeoff note below.

## The fix — two complementary changes

### A. Warm-on-recompute (collapse the ~1h gap)

`snapshot_ship_top_players_task` (`tasks.py`) now calls `queue_realm_top_ships_warm(realm)`
immediately after a `status == "completed"` snapshot (next to the existing
`materialize_landing_player_best_snapshots_task` enqueue). The new-window treemap + tier-type keys
warm **right after** the snapshot writes its rows — not ~1h later.

### B. Durable `:published` fallback (serve old until new ready)

Both compute functions now mirror landing.py's published-cache idiom (`_publish_landing_payload` /
`_get_cached_landing_payload_with_fallback`) — **write-new-then-overwrite, never delete-first** —
via the shared `data._store_realm_ship_cache(fresh_key, published_key, payload)` helper. A
**window-date-independent** durable key sits alongside the window-keyed fresh key:

| Surface | Fresh key (existing, 26h TTL) | Published key (new, `timeout=None`) |
|---|---|---|
| Treemap | `{realm}:top-ships:{mode}:win{date}:{limit}` | `{realm}:top-ships:published:{mode}:{limit}` |
| Tier-type | `{realm}:ships-by:{mode}:win{date}:t{tier}:{type}` | `{realm}:ships-by:published:{mode}:t{tier}:{type}` |

**Warm path** (`use_cache=False` — the warmer / the snapshot chain): compute, then write **both**
keys (overwrite). Empty tier-type buckets also publish their empty payload on the warm path, so a
bucket that went empty this window **clears** its stale last-good instead of serving yesterday's
ships forever.

**Read path** (`use_cache=True` — the view):
1. Fresh `win{date}` hit → return (steady state).
2. Fresh miss → **published hit → serve the old payload + queue a warm** (`queue_realm_top_ships_warm`,
   dispatch-deduped 60s + the warmer's own 300s lock) → return. **No synchronous aggregation.**
3. Both miss (first-ever / post-eviction) → synchronous compute, write both keys (today's worst
   case — strictly no worse than before).

With **A** keeping the published key fresh proactively, **B**'s read-path queued-warm is
belt-and-suspenders for `allkeys-lru` eviction or warmer lag — but it is what *guarantees* a cold
fresh key serves old-and-queues rather than blocking the request.

## Alignment-invariant tradeoff (read before "fixing" this)

The published key is **deliberately not window-keyed**, so during the rotation gap it serves the
**previous** window's numbers while the snapshot-backed `/ship/<id>` boards already show the new
window. That brief, intentional misalignment lasts only until the queued/chained warm overwrites both
keys (seconds–minutes) on near-static "most-played" data. Do **not** "fix" this by adding the window
date back into the published key — that would re-introduce the cold-blank this runbook removes. The
fresh key still carries the window date and still self-heals alignment in steady state.

## Scope / what is NOT touched

- **`/ship/<id>` drill-down board** (`ship_leaderboard`) — **excluded**. It reads
  `ShipTopPlayerSnapshot` rows on miss (fast DB read, **no aggregation**) and its 15-min TTL already
  *is* "serve old until new ready." No durable key added.
- **FE mount blank** (hard reload / back-from-profile → empty `<svg>`, no skeleton) — **excluded**.
  A separate FE concern (loading skeleton / seed-last-good) the backend cannot eliminate; tracked in
  `runbook-landing-shipleaderboard-refresh-blank-2026-06-18.md` (options 1–2).

## Cost / ops

- Cost ≈ **2× full warms/realm/day** (snapshot chain + the retained ~1h-later scheduled warmer as a
  backstop). Each full warm = 2 treemap modes + (badge-tiers × 5 types) tier-type GROUP-BYs (prod
  3×5=15 buckets). Off the request path, on the `background` queue, on the 2-vCPU PG. The warmer's
  300s lock + the dispatch dedup coalesce any overlap. **Decision:** keep the scheduled warmer (no
  `signals.py` change) — cheap, idempotent safety net.
- **Kill switch:** none added. Gated transitively by `SHIP_BADGE_SNAPSHOT_ENABLED` (the chain only
  fires on a completed snapshot) and the existing warmer schedule. The published key is bounded
  (fixed set of shapes, rewritten each warm); `allkeys-lru` may evict it → falls back to synchronous
  compute (== today's worst case).
- **First deploy needs a seed:** the published keys don't exist until the first warm. Run
  `warm_realm_top_ships_task` once per realm post-deploy (see QA) or the first cold read still
  computes synchronously.

## Code references

- `server/warships/data.py` — `_store_realm_ship_cache` (publish both keys);
  `compute_realm_top_ships` (read path: fresh → published+queue → synchronous; write both);
  `compute_realm_ships_by_tier_type` (same, plus warm-path publish-empty for the early returns).
- `server/warships/tasks.py` — `queue_realm_top_ships_warm` (lock + dispatch-dedup enqueue),
  `_realm_top_ships_warm_lock_key` / `_realm_top_ships_warm_dispatch_key`,
  `REALM_TOP_SHIPS_WARM_DISPATCH_TIMEOUT=60`; `snapshot_ship_top_players_task` chains the warm;
  `warm_realm_top_ships_task` clears the dispatch key on completion.
- `server/warships/tests/test_ship_warm_before_evict.py` — the contract.
  `test_realm_ships_by_tier_type.py::...test_warm_populates_tier_type_bucket_cache` updated for the
  warm-path publish-empty.

## QA / verification

### Local (sqlite harness)

```bash
cd server
DB_ENGINE=sqlite3 DJANGO_SECRET_KEY=test-key python -m pytest \
  warships/tests/test_ship_warm_before_evict.py \
  warships/tests/test_realm_top_ships.py \
  warships/tests/test_realm_ships_by_tier_type.py \
  warships/tests/test_ship_badges.py --nomigrations -q
```

Expect green. The new suite pins: warm writes both keys; cold fresh + published serves old + queues
warm (and dedups); both-miss computes + writes both; warm publishes empty to clear stale; the
snapshot task chains the warmer only on `status=="completed"`.

### Production (after deploy — seed first)

1. **Seed the durable keys** (first deploy only). In a Django shell on the droplet (or trigger the
   task per realm):
   ```python
   from warships.tasks import warm_realm_top_ships_task
   for r in ("na", "eu", "asia"):
       warm_realm_top_ships_task.apply_async(kwargs={"realm": r}, queue="background")
   ```
   Confirm the published keys exist: `redis-cli EXISTS na:top-ships:published:random:25` → `1`.

2. **Cold-key serves old, not blank.** Delete the current fresh key, keep published:
   ```bash
   redis-cli DEL "na:top-ships:random:win$(date -u +%F):25"
   time curl -s "https://battlestats.online/api/realm/na/top-ships?mode=random&limit=25" >/dev/null
   ```
   Expect a **fast** populated response (the published old numbers), **not** multi-second, and a
   `warm_realm_top_ships_task` enqueue in the `background` worker log.

3. **Warm overwrites published with the new window.** After the warm runs, re-`curl`; the payload's
   `captured_on` / `window_end` reflect the **new** window, and
   `redis-cli GET na:top-ships:published:random:25` now holds the new numbers.

4. **Snapshot chain fires.** Around a snapshot run (or trigger
   `snapshot_ship_top_players_task.apply_async(kwargs={"realm":"na"}, queue="background")`), confirm
   `warm_realm_top_ships_task` is enqueued **immediately** after — not ~1h later — and the new
   `win{date}` key warms within the warmer's runtime.

5. **FE steady-state smoke** (`memory/reference_frontend_visual_verify_recipe.md`): with
   `BATTLESTATS_API_ORIGIN=https://battlestats.online npm run dev`, an in-place realm/mode toggle
   keeps tiles populated. The *mount* blank is out of scope and unchanged.
