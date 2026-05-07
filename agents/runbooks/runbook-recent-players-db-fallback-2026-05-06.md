# Runbook: DB-durable snapshot fallback for the landing recent-players cache

_Created: 2026-05-06_
_Status: shipped (v1.12.25)_

## Context

The landing-page "recent players" surface (`/api/landing/recent/`) is rebuilt every 3 hours by `warm_landing_recent_players_task`, then served from Redis with no TTL (`LANDING_RECENT_PLAYERS_CACHE_TTL = None`). Production Redis is configured `allkeys-lru` with a 3 GB hard cap (see `runbook-cache-capacity-expansion-2026-05-02.md`), so under memory pressure the recent-players key gets evicted. Pre-fix, the next user read fell straight through to an inline `_build_recent_players()` rebuild — a multi-hundred-millisecond SQL aggregation that blocks the request. Symptom: "the recent players list is always slow."

The fix gives Redis a durable backstop. Read path is now a 3-tier fallback chain; eviction can no longer trigger an inline rebuild on the request path.

## The 3-tier read path

```
get_landing_recent_players_payload(realm)
    │
    ├── Tier 1: cache.get(redis)             — ~5 ms steady state
    │     └─ hit → return
    │     └─ miss ↓
    │
    ├── Tier 2: LandingRecentPlayersSnapshot — ~10 ms (DB row, ≤3 h stale)
    │     └─ hit → re-warm Redis + return
    │     └─ miss ↓
    │
    └── Tier 3: inline _build_recent_players  — 500 ms – 2 s (cold start only)
          └─ writes BOTH stores, logs a WARNING, returns
```

After cold start, every read hits Tier 1 or Tier 2. Tier 3 fires once per realm on deploy and never again until both stores are simultaneously wiped.

## Components

- **Model:** `LandingRecentPlayersSnapshot` (`server/warships/models.py`) — one row per realm, JSON `payload_json`, `generated_at` auto-now timestamp.
- **Migration:** `0059_landingrecentplayerssnapshot.py`.
- **Materializer:** `materialize_landing_recent_players_snapshot(realm)` in `server/warships/landing.py` — single source of truth that writes BOTH the DB row AND the Redis cache. Write order is **DB-first, Redis-second** by design; a failed Redis write after a successful DB write degrades gracefully (Tier 2 picks up).
- **Periodic task:** `warm_landing_recent_players_task(realm)` in `server/warships/tasks.py` — thin wrapper around the materializer. Scheduled every 180 min via `recent-players-warmer-{realm}` PeriodicTask (registration in `signals.py`, unchanged).
- **Read path:** `get_landing_recent_players_payload(force_refresh, realm)` in `landing.py` implements the 3-tier fallback.

## Staleness contract

- **Tier 1 hit:** as fresh as the last warmer tick (≤3 h via the existing periodic).
- **Tier 2 hit:** identical to Tier 1 — both stores are written by the same materializer in the same task invocation.
- **Tier 3 hit:** rebuilt at request time (live data).
- **Worst case:** 3 hours. Acceptable per user spec ("3–6 hours of staleness is fine").

To tighten staleness later, reduce `LANDING_RECENT_PLAYERS_WARM_MINUTES` env var (default 180). No code change needed.

## Deploy ordering invariant

`server/deploy/deploy_to_droplet.sh` runs `manage.py migrate --noinput` BEFORE restarting gunicorn/celery, so the `LandingRecentPlayersSnapshot` table exists by the time the new code path runs. No deploy-time backfill needed — Tier 3 handles cold start once per realm naturally.

## Operational diagnostics

If a user reports "recent players is slow":

1. **Check the WARNING log.** Tier 3 logs `recent_players: cold-start fallback to inline rebuild (realm=...)`. Steady-state should be silent. If you see Tier 3 firing repeatedly, both stores are evicting/disappearing — investigate Redis pressure AND why the DB snapshot isn't sticking.

2. **Confirm DB snapshot exists per realm:**
   ```python
   from warships.models import LandingRecentPlayersSnapshot
   list(LandingRecentPlayersSnapshot.objects.values('realm', 'generated_at'))
   ```
   Each active realm should have a row. `generated_at` should be within the last 3 hours.

3. **Confirm Redis has the key (when not evicted):**
   ```bash
   redis-cli GET na:landing:recent_players:recent25:v1 | head -c 200
   ```

4. **Force a rebuild** (mutates production):
   ```python
   from warships.landing import materialize_landing_recent_players_snapshot
   materialize_landing_recent_players_snapshot('na')
   ```
   This rebuilds the DB row AND re-warms Redis in one shot.

## Tests

5 new tests in `server/warships/tests/test_landing.py::LandingRecentPlayersSnapshotTests`:

1. `test_redis_hit_does_not_query_db_or_rebuild` — Tier 1 fast path is short-circuited.
2. `test_redis_miss_falls_back_to_db_snapshot_and_rewarms_redis` — Tier 2 + Redis re-warm.
3. `test_cold_start_materializes_inline_and_writes_both_stores` — Tier 3 + dual-write.
4. `test_warmer_writes_both_redis_and_db` — periodic task hits the materializer.
5. `test_realm_isolation_per_snapshot_row` — NA snapshot doesn't satisfy EU read.

## Out of scope

- **Generalizing the snapshot pattern across all landing surfaces.** Only two surfaces use it (best + recent). YAGNI.
- **Surfacing `generated_at` to the frontend** as a "last updated" indicator. Field exists in the model; wire to a response header in a follow-up if needed.
- **Reducing the warmer cadence below 3 h.** User accepted 3–6 h staleness; cadence stays.
- **Eviction simulation in CI.** Pattern is well-tested with mocks; wiring up a real Redis-eviction integration test isn't worth the CI complexity.

## Related runbooks

- `runbook-cache-capacity-expansion-2026-05-02.md` — Redis cap + LRU policy that triggers the eviction
- `runbook-recent-players-recency-filter-2026-05-04.md` — the eligibility math the snapshot persists
- `runbook-claude-skills-rollout-2026-04-26.md` — the skills runtime context (unrelated, but referenced for runbook conventions)
