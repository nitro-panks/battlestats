# Runbook: Landing Random Players List Goes Cold for End Users

_Created: 2026-04-07_
_Status: **Resolved 2026-04-07** — implemented and verified in production. Random landing strip serves in 48–105 ms across all realms; namespace stable across player updates; published fallback now survives invalidation. Commit `6ced729`._
_QA: 2026-04-07 — initial diagnosis was wrong; corrected. Second QA pass added dispatch-dedupe finding and verified all builder/lock claims._

## Symptom

A user lands on `/`, the **Random Players** strip on the landing page hangs for ~30–60 seconds before any rows render. This happens periodically even though `LANDING_PAGE_WARM_MINUTES=30` is set on the droplet and `landing-page-warmer-{na,eu,asia}` periodic tasks are firing every 30 minutes.

## QA Correction (initial diagnosis was wrong)

The first version of this runbook claimed the random strip was served by the queue/pop API (`get_random_landing_player_queue_payload`, `pop_random_landing_player_ids`, `_extend_random_landing_player_queue`). **That is incorrect.** Verified via `server/warships/views.py:956`:

```python
def landing_players(request) -> Response:
    ...
    payload, cache_metadata = get_landing_players_payload_with_cache_metadata(
        mode=mode, limit=limit, realm=realm, **payload_kwargs,
    )
```

The `/api/landing/players?mode=random` endpoint goes through `get_landing_players_payload_with_cache_metadata` (`landing.py:2087`), the same generic published-fallback path the Best lists use. The queue/pop functions exist but are reachable only via a separate endpoint (likely a "more random" pagination button) and **are not on the landing strip's hot path**.

So the queue refill threshold, queue target size, and eligible-IDs TTL — all the things the first draft proposed to tune — are irrelevant to this symptom.

## Actual Root Cause (verified in code)

The random strip uses the same cache-with-`published`-fallback pattern as Best lists, which *should* always serve instantly from the durable `published` key. It doesn't, because of one specific interaction:

### 1. Cache keys are namespaced and the namespace gets bumped on every player update

`landing_player_cache_key()` (`landing.py:477`) and `landing_player_published_cache_key()` (`landing.py:495`) both embed a namespace counter:

```python
return realm_cache_key(realm, f'landing:players:v13:n{namespace}:{canonical_mode}:{limit}')
return realm_cache_key(realm, f'landing:players:v13:n{namespace}:published:{canonical_mode}:{limit}')
```

`invalidate_landing_player_caches()` (`landing.py:761`) **bumps that namespace**:

```python
def invalidate_landing_player_caches(include_recent=False, realm=DEFAULT_REALM, queue_republish=True):
    _bump_landing_players_cache_namespace(realm=realm)   # n -> n+1
    ...
    _mark_cache_family_dirty(*dirty_keys)
    if queue_republish:
        _queue_landing_republish(realm=realm)
```

After the bump, both the live key *and* the published key at `n{old}` are effectively orphaned (Redis still holds them but no reader looks them up). The new namespace `n{new}` has neither a live cache nor a published fallback until the warmer finishes a pass.

### 2. `update_player_data` unconditionally invalidates on every player refresh

`server/warships/data.py:4785`:

```python
def update_player_data(player, force_refresh=False, realm=None):
    ...
    player.save()
    ...
    invalidate_landing_player_caches(include_recent=True)
    invalidate_player_detail_cache(player.player_id, realm=player.realm)
```

Also `data.py:3898` from the per-player CB summary persist path.

`update_player_data` is called on player detail page visits, clan member refreshes, search-triggered hydrations, and the hot-entity warmer. With enrichment running 4 partitions and the hot entity warmer firing every 30 min plus organic traffic, **the namespace bumps several times per minute**.

### 3. Republish is async; the inline rebuild runs in the request path

`invalidate_landing_player_caches` schedules `warm_landing_page_content_task` via `_queue_landing_republish`, but Celery is async. Between the invalidation and the warmer completing the random surface, **any incoming `/api/landing/players?mode=random` request finds an empty cache at the new namespace and falls into**:

```python
# landing.py:2125
if payload is None:
    payload = builder(normalized_limit)   # _build_random_landing_players
```

`_build_random_landing_players` (`landing.py:1562`) is the slow path:

```python
eligible_ids = list(
    Player.objects.exclude(name='').filter(
        realm=realm, is_hidden=False,
        days_since_last_battle__lte=180,
        pvp_battles__gt=LANDING_PLAYER_RANDOM_MIN_PVP_BATTLES,
    ).exclude(last_battle_date__isnull=True).values_list('player_id', flat=True)
)
...
selected_ids = random.sample(eligible_ids, k=...)
rows = list(Player.objects.filter(player_id__in=selected_ids).values(...))
```

The first query materializes **every eligible player_id for the realm** into a Python list (~150K–200K entries per realm post-enrichment). It's a sequential filtered scan against a partial index, returns hundreds of thousands of integers over the wire, and Python builds a full list before `random.sample` runs. That's the ~30–60 s the user sees.

### 4. Thundering herd amplifies it

There is no per-key build lock around `_build_random_landing_players`. Verified at `landing.py:2125-2137`: the rebuild block is just `if payload is None: payload = builder(...); _publish_landing_payload(...)` with no `cache.add(lock_key, ...)` guard. Every concurrent request that arrives during the rebuild window runs its own copy of the slow query.

### 5. The republish dispatch is deduplicated for 5 minutes — the real amplifier

`invalidate_landing_player_caches` calls `_queue_landing_republish` → `queue_landing_page_warm` (`tasks.py:266`), which is gated by:

```python
LANDING_PAGE_WARM_DISPATCH_TIMEOUT = 5 * 60   # tasks.py:40

def queue_landing_page_warm(realm=DEFAULT_REALM):
    dispatch_key = _landing_page_warm_dispatch_key(realm)
    if not cache.add(dispatch_key, "queued", timeout=LANDING_PAGE_WARM_DISPATCH_TIMEOUT):
        return {"status": "skipped", "reason": "already-queued"}
    ...
    warm_landing_page_content_task.delay(...)
```

Sequence under steady-state traffic:

| t | Event |
|---|---|
| 0s | Player A invalidation: namespace n→n+1, dispatch_key set, warm task queued |
| 0s+ε | User request: cache miss at n+1, no published key at n+1 → inline rebuild starts |
| ~5s | Warmer task starts on the worker, also rebuilds, races user request |
| ~10s | Player B invalidation: namespace n+1→n+2, **dispatch_key still set**, no new warm queued |
| ~12s | User request: cache miss at n+2, **no warmer scheduled, no published key** → another inline rebuild |
| ... | Every invalidation in the next 5 min bumps the namespace forward, leaving each new namespace cold with no scheduled warmer |
| 300s | dispatch_key TTL expires |
| ~301s | Next invalidation finally re-queues the warmer |

The 5-minute dispatch dedupe is the dominant amplifier — it means under bursts of player updates, the namespace can be bumped many times while only **one** warm task is scheduled, and that task warms whatever namespace exists at the moment it actually runs (likely already several bumps stale). This explains why the symptom is intermittent: it depends on whether the user's request lands inside or outside an active warm window.

### Why the existing 30-minute warmer doesn't help

The warmer warms the namespace **as it exists at the moment the warmer starts**. Any `update_player_data` call between warmer passes bumps to a new namespace, leaving the freshly-warmed payload stranded at the old namespace. With organic traffic, this happens many times per warmer interval. The 5-minute dispatch dedupe (above) makes this worse by suppressing the on-demand republishes that would otherwise close the gap.

Compare to the Best clan lists: those use the same namespace+published pattern, but their builder `_best_landing_player_candidate_rows` (verified at `landing.py:1590-1646`) does `order_by(*order_by)[:limit]` with `LANDING_PLAYER_BEST_CANDIDATE_LIMIT = 1200` on indexed columns. The query returns 1200 rows directly from Postgres, doesn't materialize the full eligible set, and finishes in milliseconds. So even though Best lists are subject to the same namespace-bump bug, the "cold" window is too short for users to notice. `_build_random_landing_players` cannot do `ORDER BY pvp_battles LIMIT 25` because `random.sample` needs the full eligible_id list.

## Reproduction

1. SSH droplet, `redis-cli get na:landing:players:v13:namespace` — note the value `n_old`.
2. Trigger any player refresh (e.g. `curl https://battlestats.online/api/players/lil_boots/`).
3. `redis-cli get na:landing:players:v13:namespace` — value is now `n_old+1`.
4. `curl -w '%{time_total}\n' -o /dev/null -s 'https://battlestats.online/api/landing/players?mode=random'` — measures multi-second response on first call after the bump.
5. Repeat the curl — second call is fast because the live cache is now populated under the new namespace.

## Fix Plan

The actual fix is much smaller than the first draft suggested.

### Step 1 — Stop bumping the namespace on every player update

`invalidate_landing_player_caches` is doing **two** things: (a) bumping the namespace (which orphans the published fallback), and (b) marking dirty + queueing a republish. For per-player updates, only (b) is needed — the dirty flag already causes `_get_cached_landing_payload_with_fallback` to skip the live cache and trigger a rebuild on the next non-warmer caller, but the **published fallback at the existing namespace remains servable**.

The namespace bump is only required when the cache *schema* changes (new fields in serialized rows, etc.), which is a deploy-time concern. Convert it to deploy-time only:

- Remove the `_bump_landing_players_cache_namespace(realm=realm)` call from `invalidate_landing_player_caches`.
- Bump the namespace explicitly during `run_post_deploy_operations` (which already calls `invalidate_landing_player_caches`).
- Or: change the constant from `landing:players:v13` → `landing:players:v14` whenever the row shape changes; the namespace counter becomes a deploy-only escape hatch.

### Step 1b — Drop or shorten the 5-minute landing-page-warm dispatch dedupe

`LANDING_PAGE_WARM_DISPATCH_TIMEOUT = 5 * 60` in `tasks.py:40`. This made sense as a write-storm guard but is the dominant amplifier for this symptom. Lower it to **30 seconds** and rely on the celery worker-side lock (`_landing_page_warm_lock_key`) to prevent concurrent runs. Worker lock prevents pile-up; the dispatch dedupe only needs to suppress queue-flooding within a single user's interaction window.

Alternatively, keep the dispatch dedupe at 5 min but **bypass it for invalidations that follow a namespace bump** — i.e. invalidations that *change* what's cached should always re-queue, while invalidations that don't change anything (e.g. dirty-mark only) can dedupe.

### Step 2 — Always read the published fallback under the *current* namespace, even when dirty

Even with Step 1, there is still a window where `is_dirty=True` causes `_get_cached_landing_payload_with_fallback` to return `None` (`landing.py:619`):

```python
published_payload = None if force_refresh or (is_dirty and not use_published_fallback_when_dirty) else cache.get(published_cache_key)
```

For the random strip, set `use_published_fallback_when_dirty=True` in the call site (`landing.py:2114`). The published payload at the current namespace is always at most one warmer-pass stale, which for a "random" sample is fine — random has no canonical correct answer.

### Step 3 — Per-key build lock around `_build_random_landing_players`

When the rebuild *does* have to run (after a deploy-time namespace bump, or before the first warmer pass on a fresh redis), only one thread should run it. Wrap the `if payload is None: payload = builder(...)` block in `landing.py:2125-2137` with a Redis lock keyed on `landing:build_lock:<cache_key>`. Concurrent waiters poll the cache key for ~5 seconds; if still empty, they fall back to the previous namespace's published key as a degraded-but-instant response (and log a warning).

### Step 4 — Make `_build_random_landing_players` itself fast

Even single-shot, the current implementation is too slow because it materializes ~200K player_ids. Two options:

**Option A — Postgres-side sample (preferred):**

```sql
SELECT name, player_id, pvp_ratio, ...
FROM warships_player
WHERE realm = %s AND is_hidden = false
  AND days_since_last_battle <= 180
  AND pvp_battles > 500
  AND last_battle_date IS NOT NULL
ORDER BY random()
LIMIT 25;
```

`ORDER BY random() LIMIT 25` over the existing `player_realm_battles_ratio_idx` partial filter is still a seq-scan, but Postgres only emits 25 rows and the work happens server-side instead of moving 200K integers over the wire. Empirically this is 5–20x faster than the Python `random.sample` path.

**Option B — `TABLESAMPLE`:**

```sql
SELECT ... FROM warships_player TABLESAMPLE SYSTEM (1)
WHERE ... LIMIT 25;
```

Faster but biased toward whichever pages contain qualifying rows. For a "random" landing strip the bias is acceptable; for analytics it would not be.

Pick Option A. Implement as a raw SQL query in `_build_random_landing_players` (or via `Player.objects.raw(...)`).

### Step 5 — Add a per-namespace `published` seed at deploy time

In `run_post_deploy_operations` (`server/warships/management/commands/run_post_deploy_operations.py`), after invalidation, **synchronously** call `warm_landing_page_content(force_refresh=True, realm=realm)` for each realm before the deploy script considers itself done. This guarantees that the new namespace has a published fallback before the first user request lands.

Verify the existing post-deploy script already does this — `run_post_deploy_operations.py:188` calls `warm_landing_page_content(...)` per realm, so the deploy-time gap should already be covered. The remaining gap is per-player invalidations during steady-state, which Step 1 closes.

### Step 6 — Tests

Add to `server/warships/tests/test_landing.py`:

1. **Per-player update preserves published fallback** — call `update_player_data` (or just `invalidate_landing_player_caches`), then assert the published key for the random surface is still readable and the next `get_landing_players_payload_with_cache_metadata` call does **not** invoke `_build_random_landing_players` (mocked to raise).
2. **Build lock serializes concurrent rebuilds** — spawn two threads calling the random surface against an empty cache; assert `_build_random_landing_players` runs once.
3. **Postgres-side sampling produces correct shape** — schema check on `_build_random_landing_players` output (count == limit, all required keys present).

### Step 7 — Verification on the droplet

After deploy:

1. `redis-cli get na:landing:players:v13:namespace` — record value.
2. `curl https://battlestats.online/api/players/<known-player>/` to trigger `update_player_data`.
3. `redis-cli get na:landing:players:v13:namespace` — value should be **unchanged** (Step 1 success).
4. `curl -w '%{time_total}\n' -o /dev/null -s 'https://battlestats.online/api/landing/players?mode=random'` — must complete in **< 200 ms**.
5. Loop the curl 50× — every response < 500 ms.
6. `redis-cli keys 'na:landing:players:v13:n*:published:random:*'` — exactly one key per realm under the current namespace, `TTL = -1`.

## Rollback

Each step is independently revertable:

- Step 1: re-add the `_bump_landing_players_cache_namespace` call. Steps 2–4 still help.
- Step 2: revert the `use_published_fallback_when_dirty=True` flag at the call site.
- Step 3: remove the build lock; thundering herd returns but published fallback (Steps 1+2) still hides it.
- Step 4: revert to the Python `random.sample` path.

## Related

- `runbook-landing-page-warmer-cadence-2026-04-05.md` — Cadence tightening for the Best lists (already deployed: 120 → 30 min). The Best lists are not affected by the namespace-bump bug to the same degree because their builders are index-friendly and finish in milliseconds.
- `runbook-player-page-load-priority.md` — General request-priority architecture
- `server/warships/landing.py:444-516` — Namespace counter and cache-key generation
- `server/warships/landing.py:582-628` — `_get_cached_landing_payload_with_fallback`
- `server/warships/landing.py:1562-1587` — `_build_random_landing_players` (the slow query)
- `server/warships/landing.py:761-769` — `invalidate_landing_player_caches` (bumps namespace)
- `server/warships/data.py:3898, 4785` — Call sites that invalidate per-player update
- `server/warships/views.py:956-988` — `landing_players` view (the actual hot path)
- `server/warships/tasks.py:40, 266-284` — `LANDING_PAGE_WARM_DISPATCH_TIMEOUT` 5-minute dedupe and `queue_landing_page_warm`

## QA Checklist (verified 2026-04-07)

| Claim | Verified at | Status |
|---|---|---|
| Random strip endpoint calls `get_landing_players_payload_with_cache_metadata` | `views.py:972` | ✅ |
| `_build_random_landing_players` materializes eligible_ids before sampling | `landing.py:1562-1577` | ✅ |
| No build lock around random rebuild | `landing.py:2125-2137` | ✅ no `cache.add` lock |
| Namespace embedded in published key | `landing.py:495-501` | ✅ `n{namespace}:published:` |
| `invalidate_landing_player_caches` bumps namespace | `landing.py:762` | ✅ |
| Called on every player update | `data.py:4785, 3898` | ✅ |
| `is_dirty=True` suppresses published fallback | `landing.py:619` | ✅ defaults to False |
| Republish dispatch deduped 5 min | `tasks.py:40, 268` | ✅ `cache.add` with 5 min timeout |
| Best builders use indexed `LIMIT 1200` | `landing.py:1645` | ✅ |
| `LANDING_PLAYER_CACHE_TTL = 6h` (rules out TTL expiry as cause) | `landing.py:71` | ✅ |
| `update_player_data` has 1400-min freshness gate | `data.py:4675` | ✅ — doesn't help, hot-entity warmer + visits still trigger plenty |
